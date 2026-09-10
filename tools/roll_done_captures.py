"""Move the owner's finished captures out of the box he types into.

The owner types into a bare bullet list above `## Board` in
`projects/sokrates/projects/nova/issues.md` and `.../ideas.md`. When a
cycle closes one of those captures, `tools.close_done_captures` rewrites
the bullet to start with `DONE (Cycle N):` and leaves it exactly where it
was. This is the second half: the finished ones leave the box.

Measured 2026-08-22, Cycle 313: **31 of the 33 bullets on `issues.md` and
12 of the 13 on `ideas.md` were `DONE`** -- the leftover on `ideas.md`
being the empty cursor bullet he types into, so that list was finished
work and nothing else. The two lists were **15,592 and 10,159
characters**, 25,751 together.

`nova_boards.split_capture_done` (Cycle 251) already stops a closed
capture being *read* as work: the ranking drops it and the page hides it.
That fixed the consumers and left the box, and the box is the half he
actually opens.

So this is `identity.md` rule 8 applied to his boards: *"finished items
move to a `# Processed` section with what actually happened."* Nothing is
deleted and nothing is summarised. The bullet moves, verbatim, into the
`## Processed captures` archive at the end of his file.

    python3 -m tools.roll_done_captures --board issue --dry-run
    python3 -m tools.roll_done_captures --board issue

**#203: the box is the record store and the archive is the layout.**
There is no `--file` any more, because a capture is a document and the
board it belongs to is a name. The `## Processed captures` section is one
of the things `parse_board` never modelled -- it is 19,653 words of
`issues.md` that `board_view.document_layout` holds as a `verbatim`
block, and the layout document is where it lives now. So a roll is two
writes rather than one line rewrite: the bullet is appended to that block,
and then the capture document is deleted.

**The archive is written first, and stopping between the two writes must
be able to duplicate a bullet and never to lose one.** That is
`roll_digest`'s rule, and it is sharper here than there: `delete_capture`
takes his own words and every reply written under them, and there is
nothing to restore them from. A run that dies after the layout write
leaves the bullet in both places, and the next run finds no capture
document for it and moves on -- the duplicate is visible on his page and
recoverable by hand. A run that deleted first and died would have removed
a sentence of his with no copy anywhere.

**A board with no stored layout is refused rather than given a fresh
one.** `board_store.read_layout` answers `None` for a board that has
never been migrated and that is not the same as an empty archive: minting
a layout here would be this tool deciding the order of a document it has
only ever read one section of, and `render_document` draws the whole file
from that answer. `board_migrate` writes the layout; this only ever
appends to it.

**The guard is this module's own, and that is not a second copy of
`change_capture_text`'s.** That one asks whether one bullet's text changed
and everything else on the board held still. This one deletes documents
and edits a block the records do not model, so what it has to ask is
different: did the rows and the write-ups hold still, did exactly the
`DONE` bullets leave the box, and is every one of their sentences now in
the archive. It asks all three of the store after both writes, because a
check that reads only what it sent cannot see a bullet that went nowhere.

Exits 0 when it moved captures or found none to move, 1 on a refusal or a
check failure. It is idempotent: a second run finds no `DONE` capture
documents left and reports `nothing to move`.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records, board_store
from agora_runner.board_document import (
    BOARDS, capture_replies_of, capture_text_of,
)
# By name rather than as `board_store.StoreError`: the module-level
# `board_store` is the seam a test swaps for a fake, so reading the
# exception class off it catches nothing and raises `AttributeError` out of
# the `except` clause itself. `close_done_captures` paid for this one.
from agora_runner.board_store import StoreError
from agora_runner.nova_boards import split_capture_done

PROCESSED_HEADING = "## Processed captures"


def _is_heading(line):
    return line.strip().startswith("#")


def _has_processed_heading(text):
    """Is the archive heading a *heading line* in `text`?

    A substring search would find the phrase inside any write-up that
    happened to mention it -- and these files are 190KB of his prose and
    mine, so that is a matter of time. Reviewer finding on runner#286,
    carried across the conversion because the block is still markdown.
    """
    wanted = PROCESSED_HEADING.strip().lower()
    return any(line.strip().lower() == wanted for line in (text or "").split("\n"))


def bullet_lines(doc):
    """One capture document -> the lines it occupies in the generated view.

    `render_document` draws a capture as `- <text>` with each reply indented
    four spaces under it. The archive holds the same shape, so a bullet that
    moves reads on his page exactly as it did in the box.
    """
    lines = [f"- {capture_text_of(doc)}"]
    lines.extend(f"    - {reply}" for reply in capture_replies_of(doc))
    return lines


def finished(captures):
    """The capture documents carrying a `DONE (Cycle N):` marker, in order."""
    return [doc for doc in captures or ()
            if split_capture_done(capture_text_of(doc))[0]]


class ArchiveRefused(ValueError):
    """The layout has no place this tool is willing to append to."""


def archived(blocks, docs):
    """`blocks` with every document's bullet appended to the archive block.

    Returns a new list; `blocks` is not mutated, because the caller still
    holds the version it read and a refusal below has to leave that intact.

    The block to append to is the last `verbatim` block carrying the
    heading. **A heading *after* it inside the same block is refused**
    rather than appended past: a `verbatim` block runs to the next heading
    the layout claims, so a `##` this tool does not recognise sitting below
    `## Processed captures` would take the bullets into a section nobody
    chose. Refusing costs a cycle one message; appending wrongly moves his
    sentence somewhere he will not look for it.
    """
    new = [dict(block) for block in blocks]
    lines = [line for doc in docs for line in bullet_lines(doc)]
    if not lines:
        return new

    target = None
    for index, block in enumerate(new):
        if block.get("kind") == "verbatim" and _has_processed_heading(
                block.get("markdown", "")):
            target = index
    if target is None:
        new.append({"kind": "verbatim",
                    "markdown": PROCESSED_HEADING + "\n\n" + "\n".join(lines)})
        return new

    markdown = new[target].get("markdown", "")
    body = markdown.split("\n")
    at = max(index for index, line in enumerate(body)
             if line.strip().lower() == PROCESSED_HEADING.strip().lower())
    below = [line for line in body[at + 1:] if _is_heading(line)]
    if below:
        raise ArchiveRefused(
            f"the layout block holding {PROCESSED_HEADING!r} carries "
            f"{len(below)} further heading(s) below it, the first being "
            f"{below[0].strip()!r} -- appending would file his bullet under "
            "that heading instead of the archive")
    new[target]["markdown"] = markdown.rstrip("\n") + "\n" + "\n".join(lines)
    return new


def check_after(before, after, layout_markdown, moved):
    """Every way this roll could have gone wrong, asked of the store.

    `before` and `after` are `board_records.contents` either side of the
    two writes, `layout_markdown` the archive block as it came back, and
    `moved` the documents this run meant to move. A list of complaints,
    empty when the board differs in exactly the way it was meant to.

    Read back rather than computed: the point is to catch a bullet that
    left the box and arrived nowhere, and a check built from what was
    *sent* agrees with the sender by construction.
    """
    problems = []
    if before["items"] != after["items"]:
        problems.append("board rows changed")
    if before["details"] != after["details"]:
        problems.append("detail write-ups changed")

    texts = [capture_text_of(doc) for doc in moved]
    gone = [text for text in before["captures"] if text not in after["captures"]]
    if sorted(gone) != sorted(texts):
        problems.append(
            f"{len(gone)} capture(s) left the box, expected {len(texts)}")
    for text in after["captures"]:
        if split_capture_done(text)[0]:
            problems.append(f"a DONE capture stayed: {text[:60]}")
    for text in texts:
        if text not in layout_markdown:
            problems.append(f"a moved capture is not in the archive: {text[:60]}")
    return problems


def _archive_markdown(blocks):
    """Every verbatim block holding the heading, joined. `""` when there is none."""
    return "\n".join(block.get("markdown", "") for block in (blocks or ())
                     if block.get("kind") == "verbatim"
                     and _has_processed_heading(block.get("markdown", "")))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--board", required=True, choices=list(BOARDS),
                        help="which of his two boards to roll")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would move, write nothing")
    args = parser.parse_args(argv)

    try:
        before = board_records.contents(args.board, store=board_store)
        captures = board_records.capture_documents(args.board, store=board_store)
        layout = board_store.read_layout(args.board)
    except (board_records.RecordError, StoreError) as problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    moved = finished(captures)
    if not moved:
        print(f"{args.board}: nothing to move "
              f"({len(captures)} capture(s) in the box)")
        return 0

    if layout is None:
        print(f"REFUSED: {args.board} has no stored layout, so there is no "
              "archive to append to and minting one here would decide the "
              "order of his whole document. Run `python3 -m tools.board_migrate"
              " --board <board> --file <md> --apply` first", file=sys.stderr)
        return 1

    try:
        blocks = archived(layout, moved)
    except ArchiveRefused as problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    for doc in moved:
        print(f"  {capture_text_of(doc)[:70]}")
    if args.dry_run:
        print(f"{args.board}: would move {len(moved)} finished capture(s) "
              f"to '{PROCESSED_HEADING}' (dry run)")
        return 0

    # The archive first. A failure between these two writes must be able to
    # duplicate a bullet and never to lose one -- see the module docstring.
    try:
        board_store.write_layout(args.board, blocks)
    except (StoreError, ValueError) as problem:
        print(f"{args.board}: nothing moved — the archive write failed: "
              f"{problem}", file=sys.stderr)
        return 1

    removed = 0
    for doc in moved:
        try:
            board_store.delete_capture(doc)
        except (StoreError, ValueError) as problem:
            print(f"{args.board}: archived {len(moved)}, removed {removed} "
                  f"from the box, then stopped — {problem}", file=sys.stderr)
            return 1
        removed += 1

    try:
        after = board_records.contents(args.board, store=board_store)
        stored = board_store.read_layout(args.board)
    except (board_records.RecordError, StoreError) as problem:
        print(f"{args.board}: moved {removed} capture(s), then could not read "
              f"the board back to check them — {problem}", file=sys.stderr)
        return 1

    problems = check_after(before, after, _archive_markdown(stored), moved)
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    print(f"{args.board}: moved {removed} finished capture(s) to "
          f"'{PROCESSED_HEADING}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
