"""The border of a control has to be visible, and `--line` is not.

WCAG 2.2 SC 1.4.11 Non-text Contrast (level AA) asks for 3:1 on "visual
information required to identify user interface components and states".
A control's own edge is the whole of that information for an input, a
select or an outline button -- there is nothing else on the screen saying
where the box is.

I measured the rendered app at a 390px phone viewport on 2026-09-08 and
every control failed: `--line` (#272a36) is 1.30:1 against `--bg` and
1.20:1 against `--card`. It failed quietly, which is the point -- a dim
border reads as tasteful, and no test in this repo could see it, because
`test_css_variables_are_defined.py` only asks whether a `var(--x)` names
something that exists.

So `--outline` exists for the edge of a control and `--line` stays for
dividers and card edges, where 1.4.11 does not apply (a rule between two
rows is not information required to identify a component). The two jobs
had one name and one value; that is why the value could only ever suit
one of them.

Source for every number here: `nova/resources/design-wiki/contrast-minimums.md`,
verified against https://www.w3.org/TR/WCAG22/ on 2026-09-08.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"

# The floor from SC 1.4.11. Not rounded: the Understanding document is
# explicit that 2.999:1 fails, so this is a `>=` against the exact ratio.
NON_TEXT_MINIMUM = 3.0

# A selector naming something you touch. Deliberately a small literal list
# rather than a clever pattern: a new control that this misses is a missed
# violation, and widening the list is a one-line change when one shows up.
CONTROL_SELECTOR = re.compile(
    r"(^|[\s,>])(input|textarea|select|button|summary)\b"
    r"|-btn\b|\bbtn-|-input\b|-select\b|\.chip\b|\.tab\b",
    re.IGNORECASE,
)

BORDER_LINE = re.compile(
    r"border(?:-(?:top|right|bottom|left))?(?:-color)?\s*:\s*[^;{}]*?var\(--line\)"
)

RULE = re.compile(r"([^{}]*)\{([^{}]*)\}")


def _relative_luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    r, g, b = linear
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _tokens(css: str) -> dict[str, str]:
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", css))


def _control_rules_on_line(css: str) -> list[str]:
    """Every rule whose selector names a control and whose border is `--line`."""
    offenders = []
    for match in RULE.finditer(css):
        selector, body = match.group(1), match.group(2)
        # A rule's selector is the last line before the brace; everything
        # above it is the comment this file is full of, and a comment that
        # says the word "button" is not a selector.
        lines = [line for line in selector.strip().splitlines() if line.strip()]
        name = lines[-1].strip() if lines else ""
        if CONTROL_SELECTOR.search(name) and BORDER_LINE.search(body):
            offenders.append(name)
    return offenders


def test_the_detector_can_see_a_violation():
    """The precondition. A guard that cannot fail is not a guard.

    `_control_rules_on_line` is the whole test below, so hand it a rule it
    must object to, and one it must not -- a divider is allowed to be dim.
    """
    offending = ".board-search-input { border: 1px solid var(--line); }"
    assert _control_rules_on_line(offending) == [".board-search-input"]

    divider = ".item-comment { border-top: 1px solid var(--line); }"
    assert _control_rules_on_line(divider) == []


def test_outline_token_meets_non_text_contrast():
    tokens = _tokens(CSS.read_text())
    for name in ("--outline", "--bg", "--card"):
        assert name in tokens, f"{name} is not declared in style.css"

    for background in ("--bg", "--card"):
        ratio = _contrast(tokens["--outline"], tokens[background])
        assert ratio >= NON_TEXT_MINIMUM, (
            f"--outline ({tokens['--outline']}) is {ratio:.2f}:1 against "
            f"{background} ({tokens[background]}); SC 1.4.11 needs "
            f"{NON_TEXT_MINIMUM}:1. Lighten --outline rather than lowering this."
        )


def test_no_control_borders_its_edge_with_the_divider_token():
    offenders = _control_rules_on_line(CSS.read_text())
    assert not offenders, (
        "these rules edge a control with --line, which is 1.2:1 against "
        "--card and fails SC 1.4.11; use var(--outline): " + ", ".join(sorted(set(offenders)))
    )


@pytest.mark.parametrize("token", ["--line", "--outline"])
def test_both_tokens_still_exist(token):
    """--line is not deleted. Dividers still want a quiet colour."""
    assert token in _tokens(CSS.read_text())
