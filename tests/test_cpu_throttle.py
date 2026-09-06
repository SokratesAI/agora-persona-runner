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


def test_combine_sums_the_windows_and_keeps_each_one():
    a = {("a", "p", "c"): (1000.0, 33.0)}
    b = {("a", "p", "c"): (1000.0, 990.0)}
    totals, series = ct.combine([a, b])
    assert totals == {("a", "p", "c"): (2000.0, 1023.0)}
    assert series == {("a", "p", "c"): [(1000.0, 33.0), (1000.0, 990.0)]}


def test_combine_rates_a_container_on_the_windows_it_was_present_for():
    # It restarted, so `deltas` dropped it from the second window.
    a = {("a", "p", "c"): (1000.0, 500.0), ("a", "q", "d"): (800.0, 0.0)}
    b = {("a", "q", "d"): (800.0, 8.0)}
    totals, series = ct.combine([a, b])
    assert totals[("a", "p", "c")] == (1000.0, 500.0)
    assert len(series[("a", "p", "c")]) == 1
    assert len(series[("a", "q", "d")]) == 2


def test_several_windows_print_each_ratio_and_the_range_under_the_verdict():
    # couchdb's real swing: 3.3% at 14:41 Oslo and 100.0% at 14:54.
    key = ("obsidian", "couchdb-1", "couchdb")
    samples = [{key: (1000.0, 33.0)}, {key: (1000.0, 1000.0)}]
    rows, series = ct.combine(samples)
    lines, code = ct.judge(rows, 40.0, series=series)
    assert code == 2  # 51.65% over the pair
    spread = [ln for ln in lines if "over 2 sample(s)" in ln]
    assert len(spread) == 1
    assert "3.3%" in spread[0] and "100.0%" in spread[0]
    assert "3.3% to 100.0%" in spread[0]


def test_one_window_prints_no_spread_because_there_is_nothing_to_compare():
    key = ("a", "p", "c")
    one = [{key: (1000.0, 900.0)}]
    rows, series = ct.combine(one)
    lines, _ = ct.judge(rows, 20.0, series=series)
    assert not any("sample(s):" in ln for ln in lines)
    # The precondition: the same container with a second window does print one,
    # so the assertion above is about the sample count and not about the row.
    two = one + [{key: (1000.0, 900.0)}]
    rows2, series2 = ct.combine(two)
    lines2, _ = ct.judge(rows2, 40.0, series=series2)
    assert any("over 2 sample(s):" in ln for ln in lines2)


def test_a_window_too_quiet_to_rate_is_counted_rather_than_given_a_percentage():
    key = ("a", "p", "c")
    samples = [{key: (1000.0, 900.0)}, {key: (3.0, 1.0)}]
    rows, series = ct.combine(samples)
    lines, _ = ct.judge(rows, 40.0, series=series)
    spread = [ln for ln in lines if "over 2 sample(s)" in ln][0]
    assert "33.3%" not in spread
    assert "1 too quiet to rate" in spread


def test_a_total_worth_rating_over_windows_that_are_not_says_so():
    key = ("a", "p", "c")
    samples = [{key: (40.0, 39.0)} for _ in range(4)]
    rows, series = ct.combine(samples)
    lines, code = ct.judge(rows, 80.0, series=series)
    assert code == 2  # 160 periods in total, over the floor
    spread = [ln for ln in lines if "over 4 sample(s)" in ln][0]
    assert "the spread is not" in spread
    assert "%" not in spread.split("sample(s):")[1].split("period(s)")[0]


def test_each_window_is_measured_from_the_one_before_it_not_from_the_start(monkeypatch):
    """The sampling loop must advance its baseline, or every window is cumulative.

    Cumulative ratios are the trap the module docstring opens with: a container
    that was throttled once looks throttled forever, because the counters run
    from container start. Holding the first scrape as the baseline for all of
    them reintroduces it one level down, inside the loop that was added to
    escape it.
    """
    key = ("a", "p", "c")
    scrapes = [
        {key: {"periods": 0.0, "throttled": 0.0}},
        {key: {"periods": 1000.0, "throttled": 20.0}},
        {key: {"periods": 2000.0, "throttled": 20.0}},
        {key: {"periods": 3000.0, "throttled": 1000.0}},
    ]
    taken = iter(scrapes)
    monkeypatch.setattr(ct, "node_names", lambda: (["n1"], None))
    monkeypatch.setattr(ct, "_scrape_all", lambda nodes: (next(taken), []))
    monkeypatch.setattr(ct.time, "sleep", lambda s: None)

    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    code = ct.main(["--samples", "3", "--window", "0"])

    out = "\n".join(printed)
    # 1000 of 3000 periods over the three windows, so under the raising line,
    # and the last window is the spike the total alone cannot show.
    assert code == 0
    assert "33.3%" in out
    assert "2.0% 0.0% 98.0%" in out
