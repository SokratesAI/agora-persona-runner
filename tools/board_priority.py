"""Re-rate one board row, on the owner's boards or my own.

`agora_runner.nova_boards.set_row_priority` has existed since Cycle 274
and the only thing that has ever called it is `nova_capture.board_note`,
which rates a bullet **on its way onto the board for the first time**.
There has never been a way to change a rating already written. Cycle 496
recorded the same gap for the status cell and `tools.board_status` closed
it; this is the missing sibling, and it took a capture from the owner to
surface it:

> *"Bump idea #260 (task-prioritization redesign) to Priority: Immediately.
> I see it as the most important project right now because it gives us
> control over how every other project gets worked."*

That is one cell, and the two ways to do it without this file are the two
`board_status` already named: hand-split the row on `|`, which is the
corruption `set_row_priority` was written to end, or open Obsidian, which
no cycle can do.

    python3 -m tools.board_priority --file ideas.md --number 260 \
        --priority immediate --dated 09-06 --note 'why it moved' --cycle 1087

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.board_row`, `tools.board_status` and
`tools.roll_done_captures` hold, so the caller owns the compare-and-swap:
`vault_tool.py get --rev-file` before, `board_put --if-rev-file` after.

The refusals are the point, and each one is a way this could have handed
him a broken table: a rating outside the four `PRIORITY_LABELS` spellings;
a **blank** rating, which `set_row_priority` itself accepts and which is
the one state that means "nobody has looked" (`prompt.md` step 6, and
Cycle 188's deliberate use of it); a row that is not on `## Board`, or is
closed, both of which `set_row_priority` refuses by returning `None`; a
`--dated` or `--note` carrying a `|` or a newline, either of which splits
a cell or a row; and `check` refusing the write when anything other than
that one rating moved.

A re-rating changes the rating and nothing else -- **unless it carries a
`--note`**, because `append_detail_note` stamps the row's `Updated` cell
with the same `--dated` it writes into the write-up. That is deliberate
there and it is the ordinary case here: a row is re-rated *because* time
has passed since it was last touched, so `--dated` almost always differs
from the cell. The first version of `check` did not know that and refused
every real invocation of the flag pair; the reviewer found it, and my own
fixture hid it, because the fixture's row was already dated the day I
passed. `check` therefore forgives `updated` on the target row only when a
note was appended, and only to exactly the date given -- everything else,
on every row including the target, must come back identical.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    PRIORITY_LABELS,
    append_detail_note,
    canonical_priority,
    parse_board,
    parse_notes,
    set_row_priority,
)


# The two keys `parse_board` derives from the rating cell. A re-rating is
# allowed to move both of them on the target row and nothing else; naming
# them here is what lets `check` be an equality test on everything else.
_RATING_KEYS = frozenset({"priority", "priorityKey"})


def _priority_choices():
    """Every accepted spelling: the four keys and the four written forms."""
    return [key for key in PRIORITY_LABELS if key] + [
        value for value in PRIORITY_LABELS.values() if value
    ]


def resolve_priority(value):
    """`immediate` / `🔴 Immediately` / `Immediately` -> the cell text, or `None`.

    `canonical_priority` is what the rest of the system reduces a cell to,
    so routing a typed argument through it accepts exactly the spellings
    everything else considers equal and cannot invent a fifth. The one
    thing it accepts that this must not is the blank rating: it is a legal
    cell and it means "nobody has looked", which is never what a cycle
    reaching for this tool is trying to say.
    """
    if not value or not value.strip():
        return None
    resolved = canonical_priority(value)
    return resolved or None


def check_from_contents(old, new, old_notes, new_notes, number, priority, noted, dated=None):
    """Refuse the write unless that one rating moved and nothing else did.

    Same shape and same reasoning as
    `tools.board_status.check_from_contents` used to be, before that tool moved onto `board_write.change_row`. It has one
    forgiveness of its own and it is not optional: `append_detail_note`
    stamps `Updated` with `dated`, so when `noted` is true the target row's
    `updated` may move, and only to `dated`. Asserting the new value rather
    than skipping the field is what keeps this from becoming a hole -- a
    plain exclusion would let any date through, including one a caller
    never asked for.

    `old` and `new` are the two parsed record sets and `old_notes` /
    `new_notes` the two bullet streams, all four read by `main`. This
    reaches for no document itself: #203 turns the source into a CouchDB
    range query, and a guard that fetched its own copy would be checking
    a version of the board the caller never saw.
    """
    problems = []
    old_by_number = {item["number"]: item for item in old["items"]}
    new_by_number = {item["number"]: item for item in new["items"]}

    if number not in old_by_number:
        problems.append(f"#{number} was not on the board to begin with")
    if number not in new_by_number:
        problems.append(f"#{number} is not on the board afterwards")
    else:
        moved = new_by_number[number]
        if moved["priority"] != priority:
            problems.append(
                f"#{number} came back as {moved['priority']!r}, asked for {priority!r}"
            )
        if number in old_by_number:
            # `parse_board` derives `priorityKey` from the same cell, so
            # both move together or the parser is broken; comparing the
            # rest is what says nothing *else* moved.
            forgiven = set(_RATING_KEYS)
            if noted:
                if moved.get("updated") != dated:
                    problems.append(
                        f"#{number} came back updated {moved.get('updated')!r}, "
                        f"asked for {dated!r}"
                    )
                forgiven.add("updated")
            was = {k: v for k, v in old_by_number[number].items() if k not in forgiven}
            now = {k: v for k, v in moved.items() if k not in forgiven}
            if was != now:
                problems.append(f"#{number} changed something other than its rating")

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
            problems.append(f"#{was['number']} changed underneath the re-rating")

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


def _refuse_cell(value):
    """A `|` splits the cell, a newline splits the row. Both reach his file."""
    return "|" in value or "\n" in value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="a board markdown on disk")
    parser.add_argument("--number", required=True, type=int, help="the row number")
    parser.add_argument(
        "--priority",
        required=True,
        help="low / medium / high / immediate, or the written form",
    )
    parser.add_argument("--dated", help="MM-DD, Oslo; stamped on --note only")
    parser.add_argument("--note", help="one line on why it moved, appended to the write-up")
    parser.add_argument("--cycle", type=int, help="stamped on --note")
    parser.add_argument("--out", help="where to write (default: in place)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    priority = resolve_priority(args.priority)
    if priority is None:
        print(
            f"REFUSED: '{args.priority}' is not a rating. One of: "
            + ", ".join(_priority_choices()),
            file=sys.stderr,
        )
        return 1
    if args.dated is not None and (_refuse_cell(args.dated) or not args.dated.strip()):
        print(
            "REFUSED: --dated goes straight into his write-up, so it may not "
            "be blank or carry a '|' or a newline",
            file=sys.stderr,
        )
        return 1
    if args.note is not None and (_refuse_cell(args.note) or not args.note.strip()):
        print(
            "REFUSED: --note is one line in his write-up, so it may not be "
            "blank or carry a '|' or a newline",
            file=sys.stderr,
        )
        return 1
    # A note needs a date to be stamped with, and `append_detail_note`
    # takes one rather than reaching for a clock — these files write Oslo
    # `MM-DD` and a module that formats its own dates formats them in UTC.
    # So the two arguments travel together or not at all.
    if args.note is not None and args.dated is None:
        print(
            "REFUSED: --note is written as a dated line, so it needs --dated",
            file=sys.stderr,
        )
        return 1

    before = open(args.file, encoding="utf-8").read()
    before_board = parse_board(before)
    before_notes = [note["text"] for note in parse_notes(before)]
    after = set_row_priority(before, args.number, priority)
    if after is None:
        print(
            f"REFUSED: #{args.number} is not an open row in '## Board' in that "
            "file (a finished row deliberately carries no rating)",
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

    after_board = parse_board(after)
    after_notes = [note["text"] for note in parse_notes(after)]
    problems = check_from_contents(
        before_board, after_board, before_notes, after_notes, args.number, priority, noted=bool(args.note), dated=args.dated
    )
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    was = {item["number"]: item for item in before_board["items"]}
    print(f"#{args.number}: {was[args.number]['priority'] or '(unrated)'} -> {priority}")
    print(f"{len(before)} -> {len(after)} bytes")
    if args.dry_run:
        return 0
    open(args.out or args.file, "w", encoding="utf-8").write(after)
    print(f"wrote {args.out or args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
