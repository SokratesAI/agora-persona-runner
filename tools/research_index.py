"""What have I already researched about this?

The rule and the scoring live in `agora_runner.research_topics`; this half
does the I/O -- one listing of `nova/resources/research/` and nothing else.
No document is fetched, so the call is cheap enough to make before every
research task.

    python3 -m tools.research_index --for "what else to run on the NAS"
    python3 -m tools.research_index            # the whole folder, one line each

`--paths <file>` reads a listing from a local file instead of the vault,
which is how the tests drive it and how a pod with no vault client runs it.

Exit codes: 2 when something already covers the topic, 0 when nothing does,
**1 when the listing came back empty** -- an empty folder and a broken read
print the same reassuring "nothing written yet", and that answer is the one
that costs a whole cycle, so it is never reported from a listing of zero.
This is advisory and deliberately not a `preflight` check: there is nothing
to fix here, only something to read.
"""

import argparse
import pathlib
import subprocess
import sys

import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.research_topics import index, is_strong, related, render  # noqa: E402

FOLDER = "projects/sokrates/projects/agora/nova/resources/research/"

BRIDGE_VAULT_TOOL = "/app/bridge/vault_tool.py"
"""The bridge pod holds its CouchDB credentials under `CDB_*` and
`agora_runner.vault` reads `COUCHDB_*`, so the listing 401s there -- and the
bridge pod is the shell a cycle actually runs `tools/` from. Rather than make
every caller fetch the listing by hand, fall back to the client that pod does
have. Absent in the runner pod, where the library route works."""


def _md(lines):
    return [l.strip() for l in lines if l.strip().endswith(".md")]


def _listing(local):
    if local:
        return _md(pathlib.Path(local).read_text().splitlines())
    from agora_runner.vault import vault_list_prefix
    try:
        paths = _md(vault_list_prefix(FOLDER))
    except Exception:                       # no credentials, no route, no store
        paths = []
    if paths or not pathlib.Path(BRIDGE_VAULT_TOOL).exists():
        return paths
    done = subprocess.run([sys.executable, BRIDGE_VAULT_TOOL, "ls", FOLDER],
                          capture_output=True, text=True, timeout=120)
    return _md(done.stdout.splitlines()) if done.returncode == 0 else []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--for", dest="topic",
                        help="the thing you are about to research")
    parser.add_argument("--paths", help="read a listing from a local file")
    args = parser.parse_args(argv)

    try:
        paths = _listing(args.paths)
    except OSError as exc:
        print(f"could not list {FOLDER}: {exc}", file=sys.stderr)
        return 1
    if not paths:
        print(f"listed 0 documents under {FOLDER} -- refusing to report that "
              f"nothing has been researched, because a broken listing reads "
              f"exactly the same as an empty folder.", file=sys.stderr)
        return 1

    if not args.topic:
        print(f"{len(paths)} research documents on file:")
        print(index(paths))
        return 0

    rows = related(args.topic, paths)
    print(render(args.topic, rows, len(paths)))
    return 2 if any(is_strong(r) for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
