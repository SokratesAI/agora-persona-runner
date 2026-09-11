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

    python3 -m tools.board_capture --board issue --index 3 \\
        --priority high --status backlog --dated 08-27 --dry-run

**`--index` is the capture's position in the list his board actually
shows**, which is the same number `tools.top_board_rows` prints beside
each bullet. `board_records.capture_at` resolves it through
`board_document.captures_in_order`, the one sort both this and the read
go through -- `read_captures` answers in lexical id order, where `cap_10`
sits between `cap_1` and `cap_2`, so a lookup that skipped the sort would
board the bullet he pointed at and delete a different one on any board
past ten captures. Indices still shift as soon as one is removed, so
board them **highest index first** when doing several in a row, or
re-read between calls.

The title is his first sentence and the write-up is everything he wrote,
verbatim -- `add_row`'s rule, not a new one. Any `🔴 Immediately: `
rating prefix and any `DONE (Cycle N): ` marker are stripped off both,
because those are cells now: the rating becomes the `Priority` column
and the closure becomes `✅ Done` in the `Status` column. A capture that
carries a rating prefix and no `--priority` keeps its own rating rather
than being re-guessed.

**This is issue #203's conversion of the last writer that had no door.**
It took `--file`, a path on disk, and did both halves by rewriting his
markdown; it now takes `--board` and goes through the record store, like
every other `tools/board_*.py` writer. Three doors carry it:
`board_records.capture_at` finds the bullet, `board_write.add_row` boards
the row, and `board_store.delete_capture` removes the bullet conditional
on the revision it was read at.

**The row goes in first and the bullet comes out second, and the order is
not arbitrary.** `add_row` refuses on its own after-check, so a failure
there leaves both documents untouched and his bullet where he left it. The
other order would take his words out of the box and then discover the row
could not be written, with nothing left to put back. A cycle that dies
between the two comes back to an item in both places, which is visible and
repairable -- `delete_capture` returns `False` rather than raising for a
bullet that has already gone, so re-running the pair is free.

**What this checks afterwards is the capture half only.** `add_row` owns
the row half and raises `BoardDamaged` on it -- row count +1, the new row
exactly as written, every other row and every other write-up identical --
so re-checking it here would be a second copy of a rule already enforced.
What no door below this checks is that removing one bullet removed exactly
one bullet: a capture's answers are indented replies folded into the
capture above them, and against markdown an off-by-one in the span being
cut took a neighbour's answer with it and left both documents looking
fine. Against records that span is gone, but `write_captures(prune=True)`
is still one wrong call away from expressing a deletion as an absence, so
the invariant is checked rather than assumed.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records, board_store, board_write  # noqa: E402
from agora_runner.board_document import (  # noqa: E402
    BOARDS,
    capture_replies_of,
    capture_text_of,
)
from agora_runner.board_store import StoreError  # noqa: E402
from agora_runner.board_write import refuse_cell  # noqa: E402
from agora_runner.nova_boards import (  # noqa: E402
    PRIORITY_LABELS,
    STATUS_LABELS,
    board_projects,
    canonical_priority,
    split_capture_done,
    split_capture_priority,
    split_capture_project,
    split_capture_project_tag,
    unresolved_capture_project_tag,
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


def capture_pairs(contents):
    """`[(text, replies), ...]` -- one capture and its answers, in his order.

    `contents` keeps the two halves in parallel lists because that is what
    `parse_board` returned, and every comparison here is on the pair: a
    reply lost off a capture that kept its text is the failure mode this
    module has, and two lists compared separately would each read as fine.
    """
    return list(zip(
        contents.get("captures") or (),
        [tuple(replies) for replies in contents.get("captureReplies") or ()],
    ))


def check_captures(before, after, capture_text):
    """Refuse unless exactly the one bullet named came out of the box.

    Takes the two `board_records.contents` dicts, read either side of the
    write. The row half is `add_row`'s and is not repeated here -- see the
    module docstring.
    """
    problems = []
    was, now = capture_pairs(before), capture_pairs(after)
    if len(now) != len(was) - 1:
        problems.append(
            f"capture count went {len(was)} -> {len(now)}, expected -1")
    gone = list(was)
    for kept in now:
        if kept in gone:
            gone.remove(kept)
        else:
            problems.append(
                f"a capture changed underneath the write: {kept[0][:60]!r}")
    if len(gone) == 1 and gone[0][0] != capture_text:
        problems.append(f"the wrong capture was removed: {gone[0][0][:60]!r}")
    return problems


def known_names(contents, extra=()):
    """The board's own projects first, then any the caller added.

    Order matters only for readability -- `split_capture_project_tag`
    returns the first name whose slug matches and slugs are unique -- but
    the board's own spelling winning is the right precedence anyway: if
    two boards spell one project differently, the row being written should
    keep the spelling already on its own page.
    """
    names = board_projects(contents.get("items") or [])
    for name in extra or ():
        cleaned = (name or "").strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names


def promote(text, priority, status, dated, title=None, project=None,
            known=()):
    """His bullet -> the arguments `add_row` takes, or `(None, reason)`.

    Pure: it reads no store and writes nothing, so the whole of "what does
    this bullet become" is testable without a board. `main` holds the two
    store calls either side of it.

    Returns `(fields, None)`, where `fields` carries `title`, `write_up`,
    `priority`, `status` and `project` -- `project` is `""` when the row
    lands under none, and the caller needs that value because only this
    function sees the tag it was lifted out of.
    """
    done_cycle, text = split_capture_done(text)
    own_rating, text = split_capture_priority(text)
    own_project, text = split_capture_project(text)
    if not own_project:
        # The app's shape, if he did not type the hand shape. A tag can only
        # resolve to a project that already exists -- `known` is this board's
        # cells plus the registry's names -- and an unknown slug stays in the
        # title rather than inventing one.
        own_project, text = split_capture_project_tag(text, known)
    if not text.strip():
        return None, "that capture is empty once its prefixes are stripped"

    # His tag decides unless the caller named one, the same precedence the
    # rating already has: a bullet that says which project it belongs to
    # is him answering the question, not a default to be re-guessed.
    tag = (project or own_project or "").strip()

    rating = priority if priority is not None else own_rating
    if canonical_priority(rating) is None:
        return None, f"'{rating}' is not a rating"
    if done_cycle and status == "backlog":
        # He named this case himself: *"Even some issues are fixed and done
        # but still not moved out."* A bullet already marked `DONE (Cycle N)`
        # arriving on the board as backlog would put a shipped item back at
        # the start of the queue, so the marker decides rather than the
        # default does.
        status = "done"

    return {
        "title": (title or first_sentence(text)).strip(),
        "write_up": text,
        "priority": rating,
        "status": status,
        "project": tag,
    }, None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, choices=list(BOARDS),
                        help="which of his boards the capture is on")
    parser.add_argument("--index", required=True, type=int, help="capture position")
    parser.add_argument(
        "--priority",
        help="low / medium / high / immediate; default is the bullet's own prefix",
    )
    parser.add_argument("--status", default="backlog", choices=_STATUS_CHOICES)
    parser.add_argument("--dated", required=True, help="MM-DD, Oslo")
    parser.add_argument("--title", help="override the first-sentence title")
    parser.add_argument(
        "--project",
        help="Project cell; default is the bullet's own '(Project: X)' prefix",
    )
    parser.add_argument("--cycle", type=int, help="stamped on the replies carried across")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    # Both go straight into table cells. `add_row` refuses them too and would
    # refuse before writing anything, but a refusal that costs no store call
    # is the better one -- and `--title` reaching `refuse_cell` here is what
    # makes the message name the flag he typed.
    for value, flag in ((args.dated, "--dated"), (args.title, "--title")):
        refusal = refuse_cell(value, flag)
        if refusal:
            print(f"REFUSED: {refusal}", file=sys.stderr)
            return 1
    if args.priority is not None and canonical_priority(args.priority) is None:
        print(
            f"REFUSED: '{args.priority}' is not a rating. One of: "
            + ", ".join(key for key in PRIORITY_LABELS if key),
            file=sys.stderr,
        )
        return 1

    try:
        before = board_records.contents(args.board, store=board_store)
        # Both boards mint into one registry, so this is the union the
        # project picker in the app offers -- the flag that used to widen it
        # took a path to the sibling board's markdown and could be forgotten.
        names = known_names(before, board_records.project_names(store=board_store))
        capture = board_records.capture_at(args.board, args.index, store=board_store)
    except board_records.RecordError as problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 1
    if capture is None:
        print(
            f"REFUSED: no capture at index {args.index} "
            f"({len(capture_pairs(before))} in the list)",
            file=sys.stderr,
        )
        return 1
    raw_text = capture_text_of(capture)

    fields, refusal = promote(
        raw_text, args.priority, args.status, args.dated, args.title,
        args.project, names,
    )
    if fields is None:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1

    tag = fields["project"]
    print(f"boarding — {fields['title']}")
    print(f"  status {STATUS_LABELS[fields['status']]!r}  "
          f"priority {canonical_priority(fields['priority'])!r}")
    if tag:
        print(f"  project {tag!r}, lifted out of the title")
    else:
        # Say it out loud. He picked a project in the app, the slug matched
        # nothing this call knew about, and the row went in under none with
        # the tag still in its title -- which used to happen in silence.
        missed = unresolved_capture_project_tag(raw_text, names)
        if missed:
            print(
                f"  WARNING: '#{missed}' matched no project this call knows "
                "about, so the row goes in with no project and the tag is "
                "still in its title. Pass --project <name>.",
                file=sys.stderr,
            )
    if args.dry_run:
        return 0

    try:
        row = board_write.add_row(
            args.board, fields["title"], args.dated, fields["priority"],
            status=fields["status"], write_up=fields["write_up"],
            notes=capture_replies_of(capture), project=tag,
            cycle=args.cycle, author="nova", store=board_store,
        )
    except (board_write.WriteRefused, board_write.BoardDamaged,
            board_records.RecordError) as problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    # The row is on his board from here on, so nothing below may return
    # without saying so: a failure here leaves the item in both places, which
    # is the state re-running the same call repairs.
    try:
        removed = board_store.delete_capture(capture)
    except StoreError as problem:
        print(
            f"boarded #{row['number']}, but the bullet is still in the box: "
            f"{problem}",
            file=sys.stderr,
        )
        return 1
    if not removed:
        print(
            f"boarded #{row['number']}; the bullet had already gone from the "
            "box, so nothing was removed",
            file=sys.stderr,
        )

    after = board_records.contents(args.board, store=board_store)
    problems = check_captures(before, after, raw_text)
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    print(f"boarded #{row['number']} on the {args.board} board")
    print(f"  captures {len(capture_pairs(before))} -> {len(capture_pairs(after))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
