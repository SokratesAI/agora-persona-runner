"""Group one board row into a milestone, in the record store.

Milestone M4 of idea #260's picking redesign, and the row half of it. A
milestone is *"a named group of tasks aimed at one maturity increment"* --
a project does not finish, it matures in increments, and this is the cell
that says which increment a row belongs to.

    python3 -m tools.board_milestone --board idea --number 260 \\
        --milestone 'Picking redesign' --dated 09-06 --note 'why' --cycle 1094

**This is the fourth of the ten `tools/board_*.py` writers converted onto
`agora_runner.board_write` for issue #203, and the second that needs
`append_note`.** What comes out is the same set as the three before it: the
markdown, the path on disk, the compare-and-swap the caller used to own, and
this module's own 60-line `check_from_contents`, which is what `change_row` is
once in a module that can be tested. What is left is this tool's vocabulary --
which cell, on which row, refused how.

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
generated board view: a `|` ends a cell, a line break ends a row.

**The name is scoped to the row's project, and against records that is an id
rather than a coincidence of spelling.** `store_item` mints or finds the
milestone under the row's *own* project (`entity_id.ensure_milestone`), so two
projects may each carry a `Backup` without them being one milestone -- which
`nova_next.milestone_ranks` used to get by keying on the pair of names. The
consequence worth knowing: the project a name lands under is the one in the
row's `Project` cell when this runs, and a row nobody has filed reads as
`DEFAULT_PROJECT` rather than as unfiled, so there is no row whose milestone
has nowhere to go.

`--milestone ''` clears the cell back to ungrouped, which has to stay
reachable: a milestone that turns out to be two is regrouped by first
emptying it, and unlike a size an empty name cannot be confused with a
typo. It is the one value that must be asked for explicitly, so it is
spelled `--milestone ''` rather than reached by leaving the flag off -- and it
is why `refuse_cell` grew an `allow_blank`, since every other cell rule in
this set treats blank as a caller that passed nothing useful.

A regrouping changes the milestone and nothing else -- **unless it carries a
`--note`**, which rides in the same single write as the cell move, so there is
no window where the row has moved and nothing says why. `append_note` stamps
the row's `updated` cell from the note's own `--dated` and refuses a caller
that names `updated` too, so this tool deliberately leaves it out of the
change set.

**A closed row is still refused, and against records that refusal has to be
this module's.** In markdown it was `set_row_milestone` returning `None`
because the row was not in `## Board`; the record store has no two tables, so
`change_row` would write a done row quite happily and its after-check would
agree. It is read off a board fetched before the first write, the same shape
as `board_status.refuse_row`. The reasoning is unchanged: the ranking that
reads this field only ranks open rows, so regrouping finished work changes
nothing anybody will read.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records, board_store, board_write
from agora_runner.board_document import BOARDS
from agora_runner.board_write import refuse_cell


def milestone_changes(milestone, dated=None):
    """The change set for a regrouping: the cell, and nothing derived off it.

    Unlike `status` and `priority`, `board_document.from_document` derives no
    second key from the milestone -- it reads the *name* back out of the
    registry by `milestoneId`. So this is one cell, and naming the set here
    anyway is what makes a future derived key a one-line change rather than a
    hunt through `main`.

    `dated` is the `updated` cell and `None` leaves it alone. **A caller with a
    note must not pass it**: `append_note` sets `updated` from the note's own
    date and refuses a change set that names it too.
    """
    changes = {"milestone": milestone}
    if dated is not None:
        changes["updated"] = dated
    return changes


def refuse_row(contents, number):
    """Why this row may not be regrouped, or `None` if it may.

    Read off a board fetched **before** anything is written, because both
    answers here have to mean "nothing happened". `change_row` refuses an
    absent row itself; a closed row it would write, and that refusal has no
    twin below this seam -- in markdown it was `set_row_milestone` returning
    `None` for a row that was not in `## Board`.
    """
    for item in contents["items"]:
        if item.get("number") == number:
            if item.get("done"):
                return (f"#{number} is finished, and a finished row "
                        "deliberately carries no milestone -- the ranking that "
                        "reads this field only ranks open rows")
            return None
    return f"#{number} is not a row on this board"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, choices=list(BOARDS),
                        help="which board the row is on")
    parser.add_argument("--number", required=True, type=int, help="the row number")
    parser.add_argument(
        "--milestone",
        required=True,
        help="the milestone name, or '' to clear it back to ungrouped",
    )
    parser.add_argument("--dated", help="MM-DD, Oslo; omitted leaves the cell alone")
    parser.add_argument("--note", help="one line on why it moved, appended to the write-up")
    parser.add_argument("--cycle", type=int, help="stamped on --note")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    milestone = args.milestone.strip()
    # `--milestone ''` is an instruction rather than an omission, so blank is
    # allowed here and on neither of the other two flags.
    for value, flag, blank_ok in ((args.milestone, "--milestone", True),
                                  (args.dated, "--dated", False),
                                  (args.note, "--note", False)):
        refusal = refuse_cell(value, flag, allow_blank=blank_ok)
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
    print(
        f"#{args.number}: {was['milestone'] or '(ungrouped)'}"
        f" -> {milestone or '(ungrouped)'}"
        f"  (project {was['project']})"
    )
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
                changes=milestone_changes(milestone), store=board_store,
            )
        else:
            board_write.change_row(
                args.board, args.number,
                milestone_changes(milestone, dated=args.dated),
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
