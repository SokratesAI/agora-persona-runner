"""Promote one of the owner's bare captures into a real board row.

His words, `issues.md` 2026-08-27 07:06, rated 🔴 Immediately: *"You
should immediately board 'not boarded yet' ideas and issues. Of you are
not able to start the work on it, mark it as backlog and give it a
priority. This should be done immediately! I see so many cycles just
letting them be unstaged, comments them and moves on. Even some issues
are fixed and done but still not moved out from the 'not boarded yet'
block. ... Like real Kanban. Now, the issues are a bit chaotic and its
not real Kanban! I want Kanban!"*

That is the second time he has asked. `nova_boards.add_row` was written
for the first one (2026-08-26) and its docstring quotes it -- **and it
only adds the row.** Nothing takes the bullet back out of the box he
types into, so boarding a capture by hand is two edits to one document
and a cycle that does the first and not the second leaves the item in
both places at once. Measured 2026-08-27, before this ran: **twelve**
bare captures above `## Board` across his two files, five of them
already answered by a cycle and two of them already shipped.

So this is the one call that moves an item across the board, and the
removal is the half that makes it Kanban rather than a copy:

    python3 -m tools.board_capture --file /tmp/i.md --index 3 \\
        --priority high --status backlog --dated 08-27 --dry-run

**`--index` is the capture's position in `capture_entries`, which is the
same number `tools.top_board_rows` prints beside each bullet.** Indices
shift as soon as one is removed, so board them **highest index first**
when doing several in a row, or re-read the file between calls.

The title is his first sentence and the write-up is everything he wrote,
verbatim -- `add_row`'s rule, not a new one. Any `🔴 Immediately: `
rating prefix and any `DONE (Cycle N): ` marker are stripped off both,
because those are cells now: the rating becomes the `Priority` column
and the closure becomes `✅ Done` in the `Status` column. A capture that
carries a rating prefix and no `--priority` keeps its own rating rather
than being re-guessed.

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.board_row`, `tools.roll_captures` and
`tools.roll_done_captures` hold, so the caller owns the compare-and-swap.

`check` re-parses the whole document afterwards and refuses unless the
capture count went down by exactly one, the row count up by exactly one,
every *other* capture is still there with the same text and the same
replies, and every row that was on the board is still on it unchanged.
The reply check is the one worth spelling out: a capture's answers are
indented bullets folded into the capture above them, so an off-by-one in
the span being cut takes a neighbour's answer with it and leaves both
documents looking fine.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (  # noqa: E402
    PRIORITY_LABELS,
    STATUS_LABELS,
    add_row,
    canonical_priority,
    capture_entries,
    parse_board,
    set_row_status,
    split_capture_done,
    split_capture_priority,
)

# The statuses a cycle may move a capture into. `outdated` is deliberately
# absent: `OUTDATED_STATUS`'s own comment says the split of labour is his
# -- a cycle proposes it on an existing row and he deletes -- and nothing
# he typed this week should arrive already written off.
_STATUS_CHOICES = ("backlog", "in-progress", "done", "blocked-on-edvard")


def first_sentence(text):
    """His paragraph -> the one line that goes in the table cell.

    A row's title is repeated three times across the wiki-link, the Item
    cell and the `### #N` heading, so it has to be one line; his capture
    is often several sentences. Cut at the first `. ` and keep everything
    if there isn't one. **No character count is involved** -- `add_row`'s
    docstring makes that explicit and it is the right call: a long first
    sentence goes in long, because a truncated title is a title that
    reads as a different item from the write-up under it.
    """
    one = " ".join((text or "").split())
    for end in (". ", "? ", "! "):
        at = one.find(end)
        if at > 0:
            return one[: at + 1].strip()
    return one


def check(before, after, number, title, capture_text):
    """Refuse the write unless exactly one capture became exactly one row."""
    problems = []
    old_board, new_board = parse_board(before), parse_board(after)
    old_caps = capture_entries(before)
    new_caps = capture_entries(after)

    if len(new_caps) != len(old_caps) - 1:
        problems.append(
            f"capture count went {len(old_caps)} -> {len(new_caps)}, expected -1"
        )
    gone = [(text, tuple(replies)) for _, _, text, replies in old_caps]
    left = [(text, tuple(replies)) for _, _, text, replies in new_caps]
    for kept in left:
        if kept in gone:
            gone.remove(kept)
        else:
            problems.append(f"a capture changed underneath the write: {kept[0][:60]!r}")
    if len(gone) == 1 and gone[0][0] != capture_text:
        problems.append(f"the wrong capture was removed: {gone[0][0][:60]!r}")

    old_by_number = {item["number"]: item for item in old_board["items"]}
    new_by_number = {item["number"]: item for item in new_board["items"]}
    if number in old_by_number:
        problems.append(f"#{number} was already on the board")
    if number not in new_by_number:
        problems.append(f"#{number} is not on the board afterwards")
    elif new_by_number[number]["title"] != title:
        problems.append(f"#{number} came back with a different title")
    if len(new_board["items"]) != len(old_board["items"]) + 1:
        problems.append(
            f"row count went {len(old_board['items'])} -> "
            f"{len(new_board['items'])}, expected +1"
        )
    for was in old_board["items"]:
        now = new_by_number.get(was["number"])
        if now is None:
            problems.append(f"#{was['number']} fell off the board")
        elif now["title"] != was["title"] or now["status"] != was["status"]:
            problems.append(f"#{was['number']} changed underneath the new row")
    for old_number, body in old_board["details"].items():
        if new_board["details"].get(old_number) != body:
            problems.append(f"the write-up for #{old_number} changed")
    return problems


def promote(before, index, priority, status, dated, title=None):
    """`(after, number, title)` or `(None, reason, None)`."""
    entries = capture_entries(before)
    if index < 0 or index >= len(entries):
        return None, f"no capture at index {index} ({len(entries)} in the list)", None
    start, end, text, replies = entries[index]

    done_cycle, text = split_capture_done(text)
    own_rating, text = split_capture_priority(text)
    if not text.strip():
        return None, "that capture is empty once its prefixes are stripped", None

    rating = priority if priority is not None else own_rating
    if canonical_priority(rating) is None:
        return None, f"'{rating}' is not a rating", None
    if done_cycle and status == "backlog":
        # He named this case himself: *"Even some issues are fixed and done
        # but still not moved out."* A bullet already marked `DONE (Cycle N)`
        # arriving on the board as backlog would put a shipped item back at
        # the start of the queue, so the marker decides rather than the
        # default does.
        status = "done"

    lines = before.split("\n")
    stripped = "\n".join(lines[:start] + lines[end:])

    row_title = (title or first_sentence(text)).strip()
    after, number = add_row(
        stripped, row_title, dated, rating, write_up=text, notes=replies
    )
    if after is None:
        return None, (
            "could not board it -- no '## Board' table in that file, or the "
            "title carries a '|' or a newline"
        ), None
    if status != "backlog":
        moved = set_row_status(after, number, STATUS_LABELS[status], updated=dated)
        if moved is None:
            return None, f"could not set the status to {STATUS_LABELS[status]}", None
        after = moved
    return after, number, row_title


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="his board markdown on disk")
    parser.add_argument("--index", required=True, type=int, help="capture position")
    parser.add_argument(
        "--priority",
        help="low / medium / high / immediate; default is the bullet's own prefix",
    )
    parser.add_argument("--status", default="backlog", choices=_STATUS_CHOICES)
    parser.add_argument("--dated", required=True, help="MM-DD, Oslo")
    parser.add_argument("--title", help="override the first-sentence title")
    parser.add_argument("--out", help="where to write (default: in place)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    # Both go straight into table cells. `tools.board_row` refuses these on
    # `--dated` for the reason its own comment gives: a stray `|` shifts
    # every column right of it and `parse_board` reads the tail of the date
    # as the rating.
    if "|" in args.dated or "\n" in args.dated or not args.dated.strip():
        print(
            "REFUSED: --dated goes straight into a table cell, so it may not "
            "be blank or carry a '|' or a newline",
            file=sys.stderr,
        )
        return 1
    if args.title and ("|" in args.title or "\n" in args.title):
        print("REFUSED: --title may not carry a '|' or a newline", file=sys.stderr)
        return 1
    if args.priority is not None and canonical_priority(args.priority) is None:
        print(
            f"REFUSED: '{args.priority}' is not a rating. One of: "
            + ", ".join(key for key in PRIORITY_LABELS if key),
            file=sys.stderr,
        )
        return 1

    before = open(args.file, encoding="utf-8").read()
    entries = capture_entries(before)
    if args.index < 0 or args.index >= len(entries):
        print(
            f"REFUSED: no capture at index {args.index} "
            f"({len(entries)} in the list)",
            file=sys.stderr,
        )
        return 1
    _, _, raw_text, _ = entries[args.index]

    after, number, row_title = promote(
        before, args.index, args.priority, args.status, args.dated, args.title
    )
    if after is None:
        print(f"REFUSED: {number}", file=sys.stderr)
        return 1

    problems = check(before, after, number, row_title, raw_text)
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    board = parse_board(after)
    row = next(item for item in board["items"] if item["number"] == number)
    print(f"boarded #{number} — {row_title}")
    print(f"  status {row['status']!r}  priority {row['priority']!r}")
    print(f"  captures {len(entries)} -> {len(capture_entries(after))}")
    print(f"  {len(before)} -> {len(after)} bytes")
    if args.dry_run:
        return 0
    open(args.out or args.file, "w", encoding="utf-8").write(after)
    print(f"wrote {args.out or args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
