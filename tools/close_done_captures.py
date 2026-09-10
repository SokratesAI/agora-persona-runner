"""Mark the owner's finished captures `DONE (Cycle N):` from the claim ledger.

`tools/roll_done_captures.py` moves a finished capture out of the box he
types into, and it finds one by the `DONE (Cycle N):` prefix on the
bullet. `prompt.md` step 6 tells every cycle to write that prefix by hand
in the same `get`/`put` it was already doing. **Measured 2026-08-26,
Cycle 487: 21 capture bullets sat above `## Board` on his two files and
not one of them carried the prefix**, while the claims ledger recorded a
`done` claim for **17** of them -- naming the cycle, the merged PR and
the reply it posted on the capture. So the work really was finished, the
loop really did record it, and the two facts were written in different
places by the same cycle. `roll_done_captures` then correctly found
nothing to move, every cycle since Cycle 434, and his "Not boarded yet"
box grew to 21 items of which 17 were closed.

That is his own complaint, in his own words, filed as a capture and still
sitting in the box it describes: *"they do no seem to just stay forever
in the 'not boarded yet' box as unrated. Thats not what the box is
for."*

The fix is not a fourth restatement of step 6. A cycle that claims a
capture, works it, replies on it and releases the claim `--done` has
already said the thing, in a file this loop rewrites under
compare-and-swap. This reads that record instead of asking for the habit
again -- Cycle 485's lesson, that a missing button is not a missing
habit, applied one file over.

    python3 -m tools.close_done_captures --board issue \\
        --claims claims.json --dry-run

Then `roll_done_captures` on the same board does the moving. Two tools
rather than one because they answer to different evidence: this one
believes the ledger, that one believes the bullet, and a cycle that
marked a bullet by hand still wants the second without the first.

**Only `state == "done"` counts.** `progressed` is a cycle saying in a
word it had to type that work is left -- three of today's 21 are
`progressed`, including the IDP capture and the Groq key, and marking
either one closed would hide live work from every later cycle. A capture
with no claim at all is left alone too: that is his newest, and the one
thing worse than a stale box is a box that eats a capture he just typed.

The ledger is a rolling window -- `nova_claims.prune` drops a `done` row
after `DONE_KEEP_HOURS`. So this closes what was finished recently, not
the whole history, and a capture whose claim has aged out simply stays as
it is today. Strictly better, never worse; there is nothing to back-fill
from once a row is gone.

**The invariant that makes it safe is that the slug does not move.**
`slug_for_capture` is hashed off the bullet with the DONE marker and the
rating already stripped (`top_board_rows.unboarded_captures`), so
marking a bullet cannot change its own identity. That is what makes this
idempotent, and it is asserted per bullet before the write is attempted
rather than trusted from the docstring.

**#203: the captures come out of the record store, one document each.**
Two things that were true of the markdown version are gone rather than
ported. There is no `--file`, because a capture is a document and the
board it belongs to is a name; and there is no line rewriting, because a
document's `text` *is* the bullet -- his trailing whitespace, his
wrapping and my own indentation were properties of a file, and the file
is now a generated view. The indented-reply skip goes with them: a reply
is `replies` on the capture document, so there is no longer a bullet in
the walk that could be mine to mark. `board_write.change_capture_text`
copies the replies across untouched and refuses a document with no
`_rev`, which is why the walk hands over the document it read rather than
a fresh one built from the text.

**Each bullet is its own write, and that is not a batch this could
usefully make atomic.** `change_capture_text` re-reads the whole board
before and after each one, so a mark that lands on the wrong capture is
caught on the write that did it rather than at the end of the run; and a
run that dies halfway leaves the bullets it already marked marked, which
the next run skips, because a marked bullet no longer matches the ledger
walk. That is the same idempotence the markdown version had, arrived at
from the other side.

This module has no `check_from_contents` of its own any more. The whole
board check it used to do belongs to `change_capture_text` now -- rows,
write-ups, capture count, replies and exactly-one-bullet-moved -- and a
second copy here would be a second copy of an enforced rule, the call
`board_capture`, `board_milestone` and `board_size` each made on the way
past.

Exits 0 whether it marked anything or not, 1 on a refusal or a check
failure.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records, board_store, board_write
from agora_runner.board_document import BOARDS, capture_text_of
# By name rather than as `board_store.StoreError`: the module-level
# `board_store` is the seam a test swaps for a fake, so reading the
# exception class off it catches nothing and raises `AttributeError`
# out of the `except` clause itself. Found by the mid-walk refusal test.
from agora_runner.board_store import StoreError
from agora_runner.nova_boards import split_capture_done, split_capture_priority
from agora_runner.nova_claims import (
    finished_claims, load as load_claims, slug_for_capture,
)


def done_cycles(ledger_text):
    """`{slug: cycle}` for every capture slug the ledger records as done.

    Keyed on the slug rather than filtered by board, because a capture's
    slug is a hash of his sentence and carries no board in it -- the same
    text on both files would be the same claim, which is the ledger's own
    rule and not this tool's to reinterpret.
    """
    ledger = load_claims(ledger_text)
    return {item: row.get("cycle")
            for item, row in finished_claims(ledger).items()
            if str(item).startswith("capture-")}


def plan(captures, finished):
    """`[(doc, old_text, new_text, slug, cycle)]` for what to mark.

    Takes the capture documents as read -- `board_records.capture_documents`
    output, `_rev` and all -- and hands each one straight back out, because
    `change_capture_text` writes conditional on that revision and a document
    this function re-minted would be a write aimed at nothing.

    A capture that already carries a DONE marker is skipped, which is what
    makes a second run a no-op: `split_capture_done` sees the marker, and a
    bullet with no marker is the only kind whose slug is looked up at all.
    """
    marks = []
    for doc in captures or ():
        text = capture_text_of(doc)
        already, rest = split_capture_done(text)
        if already:
            continue
        _, bare = split_capture_priority(rest)
        slug = slug_for_capture(bare)
        cycle = finished.get(slug)
        if cycle is None:
            continue
        marks.append((doc, text, f"DONE (Cycle {int(cycle)}): {text}",
                      slug, cycle))
    return marks


def mark_kept_its_slug(text, slug):
    """Does a marked bullet still hash to the slug the ledger matched?

    The one thing this tool cannot get wrong quietly. `slug_for_capture`
    reads his sentence with the marker and the rating stripped, so a
    correct mark is invisible to it; a mark that ate a word is not, and
    the next run would then mark the shortened bullet again under a new
    slug and stack a second prefix on it.

    Its own function so a test can hand it a corrupted bullet. Inside the
    walk nothing can reach the failing branch today, and a guard only ever
    called by code that cannot trip it passes for the wrong reason --
    which is the exact defect Cycle 485 filed against its own round of
    guards.
    """
    done, rest = split_capture_done(text)
    if not done:
        return False
    _, bare = split_capture_priority(rest)
    return slug_for_capture(bare) == slug


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--board", required=True, choices=list(BOARDS),
                        help="which of his two boards to walk")
    parser.add_argument("--claims", required=True,
                        help="claims.json on disk")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be marked, write nothing")
    args = parser.parse_args(argv)

    with open(args.claims, encoding="utf-8") as handle:
        finished = done_cycles(handle.read())

    try:
        captures = board_records.capture_documents(
            args.board, store=board_store)
    except board_records.RecordError as problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    marks = plan(captures, finished)
    if not marks:
        print(f"{args.board}: nothing to mark "
              f"({len(finished)} done capture claim(s) in the ledger)")
        return 0

    for _doc, old, new, slug, cycle in marks:
        if not mark_kept_its_slug(new, slug):
            print(f"{args.board}: refusing to write — marking moved the slug "
                  f"of {slug}", file=sys.stderr)
            return 1
        print(f"  DONE (Cycle {cycle}): {old[:70]}")
    if args.dry_run:
        print(f"{args.board}: would mark {len(marks)} capture(s) (dry run)")
        return 0

    # Every slug was checked above before the first write, so a refusal
    # arriving from the walk costs no half-marked board. Below this line a
    # failure has to say how far it got: the bullets already marked are
    # marked, and re-running the same call picks up from there.
    written = 0
    for doc, _old, new, _slug, _cycle in marks:
        try:
            board_write.change_capture_text(
                args.board, doc, new, store=board_store)
        except (board_write.WriteRefused, board_write.BoardDamaged,
                board_records.RecordError, StoreError) as problem:
            print(f"{args.board}: marked {written} capture(s), then stopped — "
                  f"{problem}", file=sys.stderr)
            return 1
        written += 1
    print(f"{args.board}: marked {written} capture(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
