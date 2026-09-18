"""The chat bubble renderer is its own file now (issue #233).

The fifteenth piece lifted out of `app.js` whole: `askMessage`, which draws
one message in the dock, a conversation thread or a journal card's ask, and
`askPending`, the loader under a question that has no answer yet, with the
clock and the orbit it draws. What these tests pin is the wiring, because
that is what a move can silently break: the file has to be served, loaded
before `app.js`, precached, and inside the update stamp.

The seam is asserted by name rather than by count: `bubble.js` takes eight
names from `app.js` and hands seven back. It reads `askCopyButton` and
`askRetryButton` from `ask.js`, which reads `askMessage` and `askPending`
back from it, so `ask.js` is handed call-time wrappers.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

SHARED = ("OWNER_RECORD", "appendRichText", "askCopyButton", "askRetryButton",
          "el", "openMessageActions", "stepMessageKey", "stepsLine")

HANDED_BACK = ("PENDING_CLOCK_AFTER_SECONDS", "askElapsed", "askMessage",
               "askOrbit", "askPending", "askPendingSeconds", "chatTime")

MOVED = ("function chatTime(", "function askMessage(", "function askElapsed(",
         "function askPendingSeconds(", "function orbitPhase(",
         "function askOrbit(", "function askPending(")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_bubble_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/bubble.js"] == "bubble.js"
    page = read("index.html")
    assert page.index('src="/bubble.js"') < page.index('src="/app.js"')


def test_bubble_js_is_precached_and_stamped():
    assert '"/bubble.js"' in read("sw.js")
    assert "bubble.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"


def test_the_moved_code_is_all_in_bubble_js():
    module = read("bubble.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in bubble.js"


def test_every_name_the_module_borrows_is_passed_in():
    module = read("bubble.js")
    app = read("app.js")
    call = app[app.index("window.novaBubble({"):]
    call = call[:call.index("})") + 2]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_every_name_handed_back_is_bound_in_app_js():
    module = read("bubble.js")
    app = read("app.js")
    for name in HANDED_BACK:
        assert "      %s: %s," % (name, name) in module, name
        assert "  var %s = bubbleModule.%s;" % (name, name) in app, name


def test_it_is_bound_after_the_modules_it_reads_from():
    """`askCopyButton`, `askRetryButton`, `stepMessageKey` and `stepsLine`
    are handed over as values. Bound above either module, the bubble would
    hold `undefined` and the first message drawn would throw."""
    app = read("app.js")
    bound = app.index("var bubbleModule = window.novaBubble")
    assert app.index("var askRetryButton = askModule.askRetryButton;") < bound
    assert app.index("var stepsLine = stepsModule.stepsLine;") < bound


def test_ask_js_gets_call_time_wrappers_not_the_values():
    """`ask.js` is bound before `bubble.js`, so passing `askMessage` itself
    would hand it `undefined` and every sent message would fail to draw."""
    app = read("app.js")
    call = app[app.index("window.novaAsk({"):]
    call = call[:call.index("}) : {};")]
    for name in ("askMessage", "askPending"):
        assert ("%s: function () { return %s.apply(null, arguments); }"
                % (name, name)) in call, name
