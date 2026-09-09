"""Which modules still read a board by parsing markdown? (issue #203)

    python3 -m tools.board_reader_inventory
    python3 -m tools.board_reader_inventory --assert-migrated

Issue #203 replaces the two board markdown tables with one record per row,
and its spec is explicit that what makes the change safe is not sequencing
but coverage: every reader moves in the same commit, and
`grep -rl "parse_board\\|BOARD_PATHS"` is the checklist. **That grep is
wrong in two ways and this module is the corrected checklist.**

It over-matches. `parse_board_refs` in `agora_runner/nova_journal.py` reads
the PR references out of a journal entry and has nothing to do with a
board; `agora_runner/nova_plan.py` and `tools/lint_entry.py` call it. All
three are in the spec's 29 and none of them needs to move. The distinction
is a word boundary -- `_` is a word character, so `\\bparse_board\\b` does
not match `parse_board_refs` -- and it is pinned against those real files
rather than against a fixture, so widening the pattern fails a test.

And it conflates two surfaces that have opposite fates. Calling
`parse_board` is the thing being deleted: the definition-of-done's first
line is "no module reads or writes a board by parsing markdown". Reading
`BOARD_PATHS` is just knowing where the file lives, and something still
has to, because the spec keeps a *generated* `issues.md` in the daily
backup from day one. So `--assert-migrated` raises on a parse and never on
a path, and the inventory prints them in separate columns.

**It reads code, not prose.** A `parse_board` inside a docstring or a
comment is a module *explaining* the thing being deleted, not calling it,
and the two new record modules are exactly that: `board_document.py` and
`board_view.py` are the replacement path and neither one calls
`parse_board`, but both describe it at length and both were listed as
readers still to convert. That matters beyond a wrong count -- every one
of the migration's own modules will keep naming `parse_board` in the
paragraph saying what it replaced, so a text match could never let
`--assert-migrated` reach zero on a finished migration. The tokenizer
drops COMMENT and STRING tokens before the match. A file that will not
tokenize falls back to the raw text and is reported, because
over-reporting a reader is the safe direction and silently reading a
broken file as clean is not.

What it does not detect: a module that builds a markdown row by hand
instead of calling `parse_board`. That is the same split brain and it has
no name to grep for. This is a coverage floor, not a proof.
"""
import argparse
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PARSES = "parse_board"
PATHS = "BOARD_PATHS"

_SURFACES = ((PARSES, re.compile(r"\bparse_board\b")),
             (PATHS, re.compile(r"\bBOARD_PATHS\b")))
_REFS = re.compile(r"\bparse_board_refs\b")

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache", "venv"}

#: Token types that are prose rather than code. `FSTRING_MIDDLE` is here
#: because Python 3.12 stopped emitting an f-string as one STRING token --
#: its literal text arrives as its own type and its `{...}` holes arrive as
#: ordinary NAME and OP tokens, which is exactly right: a call inside an
#: f-string is a call, and the words around it are not. Leaving it out cost
#: a test, because this module's own "still call parse_board" message is an
#: f-string and read as a call on 3.12.
_PROSE = {tokenize.COMMENT, tokenize.STRING}
if hasattr(tokenize, "FSTRING_MIDDLE"):  # Python 3.12+
    _PROSE.add(tokenize.FSTRING_MIDDLE)


def code_only(text):
    """`text` with comments and string literals blanked out.

    A name that appears only in a docstring is documentation, not a call.
    Newlines are preserved so nothing else has to care that this happened.
    Returns the text unchanged when it will not tokenize -- over-reporting
    a reader is recoverable, missing one during the migration is not.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return text
    return "".join(
        "" if tok.type in _PROSE else tok.string
        for tok in tokens)


def tokenizes(text):
    """False when `code_only` had to fall back to matching raw text."""
    try:
        list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return False
    return True


def surfaces(text):
    """The board-markdown surfaces this file's *code* uses, in a stable order."""
    code = code_only(text)
    return tuple(name for name, pattern in _SURFACES if pattern.search(code))


def refs_only(text):
    """True when a file matches the spec's grep only via `parse_board_refs`."""
    return bool(_REFS.search(code_only(text))) and not surfaces(text)


def scan(root=ROOT, include_tests=False):
    """Map every Python module under `root` that touches a board to its surfaces.

    Excludes this module: it is the instrument, not a reader.

    Also returns the files that would not tokenize, whose surfaces were
    matched against raw text and may therefore be prose.
    """
    found, refs, unreadable, untokenized = {}, [], [], []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        rel = path.relative_to(root).as_posix()
        if not include_tests and rel.startswith("tests/"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            unreadable.append(rel)
            continue
        used = surfaces(text)
        if used or refs_only(text):
            if not tokenizes(text):
                untokenized.append(rel)
        if used:
            found[rel] = used
        elif refs_only(text):
            refs.append(rel)
    return found, refs, unreadable, untokenized


def report(found, refs, unreadable, untokenized=(), assert_migrated=False,
           out=None):
    # `out=sys.stdout` as a default binds at import and writes past a
    # replaced stdout, which is how the first run of this file's own
    # test read an empty capture on a report that had printed.
    out = sys.stdout if out is None else out
    parsers = sorted(rel for rel, used in found.items() if PARSES in used)
    for rel in sorted(found):
        used = found[rel]
        print(f"{'parses' if PARSES in used else '      '}  "
              f"{'paths' if PATHS in used else '     '}  {rel}", file=out)
    print(f"{len(found)} module(s) touch a board, {len(parsers)} of them by "
          f"parsing markdown.", file=out)
    if refs:
        print(f"{len(refs)} module(s) match the spec's grep only via "
              "parse_board_refs, which reads a journal entry's PR list and "
              "is not a board reader — they do not move: "
              + ", ".join(refs), file=out)
    if untokenized:
        print(f"{len(untokenized)} file(s) would not tokenize and were "
              "matched against raw text, so a mention in a comment counts "
              f"as a call: {', '.join(sorted(untokenized))}", file=out)
    if unreadable:
        print(f"Could not read {len(unreadable)} file(s) — that is no "
              f"instrument, not no readers: {', '.join(unreadable)}", file=out)
        return 1
    if not assert_migrated:
        return 0
    if parsers:
        print(f"NOT MIGRATED — {len(parsers)} module(s) still call "
              f"parse_board: {', '.join(parsers)}", file=out)
        return 2
    print("MIGRATED — no module parses board markdown. A module still "
          "reading BOARD_PATHS is expected: the generated markdown view has "
          "to know where to write.", file=out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--assert-migrated", action="store_true",
                        help="exit 2 while any module still calls parse_board")
    parser.add_argument("--include-tests", action="store_true",
                        help="also list test modules")
    parser.add_argument("--root", default=None, help="scan this tree instead")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else ROOT
    return report(*scan(root, include_tests=args.include_tests),
                  assert_migrated=args.assert_migrated)


if __name__ == "__main__":
    sys.exit(main())
