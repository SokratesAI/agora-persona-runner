"""Board one of my own issues or ideas, instead of only appending a bullet.

Issue #97's remaining half, and it is the half that decides whether the
first half rots. `nova/resources/issues.md` and `.../ideas.md` grew a
`## Board` table and a `# Details` section, `nova_site.board_payload`
parses them with the same `parse_board` it runs on the owner's files, and
`app.js` draws my rows as rows. Measured live on 2026-08-26: five rows on
my issues board, three on ideas.

**And nothing tells a cycle how to add one.** `prompt.md` step 6 asks for
a flat bullet under `## Entries` and says nothing about the board, so
every cycle since the board was built has written the bullet -- which is
correct, it is what the step says -- and the board has stood at eight
rows while the two streams hold hundreds. That is the same shape as the
`add_row` docstring one layer down: *"Eight cycles in a row chose the
cheap correct thing; that is a missing button, not a habit."* This is the
button, for my own two files.

    python3 -m tools.board_row --file issues.md --title '...' \
        --priority high --write-up-file /tmp/w.md

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.roll_done_captures` and `tools.roll_captures` hold, so
the caller owns the compare-and-swap. `prompt.md` step 6 carries the
`get --rev-file` / `put --if-rev-file` wrapper.

It refuses rather than guesses: no `## Board` table in the file, a title
with a `|` or a newline in it, or a priority that is not one of the four
all exit 1 with the reason. A blank rating is the state that means nobody
has looked (`ideas.md` #69), so `--priority` is required here even though
`add_row` would accept an empty one for the owner's board.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    PRIORITY_LABELS,
    add_row,
    canonical_priority,
    parse_board,
)


def _priority_choices():
    """The four ratings, by key and by their written form."""
    return [key for key in PRIORITY_LABELS if key] + [
        label for key, label in PRIORITY_LABELS.items() if key
    ]


def check(before, after, number, title):
    """Refuse the write unless the row is really there and nothing else moved.

    Same guard as `roll_done_captures.check` and for the same reason: this
    edits a document the site parses, so the test that matters is what
    `parse_board` says afterwards, not what the string looks like. Every
    row that was on the board stays on it with the same title and status,
    and the new number is present exactly once.
    """
    problems = []
    old = parse_board(before)
    new = parse_board(after)
    old_by_number = {item["number"]: item for item in old["items"]}
    new_by_number = {item["number"]: item for item in new["items"]}

    if number in old_by_number:
        problems.append(f"#{number} was already on the board")
    if number not in new_by_number:
        problems.append(f"#{number} is not on the board afterwards")
    elif new_by_number[number]["title"] != title:
        problems.append(f"#{number} came back with a different title")

    if len(new["items"]) != len(old["items"]) + 1:
        problems.append(
            f"row count went {len(old['items'])} -> {len(new['items'])}, expected +1"
        )
    for was in old["items"]:
        now = new_by_number.get(was["number"])
        if now is None:
            problems.append(f"#{was['number']} fell off the board")
        elif now["title"] != was["title"] or now["status"] != was["status"]:
            problems.append(f"#{was['number']} changed underneath the new row")

    if old["captures"] != new["captures"]:
        problems.append("the capture bullets above the board changed")
    for old_number, body in old["details"].items():
        if new["details"].get(old_number) != body:
            problems.append(f"the write-up for #{old_number} changed")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="my board markdown on disk")
    parser.add_argument("--title", required=True, help="one line, no pipe")
    parser.add_argument(
        "--priority",
        required=True,
        help="low / medium / high / immediate, or the written form",
    )
    parser.add_argument("--dated", required=True, help="MM-DD, Oslo")
    parser.add_argument("--write-up-file", help="the body of the detail block")
    parser.add_argument("--out", help="where to write (default: in place)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if canonical_priority(args.priority) is None or not args.priority.strip():
        print(
            f"REFUSED: '{args.priority}' is not a rating. One of: "
            + ", ".join(_priority_choices()),
            file=sys.stderr,
        )
        return 1

    write_up = ""
    if args.write_up_file:
        write_up = open(args.write_up_file, encoding="utf-8").read()

    before = open(args.file, encoding="utf-8").read()
    after, number = add_row(
        before, args.title, args.dated, args.priority, write_up=write_up
    )
    if after is None:
        print(
            "REFUSED: could not board it -- no '## Board' table in that file, "
            "or the title carries a '|' or a newline",
            file=sys.stderr,
        )
        return 1

    problems = check(before, after, number, args.title.strip())
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    print(f"boarded #{number} — {args.title.strip()}  [{canonical_priority(args.priority)}]")
    print(f"{len(before)} -> {len(after)} bytes")
    if args.dry_run:
        return 0
    open(args.out or args.file, "w", encoding="utf-8").write(after)
    print(f"wrote {args.out or args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
