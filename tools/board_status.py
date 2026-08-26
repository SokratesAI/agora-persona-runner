"""Move one board row to a new status, on the owner's boards or my own.

`agora_runner.nova_boards.set_row_status` has existed since Cycle 202 and
**nothing has ever called it.** I grepped the whole repo: the only hit
outside the module and its tests is a comment in `tools/board_row.py`.
So a cycle that wants to say "this one is finished" has had two options —
hand-split the row on `|`, which is the exact corruption `set_row_status`
was written to end, or leave the row where it is. Both happened. Idea
#100 sat at `🟡 In progress` after all three of its heartbeats were built
and firing, and my Cycle 496 wrote *"I wanted to set idea #3 to Done
myself"* into the handoff as something it could not do.

That handoff also said the reason was a size cap — *"your ideas.md is
345KB and my vault client refuses to read anything over 256KB"*.
**Measured from the bridge shell this cycle: `vault_tool.py get` on that
same 355,534-byte file exits 0 and returns every byte, with a rev.** The
cap is real somewhere else, not on the path a cycle actually writes
through. The missing piece was never the read. It was this file.

    python3 -m tools.board_status --file ideas.md --number 100 \
        --status done --dated 08-26 --note 'what closed it' --cycle 498

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.board_row`, `tools.roll_captures` and
`tools.roll_done_captures` hold, so the caller owns the compare-and-swap:
`vault_tool.py get --rev-file` before, `put --if-rev-file` after.

`--note` is not decoration. Issue #85 is the owner watching a row close
itself with the reason in a journal entry in a different database, and
`append_detail_note`'s own docstring says so — so a status move offers to
write the why beside the row in the same call. It is optional because
moving a row *back* to `⚪ Backlog` often has nothing to explain.

The refusals are the point, and each one is a way this could have handed
him a broken table: a row that is not on `## Board` (a row in `## Done`
puts a date where this writes a status, so `_row_span` will not reach
it); a status outside the five `STATUS_LABELS` spellings; a `--dated` or
`--note` carrying a `|` or a newline, either of which splits a cell or a
row; and `check` refusing the write when any row other than the one named
moved. Closing a row deliberately blanks its rating — `set_row_priority`
refuses to rate a finished row, so a chip left behind could never be
cleared again — and `check` allows that change on the target row only.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    STATUS_LABELS,
    append_detail_note,
    parse_board,
    parse_notes,
    set_row_status,
    status_key,
)

# The statuses that blank the rating cell on their way in. Mirrors
# `nova_boards._CLOSED_STATUS_KEYS`, which is private; kept here as the
# one thing `check` has to forgive rather than by importing a private
# name. `tests/test_tools_board_status.py` asserts the two agree, so this
# copy cannot drift the way two hand-copied constants usually do.
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


def check(before, after, number, status, noted):
    """Refuse the write unless that one row moved and nothing else did.

    Same shape and same reasoning as `tools.board_row.check`: this edits a
    document the site parses, so the question is what `parse_board` says
    afterwards, not what the string looks like. The asymmetry here is that
    a status move is meant to change *one* cell of *one* row, so almost
    everything on both sides has to match exactly — which makes it a much
    tighter guard than boarding a new row ever gets.
    """
    problems = []
    old = parse_board(before)
    new = parse_board(after)
    old_by_number = {item["number"]: item for item in old["items"]}
    new_by_number = {item["number"]: item for item in new["items"]}

    if number not in old_by_number:
        problems.append(f"#{number} was not on the board to begin with")
    if number not in new_by_number:
        problems.append(f"#{number} is not on the board afterwards")
    else:
        moved = new_by_number[number]
        if moved["status"] != status:
            problems.append(
                f"#{number} came back as {moved['status']!r}, asked for {status!r}"
            )
        if number in old_by_number and moved["title"] != old_by_number[number]["title"]:
            problems.append(f"#{number} came back with a different title")
        if (
            number in old_by_number
            and status_key(status) not in CLOSED_STATUS_KEYS
            and moved["priority"] != old_by_number[number]["priority"]
        ):
            problems.append(f"#{number} lost its rating and the status is still open")

    if len(new["items"]) != len(old["items"]):
        problems.append(
            f"row count went {len(old['items'])} -> {len(new['items'])}, expected no change"
        )
    for was in old["items"]:
        if was["number"] == number:
            continue
        now = new_by_number.get(was["number"])
        if now is None:
            problems.append(f"#{was['number']} fell off the board")
        elif now != was:
            problems.append(f"#{was['number']} changed underneath the status move")

    old_notes = [note["text"] for note in parse_notes(before)]
    new_notes = [note["text"] for note in parse_notes(after)]
    if old_notes != new_notes:
        problems.append(
            f"the bullet stream changed: {len(old_notes)} -> {len(new_notes)} note(s)"
        )

    for old_number, body in old["details"].items():
        if old_number == number and noted:
            # The one write-up allowed to grow, and only by appending:
            # `append_detail_note` adds a line and touches nothing above
            # it, so anything else here is the substring-splice failure
            # `tools.doc_integrity` exists to catch after the fact.
            if not new["details"].get(old_number, "").startswith(body):
                problems.append(f"the write-up for #{old_number} was rewritten, not appended to")
            continue
        if new["details"].get(old_number) != body:
            problems.append(f"the write-up for #{old_number} changed")
    return problems


def _refuse_cell(name, value):
    """A `|` splits the cell, a newline splits the row. Both reach his file."""
    return "|" in value or "\n" in value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="a board markdown on disk")
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
    parser.add_argument("--out", help="where to write (default: in place)")
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
    if args.dated is not None and (_refuse_cell("--dated", args.dated) or not args.dated.strip()):
        print(
            "REFUSED: --dated goes straight into a table cell, so it may not "
            "be blank or carry a '|' or a newline",
            file=sys.stderr,
        )
        return 1
    if args.note is not None and (_refuse_cell("--note", args.note) or not args.note.strip()):
        print(
            "REFUSED: --note is one line in his write-up, so it may not be "
            "blank or carry a '|' or a newline",
            file=sys.stderr,
        )
        return 1
    # A note needs a date to be stamped with, and `append_detail_note`
    # takes one rather than reaching for a clock — these files write
    # Oslo `MM-DD` and a module that formats its own dates formats them
    # in UTC. So the two arguments travel together or not at all.
    if args.note is not None and args.dated is None:
        print(
            "REFUSED: --note is written as a dated line, so it needs --dated",
            file=sys.stderr,
        )
        return 1

    before = open(args.file, encoding="utf-8").read()
    after = set_row_status(before, args.number, status, updated=args.dated)
    if after is None:
        print(
            f"REFUSED: #{args.number} is not a row in '## Board' in that file "
            "(a row already in '## Done' is deliberately out of reach)",
            file=sys.stderr,
        )
        return 1

    if args.note:
        noted = append_detail_note(
            after, args.number, args.note, args.dated, cycle=args.cycle, author="nova"
        )
        if noted is None:
            print(
                f"REFUSED: could not append the note to #{args.number}'s write-up",
                file=sys.stderr,
            )
            return 1
        after = noted

    problems = check(before, after, args.number, status, noted=bool(args.note))
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    was = {item["number"]: item for item in parse_board(before)["items"]}
    print(f"#{args.number}: {was[args.number]['status']} -> {status}")
    print(f"{len(before)} -> {len(after)} bytes")
    if args.dry_run:
        return 0
    open(args.out or args.file, "w", encoding="utf-8").write(after)
    print(f"wrote {args.out or args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
