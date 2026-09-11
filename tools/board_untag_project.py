"""Lift a `(Project: X)` tag out of a boarded row's title into its `Project` cell.

The owner, capture 2026-09-01: *"Boarding of Marcus captures is putting
the literal "(Project: Marcus)" text into the row Title instead of
extracting it into the dedicated Project column -- checked directly:
roughly 30 boarded rows (ideas #190-217 and more) have "(Project: Marcus)"
baked into their title but an empty Project column, so board_projects()
can't see them and the app's Marcus project-overview page only shows the 2
rows (#146, #188) where the Project column happens to be set correctly.
Needs a cleanup pass moving the project tag out of the title text into the
Project column for every affected row, via set_row_title/whatever
nova_boards.py exposes -- not a hand-edit of the markdown table."*

He is right about the count and the cause. Measured on his two boards the
same morning: **26 rows on `ideas.md` and 12 on `issues.md`**, all tagged
`Marcus`, all sitting at the `Nova` default in the cell. `board_capture`
strips a rating prefix and a `DONE (Cycle N)` prefix off a bullet before
it becomes a title and had no third case, so his tag went in as prose.
`nova_boards.split_capture_project` is that third case and `board_capture`
now calls it; this is the pass over the rows that were already boarded
before it existed.

    python3 -m tools.board_untag_project --board idea --dry-run

**This is the second of the ten `tools/board_*.py` writers converted onto
`board_write.change_row` for issue #203**, and it is the first one that
moves *two* cells of a row rather than one -- `change_row` takes a change
set, so `{"title": ..., "project": ...}` is the whole of what used to be
`set_row_title` followed by `set_row_project`, with one after-check instead
of a hand-copied one. The path on disk is gone with it: this reads the
record store and writes it, so there is no `--file` and no compare-and-swap
for the caller to own.

**One guard did not move into `change_row` and must not, because it is this
tool's own vocabulary rather than the shared one.** `change_row` refuses
unless the row came back exactly as asked -- which says nothing about
whether what was *asked* was right. The failure this tool has to catch is a
regex that ate a word too many, and a check built on that regex's own output
agrees with it perfectly. So `refuse_move` reads the removed head off the
**original** title, without re-running the regex, and refuses unless it is a
parenthesised tag and nothing else. It runs before the first write, beside
the row-exists and legal-name checks, for `tools.board_project`'s reason:
one document per row means five rows are five writes, so the only promise
that survives is that a run naming a bad row writes nothing at all.

The project name rules are `tools.board_project`'s, imported rather than
restated -- they are about the generated markdown view the daily backup
renders, they are already written down once, and a second copy here is the
duplication issue #203 exists to remove.

**Neither pre-write refusal can currently fire on a real board, and saying
so is the point of keeping them.** `split_capture_project` bounds the name
it returns to `set_row_project`'s characters and its 40-character limit, and
it returns a suffix of the title by construction -- so today it can only
hand `refusals` a legal name and a legal move. That makes both guards
untestable through the CLI, and a guard whose failure branch is unreachable
is a guard that cannot be proved to work. They stay because they are the
place two copies of one rule are compared: this regex and
`board_project.refuse_project` are the same cell rule written down twice,
and nothing else would notice them drifting apart. The tests therefore drive
`refusals` and `refuse_move` directly and stage the CLI refusal through the
`refusals` seam, and there is one test that pins the unreachability itself
rather than leaving it as an assumption in this paragraph.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records, board_store, board_write  # noqa: E402
from agora_runner.board_document import BOARDS  # noqa: E402
from agora_runner.nova_boards import board_projects, split_capture_project  # noqa: E402
from tools.board_project import refuse_project  # noqa: E402


def tagged_rows_from_contents(contents, numbers=None):
    """`[(number, project, old_title, new_title)]` for every row carrying a tag.

    Read off a parsed record set rather than off the raw table, so a row
    this reports is a row the site sees. `numbers` narrows it; `None` is
    all of them.
    """
    wanted = set(numbers or ())
    found = []
    for item in contents["items"]:
        if wanted and item["number"] not in wanted:
            continue
        project, rest = split_capture_project(item["title"])
        if not project or not rest:
            # No tag, or a title that is *only* a tag -- the second would
            # leave the row with an empty title, which is a delete wearing
            # a retag's clothes. Neither is touched.
            continue
        found.append((item["number"], project, item["title"], rest))
    return found


def refuse_move(old_title, new_title):
    """Why this title rewrite may not be written, or `None` if it may.

    The one check `change_row` cannot make for us. It reads the head that
    would be removed off the *original* title and asserts its shape; it
    deliberately does not re-run `split_capture_project`, because a regex
    that ate a word too many produces a `new_title` that any check built on
    the same regex agrees with.

    There is deliberately no separate "and shorter" clause. A `new_title`
    equal to the old one leaves an empty head, and an empty head is not
    parenthesised, so the check below already refuses it -- a mutation
    proved that clause could not change any answer, and a condition that
    cannot fail is a condition nothing can test.
    """
    if not new_title:
        return "the retagged title would be empty"
    if not old_title.endswith(new_title):
        return "the new title is not a suffix of the old one"
    head = old_title[: len(old_title) - len(new_title)].strip()
    if not (head.startswith("(") and head.endswith(")")):
        return f"more than a tag would come off the title: {head[:60]!r}"
    return None


def refusals(contents, moves):
    """Every reason not to start writing, in the order the rows were named.

    One document per row, so this is what is left of the old all-or-nothing
    promise: the way this used to fail -- a good row and a bad one, half a
    project tagged -- is still refused with nothing written at all.
    """
    on_board = {item["number"] for item in contents["items"]}
    problems = []
    for number, project, old_title, new_title in moves:
        if number not in on_board:
            problems.append(f"#{number} is not a row on this board")
            continue
        bad_name = refuse_project(project)
        if bad_name:
            problems.append(f"#{number}: {bad_name}")
        bad_move = refuse_move(old_title, new_title)
        if bad_move:
            problems.append(f"#{number}: {bad_move}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, choices=list(BOARDS),
                        help="which board to sweep")
    parser.add_argument(
        "--number",
        action="append",
        type=int,
        default=None,
        help="repeatable; default is every row carrying a tag",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        before = board_records.contents(args.board, store=board_store)
    except board_records.RecordError as problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    moves = tagged_rows_from_contents(before, args.number)
    if not moves:
        print("nothing to do: no boarded row carries a '(Project: X)' title")
        return 0

    problems = refusals(before, moves)
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        print(f"nothing was written -- {len(moves)} row(s) were named",
              file=sys.stderr)
        return 1

    for number, project, old_title, new_title in moves:
        print(f"#{number} -> project {project!r}")
        print(f"   was: {old_title[:90]}")
        print(f"   now: {new_title[:90]}")
    if args.dry_run:
        return 0

    landed = []
    for number, project, _old_title, new_title in moves:
        try:
            board_write.change_row(
                args.board, number, {"title": new_title, "project": project},
                store=board_store)
        except (board_write.WriteRefused, board_write.BoardDamaged,
                board_records.RecordError) as problem:
            print(f"REFUSED: #{number}: {problem}", file=sys.stderr)
            if landed:
                # One document per row, so there is no revision spanning
                # these writes to roll back. Naming what landed is what
                # makes the re-run the rows that did not.
                print(
                    "already written: "
                    + ", ".join(f"#{n}" for n in landed)
                    + " -- re-run for the rest, not for these",
                    file=sys.stderr,
                )
            return 1
        landed.append(number)

    after = board_records.contents(args.board, store=board_store)
    print(f"{len(landed)} row(s) retagged on the {args.board} board")
    print(f"projects on this board: {', '.join(board_projects(after['items']))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
