"""The attach button is its own file now (issue #233, step 11).

The second piece lifted out of `app.js` whole, after the diagrams. What
these tests pin is the wiring, because that is what a move can silently
break: the file has to be served, loaded before `app.js`, precached so a
composer still opens offline, and inside the update stamp so a deploy that
changes only this file still offers him a reload. The picking, uploading
and the tray of chips are unchanged and are covered where they always were,
in `tests/browser/app.test.mjs`.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_attach_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/attach.js"] == "attach.js"
    page = read("index.html")
    assert page.index('src="/attach.js"') < page.index('src="/app.js"')


def test_attach_js_is_precached_and_stamped():
    assert '"/attach.js"' in read("sw.js")
    assert "attach.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_attach_code():
    """The move has to be a move. A copy left behind is two upload paths."""
    app = read("app.js")
    assert "function buildAttach(" not in app
    assert "window.novaAttach.build(" in app


def test_every_composer_reaches_the_moved_module():
    """Four composers mount it; a call site left on the old name is a crash.

    Both halves of the client, not just `app.js`. The chat dock's composer
    left this file for `chat-dock.js` on 2026-09-17 and took its call with
    it, so a count over `app.js` alone reads 3 and says a composer lost its
    `+` when nothing of the kind happened -- the same way round as the
    assertions a split makes vacuous.
    """
    client = read("app.js") + "\n" + read("chat-dock.js")
    assert client.count("= window.novaAttach.build({") == 4


def test_attach_js_takes_el_from_app_rather_than_copying_it():
    """One definition of `el`, the way mermaid.js already borrows it."""
    module = read("attach.js")
    assert "window.novaChat.el" in module
    assert "function el(" not in module
    assert "el: el" in read("app.js")


def test_attach_js_reads_none_of_app_js_state():
    """The whole reason this concern could travel: it talks to the server and
    hands back an object, and reaches into `app.js` for exactly one helper."""
    module = read("attach.js")
    body = module[module.index('(function () {'):]
    assert re.findall(r"window\.novaChat\.(\w+)", body) == ["el"]
    assert "window.novaAttach = {" in body
