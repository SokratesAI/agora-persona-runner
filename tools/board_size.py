"""Estimate one board row's size, S/M/L/XL, on his boards or my own.

Milestone M2 of idea #260's picking redesign, and the row half of it. The
spec's reason for the field, in his words:

> *"a marking of the size of the task/project/milestone going for t-shirt
> sizes s/m/l/xl. Just a guess/estimate on the amount of work needed to be
> done. That is practical for the ordering of projects/milestones as we
> might do the smaller ones first."*

M4 ranks on importance divided by size, so this is the number that ranking
divides by -- which is why the field has to exist and be filled before the
ranking can be written, and why the spec puts M2 in front of M4 as a real
dependency rather than a preference.

    python3 -m tools.board_size --file ideas.md --number 260 --size l \
        --dated 09-06 --note 'why it is L' --cycle 1090

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.board_row`, `tools.board_status` and `tools.board_priority`
hold, so the caller owns the compare-and-swap: `vault_tool.py get
--rev-file` before, `board_put --if-rev-file` after.

**A size is mine to set, and that is the spec's assignment rather than a
convenience**: rows are sized by *"Nova, directly, same technical-judgement
ownership as TRL"*. So there is a CLI here and deliberately no capture-box
route -- the three fields he owns (lifecycle approval, satisfaction, and the
project order) are M3 and M5, and none of them is this one.

The refusals, each one a way this could hand him a broken table or a
meaningless number: a size outside `SIZE_LABELS`; a **blank** size, which
`set_row_size` itself accepts and which is the state meaning nobody has
estimated it -- reachable on purpose, never by a caller that failed to say
what it meant; a row that is not on `## Board`, or is closed, both of which
`set_row_size` refuses by returning `None`, because an estimate of the work
left on a finished row is not a fact about anything; a `--dated` or `--note`
carrying a `|` or a newline; and `check` refusing the write when anything
other than that one size moved.

A re-estimate changes the size and nothing else -- **unless it carries a
`--note`**, for the same reason and with the same forgiveness
`tools.board_priority` documents: `append_detail_note` stamps the row's
`Updated` cell with the same `--dated` it writes into the write-up, so the
target row's date may move, and only to exactly the date given.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    SIZE_LABELS,
    append_detail_note,
    canonical_size,
    parse_board,
    parse_notes,
    set_row_size,
)


# The two keys `parse_board` derives from the size cell. A re-estimate is
# allowed to move both of them on the target row and nothing else; naming
# them here is what lets `check` be an equality test on everything else.
_SIZE_KEYS = frozenset({"size", "sizeKey"})


def _size_choices():
    """Every accepted spelling: the four keys and the four written forms."""
    return [key for key in SIZE_LABELS if key] + [
        value for value in SIZE_LABELS.values() if value
    ]


def resolve_size(value):
    """`xl` / `XL` / `x-large` -> the cell text, or `None`.

    `canonical_size` is what the rest of the system reduces a cell to, so
    routing a typed argument through it accepts exactly the spellings
    everything else considers equal and cannot invent a fifth. The one
    thing it accepts that this must not is the blank size: it is a legal
    cell and it means "nobody has estimated this", which is never what a
    cycle reaching for this tool is trying to say.
    """
    if not value or not value.strip():
        return None
    resolved = canonical_size(value)
    return resolved or None


def check(before, after, number, size, noted, dated=None):
    """Refuse the write unless that one size moved and nothing else did.

    Same shape and same reasoning as `tools.board_status.check`. It has one
    forgiveness of its own and it is not optional: `append_detail_note`
    stamps `Updated` with `dated`, so when `noted` is true the target row's
    `updated` may move, and only to `dated`. Asserting the new value rather
    than skipping the field is what keeps this from becoming a hole -- a
    plain exclusion would let any date through, including one a caller
    never asked for.
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
        if moved["size"] != size:
            problems.append(
                f"#{number} came back as {moved['size']!r}, asked for {size!r}"
            )
        if number in old_by_number:
            # `parse_board` derives `sizeKey` from the same cell, so both
            # move together or the parser is broken; comparing the rest is
            # what says nothing *else* moved.
            forgiven = set(_SIZE_KEYS)
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
                problems.append(f"#{number} changed something other than its size")

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
            problems.append(f"#{was['number']} changed underneath the re-estimate")

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


def _refuse_cell(value):
    """A `|` splits the cell, a newline splits the row. Both reach his file."""
    return "|" in value or "\n" in value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="a board markdown on disk")
    parser.add_argument("--number", required=True, type=int, help="the row number")
    parser.add_argument(
        "--size",
        required=True,
        help="s / m / l / xl, or a written form such as 'large'",
    )
    parser.add_argument("--dated", help="MM-DD, Oslo; stamped on --note only")
    parser.add_argument("--note", help="one line on why it moved, appended to the write-up")
    parser.add_argument("--cycle", type=int, help="stamped on --note")
    parser.add_argument("--out", help="where to write (default: in place)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    size = resolve_size(args.size)
    if size is None:
        print(
            f"REFUSED: '{args.size}' is not a size. One of: "
            + ", ".join(_size_choices()),
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
    after = set_row_size(before, args.number, size)
    if after is None:
        print(
            f"REFUSED: #{args.number} is not an open row in '## Board' in that "
            "file (a finished row deliberately carries no size)",
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

    problems = check(
        before, after, args.number, size, noted=bool(args.note), dated=args.dated
    )
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    was = {item["number"]: item for item in parse_board(before)["items"]}
    print(f"#{args.number}: {was[args.number]['size'] or '(unsized)'} -> {size}")
    print(f"{len(before)} -> {len(after)} bytes")
    if args.dry_run:
        return 0
    open(args.out or args.file, "w", encoding="utf-8").write(after)
    print(f"wrote {args.out or args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
