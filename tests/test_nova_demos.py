"""The demo registry and the `/demo/<slug>/` proxy (idea #135).

Two things are worth testing here and they are not the obvious one. The
registry's job is that **two callers never get the same port**, so the
allocation tests go at collisions rather than at the happy path. The
proxy's job is that a link the owner opens either shows the demo or says
plainly why it cannot, so its tests go at the three ways it can fail --
unknown slug, dead upstream, missing trailing slash -- rather than at a
200 that a static file server would also have produced.
"""

import json
import urllib.error
from datetime import datetime
from unittest.mock import patch

import pytest

from agora_runner import nova_site
from agora_runner.nova_demos import (
    DemoError,
    PORT_MAX,
    PORT_MIN,
    dumps,
    entries,
    load,
    lookup,
    register,
    unregister,
)

from tests.test_nova_site import _get, _post


def test_absent_document_is_an_empty_registry_not_an_error():
    # `vault_tool.py get` exits 0 and prints this for a path with no
    # document, so the first caller ever to start a demo is handed a
    # sentence rather than an empty file.
    assert load("[not found: projects/.../demos.json]") == {"demos": []}
    assert load("") == {"demos": []}
    assert load(None) == {"demos": []}


def test_a_registry_that_is_not_json_is_refused_rather_than_reset():
    # Resetting would silently orphan every running demo's port.
    with pytest.raises(DemoError):
        load("demos: []")
    with pytest.raises(DemoError):
        load('{"demos": "one"}')


def test_two_demos_never_share_a_port():
    registry = load("")
    first = register(registry, "alpha", "10.42.0.84", "/tmp/a")
    second = register(registry, "beta", "10.42.0.84", "/tmp/b")
    assert first != second
    assert first == PORT_MIN and second == PORT_MIN + 1


def test_a_released_port_is_reused_before_a_fresh_one():
    registry = load("")
    register(registry, "alpha", "10.42.0.84", "/tmp/a")
    register(registry, "beta", "10.42.0.84", "/tmp/b")
    unregister(registry, "alpha")
    assert register(registry, "gamma", "10.42.0.84", "/tmp/c") == PORT_MIN


def test_registering_a_live_slug_is_refused_rather_than_replacing_it():
    # A silent replace leaves the old dev server running on a port nothing
    # points at any more, which nobody would ever notice.
    registry = load("")
    register(registry, "alpha", "10.42.0.84", "/tmp/a")
    with pytest.raises(DemoError):
        register(registry, "alpha", "10.42.0.84", "/tmp/other")


def test_the_allocator_refuses_rather_than_handing_out_a_held_port():
    registry = load("")
    for i in range(PORT_MAX - PORT_MIN + 1):
        register(registry, f"demo-{i}", "10.42.0.84", "/tmp/x")
    with pytest.raises(DemoError):
        register(registry, "one-too-many", "10.42.0.84", "/tmp/x")


@pytest.mark.parametrize("slug", ["", "A", "has space", "has/slash", "-lead", "x"])
def test_a_slug_that_would_break_string_equality_or_a_url_is_refused(slug):
    with pytest.raises(DemoError):
        register(load(""), slug, "10.42.0.84", "/tmp/a")


def test_a_demo_with_no_host_is_refused():
    # A registry row with no host proxies to nothing and answers 502 to
    # whoever opens the link.
    with pytest.raises(DemoError):
        register(load(""), "alpha", "", "/tmp/a")


def test_the_registry_survives_a_round_trip_through_the_vault():
    registry = load("")
    register(registry, "alpha", "10.42.0.84", "/tmp/a",
             now=datetime(2026, 8, 26, 1, 30))
    again = load(dumps(registry))
    assert lookup(again, "alpha")["port"] == PORT_MIN
    assert lookup(again, "alpha")["started_at"] == "2026-08-26T01:30:00"
    assert lookup(again, "nobody") is None
    assert [d["slug"] for d in entries(again)] == ["alpha"]


# --- the proxy ---------------------------------------------------------

def _registry(*demos):
    return json.dumps({"demos": list(demos)})


ALPHA = {"slug": "alpha", "host": "10.42.0.84", "port": 5174,
         "dir": "/tmp/a", "started_at": "2026-08-26T01:30:00"}


class _FakeUpstream:
    def __init__(self, body, content_type="text/html", status=200):
        self.body, self.status = body, status
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_an_unknown_slug_says_so_rather_than_serving_the_nova_shell():
    # The shell is what every other unmatched path gets, and it would read
    # as "the demo loaded and is blank".
    with patch.object(nova_site, "vault_read_path", return_value=_registry()):
        status, _, body = _get("/demo/nosuch/")
    assert status == 404
    assert b"nosuch" in body


def test_a_registered_demo_is_proxied_body_and_content_type():
    seen = {}

    def _open(url, timeout=None):
        seen["url"] = url
        return _FakeUpstream(b"<h1>the demo</h1>")

    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch("urllib.request.urlopen", _open):
        status, head, body = _get("/demo/alpha/index.html?x=1")
    assert status == 200
    assert body == b"<h1>the demo</h1>"
    assert "text/html" in head
    # The slug is stripped: the dev server knows nothing about `/demo/`.
    assert seen["url"] == "http://10.42.0.84:5174/index.html?x=1"


def test_the_demo_is_never_cached():
    # A demo is edited while it is looked at; a cached reload showing the
    # previous build reads as "the demo is broken".
    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch("urllib.request.urlopen", lambda url, timeout=None: _FakeUpstream(b"x")):
        _, head, _ = _get("/demo/alpha/")
    assert "no-store" in head


def test_a_bare_slug_redirects_so_relative_assets_resolve_under_the_demo():
    # `style.css` on a page served at `/demo/alpha` resolves to
    # `/demo/style.css`, which is Nova's 404 rather than the demo's asset.
    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)):
        status, head, _ = _get("/demo/alpha")
    assert status == 302
    assert "Location: /demo/alpha/" in head


def test_a_dead_dev_server_says_where_it_was_looking():
    def _boom(url, timeout=None):
        raise OSError("Connection refused")

    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch("urllib.request.urlopen", _boom):
        status, _, body = _get("/demo/alpha/")
    assert status == 502
    assert b"10.42.0.84:5174" in body


def test_an_upstream_404_is_the_demos_answer_and_is_passed_through():
    def _http_error(url, timeout=None):
        raise urllib.error.HTTPError(url, 404, "Not Found",
                                     {"Content-Type": "text/plain"}, None)

    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch("urllib.request.urlopen", _http_error):
        status, _, _ = _get("/demo/alpha/missing.css")
    assert status == 404


def test_posting_to_a_demo_says_the_proxy_is_get_only():
    # Not the generic allowlist 404, which would read as "that demo is not
    # running" -- a different and much more confusing problem.
    status, _, body = _post("/demo/alpha/submit", {"a": 1})
    assert status == 405
    assert b"GET only" in body
