"""The demo registry and the `/demo/<slug>/` proxy (idea #135).

Two things are worth testing here and they are not the obvious one. The
registry's job is that **two callers never get the same port**, so the
allocation tests go at collisions rather than at the happy path. The
proxy's job is that a link the owner opens either shows the demo or says
plainly why it cannot, so its tests go at the three ways it can fail --
unknown slug, dead upstream, missing trailing slash -- rather than at a
200 that a static file server would also have produced.
"""

import io
from contextlib import redirect_stdout
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
    """A stand-in for the argparse namespace these subcommands receive.

    `unopened` gets a default because every production caller supplies one
    -- argparse for the CLI, `tools.tidy_workspace._sweep_demos` explicitly
    -- and spelling it on every older test would say nothing about them.
    A test that cares about that clock passes it.
    """
    from tools import demo as _demo_cli

    kw.setdefault("unopened", _demo_cli.DEFAULT_UNOPENED_MINUTES)
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

    browser = ("User-Agent: Mozilla/5.0 (Linux; Android 10; K) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 "
               "Mobile Safari/537.36\r\n")
    # The durable half runs off-thread and writes the vault, so it is
    # patched out here and tested on its own below. Recording who asked is
    # the in-memory question and it is what this test is about.
    durable = []
    with patch.object(nova_site, "_demo_registry", lambda: registry), \
         patch.object(nova_site, "_start_durable_open", durable.append), \
         patch.object(nova_site, "_demo_opener",
                      lambda: type("O", (), {"open": lambda self, r, timeout: _Up()})()):
        # A cycle's own proof-it-works probe first: it must be served and
        # must not count. Then the owner's phone, which must.
        assert _get("/demo/bakeoff/",
                    "User-Agent: Python-urllib/3.11\r\n")[0] == 200
        assert "bakeoff" not in json.loads(
            _get("/api/demo/activity")[2])["last_seen"]
        assert durable == []
        assert _get("/demo/bakeoff/", browser)[0] == 200
    seen = json.loads(_get("/api/demo/activity")[2])
    assert "bakeoff" in seen["last_seen"]
    assert seen["started_at"] <= seen["last_seen"]["bakeoff"]
    # And the browser open asks for the durable mark, which is the half
    # that survives this pod rolling.
    assert durable == ["bakeoff"]


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

    The demo here carries `opened_at`, because that is the case the floor is
    for: a record of somebody looking, which the roll destroyed. The row
    without one is the test below, and it must not be floored.
    """
    from agora_runner.nova_demos import idle_seconds

    two_days = 2 * 24 * 3600
    now = datetime.now().timestamp()
    demo = {"slug": "bakeoff",
            "opened_at": "2026-08-27T10:00:00",
            "started_at": datetime.fromtimestamp(now - two_days).isoformat(
                timespec="seconds")}
    fresh_site = {"started_at": now - 60, "last_seen": {}}
    assert idle_seconds(demo, fresh_site, now) == pytest.approx(60, abs=2)
    old_site = {"started_at": now - two_days - 60, "last_seen": {}}
    assert idle_seconds(demo, old_site, now) == pytest.approx(two_days, abs=2)


def test_an_unopened_demo_ages_from_its_hand_over_not_from_the_site_roll():
    """The 18-hour bound has to be reachable, and the floor made it not.

    Measured Cycle 609 on the live site: `roadmap`, handed over at 01:43
    Oslo, read `[no recorded open, 9 min after hand-over]` at 05:06 because
    `nova-site` had rolled ten minutes earlier. It rolls on every merge, so
    `DEFAULT_UNOPENED_MINUTES` could never be reached and a demo nobody
    opens holds its port forever.

    The floor protects a record a roll destroys. A row with no `opened_at`
    has no such record -- "nobody has ever asked for this" is the
    registry's own answer and it survived the roll -- so nothing is being
    protected and the clock runs from the hand-over.
    """
    from agora_runner.nova_demos import idle_seconds

    now = datetime.now().timestamp()
    waited = 3 * 3600 + 23 * 60
    demo = {"slug": "roadmap",
            "started_at": datetime.fromtimestamp(now - waited).isoformat(
                timespec="seconds")}
    just_rolled = {"started_at": now - 600, "last_seen": {}}
    assert idle_seconds(demo, just_rolled, now) == pytest.approx(waited, abs=2)

    # A request the site does remember still wins over the hand-over: that
    # is somebody asking, and it is later than the start by construction.
    asked = {"started_at": now - 600, "last_seen": {"roadmap": now - 120}}
    assert idle_seconds(demo, asked, now) == pytest.approx(120, abs=2)


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
        "bakeoff", environ={"CLAUDE_WORKSPACE": "/data/workspace"})
    assert why is not None
    assert "/data/workspace-concurrent" in why
    # It has to say where to put it instead, or the refusal is a dead end.
    assert "/data/workspace/demos/bakeoff" in why


def test_a_durable_directory_is_not_refused():
    """The control. A predicate that refuses everything pins nothing."""
    from agora_runner.nova_demos import ephemeral_reason

    env = {"CLAUDE_WORKSPACE": "/data/workspace"}
    assert ephemeral_reason("/data/workspace/demos/bakeoff", "bakeoff",
                            environ=env) is None
    assert ephemeral_reason("/data/workspace/agora-persona-runner",
                            environ=env) is None
    assert ephemeral_reason("/tmp/scratch", environ=env) is None


def test_containment_is_a_path_test_and_not_a_string_prefix():
    """`/data/workspace-concurrent-notes` is not inside the concurrent root.

    A `startswith` on the raw string says it is, and would refuse a
    perfectly durable directory while claiming a measured reason.
    """
    from agora_runner.nova_demos import ephemeral_reason

    env = {"CLAUDE_WORKSPACE": "/data/workspace"}
    assert ephemeral_reason("/data/workspace-concurrent-notes/demo",
                            environ=env) is None
    # ...and the traversal in the other direction still resolves.
    assert ephemeral_reason("/data/workspace-concurrent/7-1/../7-2/demo",
                            environ=env) is not None


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


def test_the_root_is_derived_from_the_environment_the_bridge_sets():
    """Not a copy of today's answer -- the bridge's own rule, re-read.

    `_concurrent_root` in `agora-claude-bridge` is
    `CLAUDE_CONCURRENT_ROOT or CLAUDE_WORKSPACE + "-concurrent"`. A literal
    here agrees with that until either variable moves, and then it silently
    calls a doomed directory durable.
    """
    from agora_runner.nova_demos import concurrent_root, ephemeral_reason

    assert concurrent_root({"CLAUDE_WORKSPACE": "/srv/box"}) == "/srv/box-concurrent"
    assert concurrent_root({"CLAUDE_CONCURRENT_ROOT": "/elsewhere",
                            "CLAUDE_WORKSPACE": "/srv/box"}) == "/elsewhere"
    # ...and the refusal follows it there.
    assert ephemeral_reason(
        "/elsewhere/7-1/demo",
        environ={"CLAUDE_CONCURRENT_ROOT": "/elsewhere"}) is not None
    assert ephemeral_reason(
        "/data/workspace-concurrent/7-1/demo",
        environ={"CLAUDE_CONCURRENT_ROOT": "/elsewhere",
                 "CLAUDE_WORKSPACE": "/srv/box"}) is None


def test_this_turns_own_workspace_is_refused_however_the_root_is_named():
    """The clause that cannot go stale: NOVA_WORKSPACE is read, not derived.

    The bridge exports the directory it handed this turn. If it is not the
    shared checkout it is a per-turn slot, whatever it is called.
    """
    from agora_runner.nova_demos import ephemeral_reason

    env = {"CLAUDE_WORKSPACE": "/data/workspace",
           "NOVA_WORKSPACE": "/somewhere/entirely/else/9-2"}
    assert ephemeral_reason("/somewhere/entirely/else/9-2/demo",
                            environ=env) is not None
    # A serialized turn gets the shared checkout as NOVA_WORKSPACE, and that
    # one is never torn down -- refusing it would refuse every demo.
    assert ephemeral_reason(
        "/data/workspace/demos/bakeoff",
        environ={"CLAUDE_WORKSPACE": "/data/workspace",
                 "NOVA_WORKSPACE": "/data/workspace"}) is None


def test_a_symlink_into_a_doomed_slot_is_still_doomed(tmp_path):
    """`abspath` calls this durable; `realpath` does not. Reviewer's finding.

    The storage is what vanishes, so the resolved path is the one that
    answers the question.
    """
    from agora_runner.nova_demos import ephemeral_reason

    slot = tmp_path / "slots" / "7-1" / "demo"
    slot.mkdir(parents=True)
    link = tmp_path / "durable"
    link.symlink_to(slot)
    env = {"CLAUDE_CONCURRENT_ROOT": str(tmp_path / "slots")}
    assert ephemeral_reason(str(link), environ=env) is not None


# ---------------------------------------------------------------------------
# A demo nobody has opened *yet* is not idle (idea #134/#135, Cycle 606).


def test_no_recorded_open_separates_waiting_from_gone_quiet():
    """The two cases `idle_seconds` cannot tell apart on its own."""
    from agora_runner.nova_demos import no_recorded_open

    now = datetime.now().timestamp()
    activity = {"started_at": now - 3600, "last_seen": {"seen": now - 60}}
    assert no_recorded_open({"slug": "waiting"}, activity) is True
    assert no_recorded_open({"slug": "seen"}, activity) is False


def test_no_recorded_open_is_false_when_the_site_did_not_answer():
    """No answer is not evidence of anything, and `idle_seconds` returns
    None there anyway, so nothing is reaped on either clock."""
    from agora_runner.nova_demos import no_recorded_open

    assert no_recorded_open({"slug": "waiting"}, None) is False
    assert no_recorded_open({"slug": "waiting"}, {"last_seen": {}}) is False


def test_reap_idle_spares_a_demo_that_has_not_been_opened_yet():
    """The bug this fixes: a link handed over overnight died before morning.

    Both rows are three hours old and past the two-hour idle clock. The one
    somebody opened is reaped; the one waiting to be opened is not.
    """
    from tools import demo as demo_cli

    now = datetime.now().timestamp()
    started = datetime.fromtimestamp(now - 3 * 3600).isoformat()
    state, _read, _write = _fake_registry([
        {"slug": "waiting", "host": "10.42.0.56", "port": 5174, "pid": 4242,
         "started_at": started},
        {"slug": "quiet", "host": "10.42.0.56", "port": 5175, "pid": 4243,
         "started_at": started},
    ])
    activity = {"started_at": now - 6 * 3600,
                "last_seen": {"quiet": now - 3 * 3600}}
    signalled = []
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli, "fetch_activity", lambda *a, **kw: activity), \
         patch.object(demo_cli.os, "getpgid", lambda pid: pid), \
         patch.object(demo_cli.os, "killpg", lambda *a: signalled.append(a)):
        assert demo_cli.cmd_reap(_args(idle=120, unopened=720)) == 0
    assert [d["slug"] for d in state["registry"]["demos"]] == ["waiting"]
    assert signalled and signalled[0][0] == 4243


def test_reap_still_stops_a_demo_nobody_ever_opened_once_its_own_clock_runs_out():
    """The longer clock is longer, not absent -- a port is still bounded."""
    from tools import demo as demo_cli

    now = datetime.now().timestamp()
    state, _read, _write = _fake_registry([
        {"slug": "waiting", "host": "10.42.0.56", "port": 5174, "pid": 4242,
         "started_at": datetime.fromtimestamp(now - 13 * 3600).isoformat()},
    ])
    activity = {"started_at": now - 20 * 3600, "last_seen": {}}
    signalled = []
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli, "fetch_activity", lambda *a, **kw: activity), \
         patch.object(demo_cli.os, "getpgid", lambda pid: pid), \
         patch.object(demo_cli.os, "killpg", lambda *a: signalled.append(a)):
        assert demo_cli.cmd_reap(_args(idle=120, unopened=720)) == 0
    assert state["registry"]["demos"] == []
    assert signalled and signalled[0][0] == 4242


def test_a_site_roll_does_not_restart_the_unopened_clock():
    """The test above passes with the bug in, because its site is old.

    `tools.tidy_workspace` calls `cmd_reap` with these defaults every
    cycle, and `nova-site` rolls on every merge -- several times an hour on
    a busy night. With the clock floored at the site's start, a demo handed
    over nineteen hours ago read as ten minutes old and was never stopped.
    The site here rolled ten minutes ago, which is the state that used to
    hide it.
    """
    from tools import demo as demo_cli

    now = datetime.now().timestamp()
    state, _read, _write = _fake_registry([
        {"slug": "waiting", "host": "10.42.0.56", "port": 5174, "pid": 4242,
         "started_at": datetime.fromtimestamp(now - 19 * 3600).isoformat()},
    ])
    activity = {"started_at": now - 600, "last_seen": {}}
    signalled = []
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli, "fetch_activity", lambda *a, **kw: activity), \
         patch.object(demo_cli.os, "getpgid", lambda pid: pid), \
         patch.object(demo_cli.os, "killpg", lambda *a: signalled.append(a)):
        assert demo_cli.cmd_reap(_args(
            idle=demo_cli.DEFAULT_IDLE_MINUTES,
            unopened=demo_cli.DEFAULT_UNOPENED_MINUTES)) == 0
    assert state["registry"]["demos"] == []
    assert signalled and signalled[0][0] == 4242


def test_a_site_roll_still_spares_a_demo_somebody_opened():
    """The other direction, and the reason the floor stays for opened rows.

    Same freshly rolled site, same nineteen-hour-old demo -- but this one
    carries the durable mark, so it is on the two-hour idle clock and its
    `last_seen` really was destroyed by the roll. Ageing it from the
    hand-over would stop a demo the owner opened and walked away from for
    ten minutes.
    """
    from tools import demo as demo_cli
    from agora_runner.nova_demos import OPENED_AT

    now = datetime.now().timestamp()
    state, _read, _write = _fake_registry([
        {"slug": "watched", "host": "10.42.0.56", "port": 5174, "pid": 4242,
         OPENED_AT: "2026-08-28T10:00:00",
         "started_at": datetime.fromtimestamp(now - 19 * 3600).isoformat()},
    ])
    activity = {"started_at": now - 600, "last_seen": {}}
    signalled = []
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "_write_registry", _write), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli, "fetch_activity", lambda *a, **kw: activity), \
         patch.object(demo_cli.os, "getpgid", lambda pid: pid), \
         patch.object(demo_cli.os, "killpg", lambda *a: signalled.append(a)):
        assert demo_cli.cmd_reap(_args(
            idle=demo_cli.DEFAULT_IDLE_MINUTES,
            unopened=demo_cli.DEFAULT_UNOPENED_MINUTES)) == 0
    assert [d["slug"] for d in state["registry"]["demos"]] == ["watched"]
    assert signalled == []


def test_tidy_workspace_passes_the_unopened_clock_through():
    """`_sweep_demos` builds the namespace by hand, so a missing key here is
    an AttributeError in the one caller that runs every cycle."""
    import argparse as _argparse

    from tools import demo as demo_cli
    from tools import tidy_workspace

    seen = {}
    with patch.object(demo_cli, "cmd_reap", lambda ns: seen.update(vars(ns))), \
         patch.object(demo_cli, "_cleanup_temps", lambda: None):
        tidy_workspace._sweep_demos()
    assert seen == {"idle": demo_cli.DEFAULT_IDLE_MINUTES,
                    "unopened": demo_cli.DEFAULT_UNOPENED_MINUTES}
    assert _argparse  # the namespace really is one


def test_the_unopened_default_is_long_enough_to_cross_a_night():
    """The number is a measurement, not a preference, so pin what it is for.

    Across 2026-08-10 to 2026-08-28 the owner's own comments show eight
    consecutive nights of silence running 6.0h to 11.9h (median 10.7h), and
    none of his 161 comments falls between midnight and 05:00 Oslo. A
    default that cannot cross the longest of those puts us back where we
    started: a link handed over at 03:00 dead before he wakes.
    """
    from tools import demo as demo_cli

    longest_night_minutes = int(11.9 * 60)
    # And with real margin over it, not six minutes: eight samples do not
    # bound the longest night he will ever have.
    assert demo_cli.DEFAULT_UNOPENED_MINUTES > longest_night_minutes * 1.4
    assert demo_cli.DEFAULT_UNOPENED_MINUTES > longest_night_minutes
    # And it is a *longer* clock than the idle one, not a second name for it.
    assert demo_cli.DEFAULT_UNOPENED_MINUTES > demo_cli.DEFAULT_IDLE_MINUTES


def test_only_a_browser_counts_as_somebody_opening_a_demo():
    """A cycle's own proof-it-works fetch must not start the idle clock.

    This is the failure that made the fix above pointless in the only flow
    that uses it: Cycle 606 started a demo for the owner to open in the
    morning, fetched it once through the public route to prove it served,
    and thereby recorded it as opened.
    """
    from agora_runner.nova_demos import opened_by_a_person

    assert opened_by_a_person(
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36") is True
    for probe in ("Python-urllib/3.11", "curl/8.5.0", "kube-probe/1.29",
                  "Go-http-client/1.1", "", None):
        assert opened_by_a_person(probe) is False, probe


def test_list_says_which_clock_a_row_is_on():
    """`cmd_list`'s two messages mean opposite things to whoever reads them,
    and nothing pinned which one a row gets."""
    from tools import demo as demo_cli

    now = datetime.now().timestamp()
    started = datetime.fromtimestamp(now - 3600).isoformat()
    state, _read, _write = _fake_registry([
        {"slug": "waiting", "host": "10.42.0.56", "port": 5174, "pid": 4242,
         "started_at": started},
        {"slug": "quiet", "host": "10.42.0.56", "port": 5175, "pid": 4243,
         "started_at": started},
    ])
    activity = {"started_at": now - 2 * 3600,
                "last_seen": {"quiet": now - 1800}}
    out = io.StringIO()
    with patch.object(demo_cli, "_read_registry", _read), \
         patch.object(demo_cli, "pod_ip", lambda: "10.42.0.56"), \
         patch.object(demo_cli, "pid_alive", lambda pid: True), \
         patch.object(demo_cli, "fetch_activity", lambda *a, **kw: activity), \
         redirect_stdout(out):
        assert demo_cli.cmd_list(_args()) == 0
    printed = out.getvalue()
    assert "no recorded open" in printed
    assert "nobody has asked for it" in printed
    # And on the right rows: the waiting one is listed first.
    assert printed.index("no recorded open") < printed.index("nobody has asked")
    assert state  # the registry really was read


def test_mark_opened_writes_once_and_only_for_a_registered_slug():
    """The durable half of `no_recorded_open` -- Cycle 608, idea #134.

    `mark_opened` is called from the site's request path, so "already
    marked" has to be answerable without a write; that is what the second
    call asserts. An unregistered slug returns False rather than inventing
    a row -- a request for a slug nobody registered is not somebody looking
    at a demo, which is the same call `_serve_demo` already makes one frame
    up.
    """
    from datetime import datetime

    from agora_runner.nova_demos import OPENED_AT, mark_opened

    registry = {"demos": [{"slug": "roadmap", "port": 5174}]}
    when = datetime(2026, 8, 29, 4, 40, 0)
    assert mark_opened(registry, "roadmap", now=when) is True
    assert registry["demos"][0][OPENED_AT] == "2026-08-29T04:40:00"
    # A second open must not rewrite the stamp: the field records the first
    # time anyone looked, and a caller that sees False skips the vault write.
    assert mark_opened(registry, "roadmap", now=datetime(2026, 8, 29, 9, 0)) is False
    assert registry["demos"][0][OPENED_AT] == "2026-08-29T04:40:00"
    assert mark_opened(registry, "never-registered", now=when) is False
    assert len(registry["demos"]) == 1


def test_no_recorded_open_survives_a_site_roll_once_the_mark_is_written():
    """The bug Cycle 606 filed and Cycle 608 fixed.

    `last_seen` lives in the nova-site pod's memory, so a roll empties it
    and a demo the owner opened yesterday reads as never opened -- which
    moves it off the two-hour idle clock onto the eighteen-hour one and
    makes `tools.demo list` print "no recorded open" about a hand-off that
    landed. The registry survives the roll, so the mark does.

    `activity` here is a freshly rolled site: it answers, and its
    `last_seen` is empty. That is the exact state the old code got wrong.
    """
    from agora_runner.nova_demos import OPENED_AT, no_recorded_open

    rolled = {"started_at": 1000.0, "last_seen": {}}
    assert no_recorded_open({"slug": "roadmap"}, rolled) is True
    opened = {"slug": "roadmap", OPENED_AT: "2026-08-29T04:40:00"}
    assert no_recorded_open(opened, rolled) is False
    # And the mark does not let it answer when there is no site to ask.
    # `idle_seconds` returns None there and nothing is reaped either way;
    # claiming otherwise would be a statement about a clock this cannot read.
    assert no_recorded_open(opened, None) is False


def test_the_durable_open_mark_is_written_once_with_the_revision_it_read():
    """`_record_durable_open` -- the vault half, called synchronously here.

    Three things in one test because they are one behaviour: it writes the
    mark, it carries the `rev` it read so a cycle allocating a port in
    between loses nothing, and it does not write a second time.
    """
    from agora_runner.nova_demos import dumps as demo_dumps

    writes = []
    registry = demo_dumps({"demos": [
        {"slug": "roadmap", "host": "10.42.0.71", "port": 5174, "pid": 63440},
    ]})
    nova_site._demo_opened_marked.discard("roadmap")
    try:
        with patch.object(nova_site, "vault_read_path_rev",
                          lambda path: (registry, "9-abc")), \
             patch.object(nova_site, "vault_write_path",
                          lambda path, content, if_rev=None: (
                              writes.append((path, content, if_rev))
                              or "written")):
            nova_site._record_durable_open("roadmap")
            nova_site._record_durable_open("roadmap")
    finally:
        nova_site._demo_opened_marked.discard("roadmap")
    assert len(writes) == 1
    path, content, if_rev = writes[0]
    assert path == nova_site.DEMOS_PATH
    assert if_rev == "9-abc"
    written = json.loads(content)["demos"][0]
    assert written["opened_at"]
    # Everything else the row carried has to come back: this is a full
    # overwrite of the document that also allocates ports.
    assert written["port"] == 5174 and written["pid"] == 63440


@pytest.mark.parametrize("outcome", [
    # The real shape of a lost swap, and the one the first version of this
    # test got wrong: `vault_write_path` RETURNS `FAILED(...)`, it does not
    # raise. `vault.py`'s own docstring calls that the write contract, and
    # every other caller in this repo branches on the string. Writing the
    # handler as if a conflict were an exception left the slug marked and
    # silently reproduced the bug the whole change exists to fix -- my
    # reviewer found it, and this is the case that pins it.
    "FAILED(409 conflict: demos.json changed since it was read)",
    "FAILED(500)",
    # And a genuine exception -- CouchDB unreachable, say -- which must land
    # in the same place rather than escaping onto a bare thread.
    RuntimeError("connection refused"),
])
def test_a_failed_durable_write_lets_the_next_request_try_again(outcome):
    """A lost compare-and-swap must not mark the slug done forever.

    `tools.demo` writes this same document to allocate ports, so losing the
    swap is ordinary rather than exceptional. The demo simply stays on the
    long clock until some later asset on the page wins -- which is where it
    was before this existed.
    """
    from agora_runner.nova_demos import dumps as demo_dumps

    registry = demo_dumps({"demos": [{"slug": "roadmap", "port": 5174}]})
    attempts = []

    def _fail(path, content, if_rev=None):
        attempts.append(if_rev)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    nova_site._demo_opened_marked.discard("roadmap")
    try:
        with patch.object(nova_site, "vault_read_path_rev",
                          lambda path: (registry, "9-abc")), \
             patch.object(nova_site, "vault_write_path", _fail):
            nova_site._record_durable_open("roadmap")
            assert "roadmap" not in nova_site._demo_opened_marked
            nova_site._record_durable_open("roadmap")
    finally:
        nova_site._demo_opened_marked.discard("roadmap")
    assert len(attempts) == 2


def test_a_row_already_marked_costs_no_write_at_all():
    """The registry, not this process, is the memory -- that is the point.

    A pod that rolled has an empty `_demo_opened_marked`, so the first
    browser request after a roll re-enters here. Reading the mark off the
    document it just fetched is what stops that becoming a write per roll.
    """
    from agora_runner.nova_demos import dumps as demo_dumps

    registry = demo_dumps({"demos": [
        {"slug": "roadmap", "port": 5174, "opened_at": "2026-08-29T04:40:00"},
    ]})
    writes = []
    nova_site._demo_opened_marked.discard("roadmap")
    try:
        with patch.object(nova_site, "vault_read_path_rev",
                          lambda path: (registry, "9-abc")), \
             patch.object(nova_site, "vault_write_path",
                          lambda *a, **kw: writes.append(a)):
            nova_site._record_durable_open("roadmap")
    finally:
        nova_site._demo_opened_marked.discard("roadmap")
    assert writes == []
