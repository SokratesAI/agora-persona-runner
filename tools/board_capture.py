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

**Since 2026-09-14 a capture can arrive as several tasks rather than one
row.** That is the plural in issue #212 -- *"break each capture into tasks
that each have a checkable definition of done"* -- and it was the half left
over when `--done-when` shipped earlier the same day:

    python3 -m tools.board_capture --board issue --index 3 \\
        --priority high --dated 09-14 --milestone 'Cost and quota' \\
        --task 'Profile the journal query' \\
        --done-when 'a flame graph names the slow call' \\
        --task 'Paginate the journal page' \\
        --done-when 'the page ships 20 entries'

`--task` and `--done-when` are paired by position, one each, and an unequal
count is refused rather than trimmed. Every row carries his words whole --
slicing his paragraph between them would be this tool deciding which of his
sentences belongs to which task -- and each says which task of how many it
is, so the repetition reads as one capture cut up.

**The all-or-nothing guarantee below is weaker for several rows, and it says
so out loud.** Rows are minted one at a time, so a failure on the second one
leaves the first on the board with his bullet still in the box. That case
prints the numbers that landed and says a blind re-run would board them
twice, which is the one thing the operator cannot see from the board itself.

**Since 2026-09-14 one of `--milestone` and `--no-milestone` is required.**
This tool took `--project` and had no way to name a milestone at all, so
every row it boarded arrived under none -- and issue #227's fourth rule is
that a task under no milestone serves no key result. `project_goals_check`
counted 24 of them that morning; cycle 1554 placed them by hand and then
came here, because a backlog cleared by hand regrows at exactly the rate
the door creates it. A default was the tempting fix and is the wrong one:
the cheap name is whichever milestone is already on screen, and a row
under the wrong milestone reads as placed to every check downstream. So
the caller either names one or says out loud that none fits yet.
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


DONE_WHEN_PREFIX = "**Done when:** "

DONE_WHEN_REFUSAL = (
    "a row is a task and a task needs a checkable definition of done -- "
    "pass --done-when \"...\" (issue #212). A capture too vague to write one "
    "for is not a task yet: ask him in a thread "
    "(python -m agora_runner.needs_input) rather than boarding a row no "
    "cycle can ever finish."
)


def apply_done_when(fields, done_when):
    """Add issue #212's definition of done to a promoted row, or refuse it.

    Issue #227's model ends at *"Task (one cycle, one checkable definition
    of done)"*, and a board row is that bottom tier. Issue #212 is the half
    of it that was never built: *"When boarding, break each capture into
    tasks that each have a checkable definition of done."* So boarding
    without one is refused here rather than left to whoever is reading.

    The sentence is appended to the write-up rather than put in front of
    his words. The write-up is his text verbatim -- `add_row`'s rule, not a
    new one -- and a `**Nova, MM-DD:**` reply already lands after it, so
    this goes where a reply goes.

    **A capture that arrived already finished is the one carve-out.** A
    bullet carrying `DONE (Cycle N)` boards straight to `✅ Done`, and
    asking when a finished thing will be finished is ceremony rather than a
    check. It is refused nothing and carries no line -- which is also why
    this reads `fields["status"]` instead of the caller's `--status`:
    `promote` is what turns that marker into `done`.

    Returns `(fields, None)` or `(None, reason)`, the same shape `promote`
    returns, so `main` has one refusal path rather than two.
    """
    if fields["status"] == "done":
        return fields, None
    stated = (done_when or "").strip()
    if not stated:
        return None, DONE_WHEN_REFUSAL
    fields = dict(fields)
    fields["write_up"] = (
        f"{fields['write_up'].rstrip()}\n\n{DONE_WHEN_PREFIX}{stated}"
    )
    return fields, None


MULTI_TASK_REFUSALS = {
    "done": (
        "a capture that arrived already finished is one row, not several -- "
        "drop --task, or drop the DONE (Cycle N) marker if the work really "
        "is still open."
    ),
    "title": (
        "--title and --task both name the row's title. Pass --task once per "
        "task instead; --title is for the single-row case."
    ),
    "empty": "a --task needs a title",
}


def _pair_refusal(tasks, stated):
    """Why these `--task`/`--done-when` lists cannot be paired, or `None`.

    They are paired by position, which is the only pairing an argument list
    can express -- so an unequal count is not a detail to be forgiven. Taking
    the shorter of the two would silently drop a task he asked for, and
    reusing one sentence across several tasks would board rows whose
    definition of done is not about them.
    """
    if len(stated) != len(tasks):
        return (
            f"{len(tasks)} --task and {len(stated)} --done-when. Each task "
            "carries its own checkable definition of done (issue #212), and "
            "they are paired in the order you typed them."
        )
    if any(not one for one in stated):
        return DONE_WHEN_REFUSAL
    if any(not one for one in tasks):
        return MULTI_TASK_REFUSALS["empty"]
    return None


def split_into_tasks(fields, tasks, done_whens, title=None):
    """One capture -> the rows it becomes, one per task, or `(None, reason)`.

    His ask, issue #212: *"When boarding, break each capture into tasks that
    each have a checkable definition of done."* The plural is the half that
    was missing -- `apply_done_when` made one row carry one sentence, and a
    capture that is really three jobs still arrived as a single row nobody
    could finish in a cycle.

    Pure, like `promote`: it reads no store, so every question about what a
    capture becomes is answered without a board.

    **His words go on every row, whole.** The write-up is his text verbatim,
    which is `add_row`'s rule and not a new one, so the alternative -- giving
    each row a slice of what he wrote -- would mean this tool deciding which
    of his sentences belongs to which task and putting words in his mouth on
    a page he reads. Each row then says which task of how many it is, so the
    repetition reads as one capture cut up rather than three duplicates.

    Returns a list of `fields` dicts, always -- the single-row case comes
    back as a list of one -- so `main` has one write path rather than two.
    """
    tasks = [(one or "").strip() for one in (tasks or ())]
    stated = [(one or "").strip() for one in (done_whens or ())]
    if not tasks:
        if len(stated) > 1:
            return None, (
                f"{len(stated)} --done-when and no --task. A second "
                "definition of done describes a second task, so name it: "
                "--task \"...\" --done-when \"...\", once per task."
            )
        one, refusal = apply_done_when(fields, stated[0] if stated else "")
        return (None, refusal) if one is None else ([one], None)

    # A finished capture is the carve-out `apply_done_when` documents, and it
    # cannot also be several open tasks: `promote` has already turned its
    # `DONE (Cycle N)` marker into `✅ Done`, so boarding three rows here
    # would put three finished rows on his board for work that shipped once.
    if fields["status"] == "done":
        return None, MULTI_TASK_REFUSALS["done"]
    if title:
        return None, MULTI_TASK_REFUSALS["title"]
    refusal = _pair_refusal(tasks, stated)
    if refusal:
        return None, refusal

    total = len(tasks)
    rows = []
    for number, (one_title, done) in enumerate(zip(tasks, stated), start=1):
        row = dict(fields)
        row["title"] = one_title
        row["write_up"] = (
            f"{fields['write_up'].rstrip()}\n\n"
            f"{DONE_WHEN_PREFIX}{done}\n\n"
            f"*Task {number} of {total}, cut from one capture.*"
        )
        rows.append(row)
    return rows, None


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
    parser.add_argument(
        "--milestone",
        help="the milestone this row belongs under, scoped to its project",
    )
    parser.add_argument(
        "--no-milestone",
        action="store_true",
        help="board it ungrouped on purpose, when no milestone fits yet",
    )
    parser.add_argument(
        "--done-when",
        action="append",
        help="how a cycle will know this row is finished, in one checkable "
             "sentence (issue #212). Required unless the capture already "
             "says DONE. Repeat it once per --task.",
    )
    parser.add_argument(
        "--task",
        action="append",
        help="cut this capture into several tasks: one --task <title> and "
             "one --done-when <sentence> per task, paired in order "
             "(issue #212). Leave it off to board the capture as one row.",
    )
    parser.add_argument("--cycle", type=int, help="stamped on the replies carried across")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    # **One of the two milestone flags is required, and that is issue #227's
    # task rule enforced where the row is created.** This tool took
    # `--project` and no `--milestone`, so every capture it boarded became a
    # task under no milestone by construction -- `project_goals_check` found
    # 24 of them on 2026-09-14 and cycle 1554 placed them one at a time. A
    # default would put the inventory straight back: the cheap name is the
    # one already on screen, and a row under the wrong milestone reads as
    # placed. So the caller either names the milestone or says out loud that
    # none fits yet, and neither is guessed for them.
    if bool(args.milestone) == bool(args.no_milestone):
        print(
            "REFUSED: name --milestone <name>, or pass --no-milestone to "
            "board it ungrouped on purpose. A row under no milestone serves "
            "no key result (issue #227), so it is a choice rather than a "
            "default.",
            file=sys.stderr,
        )
        return 1

    # Both go straight into table cells. `add_row` refuses them too and would
    # refuse before writing anything, but a refusal that costs no store call
    # is the better one -- and `--title` reaching `refuse_cell` here is what
    # makes the message name the flag he typed.
    cells = [(args.dated, "--dated"), (args.title, "--title"),
             (args.milestone, "--milestone")]
    # Every `--task` becomes an `Item` cell, so it is the same check for the
    # same reason -- a `|` in one of them would shift every column right of it.
    cells.extend((one, "--task") for one in (args.task or ()))
    for value, flag in cells:
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

    tasks, refusal = split_into_tasks(
        fields, args.task, args.done_when, args.title)
    if tasks is None:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1

    tag = fields["project"]
    for one in tasks:
        print(f"boarding — {one['title']}")
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
    if args.milestone:
        print(f"  milestone {args.milestone!r}")
    else:
        print("  milestone (ungrouped), by --no-milestone")
    if fields["status"] == "done":
        print("  done when (none -- the capture arrived already finished)")
    else:
        for one, stated in zip(tasks, args.done_when or ()):
            print(f"  done when {one['title']!r}: {stated.strip()!r}")
    if args.dry_run:
        return 0

    # **His replies go on the first row only.** They are one conversation
    # about one capture; copying them onto every task would put the same
    # answer on his board three times, and the row he already spoke under is
    # the first one.
    rows = []
    for one in tasks:
        try:
            row = board_write.add_row(
                args.board, one["title"], args.dated, one["priority"],
                status=one["status"], write_up=one["write_up"],
                notes=capture_replies_of(capture) if not rows else (),
                project=tag, cycle=args.cycle, author="nova",
                store=board_store,
            )
        except (board_write.WriteRefused, board_write.BoardDamaged,
                board_records.RecordError) as problem:
            print(f"REFUSED: {problem}", file=sys.stderr)
            if rows:
                # The all-or-nothing property this module documents holds for
                # one row and cannot hold for several: rows are minted one at
                # a time. Say exactly which ones landed, because the bullet is
                # still in the box and a blind re-run would board them twice.
                landed = ", ".join(f"#{done['number']}" for done in rows)
                print(
                    f"  {len(rows)} row(s) are already on the board: {landed}. "
                    "His bullet is still in the box, so re-running this "
                    "command boards them a second time -- close or delete "
                    "those rows first, or finish the rest by hand.",
                    file=sys.stderr,
                )
            return 1
        rows.append(row)
    row = rows[0]

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

    # The row exists now, so the milestone is a second write rather than a
    # field on `add_row`: `add_row` mints the row and knows nothing about the
    # milestone registry, and widening it would put a registry lookup inside
    # the one function that creates rows for both boards.
    #
    # **It goes after the bullet is cut, not between the two writes above.**
    # Those two are the pair the comment above guards -- a row written and a
    # bullet still in the box is the state re-running the same call repairs,
    # and slipping a third write in between would make a failed regrouping
    # leave that pair open and a re-run board the item twice. Here, the worst
    # case is one row on his board under no milestone, which is what the tool
    # did for every row before today and what `board_milestone` repairs in
    # one call.
    if args.milestone:
        for one in rows:
            try:
                board_write.change_row(
                    args.board, one["number"],
                    {"milestone": args.milestone.strip()},
                    store=board_store,
                )
            except (board_write.WriteRefused, board_write.BoardDamaged,
                    board_records.RecordError) as problem:
                print(
                    f"boarded #{one['number']}, but it is still ungrouped: "
                    f"{problem}. Run: python3 -m tools.board_milestone --board "
                    f"{args.board} --number {one['number']} --milestone "
                    f"{args.milestone!r}",
                    file=sys.stderr,
                )
                return 1

    after = board_records.contents(args.board, store=board_store)
    problems = check_captures(before, after, raw_text)
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    boarded = ", ".join(f"#{one['number']}" for one in rows)
    print(f"boarded {boarded} on the {args.board} board")
    print(f"  captures {len(capture_pairs(before))} -> {len(capture_pairs(after))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
