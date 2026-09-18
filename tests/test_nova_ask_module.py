"""The ask thread's helpers are their own file now (issue #233, step 20).

The twelfth piece lifted out of `app.js` whole: the polling constants, the
sent-but-not-yet-echoed rows, the copy and retry buttons, the "he is
watching" pings and the one-line thread note. What these tests pin is the
wiring, because that is what a move can silently break: the file has to be
served, loaded before `app.js`, precached, and inside the update stamp.

The seam is asserted by name rather than by count: `ask.js` takes four names
from `app.js` and hands ten back. It is bound where the block used to sit,
and every use of what it hands back -- the beats pages, the chat dock,
`window.novaChat` -- loads after that point.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

SHARED = ("OWNER_RECORD", "askMessage", "askPending", "el")

HANDED_BACK = (
    "ASK_POLL_MAX", "ASK_POLL_MS", "STICK_SLOP_PX", "askCopyButton",
    "askPaintNote", "askPaintSent", "askRetryButton", "mergePendingSends",
    "pingAskWatching", "pingConvWatching",
)

MOVED = ("var ASK_POLL_MS = 4000;", "function pingAskWatching(",
         "function pingConvWatching(", "function copyToClipboard(",
         "function askCopyButton(", "function askRetryButton(",
         "function mergePendingSends(", "function askPaintSent(",
         "function askPaintNote(")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_ask_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/ask.js"] == "ask.js"
    page = read("index.html")
    assert page.index('src="/ask.js"') < page.index('src="/app.js"')


def test_ask_js_is_precached_and_stamped():
    assert '"/ask.js"' in read("sw.js")
    assert "ask.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"


def test_the_moved_code_is_all_in_ask_js():
    module = read("ask.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in ask.js"


def test_every_name_the_module_borrows_is_passed_in():
    module = read("ask.js")
    app = read("app.js")
    call = app[app.index("window.novaAsk({"):]
    call = call[:call.index("})") + 2]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_every_name_handed_back_is_bound_in_app_js():
    module = read("ask.js")
    app = read("app.js")
    for name in HANDED_BACK:
        assert "      %s: %s," % (name, name) in module, name
        assert "  var %s = askModule.%s;" % (name, name) in app, name


def test_it_is_bound_before_the_load_time_readers():
    """The beats pages and `window.novaChat` take these as values while
    `app.js` loads. Bound any later, they get `undefined`."""
    app = read("app.js")
    bound = app.index("var askModule = window.novaAsk")
    assert bound < app.index("window.novaChat = {")
    assert bound < app.index("window.novaBeats({")
