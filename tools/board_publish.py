"""Draw his board file from the records, so the markdown can become a view.

`board_migrate` seeds the store and `board_migrate --status` says whether
the store still agrees with what he sees. Neither of them can draw the
other direction, and that direction is the one the switchover is blocked
on: the moment any writer starts changing records instead of markdown,
his `issues.md` and `ideas.md` are stale until something renders them
back. The spec names this by hand -- *"keep the generated markdown view
from day one, so the GitHub backup and `vault-drift` keep working and a
bad migration is visible in a diff"* -- and until now nothing did it.
`board_view.render_document` has drawn a whole board file since #960, and
its only callers were a migration checker and its tests.

**It writes a local file and never the vault.** `tools.board_put` is the
one door a board goes through, because a vault write has to be followed
by the ticket store, and putting a second door beside it is the split
brain this migration exists to avoid. So this prints the exact
`board_put` command instead of running it, and a cycle that means to
publish types it.

**The refusal is the round trip, not a word count.** The rendered file is
faithful when it re-reads as the records it was drawn from -- all four of
the parser's keys, plus the layout, which those four keys structurally
cannot see (`board_migrate.layout_differences` says why). That is a
checkable invariant with a real failure mode: a render bug drops a row's
write-up or reorders his `## Processed captures` archive and the re-read
disagrees.

**The word delta is reported and deliberately does not gate.** Rendering
is a one-time reflow -- `render_detail` normalises detail headings and
the live tables carry a ragged number of cells -- so the first publish of
a board legitimately moves words. Cycle 1360 measured what that costs on
his real boards: 120 words on `issues.md` and 216 on `ideas.md` through
the layout, against 19,653 and 6,469 dropped without it. Turning that
into a threshold would be a number I invented sitting in front of a
number I measured, so the counts are printed whole and the human decides.
That is the same call `lint_entry` makes on an absolute claim.
"""

import argparse
import collections
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import (  # noqa: E402
    board_document, board_records, board_store, board_view)
from tools import board_migrate  # noqa: E402
from tools import board_migration_preflight as preflight  # noqa: E402


def frontmatter_of(markdown):
    """The `---` block at the top of a board file, verbatim, or `""`.

    Passed into `render_document` rather than derived from the records for
    the reason its docstring gives: the frontmatter is his, it carries the
    `contract:` line each board file explains itself with, and nothing in
    the store holds it. Taking it off the live document is therefore not a
    shortcut -- it is the only place it exists.
    """
    lines = (markdown or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[:index + 1])
    return ""


def word_delta(before, after):
    """`(added, dropped)` word counts between two documents.

    Multisets, not sets: a word that appears four times in his archive and
    once in the render has lost three, and a set difference reports zero.
    """
    one = collections.Counter((before or "").split())
    two = collections.Counter((after or "").split())
    added = sum((two - one).values())
    dropped = sum((one - two).values())
    return added, dropped


def render(board, markdown, store=board_store):
    """`(text, problems)` -- his board drawn from the records.

    `problems` is empty when the render re-reads as the records it came
    from. `markdown` is the live document and is used for two things only:
    its frontmatter, which the store does not hold, and the word delta.
    """
    contents = board_records.contents(board, store=store)
    layout = store.read_layout(board)
    text = board_view.render_document(
        contents,
        frontmatter=frontmatter_of(markdown),
        layout=layout)
    problems = board_migrate.differences(
        contents, preflight.board_contents(text))
    problems += board_migrate.layout_differences(text, board, layout)
    return text, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--board", required=True,
                        choices=sorted(board_document.BOARDS),
                        help="which board to draw")
    parser.add_argument("--file", required=True, metavar="FILE",
                        help="the live board markdown, for its frontmatter "
                             "and the word delta")
    parser.add_argument("--out", metavar="FILE",
                        help="write the rendered document here; without it "
                             "nothing is written anywhere")
    parser.add_argument("--vault-path", metavar="PATH",
                        help="the vault path to print in the board_put "
                             "command; only used with --out")
    args = parser.parse_args(argv)

    with open(args.file, encoding="utf-8") as handle:
        markdown = handle.read()

    try:
        text, problems = render(args.board, markdown)
    except board_records.UnmigratedStore as exc:
        print(f"REFUSED: {exc}")
        return 2

    added, dropped = word_delta(markdown, text)
    print(f"board: {args.board}")
    print(f"rendered: {len(text)} bytes from {len(markdown)} bytes")
    print(f"words: +{added} / -{dropped}")
    if problems:
        print("faithful: NO")
        for problem in problems:
            print(f"  {problem}")
        print("REFUSED: the rendered document does not re-read as the "
              "records it was drawn from; nothing was written")
        return 2

    print("faithful: yes -- it re-reads as the records it was drawn from")
    if not args.out:
        print("nothing was written; pass --out FILE to keep it")
        return 0

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"wrote: {args.out}")
    path = args.vault_path or f"<vault path of the {args.board} board>"
    print(f"to publish it: python3 -m tools.board_put '{path}' {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
