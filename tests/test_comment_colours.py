"""The rule down the left of a comment says *who is talking*, and nothing else.

Edvard, comments board 2026-08-21, with a screenshot of his own thread:
*"my previous comments in this chat gets a grey backline, i want to have it
green all the time so i clearly see it's my comments."* The grey was
`.comment.is-acknowledged`, which fired once a cycle had read the comment --
so the colour was carrying two meanings at once, whose comment it is and
whether it has been read, and on a thread he had been in all day the second
one won every time.

And ideas board the same day: *"Give Nova cycle comments a purple
background/border in the app (mine is green, commentator is blue, Nova should
be purple)"*.

A stylesheet has no other instrument. Neither `pytest` nor the jsdom suite
computes cascade, so a rule that quietly reintroduces the grey passes
everything: the class name is still there, the element is still rendered, and
the only place the regression shows is on his phone. These two asserts are
narrow on purpose -- they pin the one declaration each ask is about, not the
shape of the file around it.
"""

import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"


def _declarations(selector):
    """Every declaration inside the blocks whose selector list contains `selector`."""
    out = []
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", CSS.read_text(encoding="utf-8")):
        heads = [h.strip() for h in match.group(1).split(",")]
        if any(h.endswith(selector) or selector in h.split() for h in heads):
            out.append(match.group(2))
    return out


def test_a_read_comment_keeps_his_green_rule():
    for block in _declarations(".comment.is-acknowledged"):
        assert "border-left-color" not in block, (
            "acknowledging his comment must not change the colour of its rule -- "
            "the READ chip is what says read"
        )


def test_a_cycle_reply_is_purple_and_the_reply_worker_is_not():
    blocks = _declarations(".comment-reply-cycle")
    assert blocks, ".comment-reply-cycle is what app.js puts on a cycle's answer"
    assert any("var(--nova)" in block for block in blocks)
    for block in _declarations(".comment-reply"):
        assert "var(--nova)" not in block, "the instant reply worker stays blue"
