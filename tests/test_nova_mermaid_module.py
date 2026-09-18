"""Diagrams are their own file now (issue #233, step 10).

The first piece lifted out of `app.js` whole rather than converted. What
these tests pin is the wiring, because that is what a move can silently
break: the file has to be served, loaded before `app.js`, precached so it
works offline, and inside the update stamp so a deploy that changes only
this file still offers him a reload. The drawing itself is unchanged and
is covered where it always was, in `tests/browser/app.test.mjs`.
"""

import os

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_mermaid_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/mermaid.js"] == "mermaid.js"
    page = read("index.html")
    assert page.index('src="/mermaid.js"') < page.index('src="/app.js"')


def test_mermaid_js_is_precached_and_stamped():
    assert '"/mermaid.js"' in read("sw.js")
    assert "mermaid.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_drawing_code():
    """The move has to be a move. A copy left behind is two renderers."""
    # The one caller, `appendRichText`, moved on into `richtext.js` (#233).
    app = read("app.js")
    richtext = read("richtext.js")
    for gone in ("function splitMermaidBlocks", "function mermaidNode",
                 "function ensureMermaid", "function mermaidSvg"):
        assert gone not in app, gone
        assert gone not in richtext, gone
    assert "window.novaMermaid.split(" in richtext
    assert "window.novaMermaid.node(" in richtext


def test_mermaid_js_takes_el_from_app_rather_than_copying_it():
    """One definition of `el`, the way message.js already borrows its helpers."""
    module = read("mermaid.js")
    assert "window.novaChat.el" in module
    assert "function el(" not in module
    assert "el: el" in read("app.js")
