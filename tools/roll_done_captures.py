"""Move Edvard's finished captures off the top of his two board files.

Edvard types into a bare bullet list above `## Board` in
`projects/sokrates/projects/nova/issues.md` and `.../ideas.md`. When a
cycle closes one of those captures it rewrites the bullet to start with
`DONE (Cycle N):` and leaves it exactly where it was. Nothing has ever
removed one. Measured 2026-08-22, Cycle 313: **31 of the 33 bullets on
`issues.md` and 12 of the 13 on `ideas.md` were `DONE`** -- the leftover
on `ideas.md` being the empty cursor bullet he types into, so that list
was finished work and nothing else. The two lists were **15,592 and
10,159 characters**, 25,751 together, and the longest single bullet in
them is **2,979**. (The first version of this docstring said 45,000 and
2,600. Both were numbers I had not taken; the reviewer on runner#286
checked them against the files this paragraph claims to have measured.)

`nova_boards.split_capture_done` (Cycle 251) already stops a closed
capture being *read* as work: the ranking drops it and the page hides
it. That fixed the consumers and left the file, and the file is the half
Edvard actually opens -- these two documents live in his own vault
precisely so his phone can reach them without the Nova app.

So this is `identity.md` rule 8 applied to his boards: *"finished items
move to a `# Processed` section with what actually happened."* Nothing
is deleted and nothing is summarised. The bullet moves, verbatim, to a
`## Processed captures` section at the **end** of the file -- past
`# Details`, not between the capture list and `## Board`, because a
block he has to scroll past to reach the board is the complaint this
fixes, not a place to put it.

    python3 -m tools.roll_done_captures --file issues.md --dry-run
    python3 -m tools.roll_done_captures --file issues.md

**It takes a path on disk and knows nothing about the vault, so the
caller owns the compare-and-swap.** A cycle boarding these same files,
or Edvard's phone syncing through Obsidian, is the concurrent writer
`nova_capture` defends against with `_rev` and the one most likely to be
running. Read and write it the way `prompt.md` step 6 does:

    V='projects/sokrates/projects/nova/issues.md'
    python3 /app/bridge/vault_tool.py get "$V" --rev-file /tmp/i.rev > /tmp/i.md \
      && python3 -m tools.roll_done_captures --file /tmp/i.md \
      && python3 /app/bridge/vault_tool.py put "$V" /tmp/i.md --if-rev-file /tmp/i.rev

No `--allow-shrink`: this moves text within one document rather than
splitting it in two, so the file comes back within a hundred bytes of
its own size and the shrink guard has nothing to complain about. That is
also why there is no archive-first ordering to get right -- there is one
write, not two.

Exits 0 when it rewrote the file or found nothing to move, 1 on a check
failure. It is idempotent: a second run finds no `DONE` bullets left in
the capture list and reports `nothing to move`.

**The check that matters is the one that asks the reader.** These files
are parsed by `nova_boards.parse_board` for the app's board pages, and
the failure mode of moving text inside one is silent: a row or a
write-up stops rendering and nobody sees it until Edvard does. So the
rewrite is verified by parsing both versions and asserting that
`items` and `details` come back **identical**, and that `captures` lost
exactly the `DONE` bullets and nothing else. That is
`roll_captures._check_render`'s lesson -- the only guard there that asks
the reader rather than the writer -- borrowed rather than re-learned.

The `## Processed captures` heading is a level-2 heading on purpose.
`nova_boards._detail_spans` ends a write-up at the next `#` or `##`, so
appending it closes the final `### #N` block cleanly instead of being
swallowed into it; and `_captures` stops at the first heading, so a
bullet down there can never be read back as something Edvard just typed.
"""

import argparse
import sys

from agora_runner.nova_boards import parse_board, split_capture_done
from agora_runner.nova_capture import _capture_span

PROCESSED_HEADING = "## Processed captures"


def _has_processed_heading(text):
    """Is the archive heading already a heading in `text`?

    A substring search would find the phrase inside any write-up that
    happened to mention it -- and these files are 190KB of Edvard's and
    my own prose, so that is a matter of time. The heading would then be
    skipped and the next roll would append bare bullets under whatever
    section ends the file. Reviewer finding on runner#286.
    """
    wanted = PROCESSED_HEADING.strip().lower()
    return any(line.strip().lower() == wanted for line in (text or "").split("\n"))


def plan(markdown):
    """`(kept, moved)` -- the capture bullets that stay, and the DONE ones.

    Both are raw file lines, not parsed text, because the whole point is
    that the bullet moves byte-identical. A wrapped capture (Obsidian on
    a phone puts a continuation on its own line) travels with the bullet
    above it, under the same test `nova_boards._captures` folds by --
    non-blank, and not starting `-`, `*` or `|`. Sharing the rule is not
    cosmetic: a line the reader folds into the bullet above must move
    with it, and a line the reader ignores must stay put, or the page and
    the file disagree about where Edvard's sentence ends. Neither real
    file has such a line today, so this is pinned by test rather than by
    data.
    """
    lines = (markdown or "").split("\n")
    start, first, end = _capture_span(lines)
    if first is None:
        return [], []

    blocks = []
    for index in range(first, end):
        stripped = lines[index].strip()
        if stripped == "-" or stripped.startswith("- "):
            blocks.append([lines[index]])
        elif blocks and stripped and not stripped.startswith(("-", "*", "|")):
            blocks[-1].append(lines[index])
        else:
            # A line inside the span that is neither a bullet nor a fold:
            # blank, or `*`/`|`-prefixed. The reader ignores it, so it is
            # not part of any capture -- but it is still in his file, and
            # a block of its own is the only way it stays there. Dropping
            # it is invisible to `check`, because `parse_board` never saw
            # it either. My own test caught that, not the reviewer's.
            blocks.append([lines[index]])

    kept, moved = [], []
    for block in blocks:
        head = block[0].strip()
        bullet = head == "-" or head.startswith("- ")
        cycle, _ = split_capture_done(head[2:]) if bullet else ("", head)
        (moved if cycle else kept).append(block)
    return kept, moved


def rewrite(markdown):
    """The file with its DONE captures moved to `## Processed captures`.

    Returns `(new_markdown, moved_count)`. `(markdown, 0)` when there is
    nothing to move, so the caller can skip the write rather than put an
    identical document and burn a revision.
    """
    kept, moved = plan(markdown)
    if not moved:
        return markdown, 0

    lines = (markdown or "").split("\n")
    start, first, end = _capture_span(lines)

    # Exactly one empty bullet, last: the cursor he types into. It is the
    # file's own documented contract and `insert_captures` restores it the
    # same way, so a capture list that was all DONE does not come back
    # with nowhere to type.
    body = [line for block in kept for line in block if line.strip() != "-"]
    head = lines[:first] + body + ["- "]

    tail = lines[end:]
    while tail and not tail[0].strip():
        tail.pop(0)

    # Both real files end in a hundred-odd blank lines. They are nothing
    # to the parser and they are still his file, so they are put back
    # under the archive rather than quietly dropped -- the reviewer on
    # runner#286 caught the first version deleting all 121 of them and
    # `check` could not see it, because `parse_board` cannot.
    rest = "\n".join(tail)
    padding = len(rest) - len(rest.rstrip("\n"))
    rest = rest.rstrip("\n")
    archived = [line for block in moved for line in block]
    if not _has_processed_heading(rest):
        rest = rest + "\n\n" + PROCESSED_HEADING + "\n"
    parts = [
        "\n".join(head),
        "",
        rest.rstrip("\n"),
        "\n".join(archived) + "\n" * (padding or 1),
    ]
    return "\n".join(parts), len(moved)


def check(before, after, moved):
    """Every way this rewrite could go wrong, asked of the reader.

    A list of complaints, empty when the two documents differ in exactly
    the way they were meant to. `parse_board` is the function the app
    renders from, so asking it is the only check that can see a write-up
    that silently stopped being part of its own heading.
    """
    old, new = parse_board(before), parse_board(after)
    problems = []
    if old["items"] != new["items"]:
        problems.append("board rows changed")
    if old["details"] != new["details"]:
        problems.append("detail write-ups changed")

    gone = [c for c in old["captures"] if c not in new["captures"]]
    if len(gone) != moved:
        problems.append(f"{len(gone)} captures left the list, expected {moved}")
    for capture in gone:
        cycle, _ = split_capture_done(capture)
        if not cycle:
            problems.append(f"an unfinished capture was moved: {capture[:60]}")
    for capture in new["captures"]:
        cycle, _ = split_capture_done(capture)
        if cycle:
            problems.append(f"a DONE capture stayed: {capture[:60]}")

    # Nothing may be lost, only relocated. Comparing the whole text rather
    # than the parsed halves catches a bullet dropped between the two
    # sections, which `parse_board` would report as a clean removal.
    for capture in gone:
        if capture.split("\n")[0][:80] not in after:
            problems.append(f"a moved capture is not in the new file: {capture[:60]}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="board markdown on disk")
    parser.add_argument("--out", help="where to write (default: in place)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    before = open(args.file, encoding="utf-8").read()
    after, moved = rewrite(before)
    if not moved:
        print("nothing to move")
        return 0

    problems = check(before, after, moved)
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 1

    print(f"moved {moved} finished capture(s) to '{PROCESSED_HEADING}'")
    print(f"{len(before)} -> {len(after)} bytes")
    if args.dry_run:
        return 0
    open(args.out or args.file, "w", encoding="utf-8").write(after)
    print(f"wrote {args.out or args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
