"""The menu drawer scrolls, because thirteen links do not fit on a phone.

The owner, `issues.md` 2026-08-26: *"The sliding sidebar menu in Nova is now
so full with pages links that it starts to move out the bottom of my
screen."*

`.nav` is `position: fixed` with `top: 0; bottom: 0`, so its box is
exactly one viewport tall no matter how many links it holds. Thirteen
`.nav-tab` links at the owner's own 360x697 (their device report,
`notes.md`) lay out 830px tall, so the last three hang below the bottom edge -- and
`body.nav-open { overflow: hidden }` stops the page behind from
scrolling, so no gesture reached them at all.

**The instrument that can actually see this is `tools.poke_page
nav-reachable`, not this file.** It opens the drawer in a real browser at
that viewport, scrolls it, and asserts the last link ends inside the
window. Measured against the live site on 2026-08-26: last link bottom
830 in a 697 viewport, unmoved by a scroll. Against the fix: 681.

This test is deliberately textual, for the same reason
`test_shell_top_is_shared.py` is: the browser suite runs under jsdom,
which does no layout, so a computed assertion here would pass whatever
the stylesheet said. What it can catch is the thing that would silently
undo the fix -- someone tidying an `overflow` line out of a rule whose
comment explains why it is there.
"""

import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"


def _nav_rule():
    css = CSS.read_text(encoding="utf-8")
    # Anchored at a line start so `.nav-tab` and `.nav.open` cannot match,
    # and non-greedy to the first closing brace.
    rule = re.search(r"\n\.nav\s*\{(.*?)\}", css, re.S)
    assert rule, "no `.nav` rule in style.css"
    # The rule carries block comments containing braces-free prose, but
    # stripping them keeps the declaration search honest either way.
    return re.sub(r"/\*.*?\*/", "", rule.group(1), flags=re.S)


def _declaration(body, prop):
    hit = re.search(r"(?:^|;)\s*" + re.escape(prop) + r"\s*:([^;]+)", body, re.S)
    return hit.group(1).strip() if hit else None


def test_the_drawer_can_scroll():
    """Without this the surplus links are drawn and unreachable."""
    value = _declaration(_nav_rule(), "overflow-y")
    assert value in {"auto", "scroll"}, (
        "`.nav` must scroll its own overflow -- it is pinned top to bottom, so "
        f"anything past one viewport is simply off screen. Found: {value!r}"
    )


def test_scrolling_the_drawer_does_not_pull_the_page():
    """The drawer scrolls over a body that is `overflow: hidden`.

    Without `overscroll-behavior: contain`, a flick past the last link
    chains to the document -- the rubber-band every phone browser does by
    default, under a menu that is supposed to be modal.
    """
    value = _declaration(_nav_rule(), "overscroll-behavior")
    assert value == "contain", (
        "`.nav` must contain its overscroll now that it scrolls. "
        f"Found: {value!r}"
    )


def test_the_drawer_is_still_pinned_to_both_edges():
    """The premise of the two tests above, pinned so it cannot drift.

    If `.nav` ever stops being `bottom: 0`, it sizes to its content
    instead and the overflow declarations become dead weight rather than
    a fix -- and nothing else in the suite would notice.
    """
    body = _nav_rule()
    assert _declaration(body, "position") == "fixed"
    assert _declaration(body, "top") == "0"
    assert _declaration(body, "bottom") == "0"
