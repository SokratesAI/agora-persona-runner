"""The first thing in this cluster that emits a span.

The collector went live on 2026-09-05 with no producer at all, so these
tests are about the two halves that can silently do nothing: the tracer
never being built, and the handler never opening a span even though it is.
"""

import io

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

    def start_as_current_span(self, name, context=None):
        span = _FakeSpan(name)
        span.context = context
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


# --- incoming trace context -------------------------------------------------
#
# These run against the real OpenTelemetry SDK rather than `_FakeTracer`,
# because the claim under test is about trace ids, and a fake that records
# whatever it is handed would agree with a broken implementation just as
# readily as with a working one.

_PARENT_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
_PARENT_SPAN = "00f067aa0ba902b7"
_TRACEPARENT = f"00-{_PARENT_TRACE}-{_PARENT_SPAN}-01"


@pytest.fixture
def real_tracer(monkeypatch):
    """A real tracer writing into memory, so trace ids are real trace ids."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(otel, "_tracer", provider.get_tracer("test"))
    return exporter


def test_an_incoming_traceparent_continues_the_callers_trace(real_tracer):
    """The whole point of instrumenting five services: one user action has to
    come back as one trace. Agora's auto-instrumentation already sends this
    header on every call it makes to this repo's servers; before this it was
    read by nothing and each service started a root trace of its own."""
    with otel.request_span("GET", "/api/journal", {"traceparent": _TRACEPARENT}):
        pass
    spans = real_tracer.get_finished_spans()
    assert len(spans) == 1
    assert format(spans[0].context.trace_id, "032x") == _PARENT_TRACE
    assert format(spans[0].parent.span_id, "016x") == _PARENT_SPAN


def test_header_case_does_not_decide_whether_the_trace_joins(real_tracer):
    """HTTP header names are case-insensitive on the wire and the propagator
    only ever looks for the lowercase spelling, so an upstream that sends
    `Traceparent` must not silently start a second trace."""
    with otel.request_span("GET", "/api/journal", {"Traceparent": _TRACEPARENT}):
        pass
    spans = real_tracer.get_finished_spans()
    assert format(spans[0].context.trace_id, "032x") == _PARENT_TRACE


def test_a_request_with_no_traceparent_is_still_a_root_span(real_tracer):
    """The control. Most requests arrive from a browser with no trace at all,
    and those must keep starting their own -- a test that only proves the
    join would pass against code that invented a parent."""
    with otel.request_span("GET", "/api/journal", {"user-agent": "curl"}):
        pass
    spans = real_tracer.get_finished_spans()
    assert spans[0].parent is None
    assert format(spans[0].context.trace_id, "032x") != _PARENT_TRACE


def test_a_malformed_traceparent_starts_its_own_trace(real_tracer):
    """The propagator does not raise on a bad header -- it returns a context
    with nothing valid in it -- so this pins the *outcome*, which is that a
    junk header degrades to an untraced-but-served request. It says nothing
    about the `except` below it; `test_headers_that_raise_...` does that."""
    with otel.request_span("GET", "/api/journal", {"traceparent": "not-a-traceparent"}):
        pass
    spans = real_tracer.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].parent is None


class _AngryHeaders:
    """The only shape that reaches the `except` -- a malformed traceparent
    does not, which a mutation of that block proved by surviving."""

    def items(self):
        raise RuntimeError("this header object is broken")


def test_headers_that_raise_do_not_take_the_request_down(real_tracer):
    with otel.request_span("GET", "/api/journal", _AngryHeaders()):
        pass
    spans = real_tracer.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].parent is None


def test_do_get_hands_the_request_headers_to_the_span(real_tracer, monkeypatch):
    """The extraction is worth nothing if the handler never passes the
    headers, and that half is invisible to every test above."""
    monkeypatch.setattr(nova_site.NovaSiteHandler, "_handle_get", lambda self: None)
    handler = _handler()
    handler.headers = {"traceparent": _TRACEPARENT}
    handler.do_GET()
    spans = real_tracer.get_finished_spans()
    assert format(spans[0].context.trace_id, "032x") == _PARENT_TRACE


def test_do_post_hands_the_request_headers_to_the_span(real_tracer, monkeypatch):
    monkeypatch.setattr(nova_site.NovaSiteHandler, "_handle_post", lambda self: None)
    handler = _handler()
    handler.command = "POST"
    handler.path = "/mcp"
    handler.headers = {"traceparent": _TRACEPARENT}
    handler.do_POST()
    spans = real_tracer.get_finished_spans()
    assert format(spans[0].context.trace_id, "032x") == _PARENT_TRACE


def test_it_reads_the_header_object_the_server_actually_passes(real_tracer):
    """Every test above hands `request_span` a plain dict. The real argument is
    `http.client.HTTPMessage`, which is an email.message.Message: it keeps
    repeated headers, preserves the case they were sent in, and is falsy when
    it is empty. This builds one off the wire bytes rather than standing in
    for it."""
    from http.client import parse_headers

    raw = (
        b"Host: nova-site:8083\r\n"
        b"Traceparent: " + _TRACEPARENT.encode() + b"\r\n"
        b"X-Dup: a\r\nX-Dup: b\r\n\r\n"
    )
    headers = parse_headers(io.BufferedReader(io.BytesIO(raw)))
    with otel.request_span("GET", "/api/journal?limit=1", headers):
        pass
    spans = real_tracer.get_finished_spans()
    assert format(spans[0].context.trace_id, "032x") == _PARENT_TRACE
    assert format(spans[0].parent.span_id, "016x") == _PARENT_SPAN


def test_invoke_do_post_hands_the_request_headers_to_the_span(real_tracer, monkeypatch):
    """This is the server Agora actually calls -- `/invoke`, `/tool-activity`
    and `/mcp` are all on it, and every tool call a cycle makes goes through
    `/mcp`. I fixed nova-site first and shipped this half only because the
    reviewer found it: my own `grep` for the call sites was truncated at
    twenty lines and this one fell off the end."""
    monkeypatch.setattr(invoke_server.InvokeHandler, "_handle_post", lambda self: None)
    handler = _invoke_handler("/mcp")
    handler.headers = {"traceparent": _TRACEPARENT}
    handler.do_POST()
    spans = real_tracer.get_finished_spans()
    assert format(spans[0].context.trace_id, "032x") == _PARENT_TRACE
    assert format(spans[0].parent.span_id, "016x") == _PARENT_SPAN


def test_invoke_without_a_traceparent_still_starts_its_own_trace(real_tracer, monkeypatch):
    """The control for the test above, on the same server."""
    monkeypatch.setattr(invoke_server.InvokeHandler, "_handle_post", lambda self: None)
    handler = _invoke_handler("/mcp")
    handler.headers = {"user-agent": "curl"}
    handler.do_POST()
    spans = real_tracer.get_finished_spans()
    assert spans[0].parent is None
    assert format(spans[0].context.trace_id, "032x") != _PARENT_TRACE


def test_a_repeated_traceparent_reads_the_same_one_HTTPMessage_does(real_tracer):
    """`HTTPMessage.get` returns the first value of a repeated header. A dict
    comprehension keeps the last, so without care this joins a different trace
    than every other reader of the same request."""
    from http.client import parse_headers

    other = "00-11111111111111111111111111111111-2222222222222222-01"
    raw = (
        b"Traceparent: " + _TRACEPARENT.encode() + b"\r\n"
        b"Traceparent: " + other.encode() + b"\r\n\r\n"
    )
    headers = parse_headers(io.BufferedReader(io.BytesIO(raw)))
    assert headers.get("traceparent") == _TRACEPARENT
    with otel.request_span("POST", "/mcp", headers):
        pass
    spans = real_tracer.get_finished_spans()
    assert format(spans[0].context.trace_id, "032x") == _PARENT_TRACE


def test_extract_context_is_none_when_there_are_no_headers():
    """`None` means "use the current context", which is what this did before
    the header was read at all."""
    assert otel.extract_context(None) is None
    assert otel.extract_context({}) is None


def test_an_outgoing_call_carries_the_trace_this_process_is_serving(real_tracer):
    """The other half of the hop. Agora calls this process, this process calls
    Agora back, and without an injected header that second call opens a trace
    of its own -- so one user action still comes back as several traces even
    though every service is instrumented."""
    with otel.request_span("POST", "/mcp", {"traceparent": _TRACEPARENT}):
        sent = otel.outgoing_headers({"x-agora-token": "kept"})
    assert sent["x-agora-token"] == "kept"
    assert sent["traceparent"].split("-")[1] == _PARENT_TRACE


def test_the_outgoing_span_id_is_this_processs_span_not_the_callers(real_tracer):
    """A child's parent is the span that made the call. Echoing the incoming
    `traceparent` verbatim would join the right trace and draw the wrong
    shape -- two siblings instead of a nested call."""
    with otel.request_span("POST", "/mcp", {"traceparent": _TRACEPARENT}):
        sent = otel.outgoing_headers()
    mine = format(real_tracer.get_finished_spans()[0].context.span_id, "016x")
    assert sent["traceparent"].split("-")[2] == mine
    assert sent["traceparent"].split("-")[2] != _PARENT_SPAN


def test_a_call_outside_any_span_sends_no_trace_header(real_tracer):
    """Most of this repo is a cron tick or a tool run from a shell. There is no
    request to be part of, and inventing a root trace for each of them would
    fill Tempo with single-span traces nobody asked for."""
    assert "traceparent" not in otel.outgoing_headers({"x-agora-token": "kept"})


def test_outgoing_headers_adds_nothing_when_tracing_is_off(monkeypatch):
    """Every test run and every local run. The dict has to come back the same."""
    monkeypatch.setattr(otel, "_tracer", None)
    assert otel.outgoing_headers({"Content-Type": "application/json"}) == {
        "Content-Type": "application/json"
    }
    assert otel.outgoing_headers() == {}


def test_outgoing_headers_does_not_mutate_the_caller(real_tracer):
    """`http_json` builds one dict and hands it to `urllib`; a function that
    edited it in place would work and would hide the missing return."""
    original = {"x-agora-token": "kept"}
    with otel.request_span("POST", "/mcp", None):
        otel.outgoing_headers(original)
    assert original == {"x-agora-token": "kept"}


def test_a_broken_propagator_does_not_fail_the_call(real_tracer, monkeypatch):
    """A tracing library is never worth an outage of the thing it watches."""
    import opentelemetry.propagate as propagate

    def boom(carrier, *a, **kw):
        raise RuntimeError("propagator exploded")

    monkeypatch.setattr(propagate, "inject", boom)
    with otel.request_span("POST", "/mcp", {"traceparent": _TRACEPARENT}):
        sent = otel.outgoing_headers({"x-agora-token": "kept"})
    assert sent == {"x-agora-token": "kept"}


def test_http_json_puts_the_trace_on_the_wire(real_tracer, monkeypatch):
    """The unit above proves the header is built; this proves it reaches the
    request object, which is the part a refactor of `http_json` would drop."""
    from agora_runner import http_util

    captured = {}

    class _Resp:
        status = 200
        headers = {}

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        return _Resp()

    monkeypatch.setattr(http_util.urllib.request, "urlopen", fake_urlopen)
    with otel.request_span("POST", "/mcp", {"traceparent": _TRACEPARENT}):
        status, _ = http_util.http_json("POST", "http://agora/x", {"a": 1})
    assert status == 200
    # `urllib.request.Request` title-cases the header names it is given.
    sent = {k.lower(): v for k, v in captured["headers"].items()}
    assert sent["content-type"] == "application/json"
    assert sent["traceparent"].split("-")[1] == _PARENT_TRACE


def test_http_json_outside_a_span_sends_what_it_always_sent(monkeypatch):
    """Tracing off is the state of every test run, so this is also the check
    that the import above did not change what an untraced call looks like."""
    from agora_runner import http_util

    monkeypatch.setattr(otel, "_tracer", None)
    captured = {}

    class _Resp:
        status = 200
        headers = {}

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        return _Resp()

    monkeypatch.setattr(http_util.urllib.request, "urlopen", fake_urlopen)
    http_util.http_json("GET", "http://agora/x", None, {"x-agora-token": "t"})
    sent = {k.lower() for k in captured["headers"]}
    assert sent == {"content-type", "accept-encoding", "x-agora-token"}


def test_tracing_off_does_not_reach_for_the_propagator_at_all(monkeypatch):
    """Dropping the `_tracer` guard passes every other test here, because a
    process with no provider has no current span and `inject` then writes
    nothing anyway. It is still wrong: this runs on every outgoing call in the
    repo, and with the SDK absent that is an ImportError and a log line per
    call rather than a branch not taken."""
    import opentelemetry.propagate as propagate

    calls = []
    monkeypatch.setattr(propagate, "inject", lambda carrier, *a, **kw: calls.append(carrier))
    monkeypatch.setattr(otel, "_tracer", None)
    assert otel.outgoing_headers({"x-agora-token": "kept"}) == {"x-agora-token": "kept"}
    assert calls == []
