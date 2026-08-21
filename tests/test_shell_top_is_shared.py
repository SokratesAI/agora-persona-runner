"""The three things at the top of the shell read one number, not three.

Edvard, `issues.md` 2026-08-21, 🔴 Immediately: *"I can't see the
hamburger sodemenu button anymore."*

`index.html` sets `viewport-fit=cover` and the manifest sets
`display: standalone`, so once the app is added to an iPhone home screen
the layout viewport starts at the top of the display and iOS paints the
status bar over the page. The hamburger was pinned at a flat `top:
1.6rem`, a 26px-66px box against a 47px notch or a 59px Dynamic Island
inset -- so the button was under the clock. `--shell-top` is
`max(1.6rem, env(safe-area-inset-top) + 0.5rem)`, which is unchanged
wherever the inset is zero.

Three declarations depend on that number: the wordmark's line
(`.status` padding-top), the button that centres on it (`.menu-btn`
top), and the drawer's first link, which has to clear the button
(`.nav` padding-top). Before this they were three literals held together
by a comment in `.menu-btn` asking the next author to keep them in step
-- "if either of those two numbers changes, this one has to change with
it". Nothing enforced that, and a stylesheet cannot be caught drifting
by a test that only asserts class names.

This test is deliberately textual rather than computed. jsdom, which
runs `tests/browser/app.test.mjs`, does no layout and resolves no
`env()`, and headless Chromium reports every safe-area inset as zero --
so the *only* renderer that can tell this fix worked is an actual
notched iPhone. What can be checked here is the thing that would silently
undo it: someone putting a literal back.
"""

import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"

_VAR = "--shell-top"


def _text():
    return CSS.read_text(encoding="utf-8")


def _declaration(selector, prop):
    """The value of `prop` inside the rule for `selector`.

    Shorthand counts: `.status` sets its top through `padding`, not
    `padding-top`, so a caller asking for `padding-top` is answered by
    the first component of `padding` when that is what the rule has.
    """
    css = _text()
    # `\n.status {` -- anchored at a line start so `.status-line` and
    # `.entry .status` cannot match, and non-greedy to the first `}`.
    rule = re.search(r"\n" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert rule, f"no rule for {selector} in style.css"
    # Comments first: every one of these three rules opens with a block
    # comment, and leaving them in makes `(?:^|;)` miss the declaration
    # that follows.
    body = re.sub(r"/\*.*?\*/", "", rule.group(1), flags=re.S)
    hit = re.search(r"(?:^|;)\s*" + re.escape(prop) + r"\s*:([^;]+)", body, re.S)
    if hit:
        return hit.group(1).strip()
    shorthand = prop.rsplit("-", 1)[0]
    hit = re.search(r"(?:^|;)\s*" + re.escape(shorthand) + r"\s*:([^;]+)", body, re.S)
    assert hit, f"{selector} sets neither {prop} nor {shorthand}"
    return _first_component(hit.group(1))


def _first_component(value):
    """The first space-separated component of a shorthand, counting
    parens -- `calc(var(--x) + 3rem) 1rem 1rem` is one component, not
    three, and a plain `.split()` cuts it in half."""
    depth = 0
    for i, ch in enumerate(value.strip()):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch.isspace() and depth == 0:
            return value.strip()[:i]
    return value.strip()


def test_shell_top_is_defined_from_the_safe_area_inset():
    css = _text()
    hit = re.search(re.escape(_VAR) + r"\s*:([^;]+)", css)
    assert hit, f"{_VAR} is not defined; the top of the shell has no single source"
    value = hit.group(1)
    assert "env(safe-area-inset-top" in value, (
        f"{_VAR} is {value.strip()!r} -- without env(safe-area-inset-top) the "
        "hamburger goes back under the iPhone status bar in standalone mode"
    )
    assert "max(" in value, (
        f"{_VAR} is {value.strip()!r} -- max() is what keeps this a no-op on "
        "every browser that reports a zero inset, which is all of them here"
    )


def test_the_three_shell_declarations_all_read_it():
    for selector, prop in (
        (".status", "padding-top"),
        (".menu-btn", "top"),
        (".nav", "padding-top"),
    ):
        value = _declaration(selector, prop)
        assert f"var({_VAR}" in value, (
            f"{selector} {{ {prop}: {value} }} does not read var({_VAR}). "
            "These three have to move together: the button centres on the "
            "wordmark, and the drawer's first link clears the button."
        )


def test_a_zero_inset_leaves_every_one_of_them_where_it_was():
    """The regression this fix could cause is moving the header on a
    desktop, where nothing was wrong. Each value has to reduce to its
    old literal when the inset is 0px: 1.6rem, 1.6rem, 4.6rem."""
    for selector, prop, was in (
        (".status", "padding-top", "1.6rem"),
        (".menu-btn", "top", "1.6rem"),
        (".nav", "padding-top", "4.6rem"),
    ):
        value = _declaration(selector, prop)
        assert _reduce_at_zero_inset(value) == was, (
            f"{selector} {{ {prop}: {value} }} resolves to "
            f"{_reduce_at_zero_inset(value)} at a zero inset, not {was} -- "
            "this fix is only safe to ship untested on an iPhone because it "
            "changes nothing where there is no inset"
        )


def _reduce_at_zero_inset(value):
    """Evaluate a `--shell-top` expression with every inset at 0px.

    Only the shapes this stylesheet actually uses: `var(--shell-top)`,
    `calc(var(--shell-top) + Nrem)`, and the variable's own
    `max(Arem, calc(env(...) + Brem))`. Anything else raises rather than
    quietly returning a number, because a wrong number here would pass.
    """
    hit = re.search(re.escape(_VAR) + r"\s*:([^;]+)", _text())
    base = hit.group(1).strip()
    inner = re.fullmatch(r"max\(\s*([\d.]+)rem\s*,\s*calc\(\s*env\([^)]*\)\s*\+\s*([\d.]+)rem\s*\)\s*\)", base)
    assert inner, f"{_VAR} is {base!r}, a shape this test cannot evaluate"
    # env() is 0px, so max(A, 0 + B) is whichever of A and B is larger.
    resolved = max(float(inner.group(1)), float(inner.group(2)))

    value = value.strip()
    if value == f"var({_VAR})":
        total = resolved
    else:
        plus = re.fullmatch(r"calc\(\s*var\(" + re.escape(_VAR) + r"\)\s*\+\s*([\d.]+)rem\s*\)", value)
        assert plus, f"{value!r} is a shape this test cannot evaluate"
        total = resolved + float(plus.group(1))
    return f"{total:g}rem"
