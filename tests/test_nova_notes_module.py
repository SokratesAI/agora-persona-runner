"""The Notes page is its own file now (issue #233, step 16).

The eighth piece lifted out of `app.js` whole, after the diagrams, the attach
button, the chat dock, the charts, the diagnostics page, the project pages and
the Beats pages. What these tests pin is the wiring, because that is what a
move can silently break: the file has to be served, loaded before `app.js`,
precached so `/notes` still opens offline, and inside the update stamp so a
deploy that changes only this file still offers him a reload. What the page
draws is unchanged.

The seam is asserted by name rather than by count: `notes.js` takes twenty
names from `app.js` and hands one back. A name dropped from the argument is a
`ReferenceError` the first time he opens `/notes`, and a name dropped from the
return is a router that navigates to a blank page.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

#: Every name `notes.js` reads off its one argument. Kept here rather than
#: derived from the file, so that deleting a line from the module is a test
#: failure instead of a shorter list that still agrees with itself. `json` is
#: on it although no line calls `json(` -- the page hands it over as a value,
#: `.then(json)`, which is how the previous split lost it.
SHARED = (
    "OWNER_LABEL", "bindHoldMenu", "buildCaptureEditor", "captureHome",
    "closeActionSheet", "convertButtons", "el", "feed", "fetchPage", "json",
    "load", "loadWhenScrolledTo", "markNav", "openActionSheet", "renderBlocks",
    "route", "savedCopyLine", "statusEl", "stopPolling", "wordmark",
)

MOVED = ("function renderNoteMessage(", "function noteActions(",
         "function moveCaptureInto(", "function renderNotes(",
         "function scrollNotesToLatest(", "function showOlderNotes(",
         "function watchForOlderNotes(", "function loadNotes(",
         "var NOTES_PAGE = 12;")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_notes_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/notes.js"] == "notes.js"
    page = read("index.html")
    assert page.index('src="/notes.js"') < page.index('src="/app.js"')


def test_notes_js_is_precached_and_stamped():
    assert '"/notes.js"' in read("sw.js")
    assert "notes.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    """The move has to be a move. A copy left behind is two Notes pages."""
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"
    assert "window.novaNotes(" in app


def test_the_moved_code_is_all_in_notes_js():
    module = read("notes.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in notes.js"


def test_every_name_the_module_borrows_is_passed_in():
    module = read("notes.js")
    app = read("app.js")
    call = app[app.index("window.novaNotes({"):]
    call = call[:call.index("});") + 3]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_the_router_gets_the_page_back():
    app = read("app.js")
    assert "loadNotes = notesPage.loadNotes;" in app
    assert "loadNotes: loadNotes," in read("notes.js")


def test_a_tab_without_the_new_file_still_boots():
    """A cached tab holding this build's `app.js` and no `notes.js` yet."""
    app = read("app.js")
    assert "if (window.novaNotes) {" in app
    assert "loadNotes = function () {};" in app
