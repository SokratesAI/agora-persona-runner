"""Estimate one board row's size, S/M/L/XL, in the record store.

Milestone M2 of idea #260's picking redesign, and the row half of it. The
spec's reason for the field, in his words:

> *"a marking of the size of the task/project/milestone going for t-shirt
> sizes s/m/l/xl. Just a guess/estimate on the amount of work needed to be
> done. That is practical for the ordering of projects/milestones as we
> might do the smaller ones first."*

M4 ranks milestones smallest first (the rating it once divided came out
with issue #202), so this is the number that ranking sorts on -- which is why the field has to exist and be filled before the
ranking can be written, and why the spec puts M2 in front of M4 as a real
dependency rather than a preference.

    python3 -m tools.board_size --board idea --number 260 --size l \\
        --dated 09-06 --note 'why it is L' --cycle 1090

**This is the fifth of the ten `tools/board_*.py` writers converted onto
`agora_runner.board_write` for issue #203, and the third that needs
`append_note`.** What comes out is the same set as the four before it: the
markdown, the path on disk, the compare-and-swap the caller used to own, and
this module's own 60-line `check_from_contents`, which is what `change_row` is
once in a module that can be tested. What is left is this tool's vocabulary --
which cell, on which row, refused how.

**A size is mine to set, and that is the spec's assignment rather than a
convenience**: rows are sized by *"Nova, directly, same technical-judgement
ownership as TRL"*. So there is a CLI here and deliberately no capture-box
route -- the three fields he owns (lifecycle approval, satisfaction, and the
project order) are M3 and M5, and none of them is this one.

**`--size` is the one flag in this set that does not go through `refuse_cell`,
and the reason is a real difference rather than an omission.** A milestone name
is a name, so nothing but a delimiter rule bounds it; a size is checked against
`SIZE_LABELS`, so the value written is one of four constants this repo owns and
a `|` or a newline cannot reach the cell through it. The vocabulary check *is*
the cell check here. `--dated` and `--note` are free text and still take
`refuse_cell`.

**Blank stays out of reach on purpose.** The empty size is a legal cell and it
means "nobody has estimated this" -- `set_row_size` accepted it and
`size_changes` would write it -- but it is never what a cycle reaching for this
tool is trying to say, so `resolve_size` refuses it and `--size ''` is a typo
rather than an instruction. That is the opposite call from `board_milestone`'s
`--milestone ''`, which clears a grouping, and the difference is that an empty
milestone cannot be confused with a mistyped one while an empty size can.

A re-estimate changes the size and nothing else -- **unless it carries a
`--note`**, which rides in the same single write as the cell move, so there is
no window where the row has been re-estimated and nothing says why.
`append_note` stamps the row's `updated` cell from the note's own `--dated` and
refuses a caller that names `updated` too, so this tool deliberately leaves it
out of the change set.

**A closed row is still refused, and against records that refusal has to be
this module's.** In markdown it was `set_row_size` returning `None` because the
row was not in `## Board`; the record store has no two tables, so `change_row`
would write a done row quite happily and its after-check would agree. It is
read off a board fetched before the first write, the same shape as
`board_status.refuse_row`. The reasoning is unchanged: an estimate of the work
left on a finished row is not a fact about anything.
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
from agora_runner.nova_boards import SIZE_LABELS, canonical_size, size_key


def _size_choices():
    """Every accepted spelling: the four keys and the four written forms."""
    return [key for key in SIZE_LABELS if key] + [
        value for value in SIZE_LABELS.values() if value
    ]


def resolve_size(value):
    """`xl` / `XL` / `x-large` -> the cell text, or `None`.

    `canonical_size` is what the rest of the system reduces a cell to, so
    routing a typed argument through it accepts exactly the spellings
    everything else considers equal and cannot invent a fifth. The one
    thing it accepts that this must not is the blank size: it is a legal
    cell and it means "nobody has estimated this", which is never what a
    cycle reaching for this tool is trying to say.
    """
    if not value or not value.strip():
        return None
    resolved = canonical_size(value)
    return resolved or None


def size_changes(size, dated=None):
    """The change set for a re-estimate: the cell, and the key it derives.

    `board_document.from_document` derives `sizeKey` off the stored `size` cell
    the same way it derives `statusKey` off `status`, so a change set naming
    only the cell reads back with a key that disagrees with it --
    `change_row`'s after-check catches that, and this is the function that
    makes it never happen.

    `dated` is the `updated` cell and `None` leaves it alone. **A caller with a
    note must not pass it**: `append_note` sets `updated` from the note's own
    date and refuses a change set that names it too.
    """
    changes = {"size": size, "sizeKey": size_key(size)}
    if dated is not None:
        changes["updated"] = dated
    return changes


def refuse_row(contents, number):
    """Why this row may not be re-estimated, or `None` if it may.

    Read off a board fetched **before** anything is written, because both
    answers here have to mean "nothing happened". `change_row` refuses an
    absent row itself; a closed row it would write, and that refusal has no
    twin below this seam -- in markdown it was `set_row_size` returning `None`
    for a row that was not in `## Board`.
    """
    for item in contents["items"]:
        if item.get("number") == number:
            if item.get("done"):
                return (f"#{number} is finished, and an estimate of the work "
                        "left on a finished row is not a fact about anything")
            return None
    return f"#{number} is not a row on this board"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, choices=list(BOARDS),
                        help="which board the row is on")
    parser.add_argument("--number", required=True, type=int, help="the row number")
    parser.add_argument(
        "--size",
        required=True,
        help="s / m / l / xl, or a written form such as 'large'",
    )
    parser.add_argument("--dated", help="MM-DD, Oslo; omitted leaves the cell alone")
    parser.add_argument("--note", help="one line on why it moved, appended to the write-up")
    parser.add_argument("--cycle", type=int, help="stamped on --note")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    size = resolve_size(args.size)
    if size is None:
        print(
            f"REFUSED: '{args.size}' is not a size. One of: "
            + ", ".join(_size_choices()),
            file=sys.stderr,
        )
        return 1
    # `--size` is absent from this loop on purpose: it is bounded to one of
    # four constants above, so no delimiter can reach its cell. See the module
    # docstring.
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

    refusal = refuse_row(before, args.number)
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1

    was = {item["number"]: item for item in before["items"]}[args.number]
    print(f"#{args.number}: {was['size'] or '(unsized)'} -> {size}")
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
                changes=size_changes(size), store=board_store,
            )
        else:
            board_write.change_row(
                args.board, args.number,
                size_changes(size, dated=args.dated),
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
