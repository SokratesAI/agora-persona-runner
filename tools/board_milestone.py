"""Group one board row into a milestone, on his boards or my own.

Milestone M4 of idea #260's picking redesign, and the row half of it. A
milestone is *"a named group of tasks aimed at one maturity increment"* --
a project does not finish, it matures in increments, and this is the cell
that says which increment a row belongs to.

    python3 -m tools.board_milestone --file ideas.md --number 260 \
        --milestone 'Picking redesign' --dated 09-06 --note 'why' --cycle 1094

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.board_row`, `tools.board_status`, `tools.board_priority` and
`tools.board_size` hold, so the caller owns the compare-and-swap:
`vault_tool.py get --rev-file` before, `board_put --if-rev-file` after.

**The grouping is mine to set, and that is the spec's assignment rather
than a convenience.** He corrected the first draft of the spec to hand this
tier to me: *"the thing is that i actually want you to reorder things, not
projects, but milestones and tasks... I want the ability to reorder tasks
and milestones but the default is that you do it."* So there is a CLI here
and deliberately no capture-box route. His override of a computed milestone
order is a pin, which is a later slice and is not this.

**There is no vocabulary and that is deliberate.** `Priority`, `Status` and
`Size` each validate against a fixed set; a milestone name is a name, and a
list of allowed ones would be me deciding in advance what increments a
project may have. What is refused instead is a name that would break the
file: a `|` ends a cell, a newline ends a row.

The name is scoped to the row's project. `nova_next.milestone_ranks` keys on
the pair, so two projects may each carry a `Backup` milestone without them
being one milestone -- which also means a milestone name on a row whose
`Project` cell is wrong groups it into the wrong project's list, silently.

`--milestone ''` clears the cell back to ungrouped, which has to stay
reachable: a milestone that turns out to be two is regrouped by first
emptying it, and unlike a size an empty name cannot be confused with a
typo. It is the one value that must be asked for explicitly, so it is
spelled `--milestone ''` rather than reached by leaving the flag off.

A regrouping changes the milestone and nothing else -- **unless it carries
a `--note`**, for the same reason and with the same forgiveness
`tools.board_size` documents: `append_detail_note` stamps the row's
`Updated` cell with the same `--dated` it writes into the write-up.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    append_detail_note,
    parse_board,
    parse_notes,
    set_row_milestone,
)


# The one key `parse_board` derives from the milestone cell. Unlike `Size`
# and `Priority` there is no second derived key, because there is no
# vocabulary to reduce a name to -- naming the set here anyway is what lets
# `check` stay an equality test on everything else, and what makes a second
# derived key a one-line change rather than a rewrite.
_MILESTONE_KEYS = frozenset({"milestone"})


def check_from_contents(old, new, old_notes, new_notes, number, milestone, noted, dated=None):
    """Refuse the write unless that one milestone moved and nothing else did.

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
        if moved["milestone"] != milestone:
            problems.append(
                f"#{number} came back as {moved['milestone']!r}, "
                f"asked for {milestone!r}"
            )
        if number in old_by_number:
            # Comparing every other field is what says nothing *else*
            # moved -- the same equality test its three siblings run.
            forgiven = set(_MILESTONE_KEYS)
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
                problems.append(
                    f"#{number} changed something other than its milestone"
                )

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
            problems.append(
                f"#{was['number']} changed underneath the regrouping"
            )

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
        "--milestone",
        required=True,
        help="the milestone name, or '' to clear it back to ungrouped",
    )
    parser.add_argument("--dated", help="MM-DD, Oslo; stamped on --note only")
    parser.add_argument("--note", help="one line on why it moved, appended to the write-up")
    parser.add_argument("--cycle", type=int, help="stamped on --note")
    parser.add_argument("--out", help="where to write (default: in place)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    milestone = args.milestone.strip()
    if _refuse_cell(milestone):
        print(
            "REFUSED: --milestone is one cell in his table, so it may not "
            "carry a '|' or a newline",
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
    after = set_row_milestone(before, args.number, milestone)
    if after is None:
        print(
            f"REFUSED: #{args.number} is not an open row in '## Board' in that "
            "file (a finished row deliberately carries no milestone)",
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
        before_board, after_board, before_notes, after_notes, args.number, milestone,
        noted=bool(args.note), dated=args.dated,
    )
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    was = {item["number"]: item for item in before_board["items"]}
    print(
        f"#{args.number}: {was[args.number]['milestone'] or '(ungrouped)'}"
        f" -> {milestone or '(ungrouped)'}"
    )
    print(f"{len(before)} -> {len(after)} bytes")
    if args.dry_run:
        return 0
    open(args.out or args.file, "w", encoding="utf-8").write(after)
    print(f"wrote {args.out or args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
