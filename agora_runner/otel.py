"""OpenTelemetry tracing for the nova-site process.

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
#: same image runs the runner process and would want its own name.
SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"

DEFAULT_SERVICE_NAME = "nova-site"

# Resolved once by init_tracing() and read by request_span(). None means
# tracing is off, which is the state for every test run and every local
# run, because neither sets the endpoint.
_tracer = None


def endpoint():
    return (os.environ.get(ENDPOINT_ENV) or "").strip()


def service_name():
    return (os.environ.get(SERVICE_NAME_ENV) or "").strip() or DEFAULT_SERVICE_NAME


def init_tracing():
    """Build the tracer, or return None and say why in the log.

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
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name()}))
        # The exporter reads OTEL_EXPORTER_OTLP_ENDPOINT itself and appends
        # /v1/traces to it; passing the endpoint again here would produce
        # two different behaviours for the same variable.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("nova_site")
    except Exception as e:  # pragma: no cover - a broken SDK must not take the site down
        log(f"otel: tracing off, could not build the tracer ({e})")
        return None
    log(f"otel: tracing on, {service_name()} -> {endpoint()}")
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


@contextmanager
def request_span(method, path):
    """Trace one HTTP request. A no-op when tracing is off.

    `path` is the raw request path and is split from its query string:
    the query carries row numbers and search text, which would make every
    request its own span name and is exactly the cardinality Tempo
    charges for.
    """
    route = (path or "/").split("?", 1)[0]
    if _tracer is None:
        yield _Recorder()
        return
    with _tracer.start_as_current_span(f"{method} {route}") as span:
        span.set_attribute("http.request.method", method)
        span.set_attribute("url.path", route)
        yield _Recorder(span)
