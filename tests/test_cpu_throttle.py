"""Tests for tools.cpu_throttle -- the CPU half of limit_headroom.

The fixture text below is trimmed from a real `nodes/server2/proxy/metrics/cadvisor`
scrape taken 2026-09-06 14:14 Oslo, keeping the two counter families this reads
and one pod-level row (empty `container` label) so the parser's drop of it is
exercised on the shape it actually meets.
"""

import tools.cpu_throttle as ct


LABELS = ('container="{c}",id="/kubepods.slice/x.scope",image="ghcr.io/x@sha256:d",'
          'name="x",namespace="{ns}",pod="{pod}"')


def _row(metric, ns, pod, container, value):
    return (f"container_cpu_cfs_{metric}_total{{"
            + LABELS.format(c=container, ns=ns, pod=pod)
            + f"}} {value} 1788696725074")


SCRAPE = "\n".join([
    "# HELP container_cpu_cfs_periods_total Number of elapsed enforcement periods",
    "# TYPE container_cpu_cfs_periods_total counter",
    _row("periods", "agents", "agora-1", "agora", 167779),
    _row("throttled_periods", "agents", "agora-1", "agora", 83566),
    _row("periods", "obsidian", "couchdb-1", "couchdb", 5000),
    _row("throttled_periods", "obsidian", "couchdb-1", "couchdb", 1000),
    # The pod-level cgroup, which aggregates the container above it.
    'container_cpu_cfs_periods_total{container="",id="/kubepods.slice/x.slice",'
    'namespace="agents",pod="agora-1"} 999999 1788696725074',
    "container_memory_rss{container=\"agora\",namespace=\"agents\",pod=\"agora-1\"} 1234",
])


def test_parse_keeps_containers_and_drops_the_pod_level_row():
    parsed = ct.parse_cadvisor(SCRAPE)
    assert set(parsed) == {("agents", "agora-1", "agora"),
                           ("obsidian", "couchdb-1", "couchdb")}
    assert parsed[("agents", "agora-1", "agora")] == {"periods": 167779.0,
                                                      "throttled": 83566.0}


def test_parse_ignores_an_unrelated_metric_family():
    # container_memory_rss is in the fixture and carries the same labels; a
    # parser matching on the label block alone would pick it up.
    assert all("rss" not in str(v) for v in ct.parse_cadvisor(SCRAPE).values())


def test_deltas_drops_a_container_that_only_appears_in_one_scrape():
    before = {("a", "p", "c"): {"periods": 10.0, "throttled": 1.0}}
    after = dict(before)
    after[("a", "p", "new")] = {"periods": 500.0, "throttled": 400.0}
    after[("a", "p", "c")] = {"periods": 20.0, "throttled": 2.0}
    rows = ct.deltas(before, after)
    assert set(rows) == {("a", "p", "c")}
    assert rows[("a", "p", "c")] == (10.0, 1.0)


def test_deltas_drops_a_restarted_container_whose_counters_reset():
    before = {("a", "p", "c"): {"periods": 5000.0, "throttled": 4000.0}}
    after = {("a", "p", "c"): {"periods": 12.0, "throttled": 3.0}}
    assert ct.deltas(before, after) == {}


def test_a_container_over_the_line_raises_and_is_named():
    rows = {("agents", "agora-1", "agora"): (1000.0, 787.0)}
    lines, code = ct.judge(rows, 20.0)
    assert code == 2
    assert any("THROTTLED" in ln and "78.7%" in ln and "agents/agora-1" in ln
               for ln in lines)


def test_a_throttled_container_under_the_line_is_printed_without_raising():
    # couchdb measured 31.5% on 2026-09-06 while nothing was wrong with it.
    rows = {("obsidian", "couchdb-1", "couchdb"): (1000.0, 315.0)}
    lines, code = ct.judge(rows, 20.0)
    assert code == 0
    assert any("31.5%" in ln and "couchdb" in ln for ln in lines)
    assert not any("THROTTLED" in ln for ln in lines)


def test_the_line_is_exclusive_so_exactly_the_threshold_does_not_raise():
    rows = {("a", "p", "c"): (1000.0, 500.0)}
    _, code = ct.judge(rows, 20.0)
    assert code == 0
    rows = {("a", "p", "c"): (1000.0, 501.0)}
    _, code = ct.judge(rows, 20.0)
    assert code == 2


def test_a_barely_scheduled_container_is_not_rated_on_a_handful_of_periods():
    # The real row: crossplane's function-patch-and-transform, 1 of 3 periods
    # throttled in 20s, which is 33.3% and means nothing.
    rows = {("crossplane-system", "fn-1", "package-runtime"): (3.0, 1.0),
            ("agents", "agora-1", "agora"): (1000.0, 100.0)}
    lines, code = ct.judge(rows, 20.0)
    assert code == 0
    assert not any("33.3%" in ln for ln in lines)
    skipped = [ln for ln in lines if "NOT JUDGED" in ln]
    assert len(skipped) == 1
    assert "fn-1" in skipped[0] and "at 3" in skipped[0]


def test_a_barely_scheduled_container_cannot_raise_however_throttled_it_is():
    # The precondition this asserts: it is over the raising line and under the
    # floor, so a floor that stopped being applied would turn this green->2.
    rows = {("crossplane-system", "fn-1", "package-runtime"): (3.0, 3.0)}
    assert 3.0 / 3.0 * 100.0 > ct.RAISE_PCT and 3.0 < ct.MIN_PERIODS
    _, code = ct.judge(rows, 20.0)
    assert code == 1  # nothing left to judge, which is not a pass


def test_nothing_ratable_is_reported_as_could_not_judge_not_as_healthy():
    lines, code = ct.judge({}, 20.0)
    assert code == 1
    assert any("COULD NOT JUDGE" in ln for ln in lines)
    assert not any("ok" in ln.split()[:1] for ln in lines)


def test_the_raise_line_is_a_parameter_and_the_caller_can_move_it():
    rows = {("a", "p", "c"): (1000.0, 315.0)}
    assert ct.judge(rows, 20.0)[1] == 0
    assert ct.judge(rows, 20.0, raise_pct=30.0)[1] == 2


def test_deltas_drops_a_row_whose_throttled_counter_alone_went_backwards():
    # A restart resets both counters together and the `dp <= 0` half already
    # catches that. This is the other shape: periods grew while throttled fell,
    # which cannot describe any real window and would produce a negative
    # percentage if it were rated.
    before = {("a", "p", "c"): {"periods": 1000.0, "throttled": 900.0}}
    after = {("a", "p", "c"): {"periods": 2000.0, "throttled": 5.0}}
    assert ct.deltas(before, after) == {}
