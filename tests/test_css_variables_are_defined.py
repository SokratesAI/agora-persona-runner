"""Every `var(--x)` in the stylesheet names a variable that exists.

Found by hand on 2026-08-15 while adding the comment composer (idea #64):
six rules said `color: var(--fg)` and nothing anywhere defined `--fg`.
The palette calls it `--text`.

**This is the class of bug the whole test suite is structurally blind
to**, which is why it gets a test of its own rather than a fix and a
comment. An undefined custom property is not a CSS parse error -- the
declaration is thrown away at computed-value time and the property falls
back to its inherited or initial value. For `color` on a `<div>` that is
invisible, because the inherited value is the one you wanted anyway. On a
form control it is not: `input`, `select`, `textarea` and `button` do not
inherit `color` from the page, so all six of these fell back to the
user-agent default -- near-black text on this stylesheet's `#12131a`
background. One of them is the box the owner types a row title into.

No renderer complains, no test that asserts a class name notices, and a
screenshot only catches it if the affected control happens to be on
screen and filled in. A previous cycle shipped `var(--bad)` the same way
and caught it only on a manual re-read.
"""

import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"

# A declaration, not a use: `--x:` at the start of a declaration.
_DEFINED_RE = re.compile(r"(--[A-Za-z0-9-]+)\s*:")
# `var(--x)` and `var(--x, fallback)`.
_USED_RE = re.compile(r"var\(\s*(--[A-Za-z0-9-]+)\s*([,)])")


def _text():
    return CSS.read_text(encoding="utf-8")


def test_every_variable_used_is_defined_somewhere():
    css = _text()
    defined = set(_DEFINED_RE.findall(css))
    missing = sorted({
        name for name, after in _USED_RE.findall(css)
        # `var(--x, something)` carries its own fallback and is safe by
        # construction, so an undefined name there is a style question
        # rather than an invisible control.
        if after == ")" and name not in defined
    })
    assert not missing, (
        f"used but never defined: {missing}. An undefined custom property is "
        "not a parse error -- the declaration is dropped and a form control "
        "falls back to the user-agent colour, which is black on this palette."
    )


def test_the_palette_still_defines_the_names_this_file_assumes():
    """A guard on the guard: if `:root` were emptied, the check above
    would pass by finding no uses either. These are the names the
    stylesheet is built on."""
    defined = set(_DEFINED_RE.findall(_text()))
    for name in ["--bg", "--card", "--line", "--text", "--dim", "--accent", "--warn"]:
        assert name in defined, name


def test_fg_specifically_is_gone():
    """The six rules this test file was written for. Named explicitly so
    a reintroduction says what it is, rather than only failing the
    general check with a bare variable name."""
    assert "var(--fg)" not in _text()
