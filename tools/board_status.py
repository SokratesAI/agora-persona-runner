"""Move one board row to a new status, in the record store.

A cycle that wants to say "this one is finished" used to have two options --
hand-split the row on `|`, which is the exact corruption `set_row_status` was
written to end, or leave the row where it is. Both happened. Idea #100 sat at
`🟡 In progress` after all three of its heartbeats were built and firing.

    python3 -m tools.board_status --board idea --number 100 \\
        --status done --dated 08-26 --note 'what closed it' --cycle 498

**This is the third of the ten `tools/board_*.py` writers converted onto
`agora_runner.board_write` for issue #203, and the first that needs
`append_note` rather than `change_row` alone.** What comes out is the same set
as the two before it: the markdown, the path on disk, the compare-and-swap the
caller used to own, and this module's own 60-line `check_from_contents`, which
is what `change_row` is once in a module that can be tested. What is left is
this tool's vocabulary -- which cell, on which row, refused how.

**`--note` is why this one waited for `append_note`.** Issue #85 is the owner
watching a row close itself with the reason in a journal entry in a different
database, so a status move offers to write the why beside the row. Against
markdown that was a second pass over the document; against records it is the
*same write* -- `append_note` carries this tool's change set through, so a
status move and its reason land together or not at all, and there is no window
where his board says a row closed and nothing says why. It stays optional
because moving a row back to `⚪ Backlog` often has nothing to explain.

**The one refusal with no twin inside `change_row` is a row that is already
done, and it moved rather than being dropped.** In markdown it was structural:
`_row_span` was told `tables=("board",)` and the `## Done` table puts a date
where this one writes a status, so the function simply could not reach the row.
The record store has no two tables -- a closed row is a row with `done` set --
so nothing downstream refuses it and this module has to, out of a board read
taken **before** the first write. Same reasoning as `board_project`'s
`missing_rows`: the refusal has to happen while nothing has been written.

The other refusals are unchanged and each one is a way this could still hand
him a broken board: a status outside the five `STATUS_LABELS` spellings; a
`--dated` or `--note` that is blank or carries a `|` or a line break, either of
which splits a cell or a row in the generated markdown view the daily backup
renders; a `--note` with no `--dated` to stamp it with; and `change_row`'s own
after-check, which refuses when anything other than the named row's named keys
moved. Closing a row deliberately blanks its rating -- `set_row_priority`
refuses to rate a finished row, so a chip left behind could never be cleared
again -- and that blanking is named in the change set rather than forgiven by
the check, which is the whole difference between the two eras.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records, board_store, board_write
from agora_runner.board_document import BOARDS
from agora_runner.nova_boards import STATUS_LABELS, priority_key, status_key

# The statuses that blank the rating cell on their way in. Mirrors
# `nova_boards._CLOSED_STATUS_KEYS`, which is private; kept here as the one
# thing this module has to know rather than by importing a private name.
# `tests/test_tools_board_status.py` asserts the two agree, so this copy
# cannot drift the way two hand-copied constants usually do.
CLOSED_STATUS_KEYS = frozenset({"done", "outdated"})


def _status_choices():
    """Every accepted spelling: the five keys and the five written forms."""
    return list(STATUS_LABELS) + list(STATUS_LABELS.values())


def resolve_status(value):
    """`done` / `✅ Done` / `Done` -> the exact cell text. Or `None`.

    `status_key` is what the app already reduces a cell to, so routing a
    typed argument through it means the CLI accepts exactly the spellings
    the rest of the system considers equal, and cannot invent a sixth.
    """
    if not value:
        return None
    return STATUS_LABELS.get(status_key(value))


def status_changes(status, dated=None):
    """The change set for a status move: the cells and the keys they derive.

    `board_document.from_document` derives `statusKey` off the stored `status`
    cell and `priorityKey` off `priority`, so a change set naming only the cell
    reads back with a key that disagrees with it -- `change_row`'s after-check
    catches that, and this is the function that makes it never happen.

    A closed status blanks the rating here rather than being forgiven by a
    check afterwards. `set_row_priority` refuses to rate a finished row, so a
    chip left behind by a close could never be cleared again.

    `dated` is the `updated` cell and `None` leaves it alone. **A caller with a
    note must not pass it**: `append_note` sets `updated` from the note's own
    date and refuses a change set that names it too.
    """
    changes = {"status": status, "statusKey": status_key(status)}
    if status_key(status) in CLOSED_STATUS_KEYS:
        changes["priority"] = ""
        changes["priorityKey"] = priority_key("")
    if dated is not None:
        changes["updated"] = dated
    return changes


def refuse_cell(value, flag):
    """Why `value` may not go in a cell, or `None` if it may.

    A `|` splits the cell and a `\\r` or `\\n` splits the row, in the markdown
    view the daily GitHub backup renders. The store would hold any of them.
    """
    if value is None:
        return None
    if not value.strip():
        return f"{flag} may not be blank"
    for character in "|\r\n":
        shown = {"\r": "a carriage return", "\n": "a newline"}.get(
            character, f"a {character!r}")
        if character in value:
            return (f"{flag} carries {shown}, which escapes its own cell in "
                    "the generated board view")
    return None


def refuse_row(contents, number):
    """Why this row may not take a status move, or `None` if it may.

    Read off a board fetched **before** anything is written, because both
    answers here have to mean "nothing happened". `change_row` refuses an
    absent row itself; a row that is already done it would happily write, and
    that refusal has no twin below this seam -- in markdown it was `_row_span`
    being told to look at one table.
    """
    for item in contents["items"]:
        if item.get("number") == number:
            if item.get("done"):
                return (f"#{number} is already in '## Done', which puts a date "
                        "where this writes a status -- it is deliberately out "
                        "of reach")
            return None
    return f"#{number} is not a row on this board"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, choices=list(BOARDS),
                        help="which board the row is on")
    parser.add_argument("--number", required=True, type=int, help="the row number")
    parser.add_argument(
        "--status",
        required=True,
        help="backlog / in-progress / blocked-on-edvard / done / outdated, "
             "or the written form",
    )
    parser.add_argument("--dated", help="MM-DD, Oslo; omitted leaves the cell alone")
    parser.add_argument("--note", help="one line on why it moved, appended to the write-up")
    parser.add_argument("--cycle", type=int, help="stamped on --note")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    status = resolve_status(args.status)
    if status is None:
        print(
            f"REFUSED: '{args.status}' is not a status. One of: "
            + ", ".join(_status_choices()),
            file=sys.stderr,
        )
        return 1
    for value, flag in ((args.dated, "--dated"), (args.note, "--note")):
        refusal = refuse_cell(value, flag)
        if refusal:
            print(f"REFUSED: {refusal}", file=sys.stderr)
            return 1
    # A note needs a date to be stamped with, and `append_note` takes one
    # rather than reaching for a clock -- these files write Oslo `MM-DD` and a
    # module that formats its own dates formats them in UTC. So the two
    # arguments travel together or not at all.
    if args.note is not None and args.dated is None:
        print(
            "REFUSED: --note is written as a dated line, so it needs --dated",
            file=sys.stderr,
        )
        return 1

    try:
        before = board_records.contents(args.board, store=board_store)
    except board_records.RecordError as problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    refusal = refuse_row(before, args.number)
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1

    was = {item["number"]: item for item in before["items"]}[args.number]
    print(f"#{args.number}: {was['status']} -> {status}")
    if args.dry_run:
        return 0

    try:
        if args.note:
            # `updated` is left out of the change set on purpose: `append_note`
            # sets it from the note's own date and refuses a caller that names
            # it too, so the note and the cell can never disagree.
            board_write.append_note(
                args.board, args.number, args.note, args.dated,
                cycle=args.cycle, author="nova",
                changes=status_changes(status), store=board_store,
            )
        else:
            board_write.change_row(
                args.board, args.number, status_changes(status, dated=args.dated),
                store=board_store,
            )
    except (board_write.WriteRefused, board_write.BoardDamaged,
            board_records.RecordError) as problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    print(f"wrote #{args.number} on the {args.board} board")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
