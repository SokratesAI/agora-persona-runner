"""Put a board file into the vault; his two boards' records are not touched.

The one door a board file goes through into the vault. For his two boards
that file is now only the generated view of the #203 records -- drawn by
`tools.board_publish`. Its `--publish` calls this module's `vault_get`
and `vault_put` itself and then stamps the records; without it, it prints
the bare command below, which after the flip leaves that stamp behind:

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

**It no longer resyncs the records, and that is the flip (Cycle 1395).**
Until the switchover this followed every write to his two boards with
`board_migrate.resync` -- markdown in, records out -- and stamped the
revision. Once `nova_site` reads only the records, that direction is wrong:
the file is a generated view and the records are truth, so a resync would
overwrite his app edits with a stale drawing. A write to one of his boards
now lands in the vault and says plainly that the records were not touched.

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

    # His two boards are the #203 records now and this file is only their
    # generated view. Resyncing the records from it -- what this did until
    # the flip -- would put back whatever the file said over every edit he
    # has made in the app since it was last drawn.
    board = record_board(args.path)
    if board is not None:
        print(f"records: not touched -- {board} is read from the #203 records "
              "now, and this wrote only its markdown view, which the site does "
              "not read. Change the board through the app or board_write.",
              file=sys.stderr)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
