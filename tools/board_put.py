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

**It stamps the revision the markdown now has, and that is new.**
`ticket_docs.currency` (runner#679) answers `current`, `stale` or
`unknown` by comparing the revision the store was built from against the
one the vault holds -- and a writer that passes no revision *clears* the
stamp, deliberately, so an unknown answer can never read as a current
one. The in-process writer stamps; this one did not, so every board write
a cycle made left the verdict at `unknown` for the rest of the day, and
the signal the migration's next slice turns on could not be observed.
It costs one read of the whole document after the write, because
`vault_tool.py` has no `rev` subcommand -- and that read is also the
guard: if the vault moved in between, the revision belongs to text the
store is not holding, so nothing is stamped and it says so.

**A `put` pushes the same local file the vault write sent**, not the
document back out of the vault, so `strip_the_print_newline` has nothing
to do there: there is no `print` in that path. That subtlety cost
runner#673 and is worth not re-introducing.

**`--append` is the other half of the bypass, and it is not optional.**
`prompt.md` step 6 tells every cycle to append its capture notes to
`nova/resources/issues.md` and `.../ideas.md` -- two of the four boards --
with `vault_tool.py append`, which is the same separate process going
around the same store. Measured this cycle: one capture note, and
`ticket_drift` reported that board stale by 868 bytes immediately after.
So `--append <marker>` runs the append and then pushes. It has to re-read
the document, because the local file is a fragment and the store needs
the whole board, and *that* read does come back through `print` -- so it
is the one path here that subtracts the newline.
"""

import argparse
import os
import subprocess
import tempfile
import sys

# Repo root on sys.path so `python3 tools/board_put.py` works and not only
# `-m`. See tests/test_tools_run_as_scripts.py.
import pathlib as _pathlib  # noqa: E402
sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import ticket_docs  # noqa: E402
from tools.ticket_migrate import VAULT_TOOL, strip_the_print_newline  # noqa: E402


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


def vault_append(path, local_file, marker):
    """Shell out to the bridge's `append`. Returns its `CompletedProcess`.

    The marker is passed straight through and is required by this tool's
    own argument parser, because `vault_tool.py append` writes to two
    different ends of the document depending on whether it gets one and
    says nothing about which it chose.
    """
    command = [sys.executable, VAULT_TOOL, "append", path, local_file, marker]
    return subprocess.run(command, capture_output=True, text=True, timeout=180)


def vault_get(path):
    """`(board text, its revision)` as the vault holds it, or `(None, None)`.

    `--append` needs the text: the local file it sent was a fragment, and
    the store holds whole boards. `vault_tool.py get` ends in `print`, so
    its stdout is the document plus one newline and that byte has to come
    back off -- storing it is exactly the false drift runner#673 fixed.

    Every path needs the revision now -- see `main`. `--rev-file` is the
    only way this program reports one; there is no `rev` subcommand, so
    the whole document comes back either way and the read costs the same
    as it always did.
    """
    handle, rev_file = tempfile.mkstemp(prefix="board-put-rev.")
    os.close(handle)
    try:
        done = subprocess.run(
            [sys.executable, VAULT_TOOL, "get", path, "--rev-file", rev_file],
            capture_output=True, text=True, timeout=180)
        if done.returncode != 0 or done.stdout.strip() == "[not found]":
            return None, None
        try:
            rev = open(rev_file, encoding="utf-8").read().strip()
        except OSError:
            rev = ""
        # `[absent]` is what the rev file carries for a path that does not
        # exist, and it is not a revision. An older bridge may write
        # nothing at all. Both mean "no stamp", which is the honest answer.
        if not rev or rev.startswith("["):
            rev = None
        return strip_the_print_newline(done.stdout), rev
    finally:
        try:
            os.unlink(rev_file)
        except OSError:
            pass


def push(path, source, source_rev=None):
    """Update the ticket documents for one board. Returns `(ok, message)`."""
    try:
        summary = ticket_docs.push_markdown(path, source, source_rev=source_rev)
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
    parser.add_argument(
        "--append",
        metavar="MARKER",
        help="append FILE under this heading instead of replacing the board "
             "(step 6's capture notes); the marker is not optional",
    )
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

    if args.append and args.if_rev_file:
        # `append` takes no revision, so honouring one would be a promise
        # this cannot keep.
        print("REFUSED: --append and --if-rev-file are different writes; "
              "vault_tool.py append has no compare-and-swap.", file=sys.stderr)
        return 1

    try:
        source = open(args.file, encoding="utf-8").read()
    except OSError as exc:
        print(f"REFUSED: cannot read {args.file} -- {exc}", file=sys.stderr)
        return 1

    if args.append:
        done = vault_append(args.path, args.file, args.append)
    else:
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

    # The revision the markdown has *now*, so the store can be asked
    # whether it is still current without fetching the file to compare
    # against -- `ticket_docs.currency`. Until this, nothing on the bridge
    # pod stamped one: `push_markdown` was called with no rev, which
    # *clears* any stamp already there, so the verdict read `unknown`
    # after every board write a cycle made. The in-process writer in
    # `agora_runner.vault` has stamped since runner#679 and this is the
    # other writer.
    #
    # It costs one extra read of the whole document on the replace path,
    # because `vault_tool.py` has no `rev` subcommand and `--rev-file`
    # only reports alongside a `get`. That read is also the guard below,
    # so it is not spent only on the rev.
    stored, source_rev = vault_get(args.path)
    if args.append:
        # The whole board, after the insert -- not the fragment just sent.
        if stored is None:
            print("STORE NOT UPDATED -- the append landed, but the board could "
                  "not be read back. Repair with: "
                  "python3 -m tools.ticket_drift --sync", file=sys.stderr)
            return 4
        source = stored
    elif stored != source:
        # Somebody wrote between the put and this read, so the revision
        # belongs to text the store is not about to hold. Stamping it
        # would claim a currency the store cannot prove, which is the one
        # failure the three-way verdict exists to make impossible: an
        # unknown answer must never be able to read as a current one.
        print("NOT STAMPED -- the vault moved between the write and the "
              "read-back, so the store carries no source revision and "
              "`currency` will answer unknown. The board itself is fine.",
              file=sys.stderr)
        source_rev = None

    ok, message = push(args.path, source, source_rev=source_rev)
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
