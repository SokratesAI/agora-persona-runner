"""Start, stop and list live demos. Run this from the bridge pod.

Idea #135, first slice of the live-demo roadmap. The owner wants to be
handed a link in a meeting and open it on his phone. This is the half that
runs the thing; `nova_site.py`'s `/demo/<slug>/` route is the half that
serves it.

    cd "$NOVA_WORKSPACE/agora-persona-runner"
    python3 -m tools.demo start bakeoff /data/workspace/demos/bakeoff
    python3 -m tools.demo list
    python3 -m tools.demo stop bakeoff

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

Exit codes: **0 did what it says, 2 the request is refused (slug taken, no
free port, no such demo), 1 something is wrong.**
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
    DEMOS_PATH,
    POD_GONE,
    PROCESS_GONE,
    STARTING,
    DemoError,
    dumps,
    entries,
    idle_seconds,
    load,
    lookup,
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
