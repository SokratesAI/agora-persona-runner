"""The text of a message in a conversation is never `--dim`.

The owner, `issues.md` 2026-08-25: *"The grey text for the notes in Nova is
heard to read against a black background. Make it white."*

Two rules put it there, and they were one decision applied twice: the notes
page dimmed a cycle's reply and the journal card's comment drawer dimmed
Nova's reply, each reasoned as "his words are the ones the page is for" and
the second cited by name in the first's comment. Both are now `--text`.

`--dim` (#8b90a0) on `--bg` (#12131a) is 5.8:1. That clears WCAG AA for body
text, which is why no check here ever objected, and it is still the wrong
number for a phone in daylight -- and it was being spent on the half of the
page that answers his questions. Speaker was never carried by the body
colour alone: the left rule, the name, and the `Cycle N` link all say it.

So this asserts the class rather than the two rules, because the next
message body someone adds will be reasoned the same way. `.comment
.is-acknowledged .comment-body` is the one deliberate exception and it is
listed by name below -- it marks a *state* (he has dealt with it), not a
speaker, and dimming something already handled is the affordance working.
"""

import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"

# The one rule allowed to dim a message body, and why it is different is in
# the docstring above and in the stylesheet beside the rule itself.
ALLOWED_DIM_BODIES = {".comment.is-acknowledged .comment-body"}

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _rules():
    """Selector, declarations. Comments are stripped first, and that is not
    tidiness: the stylesheet now quotes the removed rules verbatim inside
    the comments explaining their removal, so a check that reads raw text
    would fail on its own explanation."""
    css = _COMMENT_RE.sub("", CSS.read_text(encoding="utf-8"))
    for match in _RULE_RE.finditer(css):
        yield " ".join(match.group(1).split()), match.group(2)


def test_no_message_body_is_dim():
    offenders = sorted(
        selector
        for selector, body in _rules()
        if selector.endswith("-body")
        and "var(--dim)" in body
        and selector not in ALLOWED_DIM_BODIES
    )
    assert not offenders, (
        f"these render a message body in --dim: {offenders}. --dim on --bg is "
        "5.8:1 -- it passes WCAG AA and the owner still could not read it "
        "(issues.md 2026-08-25). Who is speaking is already carried by the "
        "left rule, the name colour and the cycle link; use --text."
    )


def test_the_two_rules_this_was_written_for_are_named():
    """A guard on the guard. The check above passes trivially if the two
    selectors are ever renamed or the rules deleted outright, and it would
    report itself working while guarding nothing -- the failure shape this
    journal has now recorded four times. These two selectors must exist and
    must resolve to --text."""
    colours = {}
    for selector, body in _rules():
        match = re.search(r"(?<![-\w])color\s*:\s*([^;]+);", body)
        if match:
            colours[selector] = match.group(1).strip()
    for selector in [".note-msg-body", ".comment-body"]:
        assert selector in colours, f"{selector} no longer sets a colour at all"
        assert colours[selector] == "var(--text)", (selector, colours[selector])


def test_the_allowed_exception_still_exists():
    """If the acknowledged-comment rule is ever removed, the allowlist above
    is dead weight pointing at nothing and should go with it."""
    selectors = {selector for selector, _ in _rules()}
    assert ALLOWED_DIM_BODIES <= selectors, (
        f"allowlisted but no longer in the stylesheet: {ALLOWED_DIM_BODIES - selectors}"
    )
