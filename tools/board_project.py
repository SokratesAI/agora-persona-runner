"""Set the `Project` cell on one or more board rows, in the record store.

`agora_runner.nova_boards.set_row_project` has existed since the project
dashboard shipped and **nothing in this repo has ever called it.** The only
hits outside the module and its tests are its own docstring. The `Project`
column that `/projects` is built on is not empty — measured on 2026-08-31,
his issues board carried `Agora` on 4 rows and `Sokrates Post` on 1, and his
ideas board `Agora` on 8, `Sokrates Post` on 4 and `WhatsApp bridge` on 1,
with everything else falling back to `DEFAULT_PROJECT`. So the cells do get
filled; every one of them was filled by hand-splitting a row on `|`, which
is the exact corruption `set_row_project` was written to end.

The owner, capture 2026-08-31: *"Make the nas project a Nova project. I want
you to also make more Nova projects for ideas and issues for other future
projects. As in, whenever we are writing multiple ideas, issues or tasks for
something, then its a project and should get their own project overview."*
Grouping rows under a project is a thing a cycle should be doing routinely,
and routine work needs a button, not a paragraph asking for a habit.

    python3 -m tools.board_project --board issue --project NAS \\
        --number 122 --number 131

**This is the first of the ten `tools/board_*.py` writers converted onto
`board_write.change_row` for issue #203**, and the shape of the conversion is
the same for all ten: the markdown, the path on disk, the compare-and-swap
the caller used to own and this module's own hand-copied after-check all go,
and what is left is the vocabulary — which cell, on which row, with what
refused before anything is written. `change_row` reads the whole board,
compare-and-swaps the row it is about to write, writes it, reads the whole
board back and refuses unless exactly that row's named keys moved. That is
the guard `check_from_contents` used to be, once, in a module that can be
tested, instead of nine times in nine writers.

**A `--file` argument is gone and so is the all-or-nothing promise that came
with it, and that is a real loss worth stating rather than dropping.** One
markdown document held every row, so setting five projects was one write and
either all five landed or none did. One document per row is the whole point
of the record store — moving one row writes one document — so five rows are
five writes and there is no revision that spans them. What replaces the
promise is the half of it that was actually load-bearing: **every named row
is checked against the board before the first one is written**, so the way
this used to fail (a good row and a typo'd one, half the project tagged) is
still refused with nothing written. What genuinely cannot be promised any
more is a store that dies between write three and write four, and rather than
pretend otherwise this prints the rows that did land, so the re-run is the
rows that did not.

The refusals are the ways this could still hand him a broken board: a row
that is not on the board; a project name over 40 characters or carrying a
`|`, a `*` or a newline, each of which escapes its own cell in the generated
markdown view the daily backup keeps; and `change_row`'s own after-check,
which refuses when anything other than the named row's `project` moved.
There is deliberately no allowed-projects list: `board_projects` reads the
names back off the rows, so a new project costs one write, and a constant
here would be the second source of truth that the spec already ruled out.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records, board_store, board_write
from agora_runner.board_document import BOARDS
from agora_runner.nova_boards import board_projects

#: The cell rules, kept here rather than imported from `set_row_project`,
#: which took markdown and is on its way out. They are about the generated
#: view, not about the store: a `|` or a newline ends a row of the table the
#: daily GitHub backup renders, and a `*` opens emphasis that does not stop
#: at the cell. A record store would happily hold any of the three.
MAX_PROJECT = 40
ILLEGAL_IN_CELL = "|*\r\n"


def refuse_project(name):
    """Why this name may not go in a cell, or `None` if it may."""
    if not name:
        return "--project may not be blank"
    if len(name) > MAX_PROJECT:
        return (f"--project is {len(name)} characters; a cell holds "
                f"{MAX_PROJECT}")
    for character in ILLEGAL_IN_CELL:
        if character in name:
            shown = {"\r": "a carriage return", "\n": "a newline"}.get(
                character, f"a {character!r}")
            return (f"--project carries {shown}, which escapes its own cell "
                    "in the generated board view")
    return None


def missing_rows(contents, numbers):
    """The named numbers that are not rows on this board, in the order given.

    This is the half of the old all-or-nothing promise that survives one
    document per row: it runs against a board read **before** the first
    write, so the failure that promise was really about — a run naming four
    good rows and one typo, leaving a project on some of its rows — is
    refused with nothing written at all.
    """
    on_board = {item["number"] for item in contents["items"]}
    return [number for number in numbers if number not in on_board]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, choices=list(BOARDS),
                        help="which board the rows are on")
    parser.add_argument(
        "--number",
        required=True,
        type=int,
        action="append",
        help="a row number; repeat it for every row in the project",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="the project name, stored on the row and rendered in its cell",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    project = (args.project or "").strip()
    refusal = refuse_project(project)
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1

    try:
        before = board_records.contents(args.board, store=board_store)
    except board_records.RecordError as problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    absent = missing_rows(before, args.number)
    if absent:
        print(
            "REFUSED: " + ", ".join(f"#{n}" for n in absent)
            + f" is not a row on the {args.board} board, so nothing was "
              "written — no row of this project is tagged",
            file=sys.stderr,
        )
        return 1

    was = {item["number"]: item for item in before["items"]}
    for number in args.number:
        # `contents` fills an unfiled row with `DEFAULT_PROJECT`, so this is
        # always a name -- there is no blank cell to write "(none)" for.
        print(f"#{number}: {was[number].get('project')} -> {project}")
    if args.dry_run:
        return 0

    landed = []
    for number in args.number:
        try:
            board_write.change_row(args.board, number, {"project": project},
                                   store=board_store)
        except (board_write.WriteRefused, board_write.BoardDamaged,
                board_records.RecordError) as problem:
            print(f"REFUSED: #{number}: {problem}", file=sys.stderr)
            if landed:
                # One document per row, so there is no revision spanning the
                # five writes to roll back. Naming what landed is what makes
                # the re-run the rows that did not.
                print(
                    "already written: "
                    + ", ".join(f"#{n}" for n in landed)
                    + f" — re-run for the rest, not for these",
                    file=sys.stderr,
                )
            return 1
        landed.append(number)

    after = board_records.contents(args.board, store=board_store)
    print(f"wrote {len(landed)} row(s) on the {args.board} board")
    print(f"projects on this board: {', '.join(board_projects(after['items']))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
