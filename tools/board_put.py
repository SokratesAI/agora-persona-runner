"""Put a board file into the vault and resync its #203 records, in that order.

`tools.board_capture` and `board_row` write a local file, and a cycle then
puts that file into the vault with `/app/bridge/vault_tool.py put` -- a
different program, in a different process, that knows nothing about the
record store. Every one of those writes would leave the records a revision
behind until somebody ran `tools.board_migrate --resync` by hand. This is
the `put` those tools' callers should use:

    python3 -m tools.board_put projects/sokrates/projects/nova/ideas.md \\
        ideas.md --if-rev-file /tmp/board.$$.rev

**It used to follow the write into a second store as well.** Until Cycle
1380 this pushed every board into the `nova_tickets` mirror first and the
records second. Nothing read that mirror once `nova_site` moved onto the
records (Cycle 1379), so the push was deleted with the mirror rather than
kept in step for nobody, and `tools.ticket_drift --sync` -- the repair it
used to name -- is gone with it.

**`board_project`, `board_untag_project`, `board_status`,
`board_milestone`, `board_size` and `board_priority`** write the record
store directly through `board_write.change_row` and `append_note`, so they
hold no local file for this tool to put. **`board_row` never leaves this
list, and that is not a backlog item**: it boards a row on *Nova's own*
`resources/issues.md`, and the record store holds the owner's two boards
only (Cycle 1335).

**The vault write goes first and the records follow it.** A `put` that
lost its compare-and-swap (exit 3, a normal outcome with three cycles
overlapping) must leave the records alone, or they would hold a board the
vault never accepted -- the markdown is the source of truth until the
switchover, and records ahead of it are worse than records behind it.

**A records resync that fails does not fail the board edit.** The markdown
has already landed and is what the owner reads, so it exits **4** instead
-- distinct from the vault's own codes, and loud -- and the line it prints
names the `board_migrate --resync` that repairs it.

**It stamps the revision the markdown now has**, so `board_records.currency`
can answer `current` without the site fetching the file to compare. It
costs one read of the whole document after the write, because
`vault_tool.py` has no `rev` subcommand -- and that read is also the guard:
if the vault moved in between, the revision belongs to text the records are
not holding, so nothing is stamped and it says so.

**The records only cover his two boards** (`RECORD_BOARDS`), so
`nova/resources/issues.md` and `.../ideas.md` are put and nothing follows.
A board the record store has never been seeded with skips too: a resync of
an unseeded board is a seed, and seeding is `--apply`'s job.

**Know the day this hook becomes wrong.** `resync` is markdown in, records
out, and issue #202 is what first writes something into the records that
the markdown cannot say -- a hand-dragged row order. From that moment this
would put every row back where the file says, and from the `nova_site`
flip it would overwrite truth with a generated view. It is a
migration-window hook and it comes out with the window, together with
`resync` itself.

**`--append` is the other half of the bypass.** `prompt.md` step 6 appends
capture notes with `vault_tool.py append`. `--append <marker>` runs the
append and, on one of his boards, re-reads the whole document to resync
from -- the local file is a fragment and the records need the whole board.
That read comes back through `print`, so it is the one path here that
subtracts the newline.
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

from agora_runner import board_records, board_store, ticket_docs  # noqa: E402
from tools import board_migrate  # noqa: E402


VAULT_TOOL = "/app/bridge/vault_tool.py"


def strip_the_print_newline(stdout):
    """`vault_tool.py get` prints the document, so its stdout is one byte long.

    The bridge's vault client ends in `print(content)`, and `print` appends
    a newline the vault does not hold (runner#673). Removing exactly one is
    lossless rather than a heuristic: `print` always adds exactly one, so a
    document that genuinely ends in four blank lines still has four.
    """
    return stdout[:-1] if stdout.endswith("\n") else stdout


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



#: His two board files, and the record-store board each one holds. The two
#: under `nova/resources/` are deliberately absent, and the reason written
#: here used to be false: it said they are "my own flat capture lists -- no
#: `## Board` table, no rows". They are not. Measured on the live
#: `nova/resources/issues.md`, 2026-09-10: a real `## Board` table, 35 rows
#: and 26 write-ups. The true reason is ownership, not shape -- issue #203's
#: spec and his standing instruction of 2026-09-09 are about the two boards
#: *he* writes, so `board_document` has no board name for mine and a resync
#: there would be a resync of something that was never seeded. Keep the
#: distinction on that footing: a cycle reading the old reason would "fix"
#: the exclusion the moment it noticed the rows.
RECORD_BOARDS = {
    "projects/sokrates/projects/nova/issues.md": "issue",
    "projects/sokrates/projects/nova/ideas.md": "idea",
}


def record_board(path):
    """The record-store board `path` holds, or `None` if it holds none.

    Lowercased on both sides for the same reason `ticket_docs.is_board`
    is: vault paths are normalised to lowercase when they are stored, so
    a caller writing mixed case reaches the same document and has to
    reach the same records.
    """
    return RECORD_BOARDS.get((path or "").lower())


def seeded(store=board_store):
    """Has the record store ever been seeded? `None` if it cannot say.

    It takes no board, because `resync`'s own refusal does not: the
    project and milestone registry is one document shared by both boards,
    and a `_rev` on it is what tells a resync from a seed. Asking a
    different question here than the thing being called asks is how a
    guard comes to pass while the call behind it refuses.

    Three answers rather than two, because "the store has never been
    migrated" and "CouchDB would not answer" mean opposite things here:
    the first is a board with nothing to keep current and is a skip, the
    second is a board whose records may now be behind and is a failure.
    Folding them together is how an unreachable store would come to read
    as a clean one.
    """
    try:
        registry = store.read_registry()
    except Exception:  # noqa: BLE001 -- the caller reports, this decides
        return None
    return bool((registry or {}).get("_rev"))


def follow_records(board, source):
    """Resync one board's records from its new markdown.

    Returns `(ok, message)`. The
    markdown has already landed and is the source of truth, so `ok` is
    False only to say the records are now behind it, never to say the
    board edit failed.
    """
    try:
        report = board_migrate.resync(source, board, apply=True)
    except Exception as exc:  # noqa: BLE001 -- the message is the report
        return False, f"{type(exc).__name__}: {exc}"
    return True, (
        f"{report['written']} row(s) written, {report['deleted']} deleted, "
        f"{report['captures_kept']} capture(s) kept, "
        f"{report['captures_minted']} minted, "
        f"{report['layout_stored']} layout block(s)"
    )


def stamp(board, source_rev, out=None):
    """Stamp the revision the records were just built from. `True` if stamped.

    Runs only after the resync reported success, because the stamp is a
    claim about records that are already stored: one written first would
    certify a write that then failed.

    A missing `source_rev` is a skip and not a failure. `main` clears it
    when the vault moved between the write and the read-back, and the
    records themselves are still correct for the text they were built from
    -- what is unavailable is the *proof*, so `currency` answers `unknown`,
    which is the honest verdict and the one thing it must never be able to
    confuse with `current`.

    **A hand-run `board_migrate --resync` does not stamp**, so a board
    repaired that way reads `stale` until the next write through here. That
    is the safe direction and it is deliberate: the wrong answer is a
    `current` verdict on records nothing can vouch for, and a `stale` one
    on records that are fine costs a re-read.
    """
    out = out or sys.stderr
    if not source_rev:
        print("records: not stamped -- no source revision to stamp, so "
              "`board_records.currency` will answer unknown", file=out)
        return False
    try:
        board_records.stamp_source_rev(board, source_rev)
    except Exception as exc:  # noqa: BLE001 -- the message is the report
        print(f"records: not stamped -- {type(exc).__name__}: {exc}. The "
              "records themselves followed; only the currency stamp is "
              "missing, so `board_records.currency` will answer unknown.",
              file=out)
        return False
    print(f"records: stamped at {source_rev}")
    return True


def follow(path, source, source_rev=None, out=None):
    """Bring the board records behind `path` back into line. `True` if the
    records are current afterwards -- which includes a board that has no
    records to keep current."""
    out = out or sys.stderr
    board = record_board(path)
    if board is None:
        return True
    ever = seeded()
    if ever is None:
        print("RECORDS NOT UPDATED -- the record store could not be read, so "
              "it may now be behind the markdown. The markdown is the source "
              "of truth and is safe. Repair with: python3 -m "
              f"tools.board_migrate --board {board} --resync --apply",
              file=out)
        return False
    if not ever:
        print(f"records: {board} has never been seeded, so there is nothing "
              "to resync")
        return True
    ok, message = follow_records(board, source)
    if not ok:
        print(f"RECORDS NOT UPDATED -- the board landed in the vault, but its "
              f"records did not follow: {message}\n"
              "The markdown is the source of truth and is safe. Repair with: "
              f"python3 -m tools.board_migrate --board {board} --resync "
              "--apply", file=out)
        return False
    print(f"records: {message}")
    # The stamp is reported and deliberately does NOT decide the return.
    # The records are in line with the markdown either way; an unstamped
    # board is one `currency` cannot speak for, which is a weaker
    # instrument and not a store that is behind. Failing here would make
    # `board_put` exit 4 on a write that fully succeeded.
    stamp(board, source_rev, out=out)
    return True


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

    # A non-board path has no records, so this command would be a slower
    # `vault_tool.py put` with a misleading name. Refusing says so
    # rather than quietly doing nothing, which is how a cycle would come to
    # believe every vault write goes through here.
    if not ticket_docs.is_board(args.path):
        print(
            f"REFUSED: {args.path} is not one of the four board files. "
            "Use vault_tool.py put for it. Boards:\n  "
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
        # Including 3, the lost compare-and-swap. The records are
        # untouched, which is correct: nothing landed.
        print(
            f"RECORDS NOT UPDATED -- the vault write exited {done.returncode}, "
            "so there is nothing for the records to follow.",
            file=sys.stderr,
        )
        return done.returncode

    # My own two boards have no records behind them, so the write is the
    # whole job and the read-back below would be spent on nothing.
    if record_board(args.path) is None:
        return 0

    # The revision the markdown has *now*, so `board_records.currency` can
    # answer without fetching the file to compare against. One extra read
    # of the whole document on the replace path, because `vault_tool.py`
    # has no `rev` subcommand -- and that read is also the guard below.
    stored, source_rev = vault_get(args.path)
    if args.append:
        # The whole board, after the insert -- not the fragment just sent.
        if stored is None:
            print("RECORDS NOT UPDATED -- the append landed, but the board "
                  "could not be read back. Repair with: python3 -m "
                  f"tools.board_migrate --board {record_board(args.path)} "
                  "--resync --apply", file=sys.stderr)
            return 4
        source = stored
    elif stored != source:
        # Somebody wrote between the put and this read, so the revision
        # belongs to text the store is not about to hold. Stamping it
        # would claim a currency the store cannot prove, which is the one
        # failure the three-way verdict exists to make impossible: an
        # unknown answer must never be able to read as a current one.
        print("NOT STAMPED -- the vault moved between the write and the "
              "read-back, so the records carry no source revision and "
              "`currency` will answer unknown. The board itself is fine.",
              file=sys.stderr)
        source_rev = None

    return 0 if follow(args.path, source, source_rev=source_rev) else 4

if __name__ == "__main__":
    raise SystemExit(main())
