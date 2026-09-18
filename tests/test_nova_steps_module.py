"""The step sheet is its own file now (issue #233, step 19).

The eleventh piece lifted out of `app.js` whole: the collapsed "used N tools"
line under an answer and the bottom sheet it opens. What these tests pin is
the wiring, because that is what a move can silently break: the file has to
be served, loaded before `app.js`, precached, and inside the update stamp.

The seam is asserted by name rather than by count: `steps.js` takes three
names from `app.js` and hands eleven back. It is bound where the block used
to sit, because `actionSheet` and `stackedSheet` read the sheet-height
constants while `app.js` is still loading.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

SHARED = ("appendRichText", "el", "transitionMs")

HANDED_BACK = (
    "CAPTURE_SHEET_OPEN_VH", "STEP_SHEET_DISMISS_VH", "STEP_SHEET_MAX_VH",
    "STEP_SHEET_MIN_VH", "STEP_SHEET_OPEN_VH", "dragSheet", "openStepSheet",
    "refreshStepSheet", "stepMessageKey", "stepsLabel", "stepsLine",
)

MOVED = ("function stepsLabel(", "function stepMessageKey(",
         "function buildStepSheet(", "function dragStepSheet(",
         "function closeStepSheet(", "function stepRow(",
         "function showStepDetail(", "function showStepList(",
         "function refreshStepSheet(", "function openStepSheet(",
         "function stepsLine(")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_steps_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/steps.js"] == "steps.js"
    page = read("index.html")
    assert page.index('src="/steps.js"') < page.index('src="/app.js"')


def test_steps_js_is_precached_and_stamped():
    assert '"/steps.js"' in read("sw.js")
    assert "steps.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"


def test_the_moved_code_is_all_in_steps_js():
    module = read("steps.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in steps.js"


def test_every_name_the_module_borrows_is_passed_in():
    module = read("steps.js")
    app = read("app.js")
    call = app[app.index("window.novaSteps({"):]
    call = call[:call.index("})") + 2]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_every_name_handed_back_is_bound_in_app_js():
    module = read("steps.js")
    app = read("app.js")
    for name in HANDED_BACK:
        assert "      %s: %s," % (name, name) in module, name
        assert "  var %s = stepsModule.%s;" % (name, name) in app, name


def test_it_is_bound_before_the_load_time_readers():
    """`actionSheet` reads `STEP_SHEET_MIN_VH` while `app.js` loads. Bound
    any later, that read is `undefined` and the sheet has no height."""
    app = read("app.js")
    assert app.index("var stepsModule = window.novaSteps") < app.index(
        "var actionSheet = ")
