"""A bare URL in the twelve-hour recap wraps inside its card.

The owner, `issues.md` 2026-09-21, with a phone screenshot: *"Urls in the 12h
summary goes out of screen."* A bare URL keeps its own text as its label
(`nova_recap._append_plain`), and a URL has no space to break at, so
`https://github.com/SokratesAI/lyceum/pull/8` ran past the card's right edge.

Measured in a real browser at 412px wide against the live site, with that
bullet injected into `/api/home`: the link's right edge at 405px against the
card's 396px before, 371px after. This test is textual for the reason
`test_nav_drawer_scrolls.py` gives -- jsdom does no layout -- and it guards
the one line that would silently undo the fix.
"""

import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"


def test_recap_item_breaks_long_words():
    rules = re.findall(r"(?m)^\.recap-item\s*\{([^}]*)\}", CSS.read_text())
    assert rules, "no .recap-item rule in style.css"
    assert any(re.search(r"overflow-wrap:\s*anywhere", body) for body in rules)
