"""Start, stop and list live demos. Run this from the bridge pod.

Idea #135, first slice of the live-demo roadmap. The owner wants to be
handed a link in a meeting and open it on his phone. This is the half that
runs the thing; `nova_site.py`'s `/demo/<slug>/` route is the half that
serves it.

    cd "$NOVA_WORKSPACE/agora-persona-runner"
    python3 -m tools.demo start bakeoff /data/workspace/demos/bakeoff
    python3 -m tools.demo list
    python3 -m tools.demo stop bakeoff
    python3 -m tools.demo promote bakeoff     # "keep this" -- opens the claim PR
    python3 -m tools.demo ship bakeoff        # ...once that PR is merged

**Why the bridge pod specifically:** it is the pod with node (v20.20.2) and
npm on it; the runner pod has neither. Measured Cycle 442, along with the
hop this rests on -- a plain pod IP in `agents` answers another pod in
`agents`, no Service, because the only network policies there are
`allow-intra-namespace-{in,e}gress` over a default deny.

**Allocation is a compare-and-swap on one vault document.** Read
`demos.json` with its revision, pick a port nothing holds, write it back
`--if-rev`. Losing the swap means a concurrent cycle allocated in between,
and the answer is to re-read rather than to retry the write -- so exit 3
from the `put` is "start over", never "force it". Same mechanism and same
reasoning as `claims.json`; see `agora_runner/nova_demos.py` for why the
registry is its own document rather than rows inside that ledger.

**A demo's files have to live outside this turn's workspace.** `start`
refuses a directory under `/data/workspace-concurrent/`, because the bridge
removes a concurrent turn's slot in its own `finally` while the dev server
-- started in its own session on purpose -- keeps running and serving the
hole it left. Scaffold under `/data/workspace/demos/<slug>` instead.

Exit codes: **0 did what it says, 2 the request is refused (slug taken, no
free port, no such demo, a directory this turn deletes), 1 something is
wrong.**
"""

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

# Repo root on sys.path so `python3 tools/demo.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_demos import (  # noqa: E402
    ALIVE,
    CLAIM_DIR,
    DEMOS_PATH,
    POD_GONE,
    PROCESS_GONE,
    STARTING,
    DemoError,
    check_promotable,
    claim_path,
    dumps,
    entries,
    ephemeral_reason,
    idle_seconds,
    load,
    lookup,
    promotion_branch,
    promotion_claim,
    register,
    unregister,
    verdict,
)

VAULT_TOOL = "/app/bridge/vault_tool.py"

#: How long to wait before deciding a dev server started. A bind failure
#: and an import error both happen in well under a second; a slow compile
#: happens after the socket is listening, so this does not have to cover
#: it.
SPAWN_CHECK_SECONDS = 1.0

#: Where the owner opens it. One hostname, path-routed, rather than a tailnet
#: device per demo that would outlive the demo it was minted for.
PUBLIC_BASE = "https://nova.tailc83eb3.ts.net/demo"

#: The site's in-cluster address. Every request for a demo goes through its
#: `/demo/<slug>/` proxy, so it is the only thing that knows whether anyone
#: is looking. Reachable from either pod -- measured Cycle 349 from the
#: bridge pod with plain `urllib`.
ACTIVITY_URL = "http://nova-site.agents.svc.cluster.local:8083/api/demo/activity"

#: How long a demo may go unasked-for before `reap --idle` stops it. Two
#: hours because the thing being protected is a demo left open in a meeting
#: that resumes after lunch, and the thing being spent is one of thirty
#: ports. Override per call; nothing reaps on idle unless asked.
DEFAULT_IDLE_MINUTES = 120


def fetch_activity(url=ACTIVITY_URL, timeout=10):
    """`/api/demo/activity`, or None if the site did not answer.

    None means "I do not know who is looking", and every caller treats that
    as "reap nothing on idle" rather than as "nobody is looking". A site
    that is down is the case where guessing kills a live demo.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"could not read demo activity from the site ({e}); "
              f"idle is unknown", file=sys.stderr)
        return None


def _run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


_TEMPS = []


def _temp(**kw):
    """A temp path this process removes on the way out.

    `delete=False` is required -- the vault client opens these by name --
    so nothing removes them unless something remembers to. Three per
    invocation, forever, in a module whose whole subject is not leaking.
    """
    fh = tempfile.NamedTemporaryFile(delete=False, **kw)
    fh.close()
    _TEMPS.append(fh.name)
    return fh.name


def _cleanup_temps():
    for path in _TEMPS:
        try:
            os.unlink(path)
        except OSError:
            pass


def _read_registry():
    """Registry text plus the rev file guarding the write-back."""
    rev = _temp(prefix="demos.", suffix=".rev")
    got = _run(["python3", VAULT_TOOL, "get", DEMOS_PATH, "--rev-file", rev])
    if got.returncode != 0:
        raise DemoError(f"could not read {DEMOS_PATH}: {got.stderr.strip()[:300]}")
    return load(got.stdout), rev


def _write_registry(registry, rev_path):
    body = _temp(suffix=".json")
    with open(body, "w") as fh:
        fh.write(dumps(registry))
    put = _run(["python3", VAULT_TOOL, "put", DEMOS_PATH, body, "--if-rev-file", rev_path])
    if put.returncode == 3:
        raise DemoError(
            "another cycle wrote the registry while this one was allocating; "
            "nothing was changed -- run the command again")
    if put.returncode != 0:
        raise DemoError(f"could not write {DEMOS_PATH}: {put.stderr.strip()[:300]}")


def pod_ip():
    """This pod's address on the cluster network.

    A UDP connect reserves nothing and sends nothing; it just asks the
    kernel which source address it would use, which is the address another
    pod has to dial. `gethostbyname(gethostname())` is the obvious version
    and it returns the loopback address on some images, which would register
    a demo nothing outside this pod can reach.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.43.0.1", 53))
        return s.getsockname()[0]


def default_command(directory, port):
    """What to run in `directory`, if the caller did not say.

    A node project gets `npm run dev`, which is what a scaffolded web demo
    ships with; anything else is served as static files, which is what a
    hand-written `index.html` needs. Both bind every interface on purpose --
    a dev server bound to localhost is reachable from nothing, which is the
    single most likely way this feature looks broken.
    """
    if os.path.exists(os.path.join(directory, "package.json")):
        return f"npm run dev -- --host 0.0.0.0 --port {port}"
    return f"python3 -m http.server {port} --bind 0.0.0.0"


def cmd_start(args):
    directory = os.path.abspath(args.directory)
    if not os.path.isdir(directory):
        print(f"no such directory: {directory}", file=sys.stderr)
        return 2
    # A demo has to outlive the cycle that started it -- that is the whole
    # point of `start_new_session` below -- and a directory inside this
    # turn's own workspace does not. See `nova_demos.ephemeral_reason`.
    doomed = ephemeral_reason(directory, args.slug)
    if doomed:
        print(f"refusing to serve a directory this turn deletes: {doomed}",
              file=sys.stderr)
        return 2
    # **Reserve the port before spawning anything.** The first version
    # spawned first and wrote the registry after, and the compare-and-swap
    # then picked a winner independently of who won the *bind*: two
    # concurrent starts both allocate 5174, one server binds and the other
    # dies with EADDRINUSE, and the loser can win the swap. It would print
    # a URL, exit 0, and have a dead process -- while the winner, losing
    # the swap, killed the only live server. The swap has to decide the
    # port before a process exists to be wrong about.
    registry, rev = _read_registry()
    port = register(registry, args.slug, pod_ip(), directory,
                    command=args.cmd or None)
    command = args.cmd or default_command(directory, port)
    log_path = os.path.join(tempfile.gettempdir(), f"demo-{args.slug}.log")
    entry = lookup(registry, args.slug)
    entry["command"] = command
    entry["log"] = log_path
    _write_registry(registry, rev)

    with open(log_path, "ab") as logfh:
        # `start_new_session` is the point of the whole line: this command
        # exits in a second and the dev server has to outlive it, including
        # the SIGHUP its process group gets when this shell goes away.
        proc = subprocess.Popen(
            shlex.split(command), cwd=directory,
            stdout=logfh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "PORT": str(port)},
        )
    # A dev server that died on startup is the case this command must never
    # report as success: the URL would 502 and the only evidence is a temp
    # log nobody knows to open.
    time.sleep(SPAWN_CHECK_SECONDS)
    if proc.poll() is not None:
        registry, rev = _read_registry()
        unregister(registry, args.slug)
        _write_registry(registry, rev)
        tail = ""
        try:
            with open(log_path) as fh:
                tail = "".join(fh.readlines()[-8:])
        except OSError:
            pass
        print(f"{command!r} exited {proc.returncode} immediately; "
              f"{args.slug} was not registered\n{tail}", file=sys.stderr)
        return 1
    registry, rev = _read_registry()
    live = lookup(registry, args.slug)
    if live is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        raise DemoError(f"{args.slug} vanished from the registry while starting")
    live["pid"] = proc.pid
    _write_registry(registry, rev)
    print(f"{PUBLIC_BASE}/{args.slug}/")
    print(f"  serving {directory} on {entry['host']}:{port} (pid {proc.pid})")
    print(f"  command: {command}")
    print(f"  log: {log_path}")
    return 0


def pid_alive(pid):
    """Does this pod hold that pid? Signal 0 checks without delivering."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists and belongs to somebody else. Alive is the safe answer:
        # the alternative frees a port something is still bound to.
        return True


def judge(entry, host):
    """`nova_demos.verdict` with the one syscall it deliberately omits.

    `pid_alive` goes in as a callable so the host check runs first -- see
    `verdict`'s docstring; passing the probe's *result* fires it at a pid
    belonging to a pod that no longer exists.
    """
    return verdict(entry, host, pid_alive)


#: What each verdict reads as on a line the owner or a cycle looks at.
VERDICT_TEXT = {
    ALIVE: "running",
    STARTING: "starting -- no pid recorded yet",
    POD_GONE: "stale -- the pod it ran in is gone",
    PROCESS_GONE: "dead -- the dev server is not running here",
}

#: What `reap` collects. `STARTING` is deliberately absent: a row with no
#: pid is one `tools.demo start` wrote a moment ago and has not finished
#: with, and reaping it makes that start kill its own healthy server.
REAPABLE = (POD_GONE, PROCESS_GONE)


def terminate(entry):
    """SIGTERM this demo's process group. Returns (may-deregister, what).

    **Only ever call this on a row `judge` calls `ALIVE`.** A pid is
    meaningful only inside the pod that recorded it and pid numbers are
    reused, so signalling a row from a pod that is gone reaches an
    unrelated process here -- see `nova_demos.verdict`.

    The `PermissionError` branch is why this returns a permission rather
    than only a message: the process is *alive* and this account cannot
    signal it, and freeing the port in the registry while something still
    holds it is the silent cross-serve `nova_demos` exists to prevent --
    the next start would allocate it, fail to bind, and serve this demo's
    page under the new slug.
    """
    pid = entry.get("pid")
    if not pid:
        return True, "no pid recorded"
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True, f"stopped pid {pid}"
    except ProcessLookupError:
        # Deregister. A registry row pointing at a dead process holds a
        # port forever and answers 502 to whoever opens it.
        return True, f"pid {pid} was already gone"
    except PermissionError as e:
        return False, f"pid {pid} is alive and could not be signalled ({e})"


def cmd_stop(args):
    registry, rev = _read_registry()
    entry = lookup(registry, args.slug)
    if entry is None:
        print(f"no demo named {args.slug!r} is registered", file=sys.stderr)
        return 2
    pid = entry.get("pid")
    killed = "no pid recorded"
    if judge(entry, pod_ip()) == POD_GONE:
        # **Never signal a pid recorded by another pod.** A pid is only
        # meaningful inside the pod that created it, and pid numbers are
        # reused: `bakeoff` sat in the registry for two days holding pid
        # 311849 from a bridge pod that no longer exists, and the code below
        # would have sent SIGTERM to whatever process group holds 311849
        # here. There is nothing of ours to stop, so this only deregisters.
        unregister(registry, args.slug)
        _write_registry(registry, rev)
        print(f"{args.slug}: the pod it ran in ({entry.get('host')}) is gone, "
              f"nothing to signal; port {entry['port']} released")
        return 0
    if pid:
        freed, killed = terminate(entry)
        if not freed:
            print(f"{args.slug}: {killed}; port {entry['port']} stays registered",
                  file=sys.stderr)
            return 1
    unregister(registry, args.slug)
    _write_registry(registry, rev)
    print(f"{args.slug}: {killed}, port {entry['port']} released")
    return 0


def cmd_list(args):
    registry, _ = _read_registry()
    rows = entries(registry)
    if not rows:
        print("no demos are running")
        return 0
    here = pod_ip()
    activity = fetch_activity()
    now = time.time()
    stale = 0
    for demo in rows:
        state = judge(demo, here)
        stale += state in REAPABLE
        age = idle_seconds(demo, activity, now) if state == ALIVE else None
        seen = ("" if age is None
                else f"  [nobody has asked for it in {int(age // 60)} min]")
        print(f"{PUBLIC_BASE}/{demo['slug']}/  [{VERDICT_TEXT[state]}]{seen}")
        print(f"  {demo['host']}:{demo['port']}  started {demo.get('started_at', '?')}"
              f"  pid {demo.get('pid', '?')}")
        print(f"  {demo.get('dir', '?')}")
    if stale:
        # A row that reads like a running demo and is not is the whole
        # failure this listing was printing before: `reap` is the button.
        print(f"\n{stale} of {len(rows)} hold a port and are not serving "
              f"anything -- `python3 -m tools.demo reap` releases them")
    return 0


def cmd_reap(args):
    """Drop every row whose demo is gone, and free its port.

    Idea #136 asks that a demo survive the owner's deploys and stop itself
    when nobody is looking. It cannot survive a roll -- the dev server dies
    with the pod, and this pod cannot restart a process in a pod that no
    longer exists -- so the honest half is that the registry stops claiming
    it did. Nothing else in this loop ever ran `stop` for a demo whose pod
    had rolled, which is why one row held port 5174 for two days.

    It leaves a `STARTING` row alone -- `verdict` has the reproduction.

    `--idle <minutes>` adds the second half: stop and deregister a demo
    that is genuinely running here and that nobody has asked for in that
    long. The site is the only thing that knows -- every request for a demo
    goes through its proxy -- so this is the one subcommand that needs the
    network, and it does nothing on idle when the site does not answer.
    """
    registry, rev = _read_registry()
    here = pod_ip()
    states = [(d, judge(d, here)) for d in entries(registry)]
    doomed = [(d, VERDICT_TEXT[v]) for d, v in states if v in REAPABLE]
    refused = []
    if args.idle is not None:
        # The other half of #136: a demo nobody is looking at. Only an
        # `ALIVE` row is a candidate -- the two above are already collected,
        # and a `STARTING` row is one `start` wrote a second ago.
        activity = fetch_activity()
        now = time.time()
        for demo, state in states:
            if state != ALIVE:
                continue
            age = idle_seconds(demo, activity, now)
            # `None` is "I do not know", not "nobody is looking": the site
            # is down, or the row predates the activity endpoint. Reaping
            # on it would stop a demo somebody is watching.
            if age is None or age < args.idle * 60:
                continue
            freed, note = terminate(demo)
            if not freed:
                refused.append((demo, note))
                continue
            doomed.append((demo, f"idle {int(age // 60)} min; {note}"))
    if not doomed:
        print("nothing to reap")
        for demo, note in refused:
            print(f"{demo['slug']}: {note}; port {demo['port']} stays registered",
                  file=sys.stderr)
        return 1 if refused else 0
    for demo, _ in doomed:
        unregister(registry, demo["slug"])
    _write_registry(registry, rev)
    for demo, why in doomed:
        print(f"{demo['slug']}: {why}; port {demo['port']} released")
    for demo, note in refused:
        print(f"{demo['slug']}: {note}; port {demo['port']} stays registered",
              file=sys.stderr)
    return 1 if refused else 0


PLATFORM_CONFIG = "SokratesAI/platform-config"
DEMO_BASE = "https://nova.tailc83eb3.ts.net/demo"


def _workspace_repo(name):
    """A checkout of `name` in this cycle's workspace, or None.

    `$NOVA_WORKSPACE` and not `/data/workspace`: a concurrent cycle gets its
    own worktree and writing to the shared one is the single thing it must
    not do.
    """
    root = os.environ.get("NOVA_WORKSPACE") or "/data/workspace"
    path = os.path.join(root, name)
    # `exists`, not `isdir`. A concurrent cycle's checkout is a `git
    # worktree`, where `.git` is a *file* pointing at the shared object
    # store -- so an `isdir` test says "no checkout here" in exactly the
    # configuration this loop runs in most of the time.
    return path if os.path.exists(os.path.join(path, ".git")) else None


def _gh_repo_exists(repo):
    """`True` / `False`, or `None` when the question could not be asked.

    Three answers rather than two on purpose. `promote` refuses when the
    repo already exists, and a failed `gh` call returning False would turn
    "I could not check" into "it is not there" -- which is the negative
    result guaranteed in advance that this repo keeps paying for.
    """
    got = _run(["gh", "repo", "view", repo, "--json", "name"])
    if got.returncode == 0:
        return True
    blob = ((got.stderr or "") + (got.stdout or "")).lower()
    if "could not resolve" in blob or "not found" in blob:
        return False
    return None


def cmd_promote(args):
    registry, _ = _read_registry()
    entry = lookup(registry, args.slug)
    name = args.name or args.slug
    directory = check_promotable(entry, name)

    repo = f"SokratesAI/{name}"
    exists = _gh_repo_exists(repo)
    if exists is None:
        print(f"could not ask GitHub whether {repo} already exists; refusing "
              "to open a claim for a repository that may be there already",
              file=sys.stderr)
        return 1
    if exists:
        print(f"{repo} already exists -- this demo has been promoted before. "
              f"Push its source with: python3 -m tools.demo ship {args.slug}",
              file=sys.stderr)
        return 2

    checkout = _workspace_repo("platform-config")
    if checkout is None:
        print("no platform-config checkout in this workspace", file=sys.stderr)
        return 1

    rel = claim_path(name)
    body = promotion_claim(
        name,
        args.description,
        f"{DEMO_BASE}/{args.slug}/",
        directory,
        time.strftime("%Y-%m-%d"),
    )
    target = os.path.join(checkout, rel)
    if os.path.exists(target):
        print(f"{rel} already exists in platform-config", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"--- {rel}")
        print(body, end="")
        return 0

    # **Never move a working tree that has work in it.** `checkout -B` below
    # rewrites the checkout's branch and files, and this runs in a workspace
    # a sibling cycle may be building in right now. Uncommitted work there
    # is not something to carry onto a promotion branch or to discover from
    # a conflict -- it is a refusal.
    dirty = _run(["git", "-C", checkout, "status", "--porcelain"])
    if dirty.returncode != 0:
        print(f"could not read the platform-config checkout: "
              f"{dirty.stderr.strip()[:300]}", file=sys.stderr)
        return 1
    if dirty.stdout.strip():
        print("the platform-config checkout has uncommitted changes; "
              "promoting would move it onto a new branch. Commit or stash "
              "them first.", file=sys.stderr)
        return 2

    branch = promotion_branch(name)
    for argv in (
        ["git", "-C", checkout, "fetch", "--quiet", "origin", "main"],
        ["git", "-C", checkout, "checkout", "--quiet", "-B", branch, "origin/main"],
    ):
        step = _run(argv)
        if step.returncode != 0:
            print(f"{' '.join(argv[3:])} failed: {step.stderr.strip()[:300]}",
                  file=sys.stderr)
            return 1
    os.makedirs(os.path.join(checkout, CLAIM_DIR), exist_ok=True)
    with open(target, "w") as fh:
        fh.write(body)
    for argv in (
        ["git", "-C", checkout, "add", rel],
        ["git", "-C", checkout, "commit", "--quiet", "-m",
         f"Promote the {args.slug} demo to a service ({name})"],
        ["git", "-C", checkout, "push", "--quiet", "-u", "origin", branch],
    ):
        step = _run(argv)
        if step.returncode != 0:
            print(f"{' '.join(argv[3:])[:80]} failed: {step.stderr.strip()[:300]}",
                  file=sys.stderr)
            return 1
    pr = _run([
        "gh", "pr", "create", "--repo", PLATFORM_CONFIG, "--head", branch,
        "--base", "main",
        "--title", f"Promote the {args.slug} demo to a service ({name})",
        "--body",
        f"`tools.demo promote {args.slug}` (idea #138). Merging this asks "
        f"Crossplane for `{repo}` and `{repo}-config` with CI, a GHCR image, "
        f"an ArgoCD Application and a tailnet hostname.\n\n"
        f"The demo's own source is not in this commit -- the composition "
        f"seeds a Node skeleton and hands off. Once the repo exists, "
        f"`python3 -m tools.demo ship {args.slug}` pushes it.",
    ])
    if pr.returncode != 0:
        print(f"gh pr create failed: {pr.stderr.strip()[:400]}", file=sys.stderr)
        return 1
    url = pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else "(no url)"
    print(f"{args.slug} -> {repo}")
    print(f"tap to keep it: {url}")
    print(f"then: python3 -m tools.demo ship {args.slug}")
    return 0


def cmd_ship(args):
    """Push the demo's source into the repo the merged claim created."""
    registry, _ = _read_registry()
    entry = lookup(registry, args.slug)
    name = args.name or args.slug
    directory = check_promotable(entry, name)
    if not os.path.isdir(directory):
        print(f"{directory} is gone -- the demo's source is not on this pod, "
              "so there is nothing to ship", file=sys.stderr)
        return 2

    repo = f"SokratesAI/{name}"
    exists = _gh_repo_exists(repo)
    if exists is None:
        print(f"could not ask GitHub whether {repo} exists", file=sys.stderr)
        return 1
    if not exists:
        print(f"{repo} does not exist yet -- the promotion PR has not been "
              "merged, or Crossplane has not reconciled it. Nothing pushed.",
              file=sys.stderr)
        return 2

    branch = f"nova/demo-source-{args.slug}"
    work = _run(["git", "-C", directory, "rev-parse", "--show-toplevel"])
    if work.returncode == 0:
        print(f"{directory} is already inside a git repository "
              f"({work.stdout.strip()}); refusing to re-init it", file=sys.stderr)
        return 2
    for argv in (
        ["git", "-C", directory, "init", "--quiet", "-b", branch],
        ["git", "-C", directory, "add", "-A"],
        ["git", "-C", directory, "-c", "user.name=Nova",
         "-c", "user.email=nova@sokratesai.io",
         "commit", "--quiet", "-m", f"The {args.slug} demo, as promoted"],
        ["git", "-C", directory, "push", "--quiet",
         f"https://github.com/{repo}.git", f"{branch}:{branch}"],
    ):
        step = _run(argv)
        if step.returncode != 0:
            print(f"{' '.join(argv[3:])[:80]} failed: {step.stderr.strip()[:300]}",
                  file=sys.stderr)
            return 1
    pr = _run([
        "gh", "pr", "create", "--repo", repo, "--head", branch, "--base", "main",
        "--title", f"The {args.slug} demo's own source",
        "--body", "The files the demo actually ran, on top of the skeleton "
                  "Crossplane seeded. Opened by `tools.demo ship`.",
    ])
    if pr.returncode != 0:
        print(f"pushed {branch} to {repo}, but gh pr create failed: "
              f"{pr.stderr.strip()[:300]}", file=sys.stderr)
        return 1
    print(f"{args.slug} source pushed to {repo}")
    print(pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else "")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="serve a directory as a demo")
    start.add_argument("slug", help="lowercase name; becomes the URL path")
    start.add_argument("directory", help="what to serve")
    start.add_argument("--cmd", default="",
                       help="override the launch command (default: npm run dev "
                            "for a node project, http.server otherwise)")
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop", help="stop a demo and release its port")
    stop.add_argument("slug")
    stop.set_defaults(func=cmd_stop)

    lst = sub.add_parser("list", help="what is running")
    lst.set_defaults(func=cmd_list)

    reap = sub.add_parser("reap", help="release ports held by demos that are gone")
    reap.add_argument("--idle", type=int, nargs="?", const=DEFAULT_IDLE_MINUTES,
                      default=None, metavar="MINUTES",
                      help=f"also stop demos nobody has asked for in this many "
                           f"minutes (default {DEFAULT_IDLE_MINUTES} when the "
                           f"flag is given with no number)")
    reap.set_defaults(func=cmd_reap)

    promote = sub.add_parser(
        "promote", help="open the claim PR that turns this demo into a service")
    promote.add_argument("slug")
    promote.add_argument("--name", default="",
                         help="repo/service name (default: the slug)")
    promote.add_argument("--description", default="",
                         help="one line for the claim's description field")
    promote.add_argument("--dry-run", action="store_true",
                         help="print the claim and open nothing")
    promote.set_defaults(func=cmd_promote)

    ship = sub.add_parser(
        "ship", help="push the demo's source once the promoted repo exists")
    ship.add_argument("slug")
    ship.add_argument("--name", default="")
    ship.set_defaults(func=cmd_ship)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DemoError as e:
        print(str(e), file=sys.stderr)
        return 2
    finally:
        _cleanup_temps()


if __name__ == "__main__":
    sys.exit(main())
