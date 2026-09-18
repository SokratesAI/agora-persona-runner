"""The home page and the galaxy are their own file now (issue #233, step 17).

The ninth piece lifted out of `app.js` whole. What these tests pin is the
wiring, because that is what a move can silently break: the file has to be
served, loaded before `app.js`, precached so `/` still opens offline, and
inside the update stamp so a deploy that changes only this file still offers
him a reload. What the pages draw is unchanged.

The seam is asserted by name rather than by count: `home.js` takes twelve
names from `app.js` and hands two back. Two of the twelve are values rather
than functions, and each has its own test below, because each is a way the
move could break with every page still drawing.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

#: Every name `home.js` reads off its one argument. Kept here rather than
#: derived from the file, so that deleting a line from the module is a test
#: failure instead of a shorter list that still agrees with itself.
SHARED = (
    "POLL_MS", "el", "feed", "fetchPage", "homeRecapFold", "livePolls",
    "markNav", "renderRecap", "route", "statusEl", "stopPolling", "wordmark",
)

MOVED = ("var galaxyFrame = null;", "function stopGalaxy(",
         "function loadHome(", "function renderHomeProject(",
         "function healthConcerns(", "function renderHealthLine(",
         "function renderHome(", "function loadHomeStrip(",
         "function renderHomeStrip(", "function loadGalaxy(",
         "function galaxySeed(", "function galaxyAge(",
         "function renderGalaxy(", "function galaxyPageHeight(",
         "function galaxyStripHeight(", "function galaxyColour(",
         "function drawGalaxy(", "function drawBodies(")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_home_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/home.js"] == "home.js"
    page = read("index.html")
    assert page.index('src="/home.js"') < page.index('src="/app.js"')


def test_home_js_is_precached_and_stamped():
    assert '"/home.js"' in read("sw.js")
    assert "home.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    """The move has to be a move. A copy left behind is two home pages."""
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"
    assert "window.novaHome(" in app


def test_the_moved_code_is_all_in_home_js():
    module = read("home.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in home.js"


def test_every_name_the_module_borrows_is_passed_in():
    module = read("home.js")
    app = read("app.js")
    call = app[app.index("window.novaHome({"):]
    call = call[:call.index("});") + 3]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_the_router_gets_both_pages_back():
    app = read("app.js")
    assert "loadHome = homePages.loadHome;" in app
    assert "loadGalaxy = homePages.loadGalaxy;" in app
    module = read("home.js")
    assert "loadHome: loadHome," in module
    assert "loadGalaxy: loadGalaxy," in module


def test_bound_after_poll_ms_is_assigned():
    """`POLL_MS` is handed over as a value and assigned near the bottom of
    `app.js`. Bound before that line, the strip would poll on
    `setTimeout(fn, undefined)` -- every 0ms, for ever."""
    app = read("app.js")
    assert app.index("var POLL_MS = ") < app.index("window.novaHome({")
    assert app.index("window.novaHome({") < app.rindex("\n  load();")


def test_stop_polling_empties_the_shared_array_in_place():
    """`livePolls` is handed to `home.js` and `beats.js` as a value, so it has
    to stay the same array. `stopPolling` used to assign a fresh `[]`, which
    left both modules pushing timers onto the old one where nothing ever
    cleared them again."""
    app = read("app.js")
    body = app[app.index("function stopPolling() {"):]
    body = body[:body.index("\n  }")]
    assert "livePolls.length = 0;" in body
    assert not re.search(r"livePolls\s*=\s*\[", body)


def test_a_tab_without_the_new_file_still_boots():
    """A cached tab holding this build's `app.js` and no `home.js` yet."""
    app = read("app.js")
    assert "if (window.novaHome) {" in app
    assert "loadHome = function () {};" in app
    assert "loadGalaxy = function () {};" in app
