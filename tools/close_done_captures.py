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

    python3 -m tools.close_done_captures --file /tmp/i.md --board issues \\
        --claims claims.json --dry-run

Then `roll_done_captures` on the same file does the moving. Two tools
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
idempotent, and it is asserted per bullet before the rewrite is returned
rather than trusted from the docstring.

Exits 0 whether it marked anything or not, 1 on a check failure.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    parse_board, split_capture_done, split_capture_priority,
)
from agora_runner.nova_capture import _capture_span
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


def plan(markdown, finished):
    """`[(line_index, old_line, new_line, slug, cycle)]` for what to mark.

    Works on raw file lines rather than on `parse_board`'s output because
    the bullet has to come back byte-identical apart from the prefix --
    his wrapping, his emoji, his trailing spaces. A capture that already
    carries a DONE marker is skipped, which is what makes a second run a
    no-op.
    """
    lines = (markdown or "").split("\n")
    start, first, end = _capture_span(lines)
    if first is None:
        return []

    marks = []
    for index in range(first, end):
        line = lines[index]
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        if line[:1].isspace():
            # An indented bullet is a cycle's reply written under his
            # capture, not a capture. `roll_done_captures.plan` folds it
            # into the block above for the same reason; marking one DONE
            # would put a prefix on my own sentence.
            continue
        bullet = stripped[2:]
        already, rest = split_capture_done(bullet)
        if already:
            continue
        _, text = split_capture_priority(rest)
        slug = slug_for_capture(text)
        cycle = finished.get(slug)
        if cycle is None:
            continue
        # Rebuilt from the line's own prefix rather than by substituting
        # the bullet text back into it, so his trailing whitespace and any
        # indentation survive and nothing can match twice.
        head = line[:len(line) - len(line.lstrip())] + "- "
        tail = line[len(head) + len(bullet):]
        marks.append((index, line,
                      f"{head}DONE (Cycle {int(cycle)}): {bullet}{tail}",
                      slug, cycle))
    return marks


def rewrite(markdown, finished):
    """The file with every ledger-closed capture marked. `(text, count)`.

    Returns the input unchanged when there is nothing to mark, so the
    caller can skip the `put` rather than burn a revision on an identical
    document.
    """
    marks = plan(markdown, finished)
    if not marks:
        return markdown, 0
    lines = (markdown or "").split("\n")
    for index, _old, new, slug, _cycle in marks:
        if not mark_kept_its_slug(new, slug):
            raise SystemExit(
                f"marking moved the slug of {slug}: refusing to write")
        lines[index] = new
    return "\n".join(lines), len(marks)


def mark_kept_its_slug(line, slug):
    """Does a marked bullet still hash to the slug the ledger matched?

    The one thing this tool cannot get wrong quietly. `slug_for_capture`
    reads his sentence with the marker and the rating stripped, so a
    correct mark is invisible to it; a mark that ate a word is not, and
    the next run would then mark the shortened bullet again under a new
    slug and stack a second prefix on it.

    Its own function so a test can hand it a corrupted line. Inside
    `rewrite` nothing can reach the failing branch today, and a guard
    only ever called by code that cannot trip it passes for the wrong
    reason -- which is the exact defect Cycle 485 filed against its own
    round of guards.
    """
    done, rest = split_capture_done(line.strip()[2:])
    if not done:
        return False
    _, text = split_capture_priority(rest)
    return slug_for_capture(text) == slug


def check(before, after, marked):
    """Ask the reader, not the writer, whether the rewrite was faithful.

    `roll_done_captures.check`'s lesson borrowed rather than re-learned:
    these files draw his two board pages, and the failure mode of editing
    one is silent. So the board rows and their write-ups must come back
    identical, the capture list must keep exactly the same number of
    bullets in the same order, and the only difference in any of them
    must be a DONE prefix.
    """
    old, new = parse_board(before or ""), parse_board(after or "")
    if old["items"] != new["items"]:
        return "board rows changed"
    if old.get("details") != new.get("details"):
        return "write-ups changed"
    if len(old["captures"]) != len(new["captures"]):
        return (f"capture count moved {len(old['captures'])} -> "
                f"{len(new['captures'])}")
    changed = 0
    for was, now in zip(old["captures"], new["captures"]):
        if was == now:
            continue
        done, rest = split_capture_done(now)
        if not done or rest != split_capture_done(was)[1]:
            return f"a capture changed beyond its DONE prefix: {was[:60]!r}"
        changed += 1
    if changed != marked:
        return f"marked {marked} bullet(s) but {changed} capture(s) changed"
    return ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--file", required=True,
                        help="board file on disk; the caller owns the "
                             "vault compare-and-swap")
    parser.add_argument("--board", required=True, choices=["issues", "ideas"],
                        help="which of his two boards, for the report only")
    parser.add_argument("--claims", required=True,
                        help="claims.json on disk")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be marked, write nothing")
    args = parser.parse_args(argv)

    with open(args.file, encoding="utf-8") as handle:
        before = handle.read()
    with open(args.claims, encoding="utf-8") as handle:
        finished = done_cycles(handle.read())

    after, marked = rewrite(before, finished)
    if not marked:
        print(f"{args.board}: nothing to mark "
              f"({len(finished)} done capture claim(s) in the ledger)")
        return 0

    problem = check(before, after, marked)
    if problem:
        print(f"{args.board}: refusing to write — {problem}", file=sys.stderr)
        return 1

    for _index, old, _new, _slug, cycle in plan(before, finished):
        print(f"  DONE (Cycle {cycle}): {old.strip()[2:][:70]}")
    if args.dry_run:
        print(f"{args.board}: would mark {marked} capture(s) (dry run)")
        return 0

    with open(args.file, "w", encoding="utf-8") as handle:
        handle.write(after)
    print(f"{args.board}: marked {marked} capture(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
