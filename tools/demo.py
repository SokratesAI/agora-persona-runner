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

# Repo root on sys.path so `python3 tools/demo.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_demos import (  # noqa: E402
    DEMOS_PATH,
    DemoError,
    dumps,
    entries,
    load,
    lookup,
    register,
    unregister,
)

VAULT_TOOL = "/app/bridge/vault_tool.py"

#: Where the owner opens it. One hostname, path-routed, rather than a tailnet
#: device per demo that would outlive the demo it was minted for.
PUBLIC_BASE = "https://nova.tailc83eb3.ts.net/demo"


def _run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def _read_registry():
    """Registry text plus the rev file guarding the write-back."""
    rev = tempfile.NamedTemporaryFile(prefix="demos.", suffix=".rev", delete=False)
    rev.close()
    got = _run(["python3", VAULT_TOOL, "get", DEMOS_PATH, "--rev-file", rev.name])
    if got.returncode != 0:
        raise DemoError(f"could not read {DEMOS_PATH}: {got.stderr.strip()[:300]}")
    return load(got.stdout), rev.name


def _write_registry(registry, rev_path):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(dumps(registry))
        body = fh.name
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
    registry, rev = _read_registry()
    port = register(registry, args.slug, pod_ip(), directory,
                    command=args.cmd or None)
    command = args.cmd or default_command(directory, port)
    log_path = os.path.join(tempfile.gettempdir(), f"demo-{args.slug}.log")
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
    entry = lookup(registry, args.slug)
    entry["pid"] = proc.pid
    entry["command"] = command
    entry["log"] = log_path
    try:
        _write_registry(registry, rev)
    except DemoError:
        # The registry is the only thing that makes this process reachable.
        # A server nothing can route to is not a demo, it is a port leak.
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        raise
    print(f"{PUBLIC_BASE}/{args.slug}/")
    print(f"  serving {directory} on {entry['host']}:{port} (pid {proc.pid})")
    print(f"  command: {command}")
    print(f"  log: {log_path}")
    return 0


def cmd_stop(args):
    registry, rev = _read_registry()
    entry = lookup(registry, args.slug)
    if entry is None:
        print(f"no demo named {args.slug!r} is registered", file=sys.stderr)
        return 2
    pid = entry.get("pid")
    killed = "no pid recorded"
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            killed = f"stopped pid {pid}"
        except (ProcessLookupError, PermissionError) as e:
            # Deregister anyway. A registry row pointing at a dead process
            # holds a port forever and answers 502 to whoever opens it.
            killed = f"pid {pid} was already gone ({e.__class__.__name__})"
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
    for demo in rows:
        print(f"{PUBLIC_BASE}/{demo['slug']}/")
        print(f"  {demo['host']}:{demo['port']}  started {demo.get('started_at', '?')}"
              f"  pid {demo.get('pid', '?')}")
        print(f"  {demo.get('dir', '?')}")
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

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DemoError as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
