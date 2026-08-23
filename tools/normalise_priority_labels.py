"""Rewrite every priority rating already in Edvard's files to `PRIORITY_LABELS`.

This was `retire_priority_glyphs.py` and it only knew how to go one way:
coloured ball -> plain word, on Edvard's *"Please do not use these
symbols '🟠' as i can't really see the difference as they are colors"*
(comments board 2026-08-19). He reversed that the next morning --
*"There has been a missinderstanding here. I do like the symbols ... if
you use the symbol and text, thats completely fine!"* -- and a
one-directional tool had nothing to offer, because the cells it had
rewritten no longer carried the glyph it matched on.

So it matches on `priority_key` now instead of on a spelling. Whatever
`PRIORITY_LABELS` currently says is what gets written, and every
spelling that has ever been in these files (`🟠 High`, `High`,
`🟠 High:` on a bullet) reads back to the same key. Reversing this
decision a third time is a four-line edit to that dict followed by one
run of this, in whichever direction it now points.

The code change alone reaches only rows something touches again, which
is why this exists at all: the boards Edvard opens in Obsidian would
otherwise keep whatever spelling was current when each row was last
written.

**Two places, and only two.** A row's fifth `## Board` cell, and the
leading glyph on a bare capture bullet above that table. Everything else
is left byte-identical, and that is the whole difficulty: the same four
glyphs appear all over the `# Details` prose, in his own words and in my
write-ups quoting a rating. A blind search-and-replace would edit his
sentences, which is the one thing a cycle may not do to his files.

So this walks the document structurally rather than matching text: it
tracks which section it is in, only rewrites cells inside a `## Board`
table row, and only rewrites a bullet that sits above the first heading.
`--check` prints what it would do and writes nothing.
"""

import argparse
import re
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    CAPTURE_PRIORITY_SEP,
    PRIORITY_LABELS,
    _ALIAS_PIPE,
    _SECTION_RE,
    _WIKILINK_RE,
    priority_key,
    split_capture_priority,
)


def _rewrite_row(line):
    """One `## Board` table line -> the line with its rating cell normalised.

    Returns `(line, changed)`. The wiki-link mask is the same one
    `nova_boards` uses: `| [[#57 — Title|57]] | ... |` contains the
    delimiter inside a cell, so a naive split shifts every column right.
    """
    stripped = line.strip()
    if not stripped.startswith("|"):
        return line, False
    masked = _WIKILINK_RE.sub(lambda m: m.group(0).replace("|", _ALIAS_PIPE), stripped)
    cells = masked.strip("|").split("|")
    # A rating lives in the fifth column. Rows that never grew one are
    # four cells wide and have nothing here to rewrite.
    if len(cells) < 5:
        return line, False
    cell = cells[4].strip()
    # Keyed, not spelling-matched. `priority_key` strips emoji and
    # aliases, so `🟠 High`, `High` and `high` all land on the same
    # bucket -- and any cell that is not a rating at all keys to
    # something absent from `PRIORITY_LABELS` and is left alone. That is
    # what makes this work in whichever direction the labels point.
    key = priority_key(cell)
    if not key or key not in PRIORITY_LABELS:
        return line, False
    if cell == PRIORITY_LABELS[key]:
        return line, False
    cells[4] = " " + PRIORITY_LABELS[key] + " "
    rebuilt = "|" + "|".join(cells) + "|"
    return rebuilt.replace(_ALIAS_PIPE, "|"), True


def _rewrite_capture(line):
    """One bare capture bullet -> the bullet with its rating normalised.

    `- 🟠 fix the sort order` -> `- 🟠 High: fix the sort order`. The
    colon is not cosmetic and does not go away when the glyph comes
    back: `split_capture_priority` requires it on any spelling carrying
    the word, because unlike a glyph the word "High" can legitimately
    open one of his sentences.

    Reading is delegated to `split_capture_priority` rather than
    duplicated here, which is what keeps the two agreeing about which
    spellings are ratings -- this tool exists to rewrite his files and
    must never recognise one the parser does not.

    A `DONE (Cycle N):` marker sits *outside* the rating, so it is
    stepped over rather than matched through -- reading the rating first
    on a closed capture finds `D` and reports it unrated.
    """
    match = re.match(r"^(\s*-\s+)(.*)$", line)
    if not match:
        return line, False
    lead, rest = match.group(1), match.group(2)
    done = re.match(r"^(DONE\s*\(\s*Cycle\s*\d+\s*\)\s*:\s*)(.*)$", rest, re.IGNORECASE)
    prefix = ""
    if done:
        prefix, rest = done.group(1), done.group(2)
    label, body = split_capture_priority(rest)
    if not label:
        return line, False
    if not body:
        # A bullet that is nothing but a rating carries no text to rate.
        # Rewriting it to a bare `🟠 High:` would invent a capture;
        # leave it exactly as it is.
        return line, False
    rewritten = lead + prefix + label + CAPTURE_PRIORITY_SEP + body
    return (rewritten, True) if rewritten != line else (line, False)


def normalise(markdown):
    """`(rewritten, [(kind, before, after)])`. Pure; no I/O."""
    lines = markdown.split("\n")
    out = []
    changes = []
    seen_heading = False
    in_board = False
    for line in lines:
        heading = _SECTION_RE.match(line)
        if heading:
            seen_heading = True
            in_board = (len(heading.group(1)) == 2
                        and heading.group(2).strip().lower() == "board")
            out.append(line)
            continue
        if in_board:
            new, changed = _rewrite_row(line)
        elif not seen_heading:
            new, changed = _rewrite_capture(line)
        else:
            new, changed = line, False
        if changed:
            changes.append(("row" if in_board else "capture", line.strip(), new.strip()))
        out.append(new)
    return "\n".join(out), changes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="markdown file on local disk")
    ap.add_argument("--check", action="store_true", help="print, do not write")
    args = ap.parse_args(argv)

    with open(args.path, encoding="utf-8") as fh:
        original = fh.read()
    rewritten, changes = normalise(original)

    for kind, before, after in changes:
        print(f"  [{kind}] {before[:90]}\n      -> {after[:90]}")
    print(f"{len(changes)} change(s) in {args.path}")

    # The guard that makes this safe to run on his files: outside the
    # ratings themselves, the two documents must be identical. Anything
    # else moving means the walk escaped the board table.
    def _strip(text):
        for glyph in ("⚪", "🔵", "🟠", "🔴"):
            text = text.replace(glyph, " ")
        for word in ("Low", "Medium", "High", "Immediately"):
            text = text.replace(word, " ")
        return re.sub(r"[\s:|]+", " ", text)

    if _strip(original) != _strip(rewritten):
        print("REFUSED: something other than a rating changed", file=sys.stderr)
        return 1

    if not args.check and changes:
        with open(args.path, "w", encoding="utf-8") as fh:
            fh.write(rewritten)
        print(f"wrote {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
