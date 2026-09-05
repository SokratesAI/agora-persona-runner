"""Tests for tools.limit_headroom.

The interesting cases are the two ways this check can lie: a peak taken over a
store younger than the window it names, and a verdict keyed on the wrong
counter. Both are exercised here against a fake Prometheus rather than the live
one, so the assertions do not move when the cluster does.
"""

import urllib.parse

import pytest

from tools import limit_headroom as lh


MIB = 1024**2


def fake_get(*, coverage_hours=24.0, limits=(), peaks=(), now=1_000_000.0):
    """A stand-in for `tools.alerts._get` that answers from the given rows.

    `limits` and `peaks` are `(namespace, pod, container, node, bytes)` tuples.
    """

    def metric(row):
        namespace, pod, container, node, _ = row
        return {"namespace": namespace, "pod": pod, "container": container, "node": node}

    def vector(rows):
        return {
            "resultType": "vector",
            "result": [{"metric": metric(r), "value": [now, str(float(r[4]))]} for r in rows],
        }

    def get(base, path):
        expr = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)["query"][0]
        if expr == "time()":
            return {"resultType": "scalar", "result": [now, str(now)]}
        if expr.startswith("prometheus_tsdb_lowest_timestamp_seconds"):
            if coverage_hours is None:
                return {"resultType": "vector", "result": []}
            oldest = now - coverage_hours * 3600.0
            return {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [now, str(oldest)]}],
            }
        if expr.startswith("container_spec_memory_limit_bytes"):
            return vector(limits)
        if expr.startswith("max_over_time(container_memory_rss"):
            return vector(peaks)
        raise AssertionError("unexpected query %r" % expr)

    return get


def run(**kwargs):
    lines = []
    window = kwargs.pop("window_hours", 24.0)
    code = lh.report(window, base="http://fake", get=fake_get(**kwargs), out=lines.append)
    return code, "\n".join(lines)


def test_grafanas_actual_kill_ratio_raises():
    """190.3Mi of a 256Mi limit is what server2 killed on 2026-09-05.

    The threshold exists to catch that container before the kernel does, so the
    real number is the one pinned here rather than a round figure above it.
    """
    assert lh.verdict(190.3 * MIB, 256 * MIB) == "raise"


def test_the_threshold_sits_below_that_kill_and_above_half():
    assert lh.WATCH_AT < lh.RAISE_AT < 190.3 / 256


def test_verdict_bands():
    assert lh.verdict(70 * MIB, 100 * MIB) == "raise"
    assert lh.verdict(69 * MIB, 100 * MIB) == "watch"
    assert lh.verdict(50 * MIB, 100 * MIB) == "watch"
    assert lh.verdict(49 * MIB, 100 * MIB) == "ok"


def test_a_container_over_the_line_raises_and_is_named():
    code, text = run(
        limits=[("infra", "grafana-1", "grafana", "server2", 256 * MIB)],
        peaks=[("infra", "grafana-1", "grafana", "server2", 190 * MIB)],
    )
    assert code == 2
    assert "NEAR LIMIT" in text
    assert "infra/grafana" in text and "grafana-1" in text and "server2" in text


def test_a_young_store_cannot_produce_a_clean_verdict():
    """The failure this guards is a Prometheus restart making everything look safe.

    Nothing here is near its limit, so without the coverage check this sweep
    would exit 0 on 1 hour of history while claiming a 24-hour peak.
    """
    code, text = run(
        coverage_hours=1.0,
        limits=[("agents", "quiet-1", "quiet", "server2", 1024 * MIB)],
        peaks=[("agents", "quiet-1", "quiet", "server2", 10 * MIB)],
    )
    assert code == 1
    assert "STORE TOO YOUNG" in text
    assert "unreadable rather than clean" in text


def test_the_same_rows_are_clean_when_the_store_covers_the_window():
    """The control for the test above: without it, TOO YOUNG could be unconditional."""
    code, text = run(
        coverage_hours=24.0,
        limits=[("agents", "quiet-1", "quiet", "server2", 1024 * MIB)],
        peaks=[("agents", "quiet-1", "quiet", "server2", 10 * MIB)],
    )
    assert code == 0
    assert "STORE TOO YOUNG" not in text


def test_a_raise_survives_a_young_store():
    """A short window can only read low, so a container over the line in one is over it."""
    code, text = run(
        coverage_hours=0.5,
        limits=[("infra", "grafana-1", "grafana", "server2", 256 * MIB)],
        peaks=[("infra", "grafana-1", "grafana", "server2", 200 * MIB)],
    )
    assert code == 2
    assert "STORE TOO YOUNG" in text


def test_an_unanswerable_store_age_is_not_clean():
    code, text = run(
        coverage_hours=None,
        limits=[("agents", "quiet-1", "quiet", "server2", 1024 * MIB)],
        peaks=[("agents", "quiet-1", "quiet", "server2", 10 * MIB)],
    )
    assert code == 1
    assert "CANNOT READ how much history" in text


def test_a_container_with_no_rss_series_is_left_out_not_zeroed():
    rows = lh.read_containers(
        24.0,
        base="http://fake",
        get=fake_get(
            limits=[
                ("agents", "a-1", "a", "server1", 256 * MIB),
                ("agents", "b-1", "b", "server1", 256 * MIB),
            ],
            peaks=[("agents", "a-1", "a", "server1", 10 * MIB)],
        ),
    )
    assert [row[2] for row in rows] == ["a"]


def test_nothing_to_judge_is_not_clean():
    code, text = run(limits=[], peaks=[])
    assert code == 1
    assert "CANNOT READ" in text


def test_rows_come_back_worst_first():
    rows = lh.read_containers(
        24.0,
        base="http://fake",
        get=fake_get(
            limits=[
                ("agents", "a-1", "a", "server1", 100 * MIB),
                ("agents", "b-1", "b", "server1", 100 * MIB),
            ],
            peaks=[
                ("agents", "a-1", "a", "server1", 10 * MIB),
                ("agents", "b-1", "b", "server1", 80 * MIB),
            ],
        ),
    )
    assert [row[2] for row in rows] == ["b", "a"]


def test_the_window_reaches_the_query_it_names():
    """A window argument that never lands in the PromQL would silently be 24h forever."""
    seen = []
    base_get = fake_get(
        limits=[("agents", "a-1", "a", "server1", 100 * MIB)],
        peaks=[("agents", "a-1", "a", "server1", 10 * MIB)],
    )

    def get(base, path):
        seen.append(urllib.parse.parse_qs(urllib.parse.urlparse(path).query)["query"][0])
        return base_get(base, path)

    lh.read_containers(6.0, base="http://fake", get=get)
    assert any("container_memory_rss{container!=\"\"}[6h]" in q for q in seen)


@pytest.mark.parametrize("counter", ["container_memory_working_set_bytes",
                                     "container_memory_max_usage_bytes",
                                     "container_memory_failcnt"])
def test_the_discarded_counters_are_not_queried(counter):
    """Measured 2026-09-05: max-usage reads 100% of the limit for two healthy pods,
    and failcnt is empty for every series under cgroup v2. Both are named in the
    docstring as unusable, so neither may quietly come back into the query."""
    seen = []
    base_get = fake_get(
        limits=[("agents", "a-1", "a", "server1", 100 * MIB)],
        peaks=[("agents", "a-1", "a", "server1", 10 * MIB)],
    )

    def get(base, path):
        seen.append(urllib.parse.parse_qs(urllib.parse.urlparse(path).query)["query"][0])
        return base_get(base, path)

    lh.report(24.0, base="http://fake", get=get, out=lambda _: None)
    assert seen and not any(counter in q for q in seen)
