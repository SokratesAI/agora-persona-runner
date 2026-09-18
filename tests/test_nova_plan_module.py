"""The `/plan` page is its own file now (issue #233, step 18).

The tenth piece lifted out of `app.js` whole. What these tests pin is the
wiring, because that is what a move can silently break: the file has to be
served, loaded before `app.js`, precached so `/plan` still opens offline, and
inside the update stamp so a deploy that changes only this file still offers
him a reload. What the page draws is unchanged.

The seam is asserted by name rather than by count: `plan.js` takes twelve
names from `app.js` and hands one back.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

SHARED = (
    "el", "feed", "fetchPage", "json", "markNav", "renderBlocks",
    "renderSpans", "route", "savedCopyLine", "statusEl", "stopPolling",
    "wordmark",
)

MOVED = ("var SPARK_W = 240;", "function svgEl(", "function sparkPoints(",
         "function goalSparkline(", "function scoreboardRow(",
         "var GOAL_STATE_WORDS = {", "function goalVerdict(",
         "function renderScoreboard(", "function rankedCard(",
         "function renderRanked(", "function planSection(",
         "function renderCoverage(", "function renderPlanDocument(",
         "function nextRow(", "function renderNextUp(",
         "function renderPlan(", "function loadPlan(")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_plan_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/plan.js"] == "plan.js"
    page = read("index.html")
    assert page.index('src="/plan.js"') < page.index('src="/app.js"')


def test_plan_js_is_precached_and_stamped():
    assert '"/plan.js"' in read("sw.js")
    assert "plan.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    """The move has to be a move. A copy left behind is two plan pages."""
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"
    assert "window.novaPlan(" in app


def test_the_moved_code_is_all_in_plan_js():
    module = read("plan.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in plan.js"


def test_every_name_the_module_borrows_is_passed_in():
    module = read("plan.js")
    app = read("app.js")
    call = app[app.index("window.novaPlan({"):]
    call = call[:call.index("})") + 2]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_the_router_gets_the_page_back():
    assert "}).loadPlan;" in read("app.js")
    assert "loadPlan: loadPlan," in read("plan.js")


def test_the_borrowed_values_are_never_reassigned():
    """`feed` and `statusEl` are handed over as values. A later
    `feed = ...` in `app.js` would leave this page drawing into the old
    node while every other page drew into the new one."""
    app = read("app.js")
    for name in ("feed", "statusEl"):
        assigned = re.findall(r"(?m)(?:^|[^.\w])%s\s*=[^=]" % name, app)
        assert len(assigned) == 1, (name, assigned)
