"""The held-row editor on the boards is its own file now (issue #233).

The seventeenth piece lifted out of `app.js` whole: `renderRowEditor` (the
title box a held board row turns into), `renderProjectPicker` and the cached
project index behind it (`loadProjects`), `renderRowConversation` (the
bubbles under a row's write-up) and `HOLD_MS`. What these tests pin is the
wiring, because that is what a move can silently break: the file has to be
served, loaded before `app.js`, precached, and inside the update stamp.

The seam is asserted by name rather than by count: `rowedit.js` takes five
names from `app.js` and hands four back.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

SHARED = ("el", "json", "loadBoard", "ownerLabel", "renderBlocks")

HANDED_BACK = ("HOLD_MS", "loadProjects", "renderRowConversation",
               "renderRowEditor")

MOVED = ("var HOLD_MS = 1000;", "function loadProjects(",
         "function forgetProjects(", "function renderProjectPicker(",
         "function renderRowEditor(", "function renderRowConversation(")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_rowedit_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/rowedit.js"] == "rowedit.js"
    page = read("index.html")
    assert page.index('src="/askthread.js"') < page.index('src="/rowedit.js"')
    assert page.index('src="/rowedit.js"') < page.index('src="/app.js"')


def test_rowedit_js_is_precached_and_stamped():
    assert '"/rowedit.js"' in read("sw.js")
    assert "rowedit.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"


def test_the_moved_code_is_all_in_rowedit_js():
    module = read("rowedit.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in rowedit.js"


def test_every_name_the_module_borrows_is_passed_in():
    module = read("rowedit.js")
    app = read("app.js")
    call = app[app.index("window.novaRowEdit({"):]
    call = call[:call.index("})") + 2]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_every_name_handed_back_is_bound_in_app_js():
    module = read("rowedit.js")
    app = read("app.js")
    for name in HANDED_BACK:
        assert "      %s: %s," % (name, name) in module, name
        assert "  var %s = rowEditModule.%s;" % (name, name) in app, name


def test_it_is_bound_after_rich_text_and_before_its_readers():
    """`renderBlocks` is handed over as a value, so the module has to be bound
    below `richtext.js`; and `project.js` takes `renderRowConversation` as a
    value while `app.js` loads, so it has to be bound above that."""
    app = read("app.js")
    bound = app.index("var rowEditModule = window.novaRowEdit")
    assert app.index("var renderBlocks = richTextModule.renderBlocks;") < bound
    assert bound < app.index("var projectPages = window.novaProject({")
