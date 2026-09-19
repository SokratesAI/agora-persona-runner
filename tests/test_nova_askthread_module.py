"""The ask thread renderer is its own file now (issue #233).

The sixteenth piece lifted out of `app.js` whole: `renderAskThread`, which
paints a whole conversation thread in the chat dock and under a journal
card's ask, with `askPaintThread` (pairs each answer with the question above
it), `lostTurn`/`askLost` (the note drawn when an answer never came) and
`tailIsWorking`. It also publishes `window.novaChat` for `message.js` and
`thread.js`. What these tests pin is the wiring, because that is what a move
can silently break: the file has to be served, loaded before `app.js`,
precached, and inside the update stamp.

The seam is asserted by name rather than by count: `askthread.js` takes
seventeen names from `app.js` and hands one back.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

SHARED = ("OWNER_RECORD", "PENDING_CLOCK_AFTER_SECONDS", "appendRichText",
          "askCopyButton", "askElapsed", "askMessage", "askOrbit",
          "askPending", "askPendingSeconds", "askQuip", "askRetryButton", "chatTime",
          "el", "openMessageActions", "openStepSheet", "refreshStepSheet",
          "stepMessageKey", "stepsLabel")

HANDED_BACK = ("renderAskThread",)

MOVED = ("function askPaintThread(", "function renderAskThread(",
         "function lostTurn(", "function askLost(", "function tailIsWorking(",
         "window.novaChat = {")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_askthread_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/askthread.js"] == "askthread.js"
    page = read("index.html")
    assert page.index('src="/bubble.js"') < page.index('src="/askthread.js"')
    assert page.index('src="/askthread.js"') < page.index('src="/app.js"')


def test_askthread_js_is_precached_and_stamped():
    assert '"/askthread.js"' in read("sw.js")
    assert "askthread.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"


def test_the_moved_code_is_all_in_askthread_js():
    module = read("askthread.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in askthread.js"


def test_every_name_the_module_borrows_is_passed_in():
    module = read("askthread.js")
    app = read("app.js")
    call = app[app.index("window.novaAskThread({"):]
    call = call[:call.index("})") + 2]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_every_name_handed_back_is_bound_in_app_js():
    module = read("askthread.js")
    app = read("app.js")
    for name in HANDED_BACK:
        assert "      %s: %s," % (name, name) in module, name
        assert "  var %s = askThreadModule.%s;" % (name, name) in app, name


def test_it_is_bound_after_the_modules_it_reads_from():
    """`askMessage`, `askPending`, `openStepSheet` and `askRetryButton` are
    handed over as values. Bound above any of their modules, the thread would
    hold `undefined` and the first thread painted would throw."""
    app = read("app.js")
    bound = app.index("var askThreadModule = window.novaAskThread")
    assert app.index("var askMessage = bubbleModule.askMessage;") < bound
    assert app.index("var askPending = bubbleModule.askPending;") < bound
    assert app.index("var openStepSheet = stepsModule.openStepSheet;") < bound
    assert app.index("var askRetryButton = askModule.askRetryButton;") < bound
