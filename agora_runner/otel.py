"""OpenTelemetry tracing for the two HTTP servers in this repo.

The collector has been live in `infra` since 2026-09-05 and nothing in
this cluster emitted a span to it -- it was proven with three spans sent
by hand and then had no producer at all. This module is the first real
one.

Two shapes were possible and this is deliberately not the auto-
instrumentation one. `opentelemetry-instrument` works by patching a
framework's request path, and nova-site has no framework: it is
`BaseHTTPRequestHandler` out of the standard library, which no
OpenTelemetry instrumentation package covers. So the span has to be
opened by hand wherever the request is handled, and once that is true the
auto-instrumentation wrapper buys nothing and costs an entrypoint change
in the Deployment.

Everything here is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set, and
every failure path returns "no tracing" rather than raising. That is not
defensive habit: this module is imported by the process that serves the
owner's phone, and a tracing library is never worth an outage of the
thing it is watching.
"""

import os
from contextlib import contextmanager

from agora_runner.log import log

#: The collector address. Set as env on the Deployment rather than
#: hardcoded here, so a move of the collector is a manifest change.
ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"

#: What the service calls itself in Tempo. Also env-driven, because the
#: same image runs the runner process and wants its own name.
SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"

#: Used only when neither the environment nor the caller names the
#: service. It is deliberately not a real service any more: this image
#: runs two processes, so a constant that names one of them turns a
#: missing env var on the *other* into spans filed under a name that is
#: already taken -- which is worse than an obviously wrong name, because
#: the Traces row would keep drawing one line and silently mix two
#: services' latency into it. Each entrypoint passes its own name to
#: `init_tracing`, so this is what a third caller that forgot gets.
DEFAULT_SERVICE_NAME = "unnamed-service"

# Resolved once by init_tracing() and read by request_span(). None means
# tracing is off, which is the state for every test run and every local
# run, because neither sets the endpoint.
_tracer = None


def endpoint():
    return (os.environ.get(ENDPOINT_ENV) or "").strip()


def service_name(default=DEFAULT_SERVICE_NAME):
    return (os.environ.get(SERVICE_NAME_ENV) or "").strip() or default


def init_tracing(default_service_name=DEFAULT_SERVICE_NAME):
    """Build the tracer, or return None and say why in the log.

    `default_service_name` is what this process calls itself when
    `OTEL_SERVICE_NAME` is unset. The environment still wins, so a
    manifest can rename a service without a code change.

    Idempotent: the second call returns the tracer the first one built,
    so importing this from more than one place cannot install two
    exporters onto the same process.
    """
    global _tracer
    if _tracer is not None:
        return _tracer
    if not endpoint():
        log(f"otel: tracing off, {ENDPOINT_ENV} is not set")
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        log(f"otel: tracing off, OpenTelemetry is not installed ({e})")
        return None
    try:
        name = service_name(default_service_name)
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: name}))
        # The exporter reads OTEL_EXPORTER_OTLP_ENDPOINT itself and appends
        # /v1/traces to it; passing the endpoint again here would produce
        # two different behaviours for the same variable.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(name)
    except Exception as e:  # pragma: no cover - a broken SDK must not take the site down
        log(f"otel: tracing off, could not build the tracer ({e})")
        return None
    log(f"otel: tracing on, {service_name(default_service_name)} -> {endpoint()}")
    return _tracer


class _Recorder:
    """What `request_span` hands back, so a caller never touches a span.

    A caller that has to ask "is tracing on" before every attribute is a
    caller that will get it wrong once. This is the same object whether
    or not there is a span behind it.
    """

    def __init__(self, span=None):
        self._span = span

    def set_status_code(self, code):
        if self._span is not None and code is not None:
            self._span.set_attribute("http.response.status_code", int(code))

    def record_exception(self, exc):
        if self._span is not None:
            self._span.record_exception(exc)


def extract_context(headers):
    """The W3C trace context the caller sent, or None if it sent none.

    Without this every service in the cluster starts a *root* trace on
    every request, so five instrumented services produce five unrelated
    single-span traces for one user action and the word "distributed" in
    distributed tracing buys nothing. Agora's outgoing HTTP is patched by
    `@opentelemetry/auto-instrumentations-node`, which injects
    `traceparent` on every call it makes -- including the calls it makes
    to this repo's two servers -- so the header is already arriving and
    was being dropped on the floor.

    Header names are lowercased because the propagator looks up
    `traceparent` in exactly that spelling and HTTP header names are
    case-insensitive on the wire.

    Returns None when there is nothing to read and on every raised failure,
    which `start_as_current_span` reads as "use the current context" -- the
    behaviour this had before. Headers that are present but carry no
    `traceparent` come back as an empty context rather than None, which
    starts a root span for the same reason; either way a caller that sends
    a malformed header gets an untraced-but-served request, not a 500.
    """
    if not headers:
        return None
    try:
        from opentelemetry.propagate import extract
    except ImportError:  # pragma: no cover - the SDK is absent, tracing is off anyway
        return None
    try:
        carrier = {}
        for k, v in headers.items():
            # First wins, which is what `HTTPMessage.get` does. A dict
            # comprehension keeps the *last*, so a request carrying two
            # `traceparent` headers would join a different trace here than
            # every other reader of the same request sees.
            carrier.setdefault(str(k).lower(), v)
        return extract(carrier)
    except Exception as e:  # a bad header must not take the site down
        log(f"otel: could not read the incoming trace context ({e})")
        return None


@contextmanager
def request_span(method, path, headers=None):
    """Trace one HTTP request. A no-op when tracing is off.

    `path` is the raw request path and is split from its query string:
    the query carries row numbers and search text, which would make every
    request its own span name and is exactly the cardinality Tempo
    charges for.

    `headers` is the incoming request's headers. Pass them: they carry the
    caller's trace, and a span opened without them is a new root trace
    rather than the next step of the one already running.
    """
    route = (path or "/").split("?", 1)[0]
    if _tracer is None:
        yield _Recorder()
        return
    parent = extract_context(headers)
    with _tracer.start_as_current_span(f"{method} {route}", context=parent) as span:
        span.set_attribute("http.request.method", method)
        span.set_attribute("url.path", route)
        yield _Recorder(span)
