"""The Pool page and the project page are their own file now (issue #233, step 14).

The sixth piece lifted out of `app.js` whole, after the diagrams, the attach
button, the chat dock, the charts and the diagnostics pages -- and the largest,
about 96 KB. What these tests pin is the wiring, because that is what a move
can silently break: the file has to be served, loaded before `app.js`,
precached so a project page still opens offline, and inside the update stamp
so a deploy that changes only this file still offers him a reload. What the
two pages draw is unchanged.

The one thing here that is not boilerplate is the seam. `project.js` takes
twelve names from `app.js` and hands two back, and both directions are
asserted by name rather than by count: a name dropped from the argument is a
`ReferenceError` the first time he opens a project, which no Python test would
otherwise see, and a name dropped from the return is a router that navigates
to a blank page.

The two pages travel together on purpose. The project page draws a project's
own candidate deck through `renderPool`, so a cut between them would leave
`renderPool` in `app.js` with both of its callers in here.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

#: Every name `project.js` reads off its one argument. Kept here rather than
#: derived from the file, so that deleting a line from the module is a test
#: failure instead of a shorter list that still agrees with itself.
SHARED = (
    "OWNER_RECORD", "el", "feed", "fetchPage", "json", "load", "markNav",
    "renderRowConversation", "route", "statusEl", "stopPolling", "wordmark",
)

#: Declarations that have to be on exactly one side of the move. Chosen to
#: span the whole block rather than its edges: the pool deck, the standings
#: list, a drawer, the roadmap, the drag plumbing and both entry points.
MOVED = (
    "function renderPool(",
    "function renderPoolHistory(",
    "function renderProjectStandings(",
    "function milestoneDrawer(",
    "function renderProjectRoadmap(",
    "function attachRowDrag(",
    "function renderProject(",
    "function loadProject(",
    "function loadPool(",
)


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_project_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/project.js"] == "project.js"
    page = read("index.html")
    assert page.index('src="/project.js"') < page.index('src="/app.js"')


def test_project_js_is_precached_and_stamped():
    assert '"/project.js"' in read("sw.js")
    assert "project.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    """The move has to be a move. A copy left behind is two project pages."""
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"
    assert "window.novaProject(" in app


def test_the_moved_code_is_all_in_project_js():
    module = read("project.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in project.js"


def test_every_name_the_module_borrows_is_passed_in():
    """Both halves of the seam, because they fail in different places.

    A name the module reads but `app.js` never passes is `undefined` at the
    first line that uses it -- a blank project page for him and nothing at all
    for a Python test that only reads `app.js`.
    """
    module = read("project.js")
    app = read("app.js")
    call = app[app.index("window.novaProject({"):]
    call = call[:call.index("});") + 3]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_the_router_gets_both_pages_back():
    app = read("app.js")
    assert "loadPool = projectPages.loadPool;" in app
    assert "loadProject = projectPages.loadProject;" in app
    module = read("project.js")
    assert "loadPool: loadPool," in module
    assert "loadProject: loadProject," in module


def test_a_tab_without_the_new_file_still_boots():
    """A cached tab holding this build's `app.js` and no `project.js` yet.

    Without the guard the whole app fails to boot over one missing page,
    which is a worse outcome than a project page that draws nothing until the
    service worker catches up.
    """
    app = read("app.js")
    assert "if (window.novaProject) {" in app
    assert "loadPool = function () {};" in app
    assert "loadProject = function () {};" in app
