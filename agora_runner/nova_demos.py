"""The demo registry: which slug is being served, from which pod and port.

Idea #135, the first slice of the live-demo roadmap in
`nova/resources/ideas/live-demo-three-environments.md`. The owner wants to
say "build a demo of this" and get a link he can open on his phone during a
meeting. The link is a path on Nova -- `nova.tailc83eb3.ts.net/demo/<slug>/`
-- reverse-proxied to a dev server running in the bridge pod, because that
is the pod with node on it and because one pod in `agents` can reach
another's IP directly (measured Cycle 442: HTTP 200 across the hop, no
Service).

**The registry has to be in the vault and not on a disk**, and that is the
one structural thing worth stating up front. `tools.demo` runs in the bridge
pod and `nova_site.py` runs in the nova-site pod: different filesystems,
nothing shared. The vault is the only medium both already speak, and it is
the same medium `claims.json` uses one module over.

**Port allocation is this document, and there is exactly one of them.**
`live-demo-three-environments.md` says port allocation must be
concurrency-safe from the first line and warns against inventing a second
allocator, because two overlapping cycles both picking 5173 means one demo
silently serving the other's page. So the registry *is* the allocator: a
port is free if and only if no entry in this document holds it, and the
document is written back with CouchDB's compare-and-swap (`--if-rev`), the
same atomicity `nova_claims` rests on. Losing the swap means somebody
allocated in between and the caller re-reads -- it never means two owners.

I deliberately did not put port leases into `claims.json` itself, which is
the literal reading of "reuse claims.json". That ledger is the handoff-item
lock every cycle's tooling reads and rewrites, and `prune` only drops claims
marked done -- so a demo that outlives its cycle would leave a permanent row
in the file, which is exactly the leak Cycle 343 found one layer down. What
that instruction protects is having one place that decides who owns a port.
This is that place; it is not a second one.
"""

import json
import os
import re
from datetime import datetime

#: Where the registry lives, beside `claims.json` in Nova's own resources.
DEMOS_PATH = "projects/sokrates/projects/agora/nova/resources/demos.json"

#: The port window `tools.demo` allocates out of. Thirty ports because the
#: bridge pod is one pod and a demo is a dev server the owner looks at for
#: minutes -- this is not a cap standing in for a measurement, it is the
#: range the allocator scans, and a demo that cannot get a port is told so
#: rather than served on a port somebody else holds. Above 5173 because that
#: is Vite's default and a demo started by hand outside this tool will have
#: taken it.
PORT_MIN = 5174
PORT_MAX = 5203

#: Same rule as a claim slug, and for the same reason: the whole mechanism
#: is string equality, so `Foo` and `foo` must not be two demos. Also the
#: slug lands in a URL path, so nothing here may need escaping.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")

#: The fallback shared checkout, matching `agora-claude-bridge`'s
#: `config.CLAUDE_WORKSPACE`. Only used to derive a root when the
#: environment says nothing, which is the case in a unit test.
DEFAULT_WORKSPACE = "/data/workspace"


def concurrent_root(environ=None):
    """Where a per-turn workspace lives, derived the way the bridge derives it.

    `agora-claude-bridge`'s `_concurrent_root` is
    `CLAUDE_CONCURRENT_ROOT or CLAUDE_WORKSPACE.rstrip("/") + "-concurrent"`,
    and both of those are environment variables set on the pod this runs in.
    So this reads the same rule off the same environment rather than keeping
    a copy of today's answer: my reviewer pointed out that the literal
    `/data/workspace-concurrent` this used to hold would go quietly wrong the
    moment either variable moved, and `_workspace_for`'s own docstring
    records that this convention already moved once, on 2026-08-23.
    """
    env = os.environ if environ is None else environ
    override = env.get("CLAUDE_CONCURRENT_ROOT")
    if override:
        return override
    return env.get("CLAUDE_WORKSPACE", DEFAULT_WORKSPACE).rstrip("/") + "-concurrent"

#: Where a demo's files should live instead: outside any per-turn slot, on
#: the same persistent volume, and not a checkout `tidy_workspace` sweeps.
#: `tools.demo`'s own docstring has used this path in its example since the
#: command was written; nothing enforced it.
DURABLE_ROOT = "/data/workspace/demos"

#: What `vault_tool.py get` prints for a path holding no document. It exits
#: 0 and prints this, so the first caller is handed a sentence rather than
#: an empty file. Anchored both ends: a real registry cannot start with it.
_ABSENT_RE = re.compile(r"\[not found: [^\]]*\]\s*$")


class DemoError(Exception):
    """The registry, or the request, is not something we can act on."""


def load(text):
    """Parse registry text. Absent or blank is an empty registry."""
    text = (text or "").strip()
    if not text or _ABSENT_RE.match(text):
        return {"demos": []}
    try:
        registry = json.loads(text)
    except ValueError as exc:
        raise DemoError(f"registry is not JSON: {exc}") from exc
    if not isinstance(registry, dict) or not isinstance(registry.get("demos"), list):
        raise DemoError("registry must be an object with a 'demos' list")
    return registry


def dumps(registry):
    """Serialise for writing back to the vault."""
    return json.dumps(registry, indent=2, sort_keys=True) + "\n"


def entries(registry):
    """Every registered demo, newest first."""
    return sorted(
        registry.get("demos", []),
        key=lambda d: d.get("started_at", ""),
        reverse=True,
    )


def lookup(registry, slug):
    """The entry for `slug`, or None. Exact match only -- see SLUG_RE."""
    for demo in registry.get("demos", []):
        if demo.get("slug") == slug:
            return demo
    return None


def ephemeral_reason(directory, slug="<slug>", environ=None):
    """Why serving `directory` would hand out a link that dies, or None.

    Measured Cycle 605, because the failure is silent in the worst way. A
    demo's dev server is spawned with `start_new_session`, so it outlives
    the cycle that started it on purpose -- but a concurrent turn's
    workspace is `shutil.rmtree`'d in the bridge's own `finally`, always.
    The process therefore survives with its content gone: `python3 -m
    http.server` kept answering on a deleted directory and served a stdlib
    404 error page, while `verdict` -- which asks whether the pid is alive,
    not whether the files are -- still read the row as `running`. So the
    registry says running, the link answers, and the demo is not there.

    Two roots count, and the second is the one that cannot go stale: the
    derived `concurrent_root`, and `$NOVA_WORKSPACE` itself whenever the
    bridge handed this turn a private one. The second needs no derivation at
    all -- it is the directory this turn was given, read off the environment
    the bridge exported -- so it still answers if the path convention moves
    again.

    Containment is a prefix test on the *resolved* path: `realpath`, not
    `abspath`, because a symlink at a durable path pointing into a slot is
    still storage that vanishes, and my reviewer found that `abspath` calls
    it safe. And it compares against `root + os.sep` rather than raw, so
    `/data/workspace-concurrent-notes` is not read as inside
    `/data/workspace-concurrent`.
    """
    env = os.environ if environ is None else environ
    roots = [concurrent_root(env)]
    mine = env.get("NOVA_WORKSPACE", "")
    shared = env.get("CLAUDE_WORKSPACE", DEFAULT_WORKSPACE)
    if mine and os.path.normpath(mine) != os.path.normpath(shared):
        roots.append(mine)
    path = os.path.realpath(directory)
    root = next((os.path.realpath(r) for r in roots
                 if path == os.path.realpath(r)
                 or path.startswith(os.path.realpath(r) + os.sep)), None)
    if root is None:
        return None
    return (
        f"{path} is inside {root}, which this turn deletes when it ends -- "
        f"the dev server would outlive it and serve an empty directory, and "
        f"the registry would still read `running`. Put the demo's files "
        f"somewhere that survives the turn, e.g. {DURABLE_ROOT}/{slug}, and "
        f"start it from there."
    )


def _free_port(registry):
    taken = {d.get("port") for d in registry.get("demos", [])}
    for port in range(PORT_MIN, PORT_MAX + 1):
        if port not in taken:
            return port
    raise DemoError(
        f"every port in {PORT_MIN}-{PORT_MAX} is registered; "
        "stop a demo before starting another"
    )


def register(registry, slug, host, directory, now=None, command=None):
    """Add a demo and return its allocated port.

    Refuses a slug that is already registered rather than replacing it: a
    silent replace would leave the old dev server running on a port nothing
    points at any more, which is a leak nobody would ever see.
    """
    if not SLUG_RE.match(slug or ""):
        raise DemoError(
            f"slug {slug!r} must be lowercase letters, digits and hyphens, "
            "2-40 characters, starting with a letter or digit"
        )
    if lookup(registry, slug) is not None:
        raise DemoError(f"demo {slug!r} is already registered; stop it first")
    if not host:
        raise DemoError("a demo needs a host to proxy to")
    port = _free_port(registry)
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    registry.setdefault("demos", []).append({
        "slug": slug,
        "host": host,
        "port": port,
        "dir": directory,
        "command": command,
        "started_at": stamp,
    })
    return port


def unregister(registry, slug):
    """Drop a demo. Returns the entry, or None if it was not registered."""
    demos = registry.get("demos", [])
    for i, demo in enumerate(demos):
        if demo.get("slug") == slug:
            return demos.pop(i)
    return None


#: The four states a registry row can be in, from the pod that would have
#: to signal it. `ALIVE` is the only one whose `pid` may be signalled, and
#: `STARTING` is the only one that is none of the operator's business yet.
ALIVE = "running"
STARTING = "starting"
POD_GONE = "pod-gone"
PROCESS_GONE = "process-gone"


def verdict(entry, host, pid_alive):
    """What this row is, judged from the pod whose address is `host`.

    Measured Cycle 551: `bakeoff` was registered on 10.42.0.84:5174 and the
    bridge pod had since rolled to 10.42.0.56. The dev server died with the
    pod it ran in, the row stayed, and `list` printed it as if it were
    serving. Two consequences, and the second is the one that matters.

    The visible one is a leak: the row holds port 5174 against the
    allocator forever, and after enough rolls every port in the window is
    held by a demo that has not existed for days.

    The one that bites is that **a pid is only meaningful inside the pod
    that created it.** `stop` signalled `os.getpgid(pid)` with no reference
    to `host`, so running it against a row from a dead pod signals whatever
    now holds pid 311849 *here* -- an unrelated process group in a live pod,
    killed on the strength of a two-day-old number. So the host check comes
    first and a row from another pod is never signalled, only dropped.

    **`STARTING` exists because the first version of this reaped a demo
    that was working.** My reviewer reproduced it: `tools.demo start`
    reserves the port and writes the row *before* it spawns anything, and
    writes the `pid` a second later -- so for about a second the row sits on
    the right host with no `pid` key at all. Reading that as "the process is
    gone" is true of the field and false of the world, and a concurrent
    `reap` would drop the row, which then makes `start` kill its own healthy
    server and fail. So a pid-less row is `STARTING` and nothing collects
    it; `stop` still clears one by hand, which is the escape for the rare
    row stranded between those two writes.

    `pid_alive` is a **callable**, not a value, and that is the second
    reviewer finding rather than a style choice: evaluated eagerly it fires
    `os.kill` at a pid recorded by a pod that no longer exists, which is the
    exact thing the paragraph above says must never happen. Harmless today
    because the probe is signal 0, and the ordering has to hold in the code
    rather than in the comment.
    """
    if entry.get("host") != host:
        return POD_GONE
    pid = entry.get("pid")
    if not pid:
        return STARTING
    return ALIVE if pid_alive(pid) else PROCESS_GONE


def started_epoch(entry):
    """`started_at` as a POSIX timestamp, or None if it is unreadable.

    `register` writes a naive local isoformat, so this is naive-local too
    and both ends of every comparison here come from the same clock.
    """
    stamp = entry.get("started_at")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None


def idle_seconds(entry, activity, now):
    """How long since anyone asked for this demo, in seconds.

    Idea #136's other half: a demo nobody is looking at runs until the pod
    dies. The site is the only thing that knows whether anyone is looking,
    because every request for a demo goes through its `/demo/<slug>/`
    proxy, so `activity` is what `/api/demo/activity` returned --
    `{"started_at": <epoch>, "last_seen": {slug: epoch}}`.

    **The site's own start time is a floor on the answer, and leaving it
    out is what would kill a demo somebody was watching.** `last_seen`
    lives in that pod's memory and is gone when it rolls, so a minute after
    a site deploy every demo has no recorded request and would read as idle
    since whenever it started -- which for a two-day-old demo is instantly
    reapable. Taking the later of "when the demo started" and "when the
    site started" says the honest thing instead: nobody has asked for this
    since the earliest moment I could have noticed, and the clock restarts
    on a roll rather than the demo dying under it.

    Returns None when the answer is unknowable -- no site start time, or a
    row with no readable `started_at` and no recorded request. None is not
    zero and callers must not reap on it.
    """
    activity = activity or {}
    floor = activity.get("started_at")
    if floor is None:
        return None
    last = activity.get("last_seen", {}).get(entry.get("slug"))
    if last is None:
        last = started_epoch(entry)
    if last is None:
        return None
    return max(0.0, now - max(last, floor))



def opened_by_a_person(user_agent):
    """True when this request for a demo came from somebody's browser.

    `no_recorded_open` below only works if "opened" means a person opened it,
    and the site cannot ask. This is the narrow version of that question:
    every browser in use sends a `User-Agent` containing `Mozilla`, for
    historical reasons nobody is going to undo, and nothing else here does
    -- `python3 -m urllib`, `curl`, `kube-probe` and `Go-http-client` all
    announce themselves.

    **It exists because the check that proves a demo works defeats the
    clock that keeps it alive.** `prompt.md` requires a cycle to fetch its
    own demo through the real public route before handing over the link,
    and Cycle 606 did exactly that: one `urllib` GET, and the demo it had
    just started for the owner to open in the morning was recorded as
    already opened and put back on the two-hour idle clock. Shipping
    `no_recorded_open` without this would have been a guard that reports itself
    working and guards nothing, in the one flow that uses it.

    Wrong in the safe direction on purpose. A real open that is not counted
    leaves the demo on the *longer* unopened clock and costs nothing; a
    probe counted as an open is the failure above.
    """
    return "Mozilla" in (user_agent or "")


def no_recorded_open(entry, activity):
    """True when the site has no record of anyone asking for this demo.

    Named for what it can actually see. It was `never_opened` for one
    commit, and my reviewer was right that the name asserts a fact about
    history this cannot know: after a site roll it says `True` about a demo
    the owner has opened twenty times.

    `idle_seconds` above answers *how long* since anyone looked, and it
    cannot tell these two apart, because both come back as "no recorded
    request":

    - a demo that was opened in a meeting and has gone quiet since, and
    - a demo that has been handed over and not opened yet.

    They want opposite treatment and the second one is the whole point of
    the feature. `reap --idle 120` stopping the first is the port hygiene
    idea #136 asked for; stopping the second guarantees that a link handed
    over at 03:00 is dead before the owner wakes -- and every one of his 161
    comments between 2026-08-10 and 2026-08-28 falls between 05:00 and
    23:00 Oslo, none at all between midnight and 05:00. So roughly half of
    this loop's cycles could never complete the hand-off this roadmap is
    for, which is why three cycles running wrote "wait for a morning" into
    the handoff instead of doing it.

    **The measurement of the age does not change and must not.**
    `idle_seconds` floors the clock at the site's own start time because
    `last_seen` lives in that pod's memory, so a site roll wipes it and a
    two-day-old demo would otherwise read as instantly reapable. Only the
    *threshold* the caller compares against changes. That is also why a
    demo that was opened before a roll used to come back here as `True`:
    after the roll the site genuinely could not tell, and the safe direction
    is the long clock, because the cost of being wrong is one of thirty
    ports and the cost of the other error is the link going dead in the
    owner's hand. The durable mark below is what removed that case; the safe
    direction still governs everything it does not cover.

    The numbers above were measured against the comments board on
    2026-08-29 and are not derivable from anything in this repo, so treat
    them as an observation with a date on it rather than a fact this code
    can re-check.

    `False` when the site did not answer at all -- `idle_seconds` returns
    `None` there and nothing is reaped on idle either way, so this never
    decides anything in that case.

    **The durable mark is checked first and it is what makes the paragraph
    above smaller than it was.** `mark_opened` writes `opened_at` into the
    registry row the first time a browser asks for a demo, and the registry
    survives a site roll, so "opened before a roll" no longer comes back
    here as `True`. What still does is a demo opened before Cycle 608
    shipped this -- those rows carry no `opened_at` and never will -- and a
    demo whose first open happened while the vault write was losing a
    compare-and-swap. Both fall back to the in-memory answer, which is the
    old behaviour and still errs onto the long clock.

    `activity` is still required to be present: with no site to ask, the
    caller learns nothing about *when* anyone looked, `idle_seconds` returns
    `None`, and nothing is reaped on idle either way. Answering `False`
    there on the strength of `opened_at` alone would be a claim about a
    clock this cannot read.
    """
    activity = activity or {}
    if activity.get("started_at") is None:
        return False
    if entry.get(OPENED_AT):
        return False
    return activity.get("last_seen", {}).get(entry.get("slug")) is None

# ---------------------------------------------------------------------------
# Promotion -- idea #138, "keep this" turns a demo into a real service.
#
# **The merge is the API.** Nothing here calls a platform service, because
# there is no platform service and idea #138 is where that call was made:
# the only step in the roadmap that needs a Crossplane claim is promotion,
# promotion is human-gated by definition ("keep this" is the owner
# deciding), and a claim commit reached both repos in 4m34s when it was
# timed. So promotion renders a `GitHubService` claim, opens a pull request
# on `platform-config`, and the tap that merges it is the whole API.
#
# Two phases on purpose, and they cannot be one call. The claim creates the
# repository, and the repository does not exist until Crossplane has
# reconciled the merge -- minutes later. So `promote` opens the PR and
# `ship` pushes the demo's source once the repo answers. A single command
# would have to block for minutes on something that may never happen if the
# PR is never tapped, which is precisely the state this design refuses to
# sit in.

#: The XRD's own `serviceName` pattern, copied rather than imported: this
#: module cannot read the cluster, and a name this rejects is one the claim
#: would be refused for on apply, hours after the PR was opened.
SERVICE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

#: Where `platform-config` keeps one claim per service.
CLAIM_DIR = "crossplane"


def claim_path(name):
    """Repo-relative path of the claim file for a service."""
    return f"{CLAIM_DIR}/service-{name}.yaml"


def promotion_branch(name):
    return f"nova/promote-{name}"


def check_promotable(entry, name):
    """Raise `DemoError` unless this demo can become the service `name`.

    Every check here is one a later step would fail on anyway -- on apply,
    or on push -- and failing there means a pull request the owner taps and
    which then quietly does nothing. The point is to refuse before anything
    is written.
    """
    if entry is None:
        raise DemoError("no such demo is registered")
    if not SERVICE_NAME_RE.match(name or ""):
        raise DemoError(
            f"{name!r} is not a legal service name: the GitHubService XRD "
            "requires lowercase letters, digits and hyphens, starting and "
            "ending with a letter or digit"
        )
    if len(name) > 63:
        raise DemoError(
            f"{name!r} is {len(name)} characters; it also becomes a Kubernetes "
            "object name, which stops at 63"
        )
    # `dir`, not `directory` -- that is the key `register` writes, and the
    # first version of this read `directory`, which is absent from every
    # entry the registry has ever held. It refused a real demo with a
    # sentence blaming whatever registered it.
    directory = entry.get("dir")
    if not directory:
        raise DemoError(
            "this demo has no directory recorded, so there is no source to "
            "promote -- it was registered by something that is not `demo start`"
        )
    return directory


def promotion_claim(name, description, demo_url, directory, today):
    """The `GitHubService` claim text for a promoted demo.

    Rendered rather than templated from a file so the tests can read the
    result without a checkout. Only `serviceName` is required by the XRD;
    `visibility` is left at the schema default (private) deliberately --
    going public is a three-commit dance the XRD documents at length, and
    doing it implicitly on a promotion would be irreversible for anything
    already cloned.
    """
    # **Collapse the description to one line before it is rendered.** It is
    # emitted as a folded scalar with a single indented line under it, so a
    # newline in the text would end the scalar and the rest would be parsed
    # as YAML at the wrong indentation -- a claim that either fails to apply
    # or, worse, applies as something else. Collapsing is the fix rather than
    # refusing, because the description is prose the owner typed and a
    # line break in it is not a mistake he should have to hear about.
    description = " ".join((description or f"{name}, promoted from a Nova demo.").split())
    return "\n".join([
        f"# {name} -- promoted from the live demo `{demo_url}` on {today}.",
        "#",
        "# Written by `tools.demo promote` (idea #138). The demo itself ran as a",
        f"# dev server out of `{directory}` on the bridge pod and was reachable",
        "# only while that pod lived; merging this claim is what makes it a real",
        "# service with its own repo, CI, image and tailnet hostname.",
        "#",
        "# The source is NOT in this claim. The composition seeds a Node skeleton",
        "# on first reconcile and then hands off (every RepositoryFile is",
        "# [Observe, Create, LateInitialize], no Update), so the demo's own files",
        "# land as an ordinary commit afterwards -- `tools.demo ship " + name + "`",
        "# does that once this merge has produced the repo.",
        "apiVersion: platform.sokratesai.io/v1alpha1",
        "kind: GitHubService",
        "metadata:",
        f"  name: {name}",
        "  namespace: platform-catalog",
        "spec:",
        f"  serviceName: {name}",
        "  description: >-",
        f"    {description}",
        "",
    ])


#: The registry key holding the durable "a person has opened this" mark.
#: An ISO-8601 local timestamp, written once and never cleared, because the
#: question it answers is about history and history does not go back.
OPENED_AT = "opened_at"


def mark_opened(registry, slug, now=None):
    """Record that a person has opened `slug`. True if that changed anything.

    `no_recorded_open` below is the thing this exists for. Until now the
    only evidence of an open was `nova_site._demo_last_seen`, which lives in
    the site pod's memory -- so every site roll made every demo look
    unopened again and moved it from the two-hour idle clock onto the
    eighteen-hour one. Cycle 606 filed that as a known residual and Cycle
    608 is fixing it: the cost of leaving it is one of thirty ports, which
    is small, but it is also the one signal `tools.demo list` prints to say
    whether the hand-off this whole roadmap is for has actually happened.
    A field that goes back to "no recorded open" after a deploy cannot
    answer that.

    Writing once is the whole design. This is a CouchDB document and a demo
    page is forty assets, so a mark written per request would be the cost
    `nova_site.DEMO_REGISTRY_TTL` exists to avoid; returning False when the
    field is already there is what lets the caller skip the write entirely.
    It is deliberately *not* a second `last_seen` -- how recently somebody
    looked stays in memory, where it is cheap and where losing it on a roll
    is handled by `idle_seconds`' floor.
    """
    entry = lookup(registry, slug)
    if entry is None or entry.get(OPENED_AT):
        return False
    entry[OPENED_AT] = (now or datetime.now()).isoformat(timespec="seconds")
    return True
