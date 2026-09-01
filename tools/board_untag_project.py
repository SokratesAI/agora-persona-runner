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

    python3 -m tools.board_untag_project --file /tmp/ideas.md --dry-run

Every affected row is done in one pass for `tools.board_project`'s reason:
this is one compare-and-swap on his file, and doing it a row at a time
means 38 of them, any one of which can lose. `--number` narrows it to
named rows when that is wanted; the default is every row that carries a
tag.

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.board_project`, `tools.board_row` and `tools.board_status`
hold, so the caller owns the compare-and-swap: `vault_tool.py get
--rev-file` before, `put --if-rev-file` after.

`check` is the tight one, because this moves *two* cells on a row rather
than one: the new title must be exactly the old title with that exact
prefix removed -- not merely shorter, and never re-derived by re-running
the regex on the result -- the project cell must hold the tag, and nothing
else on the board or in the write-ups may have moved. A row whose tag
names something `set_row_project` would refuse is skipped and reported
rather than half-written.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (  # noqa: E402
    parse_board,
    parse_notes,
    set_row_project,
    set_row_title,
    split_capture_project,
)


def tagged_rows(markdown, numbers=None):
    """`[(number, project, old_title, new_title)]` for every row carrying a tag.

    Read off `parse_board` rather than off the raw table, so a row this
    reports is a row the site sees. `numbers` narrows it; `None` is all of
    them.
    """
    wanted = set(numbers or ())
    found = []
    for item in parse_board(markdown)["items"]:
        if wanted and item["number"] not in wanted:
            continue
        project, rest = split_capture_project(item["title"])
        if not project or not rest:
            # No tag, or a title that is *only* a tag -- the second would
            # leave the row with an empty title, which `set_row_title`
            # reads as a delete. Neither is touched.
            continue
        found.append((item["number"], project, item["title"], rest))
    return found


def check(before, after, moves):
    """Refuse the write unless exactly those rows moved, exactly that far."""
    problems = []
    old = parse_board(before)
    new = parse_board(after)
    old_by_number = {item["number"]: item for item in old["items"]}
    new_by_number = {item["number"]: item for item in new["items"]}
    expected = {number: (project, new_title) for number, project, _, new_title in moves}

    for number, project, old_title, new_title in moves:
        now = new_by_number.get(number)
        if now is None:
            problems.append(f"#{number} is not on the board afterwards")
            continue
        if now["title"] != new_title:
            problems.append(f"#{number} came back titled {now['title'][:60]!r}")
        got = (now.get("project") or "").strip()
        if got != project:
            problems.append(f"#{number} came back under {got!r}, asked for {project!r}")
        # The title must be the old one minus *a parenthesised tag*, and
        # nothing else. Comparing against `new_title` above is not enough
        # on its own: `new_title` came from the same regex, so a regex
        # that ate a word too many agrees with itself perfectly. This
        # reads the removed head off the *original* title and asserts its
        # shape, without re-running the regex that produced it.
        was = old_by_number.get(number, {}).get("title", "")
        if not was.endswith(new_title) or len(was) <= len(new_title):
            problems.append(f"#{number}'s new title is not a suffix of its old one")
            continue
        head = was[: len(was) - len(new_title)].strip()
        if not (head.startswith("(") and head.endswith(")")):
            problems.append(
                f"#{number} lost more than a tag from its title: {head[:60]!r}"
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
        if was["number"] in expected:
            rest = {k: v for k, v in now.items() if k not in ("project", "title")}
            if rest != {k: v for k, v in was.items() if k not in ("project", "title")}:
                problems.append(f"#{was['number']} changed more than its title and project")
        elif now != was:
            problems.append(f"#{was['number']} changed underneath the retag")

    old_notes = [note["text"] for note in parse_notes(before)]
    new_notes = [note["text"] for note in parse_notes(after)]
    if old_notes != new_notes:
        problems.append(
            f"the bullet stream changed: {len(old_notes)} -> {len(new_notes)} note(s)"
        )

    # The write-ups carry the same title in their `### #N — ...` heading,
    # so they are *expected* to move on a retagged row and only there.
    for number, body in old["details"].items():
        if number in expected:
            continue
        if new["details"].get(number) != body:
            problems.append(f"the write-up for #{number} changed")
    return problems


def untag(markdown, numbers=None):
    """`(after, moves, skipped)`. `after is None` means nothing to do."""
    moves = tagged_rows(markdown, numbers)
    after = markdown
    done, skipped = [], []
    for number, project, old_title, new_title in moves:
        stepped = set_row_title(after, number, new_title)
        if stepped is None:
            skipped.append((number, project, "could not rewrite the title"))
            continue
        tagged = set_row_project(stepped, number, project)
        if tagged is None:
            skipped.append((number, project, f"{project!r} is not a legal project name"))
            continue
        after = tagged
        done.append((number, project, old_title, new_title))
    if not done:
        return None, [], skipped
    return after, done, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="his board markdown on disk")
    parser.add_argument(
        "--number",
        action="append",
        type=int,
        default=None,
        help="repeatable; default is every row carrying a tag",
    )
    parser.add_argument("--out", help="where to write (default: in place)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    before = open(args.file, encoding="utf-8").read()
    after, moves, skipped = untag(before, args.number)

    for number, project, reason in skipped:
        print(f"SKIPPED #{number} ({project!r}): {reason}", file=sys.stderr)
    if after is None:
        print("nothing to do: no boarded row carries a '(Project: X)' title")
        return 0 if not skipped else 1

    problems = check(before, after, moves)
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    for number, project, old_title, new_title in moves:
        print(f"#{number} -> project {project!r}")
        print(f"   was: {old_title[:90]}")
        print(f"   now: {new_title[:90]}")
    print(f"{len(moves)} row(s) retagged; {len(before)} -> {len(after)} bytes")
    if args.dry_run:
        return 0
    open(args.out or args.file, "w", encoding="utf-8").write(after)
    print(f"wrote {args.out or args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
