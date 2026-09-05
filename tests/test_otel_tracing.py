"""The first thing in this cluster that emits a span.

The collector went live on 2026-09-05 with no producer at all, so these
tests are about the two halves that can silently do nothing: the tracer
never being built, and the handler never opening a span even though it is.
"""

import pytest

from agora_runner import nova_site, otel


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
