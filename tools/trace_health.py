"""Is every service that traces still tracing, and are its traces still joined?

Idea #257 took eleven cycles to build: five services emit OpenTelemetry spans,
each one continues a `traceparent` it is handed, a tail sampler keeps every
error and one ordinary success in ten, a span-metrics connector counts before
the sampler, and Tempo stores the survivors on a 2Gi disk behind a Grafana
row. Nothing checked any of it. A NetworkPolicy, a dropped env var or a rolled
image could take a service's spans away and the only signal would be a cycle
happening to look -- which is how every other check in `tools.preflight` came
to exist.

**The raise is self-calibrating, because a threshold I invented would be the
whole bug.** The list of services is not written down here: it is whichever
`service_name` labels Prometheus has seen in the last 24 hours. A service is
raised when it emitted spans in that window and **zero** in the last hour.
Zero is the one number that needs no judgement -- it says the pipeline from
that process to Prometheus is broken, not that the platform was quiet. I
measured the alternative before choosing it (2026-09-06 00:33 Oslo): across
every hourly bucket the store holds, the quietest service-hour on record is
`agora-persona-runner` at 16 spans and no service has a zero hour, so a
non-zero floor would be a number with nothing behind it.

**Joined traces are reported and deliberately do not raise.** A trace spanning
two services proves propagation end to end, and there were 4 of them in the
newest 100 when I wrote this. But the sampler keeps a tenth of ordinary
successes and the platform is genuinely idle at night, so a window with none
is a quiet hour rather than a regression. Raising on it would be the alarm
that cries wolf until a cycle learns to skip it -- issue #88's failure shape.
The count is printed so a cycle can see it fall to zero and go looking.

Exit 0 clean, exit 2 a service stopped emitting, exit 1 neither store could be
read. Unreadable never reads as clean.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROMETHEUS = "http://prometheus.infra.svc.cluster.local:9090"
TEMPO = "http://tempo.infra.svc.cluster.local:3200"
TIMEOUT = 30

#: How many of the newest traces to ask Tempo for when counting joined ones.
#: Not a threshold -- nothing is judged against it; it bounds one query.
TRACE_SAMPLE = 100


def _prom(base: str, expr: str) -> dict:
    url = base + "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise ValueError(f"prometheus answered status={payload.get('status')!r}")
    return payload["data"]


def _series(data: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for entry in data.get("result", []):
        name = (entry.get("metric") or {}).get("service_name")
        if name:
            out[name] = float(entry["value"][1])
    return out


def collect_spans(base: str = PROMETHEUS) -> dict:
    """Span counts per service over the last hour and the last 24 hours."""
    expr = "sum by (service_name) (increase(traces_span_metrics_calls_total[%s]))"
    try:
        hour = _series(_prom(base, expr % "1h"))
        day = _series(_prom(base, expr % "24h"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"unreadable": f"COULD NOT READ prometheus at {base}: {exc}"}
    return {"hour": hour, "day": day}


def collect_traces(base: str = TEMPO, limit: int = TRACE_SAMPLE) -> dict:
    """How many of the newest traces span more than one service."""
    url = base + "/api/search?" + urllib.parse.urlencode({"q": "{}", "limit": str(limit)})
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"unreadable": f"COULD NOT READ tempo at {base}: {exc}"}

    return _joined_from(payload)


def _joined_from(payload: dict) -> dict:
    """Split from the fetch so the join rule is testable without a Tempo."""
    traces = payload.get("traces") or []
    joined: list[tuple[str, list[str]]] = []
    for trace in traces:
        # `serviceStats` is Tempo's own per-service breakdown of the trace. A
        # trace with one entry is single-hop; the root service name alone
        # cannot tell them apart, which is why it is only the fallback.
        stats = trace.get("serviceStats") or {}
        names = sorted(stats) if stats else [str(trace.get("rootServiceName") or "?")]
        if len(names) > 1:
            joined.append((str(trace.get("traceID") or "?"), names))
    return {"read": len(traces), "joined": joined}


def report(spans: dict, traces: dict) -> tuple[int, list[str]]:
    lines: list[str] = []
    status = 0

    if "unreadable" in spans:
        lines.append(spans["unreadable"])
        status = 1
    else:
        hour, day = spans["hour"], spans["day"]
        silent = sorted(name for name, count in day.items() if count > 0 and hour.get(name, 0) == 0)
        if silent:
            status = 2
            lines.append(
                f"STOPPED TRACING — {len(silent)} service(s) emitted spans in the last 24h "
                "and none in the last hour."
            )
            for name in silent:
                lines.append(f"  {name}: {day[name]:.0f} span(s) in 24h, 0 in the last hour")
        for name in sorted(day):
            lines.append(f"  {name}: {hour.get(name, 0):.0f} span(s)/h, {day[name]:.0f} in 24h")
        if not day:
            status = 2
            lines.append(
                "STOPPED TRACING — no service has emitted a span in 24h. The span-metrics "
                "connector counts before the sampler, so this is not a sampling effect."
            )

    if "unreadable" in traces:
        lines.append(traces["unreadable"])
        status = max(status, 1)
    else:
        joined = traces["joined"]
        lines.append(
            f"{len(joined)} of the newest {traces['read']} trace(s) span more than one service. "
            "Not judged: a window with none is a quiet platform, not a regression."
        )
        for trace_id, names in joined[:3]:
            lines.append(f"  {trace_id}  {' -> '.join(names)}")

    lines.append(
        "Read from the live prometheus and tempo, not from git. Which services should be "
        "tracing is never written down here — it is whichever ones Prometheus has seen in "
        "the last 24h, so a service added or removed needs no edit."
    )
    return status, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prometheus", default=PROMETHEUS)
    parser.add_argument("--tempo", default=TEMPO)
    args = parser.parse_args(argv)

    status, lines = report(collect_spans(args.prometheus), collect_traces(args.tempo))
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
