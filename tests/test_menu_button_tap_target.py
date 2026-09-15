"""The hamburger is the app's only way to another page, and it is 40px.

WCAG 2.2 SC 2.5.8 Target Size (Minimum) sets a floor of 24 by 24 CSS
pixels, and every target in this app already clears it -- measured in
Chromium at a 390px viewport on 2026-09-08 and again on 2026-09-15, 117
non-inline targets, none under 24. So this test is not about a failure.

It is about the two numbers the platform vendors actually recommend,
which are both higher and neither of which this button met:

- Apple's Human Interface Guidelines give iOS a *default* control size of
  44x44 pt and a *minimum* of 28x28 pt. (Those are two different columns
  of one table, and the 44 that gets quoted everywhere is the default.)
- Material's target-size guidance: "For most platforms, consider making
  touch targets at least 48 x 48dp. A touch target this size results in a
  physical size of about 9mm ... The recommended target size for
  touchscreen elements is 7-10mm."

Source for both, fetched and read on 2026-09-15:
`nova/resources/design-wiki/touch-target-size.md`.

`.menu-btn` paints at 2.5rem. Rather than grow the painted square on a
header whose three top edges have to agree, an absolutely positioned
`::before` extends the hit area outward by 4px on every side: 40 + 4 + 4
is 48. This test is what keeps those two numbers in step, because they
live in two rules and nothing else relates them.
"""

from __future__ import annotations

import re
from pathlib import Path

STYLE = Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"

# Material's touch-target recommendation, in CSS pixels.
TOUCH_TARGET_MIN_PX = 48
REM_PX = 16


def _block(css: str, selector: str) -> str:
    """The declarations of the first rule whose selector list is exactly `selector`."""
    match = re.search(
        r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.MULTILINE
    )
    assert match, f"no rule for {selector!r} in style.css"
    return match.group(1)


def _length_px(block: str, prop: str) -> float:
    match = re.search(rf"(?<![-\w]){re.escape(prop)}\s*:\s*(-?[\d.]+)(rem|px)\s*;", block)
    assert match, f"no plain-length {prop!r} in {block!r}"
    value = float(match.group(1))
    return value * REM_PX if match.group(2) == "rem" else value


def test_menu_button_tap_area_reaches_48_css_pixels() -> None:
    css = STYLE.read_text()
    painted = _block(css, ".menu-btn")
    width = _length_px(painted, "width")
    height = _length_px(painted, "height")

    grown = _block(css, ".menu-btn::before")
    inset = _length_px(grown, "inset")
    assert inset < 0, "a positive inset shrinks the pseudo-element inside the button"
    assert 'content:' in grown.replace(" ", "") or 'content :' in grown, (
        "a pseudo-element with no `content` is not generated, so it adds no hit area"
    )

    grow = -inset
    assert width + 2 * grow >= TOUCH_TARGET_MIN_PX, (
        f"menu button tap width is {width + 2 * grow}px, under {TOUCH_TARGET_MIN_PX}"
    )
    assert height + 2 * grow >= TOUCH_TARGET_MIN_PX, (
        f"menu button tap height is {height + 2 * grow}px, under {TOUCH_TARGET_MIN_PX}"
    )


def test_menu_button_grower_is_taken_out_of_flow() -> None:
    """`.menu-btn` is a flex column of three bars.

    Without `position: absolute` the pseudo-element is a fourth flex item
    and pushes the bars off centre -- so this is not a style preference,
    it is the difference between a bigger target and a broken glyph.
    """
    grown = _block(STYLE.read_text(), ".menu-btn::before")
    assert re.search(r"position\s*:\s*absolute\s*;", grown), (
        "the hit-area pseudo-element must be out of flow"
    )
