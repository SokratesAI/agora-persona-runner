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


#: The three states a registry row can be in, from the pod that would have
#: to signal it. `RUNNING` is the only one whose `pid` may be signalled.
ALIVE = "running"
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
    that created it.** `stop` signals `os.getpgid(pid)` with no reference to
    `host`, so running it against a row from a dead pod signals whatever
    now happens to hold pid 311849 *here* -- an unrelated process group in
    a live pod, killed on the strength of a two-day-old number. So the host
    check comes first and a row from another pod is never signalled, only
    dropped.

    `pid_alive` is passed in rather than probed here so this stays a pure
    function; the caller owns the one syscall.
    """
    if entry.get("host") != host:
        return POD_GONE
    if not entry.get("pid"):
        return PROCESS_GONE
    return ALIVE if pid_alive else PROCESS_GONE
