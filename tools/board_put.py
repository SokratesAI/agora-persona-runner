"""Put a board file into the vault and update its ticket documents, in that order.

Slice 4 of the store migration (runner#672) made every board write that
happens *inside* the runner follow through to `nova_tickets`: the hook is
in `agora_runner.vault.vault_write_path`, which is the one place an
in-process board write lands. Its own handoff named what that does not
cover, and this is it.

**`tools.board_capture`, `board_row`, `board_status`, `board_project` and
`board_untag_project` write a local file, and a cycle then puts that file
into the vault with `/app/bridge/vault_tool.py put`** -- a different
program, in a different repo, in a different process, that knows nothing
about the ticket store. Every one of those writes leaves the store a
revision behind, and it stays behind until somebody runs
`tools.ticket_drift --sync`. That is the drift `ticket_drift`'s own
docstring predicted on the day it was written, and it is the reason
nothing may read a board out of CouchDB yet: a reader switched onto a
store nothing keeps current serves the owner a board that is quietly a
day old.

So this is the `put` those tools' callers should use. One command, and
the ordering is the whole design:

    python3 -m tools.board_put projects/sokrates/projects/nova/ideas.md \\
        ideas.md --if-rev-file /tmp/board.$$.rev

**The vault write goes first and the store follows it.** Pushing from
inside `board_status` -- the other obvious place -- would push before the
vault write has happened, so a `put` that then lost its compare-and-swap
(exit 3, which is a normal outcome with three cycles overlapping) would
leave the store holding a board the vault never accepted. Drift in that
direction is worse than the drift this closes, because the markdown is
the source of truth and the store would be ahead of it.

**A failed push does not fail the board edit**, for the same reason the
in-process hook does not: the markdown has already landed and is what the
owner reads, so making the store's availability the board's availability
would be backwards. It exits **4** instead -- distinct from the vault's
own codes, and loud -- because a caller that reads 0 as "everything is
current" would be wrong, and `ticket_drift --sync` is the repair.

**It reads the same local file the vault write sent**, not the document
back out of the vault, so `strip_the_print_newline` has nothing to do
here: there is no `print` in this path. That subtlety cost runner#673 and
is worth not re-introducing.
"""

import argparse
import subprocess
import sys

# Repo root on sys.path so `python3 tools/board_put.py` works and not only
# `-m`. See tests/test_tools_run_as_scripts.py.
import pathlib as _pathlib  # noqa: E402
sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import ticket_docs  # noqa: E402
from tools.ticket_migrate import VAULT_TOOL  # noqa: E402


def vault_put(path, local_file, if_rev_file=None):
    """Shell out to the bridge's vault client. Returns its `CompletedProcess`.

    `vault_tool.py` lives only on the bridge pod, which is where a cycle's
    `Bash` runs and where every board tool is run from. It is called
    rather than reimplemented because its compare-and-swap is the thing
    being preserved, and a second implementation of that is a second thing
    to get wrong.

    `subprocess.run` is looked up on the module at call time rather than
    bound as a default argument. A default is evaluated once, at import,
    so a test that replaces it is replacing something this function had
    already stopped reading -- and the first run of these tests put a
    23-byte file at his real `issues.md` path, saved only by the vault
    client's own collapse guard.
    """
    command = [sys.executable, VAULT_TOOL, "put", path, local_file]
    if if_rev_file:
        command += ["--if-rev-file", if_rev_file]
    return subprocess.run(command, capture_output=True, text=True, timeout=180)


def push(path, source):
    """Update the ticket documents for one board. Returns `(ok, message)`."""
    try:
        summary = ticket_docs.push_markdown(path, source)
    except Exception as exc:  # noqa: BLE001 -- the message is the report
        return False, f"{type(exc).__name__}: {exc}"
    return True, (
        f"{summary['written']} written, {summary['deleted']} deleted, "
        f"{summary['unchanged']} unchanged"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="the board's vault path")
    parser.add_argument("file", help="the edited board markdown on disk")
    parser.add_argument("--if-rev-file", help="the rev file from the paired get")
    args = parser.parse_args(argv)

    # A non-board path has no ticket documents, so this command would be a
    # slower `vault_tool.py put` with a misleading name. Refusing says so
    # rather than quietly doing nothing, which is how a cycle would come to
    # believe every vault write goes through here.
    if not ticket_docs.is_board(args.path):
        print(
            f"REFUSED: {args.path} is not one of the four board files, so it "
            "has no tickets. Use vault_tool.py put for it. Boards:\n  "
            + "\n  ".join(ticket_docs.BOARDS),
            file=sys.stderr,
        )
        return 1

    try:
        source = open(args.file, encoding="utf-8").read()
    except OSError as exc:
        print(f"REFUSED: cannot read {args.file} -- {exc}", file=sys.stderr)
        return 1

    done = vault_put(args.path, args.file, args.if_rev_file)
    sys.stdout.write(done.stdout or "")
    sys.stderr.write(done.stderr or "")
    if done.returncode != 0:
        # Including 3, the lost compare-and-swap. The store is untouched,
        # which is correct: nothing landed.
        print(
            f"STORE NOT UPDATED -- the vault write exited {done.returncode}, "
            "so there is nothing for the tickets to follow.",
            file=sys.stderr,
        )
        return done.returncode

    ok, message = push(args.path, source)
    if not ok:
        print(
            f"STORE NOT UPDATED -- the board landed in the vault, but the "
            f"ticket documents did not follow: {message}\n"
            "The markdown is the source of truth and is safe. Repair the "
            "store with: python3 -m tools.ticket_drift --sync",
            file=sys.stderr,
        )
        return 4
    print(f"tickets: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
