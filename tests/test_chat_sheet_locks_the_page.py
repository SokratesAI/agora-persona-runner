"""The page behind the chat sheet does not scroll while the sheet is up.

The owner, `issues.md` 2026-08-31: *"When i have the chat modal open, i
should not be able to scroll the page its hovering over. Currently i can
and its wierd."*

`.chat-dock` is `position: fixed`, which takes it out of the flow and
leaves the document under it an ordinary scroller. So every gesture that
missed the thread -- the head, the composer, a flick past the last
message -- scrolled the journal behind the sheet, and closing the sheet
landed him somewhere he had never navigated to.

`body.chat-open` was already set by `setOpen` at every width, for the
hamburger rule inside the phone media query. The fix is the one
declaration that class was missing, and it is the same one
`body.nav-open` has carried for the menu drawer since long before this.

**This test is deliberately textual, and the real instrument is
`tools.poke_page chat-scroll-lock`.** The browser suite runs under jsdom,
which does no layout and resolves no `overflow`, so a computed assertion
here would pass whatever the stylesheet said. Measured in Chromium at the
owner's own 360x697 on 2026-08-31: a wheel gesture under an open sheet moved
the page the full 500px without the rule and 0px with it, in the same run
that proved the page still scrolls 500px while the sheet is shut. The
gesture has to be a wheel -- `overflow: hidden` stops the user and goes on
letting script scroll the box, so a `scrollTo` reads FAIL against the fix
as loudly as against the bug. What
this file can catch is the thing that would silently undo it -- someone
tidying an `overflow` line out of a one-declaration rule.
"""

import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "style.css"
APP = pathlib.Path(__file__).resolve().parents[1] / "agora_runner" / "nova_public" / "app.js"


def _rule(selector):
    css = CSS.read_text(encoding="utf-8")
    # Anchored at a line start so a longer selector sharing this prefix --
    # `body.chat-open .menu-btn` is a real one in this file -- cannot match.
    hit = re.search(r"\n" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert hit, f"no `{selector}` rule in style.css"
    return re.sub(r"/\*.*?\*/", "", hit.group(1), flags=re.S)


def _declaration(body, prop):
    hit = re.search(r"(?:^|;)\s*" + re.escape(prop) + r"\s*:([^;]+)", body, re.S)
    return hit.group(1).strip() if hit else None


def test_the_page_cannot_scroll_behind_the_chat_sheet():
    """Without this the journal scrolls under the sheet on every stray drag."""
    value = _declaration(_rule("body.chat-open"), "overflow")
    assert value == "hidden", (
        "`body.chat-open` must stop the document scrolling while the chat sheet "
        f"is up -- it is `position: fixed` and hides nothing. Found: {value!r}"
    )


def test_the_menu_drawer_still_locks_the_page_too():
    """The rule this one is modelled on, pinned so the pair cannot drift apart.

    If `body.nav-open` ever loses its lock, the sentence above -- "the same
    declaration the drawer has always had" -- stops being true, and the two
    sheets in this app start behaving differently for no reason a reader
    could find.
    """
    assert _declaration(_rule("body.nav-open"), "overflow") == "hidden"


def test_the_class_is_still_set_at_every_width():
    """The premise of the rule above.

    `setOpen` puts `chat-open` on `body` unconditionally. If that ever moves
    behind a width check -- it is only *read* inside a media query today, so
    that is a plausible tidy -- the lock would silently stop applying on a
    desktop, and no computed assertion in this repo could see it.
    """
    src = APP.read_text(encoding="utf-8")
    assert 'document.body.classList.toggle("chat-open", isOpen);' in src, (
        "`setOpen` no longer marks the body unconditionally, so "
        "`body.chat-open` cannot be relied on to hold the scroll lock"
    )
