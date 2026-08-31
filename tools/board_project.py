"""Set the `Project` cell on one or more board rows, on his boards or my own.

`agora_runner.nova_boards.set_row_project` has existed since the project
dashboard shipped and **nothing has ever called it.** The only hits outside
the module and its tests are its own docstring. So the `Project` column that
`/projects` is built on could only ever be filled in by hand-splitting a row
on `|` — which is the exact corruption `set_row_project` was written to end —
and the result is that the page has almost nothing on it.

The owner, capture 2026-08-31: *"Make the nas project a Nova project. I want
you to also make more Nova projects for ideas and issues for other future
projects. As in, whenever we are writing multiple ideas, issues or tasks for
something, then its a project and should get their own project overview."*
Grouping rows under a project is a thing a cycle should be doing routinely,
and routine work needs a button, not a paragraph asking for a habit.

    python3 -m tools.board_project --file issues.md --project NAS \\
        --number 122 --number 131

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.board_row`, `tools.board_status` and `tools.roll_captures`
hold, so the caller owns the compare-and-swap: `vault_tool.py get
--rev-file` before, `put --if-rev-file` after.

`--number` repeats because a project is by definition more than one row —
tagging five rows one process at a time would mean five compare-and-swap
pairs on one file, and losing one of them halfway leaves a project that
exists on some of its rows. All the rows are set in memory and the whole
document is checked once, so either every row named lands or none does.

The refusals are the ways this could hand him a broken table: a row that is
not on `## Board` (a `## Done` row has a different column layout, so a sixth
cell there would land past `Where`); a project name carrying a `|`, a
newline or a `*`, each of which escapes its own cell; and `check` refusing
the write when anything other than the named rows' `Project` cells moved.
There is deliberately no allowed-projects list: `board_projects` reads the
names back off the rows, so a new project costs one cell, and a constant
here would be the second source of truth that design already ruled out.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    board_projects,
    parse_board,
    parse_notes,
    set_row_project,
)


def check(before, after, numbers, project):
    """Refuse the write unless those rows' projects moved and nothing else did.

    Same shape and same reasoning as `tools.board_status.check`: this edits a
    document the site parses, so the question is what `parse_board` says
    afterwards, not what the string looks like. Setting a project changes one
    cell per named row and nothing else at all — not a title, not a status,
    not a rating, not a write-up, not the bullet stream — which makes this the
    tightest guard of the three board tools.
    """
    problems = []
    old = parse_board(before)
    new = parse_board(after)
    old_by_number = {item["number"]: item for item in old["items"]}
    new_by_number = {item["number"]: item for item in new["items"]}

    wanted = set(numbers)
    for number in numbers:
        if number not in old_by_number:
            problems.append(f"#{number} was not on the board to begin with")
        if number not in new_by_number:
            problems.append(f"#{number} is not on the board afterwards")
            continue
        got = (new_by_number[number].get("project") or "").strip()
        if got != project:
            problems.append(
                f"#{number} came back under {got!r}, asked for {project!r}"
            )

    if len(new["items"]) != len(old["items"]):
        problems.append(
            f"row count went {len(old['items'])} -> {len(new['items'])}, expected no change"
        )
    for was in old["items"]:
        now = new_by_number.get(was["number"])
        if now is None:
            problems.append(f"#{was['number']} fell off the board")
            continue
        if was["number"] in wanted:
            # The one cell allowed to differ, and only on a row that was
            # named. Everything else on that row is compared here too.
            if {k: v for k, v in now.items() if k != "project"} != {
                k: v for k, v in was.items() if k != "project"
            }:
                problems.append(f"#{was['number']} changed more than its project")
        elif now != was:
            problems.append(f"#{was['number']} changed underneath the project move")

    old_notes = [note["text"] for note in parse_notes(before)]
    new_notes = [note["text"] for note in parse_notes(after)]
    if old_notes != new_notes:
        problems.append(
            f"the bullet stream changed: {len(old_notes)} -> {len(new_notes)} note(s)"
        )

    for number, body in old["details"].items():
        if new["details"].get(number) != body:
            problems.append(f"the write-up for #{number} changed")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="a board markdown on disk")
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
        help="the project name, written into the row's sixth cell verbatim",
    )
    parser.add_argument("--out", help="where to write (default: in place)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    project = (args.project or "").strip()
    if not project:
        print("REFUSED: --project may not be blank", file=sys.stderr)
        return 1

    before = open(args.file, encoding="utf-8").read()
    after = before
    for number in args.number:
        stepped = set_row_project(after, number, project)
        if stepped is None:
            print(
                f"REFUSED: #{number} is not a row in '## Board' in that file, or "
                f"'{project}' is not a legal cell (over 40 characters, or carrying "
                "a '|', a '*' or a newline)",
                file=sys.stderr,
            )
            return 1
        after = stepped

    problems = check(before, after, args.number, project)
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    was = {item["number"]: item for item in parse_board(before)["items"]}
    for number in args.number:
        old = (was[number].get("project") or "").strip() or "(none)"
        print(f"#{number}: {old} -> {project}")
    print(f"projects on this board: {', '.join(board_projects(parse_board(after)['items']))}")
    print(f"{len(before)} -> {len(after)} bytes")
    if args.dry_run:
        return 0
    open(args.out or args.file, "w", encoding="utf-8").write(after)
    print(f"wrote {args.out or args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
