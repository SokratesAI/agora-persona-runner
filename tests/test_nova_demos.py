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


def _args(**kw):
    """A stand-in for the argparse namespace these subcommands receive."""
    return type("Args", (), kw)()


@pytest.fixture(autouse=True)
def _no_network_activity():
    """`list` and `reap --idle` ask the site who is looking at each demo.

    Left unpatched every test in this file would make a real DNS lookup for
    a cluster address, so the default here is "the site did not answer" --
    which is also the case the reaper must be safe in. A test that wants an
    answer patches it itself.
    """
    from tools import demo as demo_cli
    with patch.object(demo_cli, "fetch_activity", lambda *a, **kw: None):
        yield


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


def test_the_allocator_hands_out_a_different_port_each_time():
    # Named for what it checks. It does *not* prove two concurrent
    # `tools.demo start` calls cannot collide -- that rests on the
    # compare-and-swap in `_write_registry` and on reserving the port
    # before spawning, which `test_a_dev_server_that_dies_on_startup...`
    # below is about. A reviewer called the old name a claim this test
    # cannot support and was right.
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

@pytest.fixture(autouse=True)
def _no_registry_cache():
    """The proxy reuses a registry read for `DEMO_REGISTRY_TTL` seconds.

    That is right in production -- a demo page is forty assets and the
    registry is a CouchDB round trip -- and it is process-global, so
    without this the second test in this file reads the first one's
    registry. Caught by three tests going 404 at once.
    """
    nova_site._demo_registry_cache.update(at=0.0, text=None)
    yield
    nova_site._demo_registry_cache.update(at=0.0, text=None)


def _registry(*demos):
    return json.dumps({"demos": list(demos)})


ALPHA = {"slug": "alpha", "host": "10.42.0.84", "port": 5174,
         "dir": "/tmp/a", "started_at": "2026-08-26T01:30:00"}


class _Headers(dict):
    """`HTTPError.headers` is an email.Message in real life; `.read` on the
    error body takes a limit now, and a plain dict has no `.get` semantics
    problem -- this exists only so `read(n)` and `get(name)` both work."""

    def read(self, limit=None):
        return b""


class _Opener:
    """Stands in for `nova_site._demo_opener()`.

    The tests patch the opener rather than `urllib.request.urlopen` because
    the handler no longer calls `urlopen` -- it uses an opener with redirect
    following removed. `conftest.py` blocks real sockets, so patching the
    wrong reference fails loudly rather than quietly reaching the network,
    which is how this got caught.
    """

    def __init__(self, fn):
        self._fn = fn

    def open(self, request, timeout=None):
        # The handler passes a `Request` now, because it forwards `Range`
        # and `Accept`. Tests assert on the URL, so unwrap it here.
        url = getattr(request, "full_url", request)
        self.headers = getattr(request, "headers", {})
        return self._fn(url, timeout=timeout)


class _FakeUpstream:
    def __init__(self, body, content_type="text/html", status=200):
        self.body, self.status = body, status
        self.headers = {"Content-Type": content_type}

    def read(self, limit=None):
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
         patch.object(nova_site, "_demo_opener", lambda: _Opener(_open)):
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
         patch.object(nova_site, "_demo_opener",
                      lambda: _Opener(lambda url, timeout=None: _FakeUpstream(b"x"))):
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
         patch.object(nova_site, "_demo_opener", lambda: _Opener(_boom)):
        status, _, body = _get("/demo/alpha/")
    assert status == 502
    assert b"10.42.0.84:5174" in body


def test_an_upstream_404_is_the_demos_answer_and_is_passed_through():
    def _http_error(url, timeout=None):
        raise urllib.error.HTTPError(url, 404, "Not Found",
                                     _Headers({"Content-Type": "text/plain"}), None)

    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch.object(nova_site, "_demo_opener", lambda: _Opener(_http_error)):
        status, _, _ = _get("/demo/alpha/missing.css")
    assert status == 404


def test_posting_to_a_demo_says_the_proxy_is_get_only():
    # Not the generic allowlist 404, which would read as "that demo is not
    # running" -- a different and much more confusing problem.
    status, _, body = _post("/demo/alpha/submit", {"a": 1})
    assert status == 405
    assert b"GET only" in body


def test_a_redirect_is_handed_to_the_browser_under_the_demo_prefix():
    """A static server answers `/sub` with a 301 to `/sub/`.

    Followed here, the browser never learns the URL moved and resolves that
    page's relative links one directory too high. Passed through with the
    prefix restored, the browser asks again at the right address.
    """
    def _redirect(url, timeout=None):
        raise urllib.error.HTTPError(
            url, 301, "Moved Permanently",
            _Headers({"Content-Type": "text/html", "Location": "/sub/"}), None)

    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch.object(nova_site, "_demo_opener", lambda: _Opener(_redirect)):
        status, head, _ = _get("/demo/alpha/sub")
    assert status == 301
    assert "Location: /demo/alpha/sub/" in head


def test_an_off_site_redirect_is_not_rewritten_under_the_demo():
    def _redirect(url, timeout=None):
        raise urllib.error.HTTPError(
            url, 302, "Found",
            _Headers({"Content-Type": "text/html",
                      "Location": "https://example.com/x"}), None)

    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch.object(nova_site, "_demo_opener", lambda: _Opener(_redirect)):
        _, head, _ = _get("/demo/alpha/away.html")
    assert "Location: https://example.com/x" in head


def test_the_demos_own_absolute_redirect_comes_back_under_the_prefix():
    """A framework that has never heard of a reverse proxy writes this.

    Unrewritten it points his phone at a cluster pod IP it cannot route to,
    and leaks the topology into the address bar.
    """
    def _redirect(url, timeout=None):
        raise urllib.error.HTTPError(
            url, 302, "Found",
            _Headers({"Content-Type": "text/html",
                      "Location": "http://10.42.0.84:5174/dir/"}), None)

    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch.object(nova_site, "_demo_opener", lambda: _Opener(_redirect)):
        _, head, _ = _get("/demo/alpha/dir")
    assert "Location: /demo/alpha/dir/" in head


def test_a_body_past_the_cap_is_refused_rather_than_buffered():
    """This pod has a 256Mi limit and reads the whole body before sending.

    An unbounded read is not a slow demo, it is the whole site dying on
    someone's screen recording.
    """
    big = b"x" * (nova_site.DEMO_MAX_BYTES + 1)

    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch.object(nova_site, "_demo_opener",
                      lambda: _Opener(lambda url, timeout=None:
                                      _FakeUpstream(big, "video/mp4"))):
        status, _, body = _get("/demo/alpha/big.mp4")
    assert status == 502
    assert b"larger than" in body


def test_content_encoding_survives_the_proxy():
    """An upstream serving a precompressed asset without this header has
    its gzip bytes rendered as CSS."""
    def _gzipped(url, timeout=None):
        up = _FakeUpstream(b"\x1f\x8b garbage", "text/css")
        up.headers["Content-Encoding"] = "gzip"
        return up

    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch.object(nova_site, "_demo_opener", lambda: _Opener(_gzipped)):
        _, head, _ = _get("/demo/alpha/style.css")
    assert "Content-Encoding: gzip" in head


def test_a_registry_row_with_no_host_is_a_502_and_not_a_traceback():
    """This route is dispatched above `do_GET`'s `try`, so an unguarded
    subscript here drops the connection with no answer at all."""
    broken = {"slug": "alpha", "port": 5174, "started_at": "2026-08-26T01:30:00"}
    with patch.object(nova_site, "vault_read_path", return_value=_registry(broken)):
        status, _, body = _get("/demo/alpha/")
    assert status == 502
    assert b"no host or port" in body


def test_a_range_header_reaches_the_demo_so_video_can_seek():
    seen = {}

    def _open(url, timeout=None):
        return _FakeUpstream(b"bytes")

    opener = _Opener(_open)
    with patch.object(nova_site, "vault_read_path", return_value=_registry(ALPHA)), \
         patch.object(nova_site, "_demo_opener", lambda: opener):
        _get("/demo/alpha/clip.mp4", headers="Range: bytes=0-99\r\n")
    assert opener.headers.get("Range") == "bytes=0-99"


# --- tools.demo -------------------------------------------------------

def test_a_dev_server_that_dies_on_startup_is_not_reported_as_a_running_demo(
        tmp_path, capsys):
    """The failure `start` must never dress up as success.

    Two concurrent starts both allocate the same port, one binds and the
    other dies with EADDRINUSE. If the loser prints a URL and exits 0, the
    owner opens a link that 502s and the only evidence is a temp log
    nobody knows about. So a dead process deregisters and exits non-zero.
    """
    from tools import demo as demo_cli

    written = []
    state = {"registry": {"demos": []}}

    def _read():
        return json.loads(json.dumps(state["registry"])), "/tmp/fake.rev"

    def _write(registry, rev):
        state["registry"] = json.loads(json.dumps(registry))
        written.append(state["registry"])

    args = type("A", (), {"slug": "alpha", "directory": str(tmp_path),
                          "cmd": "python3 -c 'raise SystemExit(3)'"})()
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.84"), \
         patch.object(demo_cli, "SPAWN_CHECK_SECONDS", 0.6):
        code = demo_cli.cmd_start(args)
    assert code == 1
    # The port is back, and no URL was printed for a demo that is not there.
    assert state["registry"]["demos"] == []
    assert "/demo/alpha/" not in capsys.readouterr().out


def test_the_port_is_registered_before_the_dev_server_is_spawned(tmp_path):
    """The ordering is the fix, so the ordering is what is pinned.

    Reserving after spawning lets the compare-and-swap pick a winner
    independently of who won the bind.
    """
    from tools import demo as demo_cli

    order = []
    state = {"registry": {"demos": []}}

    def _read():
        return json.loads(json.dumps(state["registry"])), "/tmp/fake.rev"

    def _write(registry, rev):
        state["registry"] = json.loads(json.dumps(registry))
        order.append("registered")

    real_popen = demo_cli.subprocess.Popen

    def _popen(*a, **kw):
        order.append("spawned")
        return real_popen(*a, **kw)

    args = type("A", (), {"slug": "alpha", "directory": str(tmp_path),
                          "cmd": "python3 -c 'import time; time.sleep(5)'"})()
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.84"), \
         patch.object(demo_cli.subprocess, "Popen", _popen), \
         patch.object(demo_cli, "SPAWN_CHECK_SECONDS", 0.3):
        assert demo_cli.cmd_start(args) == 0
    assert order[0] == "registered"
    assert "spawned" in order
    demo_cli.os.killpg(
        demo_cli.os.getpgid(state["registry"]["demos"][0]["pid"]),
        demo_cli.signal.SIGTERM)


# --- a demo does not survive the pod it runs in (idea #136) ------------

def test_a_row_from_another_pod_is_never_judged_running():
    """The registry records a pid, and a pid means nothing across pods.

    Measured Cycle 551: `bakeoff` was registered on 10.42.0.84 and the
    bridge pod had rolled to 10.42.0.56. If `verdict` looked at the pid
    first it would ask this pod about pid 311849, which here belongs to
    something else entirely.
    """
    from agora_runner.nova_demos import POD_GONE, verdict
    entry = {"slug": "bakeoff", "host": "10.42.0.84", "port": 5174, "pid": 311849}
    # pid_alive=True on purpose: a live pid must not rescue a dead pod.
    assert verdict(entry, "10.42.0.56", lambda pid: True) == POD_GONE


def test_a_row_on_this_pod_is_judged_by_its_process():
    from agora_runner.nova_demos import ALIVE, PROCESS_GONE, verdict
    entry = {"slug": "a", "host": "10.42.0.56", "port": 5174, "pid": 42}
    assert verdict(entry, "10.42.0.56", lambda pid: True) == ALIVE
    assert verdict(entry, "10.42.0.56", lambda pid: False) == PROCESS_GONE


def test_a_row_with_no_pid_yet_is_starting_and_not_dead():
    """`start` writes the row a second before it writes the pid.

    My reviewer reproduced the consequence of getting this wrong: a
    concurrent `reap` drops the row, `cmd_start` then finds itself gone,
    kills its own healthy dev server and fails. So the pid-less state is
    its own verdict and `reap` does not collect it.
    """
    from agora_runner.nova_demos import STARTING, verdict
    entry = {"slug": "a", "host": "10.42.0.56", "port": 5174}
    assert verdict(entry, "10.42.0.56", lambda pid: True) == STARTING


def test_a_foreign_row_is_judged_without_probing_its_pid():
    """Eager evaluation would fire os.kill at a pid this pod reused."""
    from agora_runner.nova_demos import POD_GONE, verdict

    def _boom(pid):
        raise AssertionError("probed a pid recorded by another pod")

    entry = {"slug": "a", "host": "10.42.0.84", "port": 5174, "pid": 311849}
    assert verdict(entry, "10.42.0.56", _boom) == POD_GONE


def _fake_registry(demos):
    state = {"registry": {"demos": demos}}

    def _read():
        return json.loads(json.dumps(state["registry"])), "/tmp/fake.rev"

    def _write(registry, rev):
        state["registry"] = json.loads(json.dumps(registry))

    return state, _read, _write


def test_stop_on_a_demo_from_a_dead_pod_signals_nothing_and_frees_the_port():
    """The bug this closes: SIGTERM to an unrelated process group.

    `stop` used to reach straight for `os.getpgid(entry['pid'])`. Against a
    row left by a pod that has rolled, that pid now belongs to whatever
    this pod started since -- so the fix is that the host check comes
    before the signal, and a foreign row is only ever deregistered.
    """
    from tools import demo as demo_cli

    state, _read, _write = _fake_registry([
        {"slug": "bakeoff", "host": "10.42.0.84", "port": 5174, "pid": 311849},
    ])
    signalled = []
    args = type("A", (), {"slug": "bakeoff"})()
    # **`getpgid` has to succeed here or this test pins nothing.** Left
    # unpatched it raises ProcessLookupError, `stop` catches that, and the
    # unfixed code reaches the same deregistering exit -- which is how the
    # first version of this test passed with the fix reverted. The case
    # that matters is the pid being *reused*: 311849 exists on this pod and
    # belongs to something else, so the pre-fix path would signal it.
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli.os, "getpgid", lambda pid: pid), \
         patch.object(demo_cli.os, "killpg", lambda *a: signalled.append(a)):
        assert demo_cli.cmd_stop(args) == 0
    assert signalled == []
    assert state["registry"]["demos"] == []


def test_stop_still_signals_a_demo_running_on_this_pod():
    """The mirror of the test above -- the host check must not disarm stop."""
    from tools import demo as demo_cli

    state, _read, _write = _fake_registry([
        {"slug": "live", "host": "10.42.0.56", "port": 5174, "pid": 4242},
    ])
    signalled = []
    args = type("A", (), {"slug": "live"})()
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli.os, "getpgid", lambda pid: pid), \
         patch.object(demo_cli.os, "killpg", lambda *a: signalled.append(a)):
        assert demo_cli.cmd_stop(args) == 0
    assert signalled and signalled[0][0] == 4242
    assert state["registry"]["demos"] == []


def test_reap_releases_the_ports_of_demos_that_are_gone_and_keeps_the_live_one():
    from tools import demo as demo_cli

    state, _read, _write = _fake_registry([
        {"slug": "old", "host": "10.42.0.84", "port": 5174, "pid": 311849},
        {"slug": "dead", "host": "10.42.0.56", "port": 5175, "pid": 9},
        {"slug": "live", "host": "10.42.0.56", "port": 5176, "pid": 4242},
    ])
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: pid == 4242):
        assert demo_cli.cmd_reap(_args(idle=None)) == 0
    left = [d["slug"] for d in state["registry"]["demos"]]
    assert left == ["live"]
    # The whole point is the port coming back to the allocator.
    assert register({"demos": state["registry"]["demos"]}, "next", "h", "/d") == PORT_MIN


def test_reap_writes_nothing_when_every_demo_is_serving():
    from tools import demo as demo_cli

    state, _read, _write = _fake_registry([
        {"slug": "live", "host": "10.42.0.56", "port": 5174, "pid": 4242},
    ])
    wrote = []
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry",
                      lambda r, rev: wrote.append(r)), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True):
        assert demo_cli.cmd_reap(_args(idle=None)) == 0
    assert wrote == []


def test_list_says_which_rows_are_not_serving_anything(capsys):
    from tools import demo as demo_cli

    state, _read, _write = _fake_registry([
        {"slug": "old", "host": "10.42.0.84", "port": 5174, "pid": 311849,
         "started_at": "2026-08-25T23:10:04", "dir": "/d"},
        {"slug": "live", "host": "10.42.0.56", "port": 5176, "pid": 4242,
         "started_at": "2026-08-27T23:10:04", "dir": "/d"},
    ])
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: pid == 4242):
        assert demo_cli.cmd_list(_args()) == 0
    out = capsys.readouterr().out
    assert "the pod it ran in is gone" in out
    assert "1 of 2 hold a port and are not serving anything" in out


def test_reap_leaves_a_demo_that_is_still_starting_alone():
    """The reviewer's reproduction, as a test.

    `cmd_start` writes the row, sleeps a second, then writes the pid. A
    `reap` inside that window used to read the row as dead and drop it,
    which makes `start` kill its own live server and fail.
    """
    from tools import demo as demo_cli

    state, _read, _write = _fake_registry([
        {"slug": "starting", "host": "10.42.0.56", "port": 5174},
        {"slug": "old", "host": "10.42.0.84", "port": 5175, "pid": 311849},
    ])
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: False):
        assert demo_cli.cmd_reap(_args(idle=None)) == 0
    assert [d["slug"] for d in state["registry"]["demos"]] == ["starting"]


def test_list_does_not_offer_to_reap_a_demo_that_is_starting(capsys):
    from tools import demo as demo_cli

    state, _read, _write = _fake_registry([
        {"slug": "starting", "host": "10.42.0.56", "port": 5174,
         "started_at": "2026-08-27T23:14:02", "dir": "/d"},
    ])
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"):
        assert demo_cli.cmd_list(_args()) == 0
    out = capsys.readouterr().out
    assert "starting -- no pid recorded yet" in out
    assert "hold a port and are not serving" not in out


def test_judge_does_not_probe_the_pid_of_a_row_from_another_pod():
    """`judge` is a wrapper, so its own ordering needs its own test.

    The `verdict` version of this passes even when `judge` computes the
    probe eagerly and hands `verdict` a constant -- which is exactly the
    mutation that survived the first round.
    """
    from tools import demo as demo_cli

    def _boom(pid):
        raise AssertionError("probed a pid recorded by another pod")

    entry = {"slug": "old", "host": "10.42.0.84", "port": 5174, "pid": 311849}
    with patch.object(demo_cli, "pid_alive", _boom):
        assert demo_cli.judge(entry, "10.42.0.56") == "pod-gone"


# -- Idea #136's other half: a demo nobody is looking at ---------------------
#
# The tests worth having here are the ones about *not* reaping. Stopping an
# idle demo is the easy half and a wrong answer costs a port; refusing to
# stop one somebody is watching is the half that shows on his screen.


def test_the_site_records_who_asked_for_a_demo_and_publishes_it():
    from agora_runner.nova_demos import dumps as demo_dumps

    registry = demo_dumps({"demos": [
        {"slug": "bakeoff", "host": "10.42.0.56", "port": 5174, "pid": 4242},
    ]})
    body = b"<html>demo</html>"

    class _Up:
        status = 200
        headers = {"Content-Type": "text/html"}

        def read(self, n):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch.object(nova_site, "_demo_registry", lambda: registry), \
         patch.object(nova_site, "_demo_opener",
                      lambda: type("O", (), {"open": lambda self, r, timeout: _Up()})()):
        assert _get("/demo/bakeoff/")[0] == 200
    seen = json.loads(_get("/api/demo/activity")[2])
    assert "bakeoff" in seen["last_seen"]
    assert seen["started_at"] <= seen["last_seen"]["bakeoff"]


def test_a_request_for_a_slug_nobody_registered_is_not_someone_looking():
    with patch.object(nova_site, "_demo_registry", lambda: '{"demos": []}'):
        assert _get("/demo/ghost/")[0] == 404
    assert "ghost" not in json.loads(_get("/api/demo/activity")[2])["last_seen"]


def test_idle_is_measured_from_the_site_restart_not_from_a_forgotten_request():
    """The failure this rule exists to prevent, as a test.

    `last_seen` is in the site pod's memory. A minute after a site deploy no
    demo has a recorded request, so measuring from `started_at` alone would
    call a two-day-old demo idle for two days and reap it instantly -- while
    somebody had it open. The site's own start time is the floor.
    """
    from agora_runner.nova_demos import idle_seconds

    two_days = 2 * 24 * 3600
    now = datetime.now().timestamp()
    demo = {"slug": "bakeoff",
            "started_at": datetime.fromtimestamp(now - two_days).isoformat(
                timespec="seconds")}
    fresh_site = {"started_at": now - 60, "last_seen": {}}
    assert idle_seconds(demo, fresh_site, now) == pytest.approx(60, abs=2)
    old_site = {"started_at": now - two_days - 60, "last_seen": {}}
    assert idle_seconds(demo, old_site, now) == pytest.approx(two_days, abs=2)


def test_idle_is_unknown_rather_than_zero_when_the_site_did_not_answer():
    from agora_runner.nova_demos import idle_seconds

    assert idle_seconds({"slug": "x", "started_at": "2026-08-25T10:00:00"},
                        None, 1_000_000.0) is None
    # A row written before this feature existed has no readable start time
    # either; that is also "I do not know", not "idle forever".
    assert idle_seconds({"slug": "x"}, {"started_at": 1.0, "last_seen": {}},
                        1_000_000.0) is None


def test_reap_idle_stops_a_demo_nobody_asked_for_and_frees_its_port():
    from tools import demo as demo_cli

    now = datetime.now().timestamp()
    state, _read, _write = _fake_registry([
        {"slug": "cold", "host": "10.42.0.56", "port": 5174, "pid": 4242},
        {"slug": "warm", "host": "10.42.0.56", "port": 5175, "pid": 4243},
    ])
    activity = {"started_at": now - 10 * 3600,
                "last_seen": {"cold": now - 5 * 3600, "warm": now - 60}}
    signalled = []
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli, "fetch_activity", lambda *a, **kw: activity), \
         patch.object(demo_cli.os, "getpgid", lambda pid: pid), \
         patch.object(demo_cli.os, "killpg", lambda *a: signalled.append(a)):
        assert demo_cli.cmd_reap(_args(idle=120)) == 0
    assert [d["slug"] for d in state["registry"]["demos"]] == ["warm"]
    assert signalled and signalled[0][0] == 4242


def test_reap_idle_stops_nothing_when_the_site_does_not_answer():
    """`fetch_activity` returning None must not read as 'nobody is looking'.

    This is the case that matters: the site being down is exactly when the
    naive answer -- no recorded requests, therefore idle -- would stop every
    demo the owner has open.
    """
    from tools import demo as demo_cli

    state, _read, _write = _fake_registry([
        {"slug": "live", "host": "10.42.0.56", "port": 5174, "pid": 4242},
    ])
    wrote, signalled = [], []
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry",
                      lambda r, rev: wrote.append(r)), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli, "fetch_activity", lambda *a, **kw: None), \
         patch.object(demo_cli.os, "killpg", lambda *a: signalled.append(a)):
        assert demo_cli.cmd_reap(_args(idle=1)) == 0
    assert wrote == [] and signalled == []


def test_reap_without_the_flag_never_touches_a_running_demo_however_old():
    """Idle reaping is opt-in: no `--idle`, no judgement about who is looking."""
    from tools import demo as demo_cli

    now = datetime.now().timestamp()
    state, _read, _write = _fake_registry([
        {"slug": "ancient", "host": "10.42.0.56", "port": 5174, "pid": 4242},
    ])
    wrote, signalled = [], []
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry",
                      lambda r, rev: wrote.append(r)), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli, "fetch_activity",
                      lambda *a, **kw: {"started_at": now - 10 ** 6,
                                        "last_seen": {}}), \
         patch.object(demo_cli.os, "killpg", lambda *a: signalled.append(a)):
        assert demo_cli.cmd_reap(_args(idle=None)) == 0
    assert wrote == [] and signalled == []


def test_reap_idle_keeps_the_port_registered_when_it_cannot_signal():
    """Alive and unsignallable is the one case that must not deregister.

    Freeing the port while something still holds it is the silent
    cross-serve: the next `start` allocates it, fails to bind, and serves
    this demo's page under the new slug.
    """
    from tools import demo as demo_cli

    now = datetime.now().timestamp()
    state, _read, _write = _fake_registry([
        {"slug": "stubborn", "host": "10.42.0.56", "port": 5174, "pid": 4242},
    ])
    wrote = []

    def _refuse(*a):
        raise PermissionError("Operation not permitted")

    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry",
                      lambda r, rev: wrote.append(r)), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli, "fetch_activity",
                      lambda *a, **kw: {"started_at": now - 10 ** 6,
                                        "last_seen": {"stubborn": now - 10 ** 5}}), \
         patch.object(demo_cli.os, "getpgid", lambda pid: pid), \
         patch.object(demo_cli.os, "killpg", _refuse):
        assert demo_cli.cmd_reap(_args(idle=1)) == 1
    assert wrote == []


# --- a demo does not survive the *cycle* it runs in either (Cycle 605) ---

def test_a_concurrent_workspace_directory_is_named_as_doomed():
    """The predicate, at the exact shape the bridge deletes.

    `_run_cli_once` ends `if slot: shutil.rmtree(workspace)`, so every path
    under the concurrent root goes when the turn does -- and the dev server,
    deliberately in its own session, does not.
    """
    from agora_runner.nova_demos import ephemeral_reason

    why = ephemeral_reason(
        "/data/workspace-concurrent/7-135015072507584/agora-persona-runner/demo",
        "bakeoff")
    assert why is not None
    assert "/data/workspace-concurrent" in why
    # It has to say where to put it instead, or the refusal is a dead end.
    assert "/data/workspace/demos/bakeoff" in why


def test_a_durable_directory_is_not_refused():
    """The control. A predicate that refuses everything pins nothing."""
    from agora_runner.nova_demos import ephemeral_reason

    assert ephemeral_reason("/data/workspace/demos/bakeoff", "bakeoff") is None
    assert ephemeral_reason("/data/workspace/agora-persona-runner") is None
    assert ephemeral_reason("/tmp/scratch") is None


def test_containment_is_a_path_test_and_not_a_string_prefix():
    """`/data/workspace-concurrent-notes` is not inside the concurrent root.

    A `startswith` on the raw string says it is, and would refuse a
    perfectly durable directory while claiming a measured reason.
    """
    from agora_runner.nova_demos import ephemeral_reason

    assert ephemeral_reason("/data/workspace-concurrent-notes/demo") is None
    # ...and the traversal in the other direction still resolves.
    assert ephemeral_reason(
        "/data/workspace-concurrent/7-1/../7-2/demo") is not None


def test_start_refuses_a_directory_this_turn_deletes(tmp_path, capsys):
    """End to end: nothing is registered and nothing is spawned.

    The registry write is the part that matters. A refusal that still
    allocated a port would leak one every time.
    """
    from tools import demo as demo_cli

    state = {"registry": {"demos": []}}
    spawned = []

    def _read():
        return json.loads(json.dumps(state["registry"])), "/tmp/fake.rev"

    def _write(registry, rev):
        state["registry"] = json.loads(json.dumps(registry))

    doomed = tmp_path / "slot" / "demo"
    doomed.mkdir(parents=True)
    args = type("A", (), {"slug": "alpha", "directory": str(doomed),
                          "cmd": ""})()
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.84"), \
         patch.object(demo_cli.subprocess, "Popen",
                      lambda *a, **kw: spawned.append(a)), \
         patch.object(demo_cli, "ephemeral_reason",
                      lambda d, slug="<slug>": "it is doomed"):
        code = demo_cli.cmd_start(args)
    assert code == 2
    assert state["registry"]["demos"] == []
    assert spawned == []
    assert "it is doomed" in capsys.readouterr().err
