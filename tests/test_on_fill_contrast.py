"""Text painted on a filled colour is always `--bg`, and `--bg` reads on every fill.

Material 3 calls this an "on" role: "Roles starting with this term indicate a
color for text or icons on top of its paired parent color" -- and it says to
"apply colors only in the intended pairs". This stylesheet has had exactly one
such role without naming it: every filled button and chip (`--accent`,
`--danger`, `--nova` behind the text) writes `color: var(--bg)`. Except
`.comment-unread`, which wrote the same hex out by hand, so retuning `--bg`
would have left one chip behind. It is `var(--bg)` now, same pixels.

The floor is WCAG's, not Material's, and that is deliberate. The Material
page promises only that "these color pairs provide an accessible minimum 3:1
contrast", and 3:1 is the large-text and non-text floor. These chips carry
0.68rem-0.9rem text, which is normal text under WCAG 2.2 SC 1.4.3 (level AA):
4.5:1.

Sources: `nova/resources/design-wiki/material-3-token-roles.md` (verified
2026-09-11 against https://m3.material.io/styles/color/roles) and
`contrast-minimums.md` (verified 2026-09-08 against https://www.w3.org/TR/WCAG22/).
"""

from __future__ import annotations

import re
from pathlib import Path

from test_control_outline_contrast import RULE, _contrast, _tokens

CSS = Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"

# SC 1.4.3, normal text. Not rounded: 4.499:1 fails.
TEXT_MINIMUM = 4.5

# The one "on" colour. Every fill in this palette is a light tone on a dark
# page, so the page colour itself is the text colour that reads on all of them.
ON_FILL = "var(--bg)"

# Backgrounds that are surfaces rather than fills: text on these is `--text`
# or `--dim`, and it is a different pairing with a different test.
SURFACES = {"--bg", "--card", "--line", "--report-bg"}

FILL = re.compile(r"background(?:-color)?\s*:\s*var\(\s*(--[a-z0-9-]+)\s*\)")
# `color:` on its own -- not border-color, not background-color.
TEXT_COLOUR = re.compile(r"(?<![-\w])color\s*:\s*([^;]+)")


def _css() -> str:
    # A comment can hold a `{` or a selector-looking phrase; the first
    # outline migration rewrote three rules by reading a comment as one.
    return re.sub(r"/\*.*?\*/", "", CSS.read_text(), flags=re.S)


def _text_on_fills(css: str) -> list[tuple[str, str, str]]:
    """(selector, fill token, text colour) for every rule that paints text on a fill.

    A fill rule with no `color:` is a bar or a dot with nothing written on it,
    so it has no pairing to check.
    """
    found = []
    for selector, body in RULE.findall(css):
        fill = FILL.search(body)
        if not fill or fill.group(1) in SURFACES:
            continue
        text = TEXT_COLOUR.search(body)
        if text:
            found.append((" ".join(selector.split()), fill.group(1), text.group(1).strip()))
    return found


def test_the_detector_finds_the_filled_controls():
    # Without this, a stylesheet reshuffle that the pattern stops matching
    # would pass both tests below with nothing in them.
    selectors = {s for s, _, _ in _text_on_fills(_css())}
    assert {".capture-send", ".prio-immediate", ".comment-unread"} <= selectors


def test_text_on_a_fill_is_the_on_colour():
    wrong = [f"{s} writes {c} on {f}" for s, f, c in _text_on_fills(_css()) if c != ON_FILL]
    assert not wrong, "text on a fill must be var(--bg): " + "; ".join(wrong)


def test_the_on_colour_reads_on_every_fill():
    css = _css()
    tokens = _tokens(css)
    fills = sorted({f for _, f, _ in _text_on_fills(css)})
    short = [
        f"--bg on {f} is {_contrast(tokens['--bg'], tokens[f]):.2f}:1"
        for f in fills
        if _contrast(tokens["--bg"], tokens[f]) < TEXT_MINIMUM
    ]
    assert not short, f"below the {TEXT_MINIMUM}:1 text floor: " + "; ".join(short)
