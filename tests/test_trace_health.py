"""Tests for tools.trace_health.

The interesting half is what does NOT raise: a quiet joined-trace window, and
a service that has been silent for a full day. Both look like a regression to
a check that only counts, and neither is one.
"""

from tools import trace_health


def _spans(hour, day):
    return {"hour": hour, "day": day}


def _traces(read=100, joined=()):
    return {"read": read, "joined": list(joined)}


def test_all_services_emitting_is_clean():
    status, lines = report = trace_health.report(
        _spans({"agora": 16964.0, "nova-site": 892.0}, {"agora": 83229.0, "nova-site": 4405.0}),
        _traces(joined=[("abc", ["agora", "agora-persona-runner"])]),
    )
    assert status == 0
    assert not any("STOPPED TRACING" in line for line in lines)
    assert any("agora: 16964 span(s)/h, 83229 in 24h" in line for line in lines)
    assert report[1] is lines


def test_a_service_that_went_silent_raises_and_is_named():
    status, lines = trace_health.report(
        _spans({"agora": 16964.0}, {"agora": 83229.0, "marcus": 2448.0}),
        _traces(),
    )
    assert status == 2
    assert any("STOPPED TRACING" in line for line in lines)
    assert any("marcus: 2448 span(s) in 24h, 0 in the last hour" in line for line in lines)
    # The service still emitting must not be named as silent.
    assert not any(line.startswith("  agora: 83229 span(s) in 24h") for line in lines)


def test_a_service_absent_from_both_windows_is_not_a_finding():
    """A service that has not traced for a full day is gone, not broken.

    Without this the check would raise forever on every service ever deleted,
    which is the alarm that teaches a cycle to skip the alarm.
    """
    status, lines = trace_health.report(
        _spans({"agora": 100.0, "retired-service": 0.0},
               {"agora": 500.0, "retired-service": 0.0}),
        _traces(),
    )
    assert status == 0
    assert not any("STOPPED TRACING" in line for line in lines)
    # It is still printed -- the series exists, so hiding it would be a
    # different lie from raising on it.
    assert any("retired-service: 0 span(s)/h, 0 in 24h" in line for line in lines)


def test_no_joined_traces_does_not_raise():
    status, lines = trace_health.report(
        _spans({"agora": 100.0}, {"agora": 500.0}),
        _traces(read=100, joined=[]),
    )
    assert status == 0
    assert any("0 of the newest 100 trace(s) span more than one service" in line for line in lines)


def test_nothing_tracing_at_all_raises():
    status, lines = trace_health.report(_spans({}, {}), _traces())
    assert status == 2
    assert any("no service has emitted a span in 24h" in line for line in lines)


def test_unreadable_prometheus_is_exit_1_not_clean():
    status, lines = trace_health.report({"unreadable": "COULD NOT READ prometheus at x: boom"}, _traces())
    assert status == 1
    assert any("COULD NOT READ prometheus" in line for line in lines)


def test_unreadable_tempo_does_not_mask_a_silent_service():
    """Tempo being down must not downgrade a real span finding to exit 1."""
    status, lines = trace_health.report(
        _spans({"agora": 100.0}, {"agora": 500.0, "marcus": 2448.0}),
        {"unreadable": "COULD NOT READ tempo at x: boom"},
    )
    assert status == 2
    assert any("COULD NOT READ tempo" in line for line in lines)


def test_collect_traces_uses_service_stats_not_the_root_name():
    """A single-hop trace and a joined one share a rootServiceName.

    Counting roots would call every trace single-service; counting
    `serviceStats` keys is what actually reads the join.
    """
    payload = {
        "traces": [
            {"traceID": "one", "rootServiceName": "agora",
             "serviceStats": {"agora": {}, "agora-persona-runner": {}}},
            {"traceID": "two", "rootServiceName": "agora", "serviceStats": {"agora": {}}},
            {"traceID": "three", "rootServiceName": "agora"},
        ]
    }
    got = trace_health._joined_from(payload)
    assert got["read"] == 3
    assert [t[0] for t in got["joined"]] == ["one"]
    assert got["joined"][0][1] == ["agora", "agora-persona-runner"]


def test_span_series_ignores_a_result_with_no_service_name():
    data = {"result": [
        {"metric": {"service_name": "agora"}, "value": [0, "12.5"]},
        {"metric": {}, "value": [0, "99"]},
    ]}
    assert trace_health._series(data) == {"agora": 12.5}
