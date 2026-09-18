"""The composer's model picker is its own file now (issue #233).

The thirteenth piece lifted out of `app.js` whole: the model catalog kept
between opens, the per-thread choice, the pill that sizes itself to its
label and the change handler that posts it. What these tests pin is the
wiring, because that is what a move can silently break: the file has to be
served, loaded before `app.js`, precached, and inside the update stamp.

The seam is asserted by name rather than by count: `models.js` takes five
names from `app.js` and hands one back, `paintModelPicker`, which the chat
dock takes as a value while `app.js` loads -- so it is bound before that.
"""

import os
import re

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")

SHARED = ("el", "fetchPage", "localStore", "modelOption", "toast")

HANDED_BACK = ("paintModelPicker",)

MOVED = ('var MODEL_CATALOG_KEY = "nova.modelCatalog.v1";',
         "function loadModelCatalog(", "function saveModelCatalog(",
         "function fillModelOptions(", "function modelLabel(",
         "function fitModelPick(", "function modelPicker(",
         "function paintModelPicker(")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_models_js_is_served_and_loads_before_app_js():
    assert nova_site.STATIC_ROUTES["/models.js"] == "models.js"
    page = read("index.html")
    assert page.index('src="/models.js"') < page.index('src="/app.js"')


def test_models_js_is_precached_and_stamped():
    assert '"/models.js"' in read("sw.js")
    assert "models.js" in nova_site.SW_BUILD_INPUTS


def test_app_js_kept_no_copy_of_the_moved_code():
    app = read("app.js")
    for gone in MOVED:
        assert gone not in app, gone + " is still in app.js"


def test_the_moved_code_is_all_in_models_js():
    module = read("models.js")
    for kept in MOVED:
        assert kept in module, kept + " did not arrive in models.js"


def test_every_name_the_module_borrows_is_passed_in():
    module = read("models.js")
    app = read("app.js")
    call = app[app.index("window.novaModels({"):]
    call = call[:call.index("})") + 2]
    for name in SHARED:
        assert "var %s = shared.%s;" % (name, name) in module, name
        assert re.search(r"\b%s: %s,?" % (name, name), call), name


def test_every_name_handed_back_is_bound_in_app_js():
    module = read("models.js")
    app = read("app.js")
    for name in HANDED_BACK:
        assert "      %s: %s," % (name, name) in module, name
        assert "  var %s = modelsModule.%s;" % (name, name) in app, name


def test_it_is_bound_before_the_chat_dock_takes_it():
    """The chat dock takes `paintModelPicker` as a value while `app.js`
    loads. Bound any later, it gets `undefined`."""
    app = read("app.js")
    bound = app.index("var modelsModule = window.novaModels")
    assert bound < app.index("window.novaChatDock({")
