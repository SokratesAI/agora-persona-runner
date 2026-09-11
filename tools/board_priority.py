"""Rate one unrated board row, in the record store.

`agora_runner.nova_boards.set_row_priority` has existed since Cycle 274
and the only thing that has ever called it is `nova_capture.board_note`,
which rates a bullet **on its way onto the board for the first time**.
Cycle 1087 built this to change a rating already written, on a capture from
the owner:

> *"Bump idea #260 (task-prioritization redesign) to Priority: Immediately.
> I see it as the most important project right now because it gives us
> control over how every other project gets worked."*

    python3 -m tools.board_priority --board idea --number 261 \\
        --priority medium --dated 09-11 --note 'why this rating' --cycle 1412

**It no longer changes a rating that is already there, and it never writes
🔴 Immediately** (issue #202, Cycle 1412). The spec's section "His ranking is
not a cycle's to change" is why: on 2026-09-09 idea #267, a row a cycle wrote,
read 🔴 Immediately at 06:29 and 🔵 Medium at 06:57 with the owner touching
neither, and in between it sat at the top of `/api/next` and took most of the
merged PRs. The only writers were cycles. His rule, in
`row-order-and-priority-migration.md`: *"A position or rating he set is
recorded as his, and a cycle may not overwrite it. `tools/board_priority.py`
and every write path refuse it, loudly, rather than winning quietly."* The
app's own rating route went in Cycle 1404, so this is the one write path left.

Nothing records who set a rating, so every rating already on an open row is
treated as his. That over-covers the rows I rated at boarding, and it costs
nothing: since #202 closed, no ranking reads a rating at all (`nova_next.rank`
lost its last one, the skip-to-top tier, in Cycle 1415), so re-rating one of
mine would change nothing a cycle does. Immediately stays refused anyway: it is
the rating #267 carried when it took the queue, it is his intent to state, and
refusing it costs nothing now that it orders nothing. So a blank row may still
be rated -- blank means nobody has looked -- but only below Immediately. A cycle that thinks one
of his ratings is wrong says so in a comment on the row, and he decides.

**This is the last of the `tools/board_*.py` writers converted onto
`agora_runner.board_write` for issue #203** (Cycle 1377). Until then it parsed
the markdown it had just edited to check its own edit, and Cycle 1371 filed
that as "not a board read". It was one all the same: once the switchover
makes the markdown a view generated from the records, an edit to that view is
overwritten by the next render and the rating he asked for never reaches the
store. So it goes the way `board_status` went -- `--board`, not `--file`; no
path on disk and no compare-and-swap for the caller to own; and this module's
own `check_from_contents` is gone, because that after-check is
`board_write.change_row`, written once and tested there.

**It rates his two boards and no longer rates mine.** The markdown version
took any file, including `nova/resources/issues.md` and `ideas.md`. #203 does
not migrate those two, so they have no records to write, and `board_status`
made the same cut at its own conversion. A cycle re-rating one of my own rows
edits that file directly; nothing here has ever been asked to.

The refusals are the ones the markdown version had, and each is still a way
this could hand him a broken board: a rating outside the four
`PRIORITY_LABELS` spellings; a **blank** rating, which is the one state that
means "nobody has looked" (`prompt.md` step 6, and Cycle 188's deliberate use
of it); a row that is not on the board; a row that is finished; a `--dated` or
`--note` that is blank or carries a `|` or a line break
(`board_write.refuse_cell`); a `--note` with no `--dated` to stamp it with;
and `change_row`'s own after-check, which refuses when anything other than
the named row's rating (and, with a note, its write-up and `updated` cell)
moved.

**A finished row is refused off the status cell, not off `done`.**
`set_row_priority` said why and the reason survives the move: most finished
rows never go to `## Done`, so a row can be `✅ Done` with `done` false, and a
check on `done` alone would put a rating chip on a finished item -- the one
state Cycle 188 deliberately left empty, and one `board_status` blanks on its
way in precisely so this tool never has to see it. Both are checked.

**`--dated` without `--note` is accepted and does nothing**, as it did before:
a re-rating is not a touch of the row, and `updated` moves only with the note
that explains it.
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
from agora_runner.nova_boards import (
    PRIORITY_LABELS,
    canonical_priority,
    priority_key,
    status_key,
)

# The statuses that mean a row is finished. Mirrors
# `nova_boards._CLOSED_STATUS_KEYS`, which is private -- the same copy
# `tools.board_status` keeps, asserted equal to the module's by the tests of
# both tools so neither can drift.
CLOSED_STATUS_KEYS = frozenset({"done", "outdated"})

# Only the owner puts a row at this rating; see the module docstring.
IMMEDIATE = PRIORITY_LABELS["immediate"]


def _priority_choices():
    """Every accepted spelling: the four keys and the four written forms."""
    return [key for key in PRIORITY_LABELS if key] + [
        value for value in PRIORITY_LABELS.values() if value
    ]


def resolve_priority(value):
    """`immediate` / `🔴 Immediately` / `Immediately` -> the cell text, or `None`.

    `canonical_priority` is what the rest of the system reduces a cell to,
    so routing a typed argument through it accepts exactly the spellings
    everything else considers equal and cannot invent a fifth. The one
    thing it accepts that this must not is the blank rating: it is a legal
    cell and it means "nobody has looked", which is never what a cycle
    reaching for this tool is trying to say.
    """
    if not value or not value.strip():
        return None
    resolved = canonical_priority(value)
    return resolved or None


def priority_changes(priority):
    """The change set for a re-rating: the cell and the key it derives.

    `board_document.from_document` derives `priorityKey` off the stored
    `priority` cell, so a change set naming only the cell reads back with a
    key that disagrees with it -- `change_row`'s after-check catches that, and
    this is the function that makes it never happen. `updated` is never in it:
    `append_note` sets that from the note's own date, and a re-rating with no
    note does not touch it.
    """
    return {"priority": priority, "priorityKey": priority_key(priority)}


def refuse_row(contents, number, priority=None):
    """Why this row may not be rated `priority`, or `None` if it may.

    Read off a board fetched **before** anything is written, because every
    answer here has to mean "nothing happened". `change_row` refuses an absent
    row itself; a finished one it would happily rate, so that refusal lives
    here -- off `done` *and* off the status cell, because a `✅ Done` row that
    never moved to `## Done` has `done` false. The two #202 refusals are here
    too, for the same reason: a rating already on the row, and Immediately.
    """
    for item in contents["items"]:
        if item.get("number") == number:
            if item.get("done") or status_key(item.get("status", "")) in CLOSED_STATUS_KEYS:
                return (f"#{number} is finished ({item.get('status') or 'in ## Done'}), "
                        "and a finished row deliberately carries no rating")
            if item.get("priority"):
                return (f"#{number} already carries {item['priority']}, and a rating on "
                        "a boarded row is his: a cycle may not overwrite it (issue #202). "
                        "Propose the change in a comment on the row instead")
            if priority == IMMEDIATE:
                return (f"#{number}: {IMMEDIATE} is his to give -- a cycle may not "
                        "put a row there (issue #202)")
            return None
    return f"#{number} is not a row on this board"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, choices=list(BOARDS),
                        help="which board the row is on")
    parser.add_argument("--number", required=True, type=int, help="the row number")
    parser.add_argument(
        "--priority",
        required=True,
        help="low / medium / high / immediate, or the written form",
    )
    parser.add_argument("--dated", help="MM-DD, Oslo; stamped on --note only")
    parser.add_argument("--note", help="one line on why it moved, appended to the write-up")
    parser.add_argument("--cycle", type=int, help="stamped on --note")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    priority = resolve_priority(args.priority)
    if priority is None:
        print(
            f"REFUSED: '{args.priority}' is not a rating. One of: "
            + ", ".join(_priority_choices()),
            file=sys.stderr,
        )
        return 1
    for value, flag in ((args.dated, "--dated"), (args.note, "--note")):
        refusal = refuse_cell(value, flag)
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

    refusal = refuse_row(before, args.number, priority)
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1

    was = {item["number"]: item for item in before["items"]}[args.number]
    print(f"#{args.number}: {was['priority'] or '(unrated)'} -> {priority}")
    if args.dry_run:
        return 0

    try:
        if args.note:
            board_write.append_note(
                args.board, args.number, args.note, args.dated,
                cycle=args.cycle, author="nova",
                changes=priority_changes(priority), store=board_store,
            )
        else:
            board_write.change_row(
                args.board, args.number, priority_changes(priority),
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
