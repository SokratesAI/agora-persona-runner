"""The Beats, Catalog and Alerts pages are their own file now (issue #233, step 15).

The seventh piece lifted out of `app.js` whole, after the diagrams, the attach
button, the chat dock, the charts, the diagnostics page and the project pages.
What these tests pin is the wiring, because that is what a move can silently
break: the file has to be served, loaded before `app.js`, precached so
`/heartbeats` still opens offline, and inside the update stamp so a deploy that
changes only this file still offers him a reload. What the three pages draw is
unchanged.

Two things here are not boilerplate. The seam: `beats.js` takes thirteen names
from `app.js` and hands three back, and both directions are asserted by name
rather than by count, because a name dropped from the argument is a
`ReferenceError` the first time he opens `/heartbeats` and a name dropped from
the return is a router that navigates to a blank page. And the bind order:
this module is the first one that could not be bound where its code used to
sit, because it reads `POLL_MS`, which `app.js` declares further down its own
body. A bind above that line hands the module `undefined` and the Beats page
polls on `setTimeout(fn, undefined)` -- every 0ms, forever, on his phone. No
test that reads either file alone can see that, so it is asserted here as an
ordering between three lines of `app.js`.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

#: Every name `beats.js` reads off its one argument. Kept here rather than
#: derived from the file, so that deleting a line from the module is a test
#: failure instead of a shorter list that still agrees with itself.
SHARED = (
    "ASK_POLL_MAX", "ASK_POLL_MS", "POLL_MS", "el", "feed", "fetchPage",
    "fmtStamp", "livePolls", "markNav", "route", "statusEl", "stopPolling",
    "wordmark",
)

#: What the router still reaches into this module for.
RETURNED = ("loadAlerts", "loadCatalog", "loadHeartbeats")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_beats_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/beats.js"] == "beats.js"
    page = read("index.html")
    assert page.index('src="/beats.js"') < page.index('src="/app.js"')


def test_beats_js_is_precached_and_stamped():
    assert '"/beats.js"' in read("sw.js")
    assert "beats.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    """The move has to be a move. A copy left behind is two Beats pages."""
    app = read("app.js")
    for gone in ("function renderHeartbeats(", "function renderCatalog(",
                 "function renderAlerts(", "function hbStateLine(",
                 "function catalogCard(", "function marcusStopCard("):
        assert gone not in app, gone + " is still in app.js"
    assert "window.novaBeats(" in app


def test_the_moved_code_is_all_in_beats_js():
    module = read("beats.js")
    for kept in ("function renderHeartbeats(", "function renderCatalog(",
                 "function renderAlerts(", "function hbStateLine(",
                 "function catalogCard(", "function marcusStopCard("):
        assert kept in module, kept + " did not arrive in beats.js"


def test_every_name_the_module_borrows_is_passed_in():
    """Both halves of the seam, because they fail in different places.

    A name the module reads but `app.js` never passes is `undefined` at the
    first line that uses it -- a blank `/heartbeats` for him and nothing at
    all for a Python test that only reads `app.js`.
    """
    module = read("beats.js")
    app = read("app.js")
    call = app[app.index("window.novaBeats({"):]
    call = call[:call.index("});") + 3]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_the_router_gets_all_three_pages_back():
    app = read("app.js")
    module = read("beats.js")
    for name in RETURNED:
        assert "%s = beatsPages.%s;" % (name, name) in app, name
        assert "%s: %s," % (name, name) in module, name


def test_the_module_is_bound_after_poll_ms_is_assigned():
    """`POLL_MS` is passed by value, so the bind has to come after its line.

    `var` hoists and the assignment does not: bound above `var POLL_MS =
    30000;` the module receives `undefined`, and `setTimeout(loadHeartbeats,
    undefined)` re-runs immediately rather than in thirty seconds. Both files
    parse and every other test here passes in that state, which is why this
    reads the two line numbers directly.
    """
    app = read("app.js")
    assert app.index("var POLL_MS = 30000;") < app.index("window.novaBeats({")
    assert app.index("window.novaBeats({") < app.index("\n  load();")


def test_a_tab_without_the_new_file_still_boots():
    """A cached tab holding this build's `app.js` and no `beats.js` yet.

    Without the guard the whole app fails to boot over three missing pages,
    which is a worse outcome than three pages that draw nothing until the
    service worker catches up.
    """
    app = read("app.js")
    assert "if (window.novaBeats) {" in app
    assert "loadHeartbeats = function () {};" in app
