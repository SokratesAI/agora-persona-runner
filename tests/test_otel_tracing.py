"""The first thing in this cluster that emits a span.

The collector went live on 2026-09-05 with no producer at all, so these
tests are about the two halves that can silently do nothing: the tracer
never being built, and the handler never opening a span even though it is.
"""

import pytest

from importlib import import_module

# `agora_runner/__init__` does `from agora_runner.main import *`, so the
# package attribute `main` is the *function*, not the module -- a plain
# `import agora_runner.main as runner_main` binds the function and
# monkeypatch then fails with a bare AttributeError.
runner_main = import_module("agora_runner.main")
from agora_runner import invoke_server, nova_site, nova_site_main, otel


class _StopMain(Exception):
    """Cuts `main()` off after the tracing call, which is all these test."""


def _raise_stop(*args, **kwargs):
    raise _StopMain()


class _SignalStub:
    """`main()` installs SIGTERM handlers; a test process must keep its own."""

    SIGTERM = 15
    SIGINT = 2

    @staticmethod
    def signal(signum, handler):
        return None


class _FakeSpan:
    def __init__(self, name):
        self.name = name
        self.attributes = {}
        self.exceptions = []

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def record_exception(self, exc):
        self.exceptions.append(exc)


class _FakeTracer:
    def __init__(self):
        self.spans = []

    def start_as_current_span(self, name):
        span = _FakeSpan(name)
        self.spans.append(span)

        class _Ctx:
            def __enter__(self_inner):
                return span

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()


@pytest.fixture
def tracer(monkeypatch):
    fake = _FakeTracer()
    monkeypatch.setattr(otel, "_tracer", fake)
    return fake


def test_tracing_is_off_when_the_endpoint_is_unset(monkeypatch):
    """Every test run and every local run is this case, so it must be silent."""
    monkeypatch.delenv(otel.ENDPOINT_ENV, raising=False)
    monkeypatch.setattr(otel, "_tracer", None)
    assert otel.init_tracing() is None
    with otel.request_span("GET", "/journal") as recorder:
        recorder.set_status_code(200)
        recorder.record_exception(ValueError("nothing should blow up"))


def test_init_tracing_does_not_build_a_second_exporter(monkeypatch):
    """Two calls must hand back one tracer -- two exporters would double
    every span on the wire and neither would be wrong-looking locally."""
    sentinel = object()
    monkeypatch.setattr(otel, "_tracer", sentinel)
    monkeypatch.setenv(otel.ENDPOINT_ENV, "http://otel-collector.infra.svc.cluster.local:4318")
    assert otel.init_tracing() is sentinel


def test_span_is_named_for_the_route_without_the_query(tracer):
    """`/api/board?name=ideas&q=otel` and `/api/board?name=issues` are one
    route. Naming spans by the raw path makes every request its own name."""
    with otel.request_span("GET", "/api/board?name=ideas&q=otel"):
        pass
    assert [s.name for s in tracer.spans] == ["GET /api/board"]
    assert tracer.spans[0].attributes["url.path"] == "/api/board"
    assert tracer.spans[0].attributes["http.request.method"] == "GET"


def test_status_code_lands_on_the_span(tracer):
    with otel.request_span("GET", "/journal") as recorder:
        recorder.set_status_code(404)
    assert tracer.spans[0].attributes["http.response.status_code"] == 404


def _handler():
    handler = object.__new__(nova_site.NovaSiteHandler)
    handler.command = "GET"
    handler.path = "/api/journal?limit=1"
    return handler


def test_do_get_opens_a_span_around_the_real_routing(tracer, monkeypatch):
    """The wrapper is the whole feature: `_handle_get` holds the routing and
    nothing else calls it, so an unwrapped `do_GET` traces nothing."""
    called = []
    monkeypatch.setattr(nova_site.NovaSiteHandler, "_handle_get", lambda self: called.append(self.path))
    handler = _handler()
    handler.do_GET()
    assert called == ["/api/journal?limit=1"]
    assert [s.name for s in tracer.spans] == ["GET /api/journal"]


def test_do_post_opens_a_span_around_the_real_routing(tracer, monkeypatch):
    called = []
    monkeypatch.setattr(nova_site.NovaSiteHandler, "_handle_post", lambda self: called.append(self.path))
    handler = _handler()
    handler.command = "POST"
    handler.path = "/api/capture"
    handler.do_POST()
    assert called == ["/api/capture"]
    assert [s.name for s in tracer.spans] == ["POST /api/capture"]


def test_a_head_request_is_not_traced_as_a_get(tracer, monkeypatch):
    """`do_HEAD` calls `do_GET`. Reading the method off `self.command`
    rather than off the wrapper's own name is what keeps that honest."""
    monkeypatch.setattr(nova_site.NovaSiteHandler, "_handle_get", lambda self: None)
    handler = _handler()
    handler.command = "HEAD"
    handler.path = "/"
    handler.do_HEAD()
    assert [s.name for s in tracer.spans] == ["HEAD /"]


def test_send_response_only_records_the_status_on_the_open_span(tracer, monkeypatch):
    """Every reply funnels through `send_response_only`, so a span gets its
    status without twenty call sites knowing about tracing."""
    sent = []
    monkeypatch.setattr(
        nova_site.BaseHTTPRequestHandler,
        "send_response_only",
        lambda self, code, message=None: sent.append(code),
    )

    def _routing(self):
        self.send_response_only(503)

    monkeypatch.setattr(nova_site.NovaSiteHandler, "_handle_get", _routing)
    handler = _handler()
    handler.do_GET()
    assert sent == [503]
    assert tracer.spans[0].attributes["http.response.status_code"] == 503


def test_send_response_only_survives_a_handler_with_no_span(monkeypatch):
    """Several tests build a handler and call an inner method directly. A
    status sent outside `do_GET` must not raise on a missing recorder."""
    sent = []
    monkeypatch.setattr(
        nova_site.BaseHTTPRequestHandler,
        "send_response_only",
        lambda self, code, message=None: sent.append(code),
    )
    handler = object.__new__(nova_site.NovaSiteHandler)
    handler.send_response_only(200)
    assert sent == [200]


# --- the runner's own HTTP server -------------------------------------
#
# The runner and nova-site share an image and a module, and until now only
# one of them emitted anything. These are the same two silent failures as
# above, asked of the other process: the wrapper never opening a span, and
# the service naming itself after its housemate.


def _invoke_handler(path="/mcp"):
    handler = object.__new__(invoke_server.InvokeHandler)
    handler.command = "POST"
    handler.path = path
    return handler


def test_invoke_do_post_opens_a_span_around_the_real_routing(tracer, monkeypatch):
    """`_handle_post` holds every route on this server and nothing else
    calls it, so an unwrapped `do_POST` traces nothing at all."""
    called = []
    monkeypatch.setattr(
        invoke_server.InvokeHandler, "_handle_post", lambda self: called.append(self.path)
    )
    handler = _invoke_handler("/tool-activity")
    handler.do_POST()
    assert called == ["/tool-activity"]
    assert [s.name for s in tracer.spans] == ["POST /tool-activity"]


def test_invoke_span_drops_the_query_string(tracer, monkeypatch):
    monkeypatch.setattr(invoke_server.InvokeHandler, "_handle_post", lambda self: None)
    handler = _invoke_handler("/invoke?preview=1")
    handler.do_POST()
    assert [s.name for s in tracer.spans] == ["POST /invoke"]
    assert tracer.spans[0].attributes["url.path"] == "/invoke"


def test_invoke_send_response_only_records_the_status(tracer, monkeypatch):
    """Every reply on this server goes through `_send`, which calls
    `send_response`, which lands here."""
    sent = []
    monkeypatch.setattr(
        invoke_server.BaseHTTPRequestHandler,
        "send_response_only",
        lambda self, code, message=None: sent.append(code),
    )

    def _routing(self):
        self.send_response_only(401)

    monkeypatch.setattr(invoke_server.InvokeHandler, "_handle_post", _routing)
    handler = _invoke_handler()
    handler.do_POST()
    assert sent == [401]
    assert tracer.spans[0].attributes["http.response.status_code"] == 401


def test_invoke_send_response_only_survives_a_handler_with_no_span(monkeypatch):
    sent = []
    monkeypatch.setattr(
        invoke_server.BaseHTTPRequestHandler,
        "send_response_only",
        lambda self, code, message=None: sent.append(code),
    )
    handler = object.__new__(invoke_server.InvokeHandler)
    handler.send_response_only(200)
    assert sent == [200]


def test_each_entrypoint_names_its_own_service(monkeypatch):
    """The two processes share an image, so a runner that inherited the
    module default would file its spans under `nova-site` and quietly mix
    two services' latency into one line on the Traces row."""
    named = []
    monkeypatch.setattr(runner_main, "init_tracing", lambda name=None: named.append(name))
    monkeypatch.setattr(runner_main, "start_invoke_server", lambda: None)
    monkeypatch.setattr(runner_main, "start_catalog_refresh", _raise_stop)
    monkeypatch.setattr(runner_main, "signal", _SignalStub())
    with pytest.raises(_StopMain):
        runner_main.main()
    assert named == ["agora-persona-runner"]

    named.clear()
    monkeypatch.setattr(nova_site_main, "init_tracing", lambda name=None: named.append(name))
    monkeypatch.setattr(nova_site_main, "start_nova_site", _raise_stop)
    monkeypatch.setattr(nova_site_main, "signal", _SignalStub())
    with pytest.raises(_StopMain):
        nova_site_main.main()
    assert named == ["nova-site"]


def test_the_module_default_is_not_a_real_service_name():
    """A third caller that forgets to name itself must be obvious in Tempo
    rather than landing on top of one of these two."""
    assert otel.DEFAULT_SERVICE_NAME not in ("nova-site", "agora-persona-runner")


def test_the_environment_still_wins_over_the_process_name(monkeypatch):
    monkeypatch.setenv(otel.SERVICE_NAME_ENV, "renamed-by-the-manifest")
    assert otel.service_name("agora-persona-runner") == "renamed-by-the-manifest"
    monkeypatch.setenv(otel.SERVICE_NAME_ENV, "   ")
    assert otel.service_name("agora-persona-runner") == "agora-persona-runner"
