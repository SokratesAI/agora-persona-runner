"""The rich-text renderer is its own file now (issue #233).

The fourteenth piece lifted out of `app.js` whole: the code that turns a
comment, a journal entry or a board write-up into paragraphs, lists, links,
attachments and code blocks, and the scroll watcher that loads a long page as
it comes into view. What these tests pin is the wiring, because that is what
a move can silently break: the file has to be served, loaded before `app.js`,
precached, and inside the update stamp.

The seam is asserted by name rather than by count: `richtext.js` takes three
names from `app.js` and hands five back. `ATTACH_RE` is a value, so the
module is bound after the line that assigns it.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

SHARED = ("ATTACH_RE", "attachNode", "el")

HANDED_BACK = ("appendRichText", "loadWhenScrolledTo", "renderBlocks",
               "renderSpans", "stopScrollWatch")

MOVED = ("function appendRichText(", "function appendPlainText(",
         "function appendInlineText(", "function appendInlineMarks(",
         "function loadWhenScrolledTo(", "function stopScrollWatch(",
         "function renderSpans(", "function renderBlocks(")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_richtext_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/richtext.js"] == "richtext.js"
    page = read("index.html")
    assert page.index('src="/richtext.js"') < page.index('src="/app.js"')


def test_richtext_js_is_precached_and_stamped():
    assert '"/richtext.js"' in read("sw.js")
    assert "richtext.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"


def test_the_moved_code_is_all_in_richtext_js():
    module = read("richtext.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in richtext.js"


def test_every_name_the_module_borrows_is_passed_in():
    module = read("richtext.js")
    app = read("app.js")
    call = app[app.index("window.novaRichText({"):]
    call = call[:call.index("})") + 2]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_every_name_handed_back_is_bound_in_app_js():
    module = read("richtext.js")
    app = read("app.js")
    for name in HANDED_BACK:
        assert "      %s: %s," % (name, name) in module, name
        assert "  var %s = richTextModule.%s;" % (name, name) in app, name


def test_it_is_bound_after_the_regex_it_borrows():
    """`ATTACH_RE` is handed over as a value. Bound above its assignment,
    the module would hold `undefined` and every attachment would throw."""
    app = read("app.js")
    assert app.index("var ATTACH_RE = ") < app.index("var richTextModule = window.novaRichText")
