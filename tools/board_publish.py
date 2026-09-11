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

**Without `--publish` it writes a local file and never the vault**, and
prints the `board_put` command a cycle would type.

**`--publish` is the whole trip in one call, because after the flip the
hand-typed version is wrong** (Cycle 1396). `board_put` no longer touches
the records, so a view it writes moves the vault revision past the one the
records are stamped with, and `nova_site._his_board` then logs every
request as "the generated markdown and the records disagree". So this
reads the live file and its revision, draws it, refuses unless it re-reads
faithfully, writes it with that revision as the compare-and-swap, reads it
back, and only when the vault holds exactly what was drawn stamps the new
revision onto the records. A lost race writes nothing and stamps nothing.

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
import os
import sys
import tempfile

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_document, board_records, board_store  # noqa: E402
from agora_runner import board_publish as shared  # noqa: E402
from tools import board_put  # noqa: E402


# The render, the round-trip refusal and the publish itself live in
# `agora_runner.board_publish` now (Cycle 1397), because nova-site runs them
# after every board write and `tools/` is not in its image. What stays here is
# the bridge pod's way to the vault: `vault_tool.py` in a subprocess, since
# `agora_runner.vault` answers 401 from this pod.
frontmatter_of = shared.frontmatter_of
word_delta = shared.word_delta


def render(board, markdown, store=board_store):
    """`(text, problems)` -- see `agora_runner.board_publish.render`."""
    return shared.render(board, markdown, store=store)


def vault_path_of(board):
    """His vault path for `board`, off `board_put`'s own table."""
    for path, name in board_put.RECORD_BOARDS.items():
        if name == board:
            return path
    raise KeyError(board)


def _read(path):
    return board_put.vault_get(path)


def _write(path, text, rev):
    """One compare-and-swap put through `vault_tool.py`, as `(ok, detail)`."""
    with tempfile.TemporaryDirectory(prefix="board-publish.") as scratch:
        body = os.path.join(scratch, "board.md")
        rev_file = os.path.join(scratch, "board.rev")
        with open(body, "w", encoding="utf-8") as handle:
            handle.write(text)
        with open(rev_file, "w", encoding="utf-8") as handle:
            handle.write(f"{rev}\n")
        done = board_put.vault_put(path, body, if_rev_file=rev_file)
    return done.returncode == 0, (done.stdout or "") + (done.stderr or "")


def publish(board, store=board_store):
    """Draw, write, read back and stamp -- `agora_runner.board_publish.publish`
    over the bridge pod's vault client. Same `(code, lines)` contract."""
    return shared.publish(board, _read, _write, store=store)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--board", required=True,
                        choices=sorted(board_document.BOARDS),
                        help="which board to draw")
    parser.add_argument("--publish", action="store_true",
                        help="read his live file from the vault, write the "
                             "view back over it and stamp the records")
    parser.add_argument("--file", metavar="FILE",
                        help="the live board markdown, for its frontmatter "
                             "and the word delta")
    parser.add_argument("--out", metavar="FILE",
                        help="write the rendered document here; without it "
                             "nothing is written anywhere")
    parser.add_argument("--vault-path", metavar="PATH",
                        help="the vault path to print in the board_put "
                             "command; only used with --out")
    args = parser.parse_args(argv)

    if args.publish:
        code, lines = publish(args.board)
        print("\n".join(lines))
        return code
    if not args.file:
        parser.error("--file is required without --publish")

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
