"""Tests for `tools.host_cpu_history` -- the reader over the CPU ledger."""

import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from tools import host_cpu_history as hch


NOW = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)


def sample(at, node="server1", busy=100.0, pod=None, rows=None):
    return {"_at": at.isoformat(), "node": node,
            "pod": pod or f"sweep-{node}-{at.isoformat()}",
            "cpu_busy_percent": busy,
            "rows": rows if rows is not None else [
                {"comm": "python3", "cpu_now_percent": busy / 2.0, "pid": 1}]}


def ledger(tmp_path, rows, name="cpu.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                    encoding="utf-8")
    return str(path)


BOTH = {"server1": 4, "server2": 4}


def fake_nodes(cores=None):
    """A cluster. The default is server1 alone, because a node in the cluster
    with no sample in the window is now a finding -- so a fixture that hands
    back both nodes and only ever writes server1 rows would exit 1 on every
    test that is about something else."""
    body = {"items": [{"metadata": {"name": name},
                       "status": {"allocatable": {"cpu": str(value)}}}
                      for name, value in (cores or {"server1": 4}).items()]}

    class Proc:
        returncode = 0
        stdout = json.dumps(body)
        stderr = ""

    return lambda *a, **k: Proc()


def run(path, runner=None, out=None, **kwargs):
    out = out or io.StringIO()
    code = hch.report(ledger=path, now=NOW, runner=runner or fake_nodes(),
                      out=out, **kwargs)
    return code, out.getvalue()


def hot_series(count=4, busy=380.0, node="server1", start=None):
    start = start or NOW - timedelta(hours=3)
    return [sample(start + timedelta(minutes=30 * i), node=node, busy=busy,
                   pod=f"hot-{node}-{i}") for i in range(count)]


def test_a_sustained_run_is_the_finding_and_names_the_process(tmp_path):
    rows = hot_series()
    for row in rows:
        row["rows"] = [{"comm": "ollama", "cpu_now_percent": 300.0, "pid": 7},
                       {"comm": "k3s-server", "cpu_now_percent": 20.0,
                        "pid": 8}]
    code, text = run(ledger(tmp_path, rows))
    assert code == 2
    assert "RAN HOT  server1" in text
    assert "ollama" in text
    # The top burner is named above the small one, not merely present.
    assert text.index("ollama") < text.index("k3s-server")


def test_a_quiet_box_is_clean(tmp_path):
    rows = [sample(NOW - timedelta(hours=3) + timedelta(minutes=30 * i),
                   busy=110.0, pod=f"calm-{i}") for i in range(4)]
    code, text = run(ledger(tmp_path, rows))
    assert code == 0
    assert "RAN HOT" not in text
    assert "no node held 75% of its cores" in text


def test_one_hot_sample_is_a_spike_and_not_a_run(tmp_path):
    """A single sweep is a one-second reading of a whole box."""
    rows = [sample(NOW - timedelta(hours=3), busy=110.0, pod="a"),
            sample(NOW - timedelta(hours=2, minutes=30), busy=380.0, pod="b"),
            sample(NOW - timedelta(hours=2), busy=110.0, pod="c")]
    code, text = run(ledger(tmp_path, rows))
    assert code == 0
    assert "busiest 3.80 core(s)" in text


def test_a_run_shorter_than_the_span_does_not_raise(tmp_path):
    """Two samples thirty minutes apart is half the sustain threshold."""
    rows = hot_series(count=2)
    code, _ = run(ledger(tmp_path, rows))
    assert code == 0
    code, _ = run(ledger(tmp_path, rows), min_span_hours=0.5)
    assert code == 2


def test_a_run_cannot_be_assembled_across_a_gap(tmp_path):
    """Two hot samples either side of a hole are not an hour of anything."""
    rows = [sample(NOW - timedelta(hours=10), busy=380.0, pod="a"),
            sample(NOW - timedelta(hours=1), busy=380.0, pod="b")]
    code, text = run(ledger(tmp_path, rows))
    assert code == 0
    assert "BLIND  server1" in text


def test_a_repeated_sweep_is_one_measurement(tmp_path):
    """The live ledger held the 18:00 sweep seven times; taken at face value
    that is a plateau, which is exactly what this check looks for."""
    one = sample(NOW - timedelta(hours=3), busy=380.0, pod="the-same-pod")
    code, text = run(ledger(tmp_path, [dict(one) for _ in range(7)]))
    assert code == 0
    assert "6 repeat(s)" in text
    # The count is the assertion that can fail. Both of the two above still
    # passed with the dedupe's `continue` deleted, because seven copies of one
    # stamp collapse to a zero-length run either way.
    assert "1 sample(s) across 1 node(s)" in text


def test_a_repeat_is_dropped_by_pod_and_not_by_stamp(tmp_path):
    """Two nodes swept at the same instant are two samples, not a repeat."""
    at = NOW - timedelta(hours=1)
    rows = [sample(at, node="server1", pod="a"),
            sample(at, node="server2", pod="b")]
    code, text = run(ledger(tmp_path, rows), runner=fake_nodes(BOTH))
    assert code == 0
    assert "2 sample(s) across 2 node(s)" in text
    assert "repeat(s)" not in text


def test_the_gap_line_is_a_caveat_and_never_the_verdict(tmp_path):
    rows = [sample(NOW - timedelta(hours=20), busy=110.0, pod="a"),
            sample(NOW - timedelta(hours=1), busy=110.0, pod="b")]
    code, text = run(ledger(tmp_path, rows))
    assert code == 0
    assert "19.0h" in text


def test_an_unreadable_ledger_is_never_clean(tmp_path):
    code, text = run(str(tmp_path / "nothing.jsonl"))
    assert code == 1
    assert "CANNOT READ" in text


def test_a_file_of_junk_is_an_empty_instrument(tmp_path):
    path = tmp_path / "cpu.jsonl"
    path.write_text("not json\n{\"pod\": \"x\"}\n", encoding="utf-8")
    code, text = run(str(path))
    assert code == 1
    assert "none of them is a usable sample" in text


def test_one_bad_line_does_not_blind_the_reader(tmp_path):
    path = tmp_path / "cpu.jsonl"
    good = "\n".join(json.dumps(row) for row in hot_series())
    path.write_text("{ broken\n" + good + "\n", encoding="utf-8")
    code, text = run(str(path))
    assert code == 2
    assert "1 line(s) could not be used" in text


def test_without_a_core_count_nothing_is_judged(tmp_path):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "forbidden"

    code, text = run(ledger(tmp_path, hot_series()),
                     runner=lambda *a, **k: Proc())
    assert code == 1
    assert "CANNOT JUDGE" in text
    # The reason a default core count is refused, spelled out for the reader.
    assert "380% and 38% read the same" in text


def test_a_node_missing_from_the_cluster_is_named_not_judged(tmp_path):
    """It is writing samples right now and the cluster does not list it, so
    there is no core count to judge 380% against. Named, never guessed at."""
    rows = hot_series(node="ghost") + [
        sample(NOW - timedelta(hours=1), node="server1", busy=50.0, pod="s1")]
    code, text = run(ledger(tmp_path, rows), runner=fake_nodes({"server1": 4}))
    assert code == 0
    assert "CANNOT SEE  ghost" in text


def test_a_ledger_older_than_the_window_is_a_stopped_instrument(tmp_path):
    rows = hot_series(start=NOW - timedelta(days=9))
    code, text = run(ledger(tmp_path, rows))
    assert code == 1
    assert "older than the 72h window" in text


def test_millicore_capacity_is_read_as_cores(tmp_path):
    """A node reporting `4000m` is four cores, not four thousand."""
    cores, why = hch.read_cores(runner=fake_nodes({"server1": "4000m"}))
    assert why is None
    assert cores == {"server1": 4.0}


def test_the_threshold_is_a_fraction_of_that_node_s_cores(tmp_path):
    """380% is hot on a 4-core box and cool on a 16-core one."""
    rows = hot_series()
    code, _ = run(ledger(tmp_path, rows), runner=fake_nodes({"server1": 4}))
    assert code == 2
    code, _ = run(ledger(tmp_path, rows), runner=fake_nodes({"server1": 16}))
    assert code == 0


def test_processes_of_one_name_are_summed_within_a_sample(tmp_path):
    """Three `python3` PIDs in one sweep are one line, and its peak is the
    sample total -- a per-row peak beside a per-sample mean printed a mean
    larger than its own peak."""
    rows = hot_series()
    for row in rows:
        row["rows"] = [{"comm": "python3", "cpu_now_percent": 100.0,
                        "pid": pid} for pid in (1, 2, 3)]
    code, text = run(ledger(tmp_path, rows))
    assert code == 2
    assert "python3  mean 3.00 core(s), peak 3.00" in text
    assert f"in {len(rows)} of {len(rows)} sample(s)" in text


def test_a_sample_with_no_busy_figure_is_not_a_zero(tmp_path):
    row = sample(NOW - timedelta(hours=1))
    row["cpu_busy_percent"] = None
    code, text = run(ledger(tmp_path, [row]))
    assert code == 1
    assert "none of them is a usable sample" in text


def test_a_z_stamp_is_utc_and_not_naive(tmp_path):
    at = hch._parse_at("2026-09-12T18:00:00Z")
    assert at == datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)


def test_the_ledger_default_is_the_one_the_writer_uses():
    """One spelling of the path, or this judges a file nobody writes."""
    from tools import host_memory_trend

    assert hch.DEFAULT_LEDGER == host_memory_trend.DEFAULT_CPU_LEDGER


def test_main_takes_the_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(hch, "read_cores", lambda runner=None: ({"server1": 4},
                                                                None))
    path = ledger(tmp_path, hot_series())
    assert hch.main(["--ledger", path, "--busy-fraction", "0.9"]) == 2
    assert hch.main(["--ledger", path, "--busy-fraction", "0.99"]) == 0


@pytest.mark.parametrize("hours,expected", [(72.0, 2), (0.5, 1)])
def test_the_window_bounds_what_is_judged(tmp_path, hours, expected):
    rows = hot_series(start=NOW - timedelta(hours=5))
    code, _ = run(ledger(tmp_path, rows), window_hours=hours)
    assert code == expected


def test_a_hole_inside_a_hot_stretch_splits_it_into_two_runs(tmp_path):
    """The first version reported two readings nine hours apart as a
    nine-hour incident. Each side is judged on its own length."""
    early = hot_series(count=3, start=NOW - timedelta(hours=20))
    late = hot_series(count=3, start=NOW - timedelta(hours=3))
    for index, row in enumerate(late):
        row["pod"] = f"late-{index}"
    code, text = run(ledger(tmp_path, early + late))
    assert code == 2
    assert text.count("RAN HOT") == 2


def test_a_short_hot_stretch_before_a_hole_is_not_carried_over(tmp_path):
    """One hot sample, a hole, then a real run: only the run is reported."""
    stray = [sample(NOW - timedelta(hours=30), busy=380.0, pod="stray")]
    late = hot_series(count=3, start=NOW - timedelta(hours=3))
    code, text = run(ledger(tmp_path, stray + late))
    assert code == 2
    assert text.count("RAN HOT") == 1
    assert "3 sample(s), peak" in text


def test_the_normal_stride_between_sweeps_is_not_a_blind_window(tmp_path):
    """The sweep fires every 30 minutes, so a 30-minute stride is the check
    working. A BLIND line between every pair of samples is a caveat nobody
    can read past -- and no test caught that until this one."""
    rows = [sample(NOW - timedelta(hours=3) + timedelta(minutes=30 * i),
                   busy=110.0, pod=f"calm-{i}") for i in range(6)]
    code, text = run(ledger(tmp_path, rows))
    assert code == 0
    assert "  BLIND  " not in text


def test_a_node_that_stopped_reporting_is_not_a_quiet_node(tmp_path):
    """The reviewer's finding: a node whose sweep died and whose last reading
    aged out of the window vanished from the report entirely -- no line, no
    caveat, verdict `ok` -- while the other node kept the window non-empty."""
    rows = [sample(NOW - timedelta(hours=1), node="server1", busy=50.0,
                   pod="s1-a"),
            sample(NOW - timedelta(minutes=30), node="server1", busy=50.0,
                   pod="s1-b"),
            sample(NOW - timedelta(hours=100), node="server2", busy=380.0,
                   pod="s2-a")]
    code, text = run(ledger(tmp_path, rows), runner=fake_nodes(BOTH))
    assert code == 1
    assert "server2  STOPPED REPORTING" in text
    assert "CANNOT JUDGE  1 node(s)" in text


def test_a_cluster_node_that_never_wrote_a_sample_is_named(tmp_path):
    rows = [sample(NOW - timedelta(hours=1), node="server1", busy=50.0,
                   pod="s1-a")]
    code, text = run(ledger(tmp_path, rows), runner=fake_nodes(BOTH))
    assert code == 1
    assert "never in this ledger" in text


def test_a_node_that_left_the_cluster_does_not_raise(tmp_path):
    """Its old samples would otherwise keep this red forever."""
    rows = [sample(NOW - timedelta(hours=1), node="server1", busy=50.0,
                   pod="s1-a"),
            sample(NOW - timedelta(hours=100), node="gone", busy=380.0,
                   pod="g-a")]
    code, text = run(ledger(tmp_path, rows), runner=fake_nodes({"server1": 4}))
    assert code == 0
    assert "a node that has left" in text


def test_a_hot_run_outranks_a_silent_node(tmp_path):
    """One is a finding a cycle can act on; the other is a missing meter."""
    rows = hot_series() + [sample(NOW - timedelta(hours=100), node="server2",
                                  busy=50.0, pod="s2-old")]
    code, text = run(ledger(tmp_path, rows), runner=fake_nodes(BOTH))
    assert code == 2
    assert "server2  STOPPED REPORTING" in text


def test_the_oslo_stamp_follows_daylight_saving(tmp_path):
    """A hardcoded +2 prints every winter incident an hour off, and changes
    no verdict, so nothing would ever have failed on it."""
    summer = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    winter = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
    assert hch._oslo(summer).endswith("14:00")
    assert hch._oslo(winter).endswith("13:00")


def thrashing(at, node="server1", owned=12.0, node_busy=382.4, pod=None):
    """A sample in the shape issue #169 describes.

    Almost nothing is charged to a pid and the box is nearly pegged: 3.8 of 4
    cores busy, 0.12 of a core of it owned by processes. That is what a node
    out of memory and swap looks like -- the cores are in reclaim, iowait and
    softirq, which belong to no process -- and it is the case the reader was
    blind to while it judged the per-process sum alone.
    """
    row = sample(at, node=node, busy=owned, pod=pod)
    row["cpu_node_percent"] = node_busy
    return row


def test_a_thrashing_node_is_hot_even_though_no_process_is(tmp_path, capsys):
    # The separating test. Every sample here has owned CPU far below the
    # threshold, so a reader judging `cpu_busy_percent` calls this quiet.
    rows = [thrashing(NOW - timedelta(hours=6) + timedelta(minutes=30 * i))
            for i in range(8)]
    code = hch.report(ledger=ledger(tmp_path, rows), now=NOW,
                      runner=fake_nodes(), out=io.StringIO())
    assert code == 2


def test_the_same_samples_without_the_node_figure_read_quiet(tmp_path):
    # The other half of the pair: this is what the reader saw before, and it
    # is still what it sees for a sample an older sweep image wrote. Without
    # this the test above could pass on a threshold change rather than on the
    # node figure being used.
    rows = []
    for row in [thrashing(NOW - timedelta(hours=6) + timedelta(minutes=30 * i))
                for i in range(8)]:
        row.pop("cpu_node_percent")
        rows.append(row)
    out = io.StringIO()
    code = hch.report(ledger=ledger(tmp_path, rows), now=NOW,
                      runner=fake_nodes(), out=out)
    assert code == 0
    assert "OWNED ONLY  8 of 8 sample(s)" in out.getvalue()


def test_the_run_names_the_cores_no_process_owns(tmp_path):
    rows = [thrashing(NOW - timedelta(hours=6) + timedelta(minutes=30 * i))
            for i in range(8)]
    out = io.StringIO()
    assert hch.report(ledger=ledger(tmp_path, rows), now=NOW,
                      runner=fake_nodes(), out=out) == 2
    text = out.getvalue()
    # 382.4 - 12.0 = 370.4% of one core, so 3.70 cores belong to nobody.
    assert "3.70 core(s) charged to no process" in text
    assert "look at memory and swap on that node first" in text


def test_a_node_busier_than_its_processes_by_a_hair_says_nothing(tmp_path):
    # The two figures are read over the same window, not the same instants,
    # so a small positive gap is arithmetic. Naming it on every clean run
    # would make the clause meaningless by the time it mattered.
    rows = [thrashing(NOW - timedelta(minutes=30 * i), owned=100.0,
                      node_busy=100.0 + hch.UNOWNED_FLOOR - 0.1)
            for i in range(4)]
    out = io.StringIO()
    hch.report(ledger=ledger(tmp_path, rows), now=NOW,
               runner=fake_nodes(), out=out)
    assert "charged to no process" not in out.getvalue()


def test_a_node_busy_in_processes_is_still_judged_on_the_node_figure(tmp_path):
    # The node figure is preferred in BOTH directions. A sweep whose process
    # sum double-counts threads could exceed the node's own reading, and the
    # node is the box's own answer about itself.
    rows = [thrashing(NOW - timedelta(hours=6) + timedelta(minutes=30 * i),
                      owned=390.0, node_busy=80.0)
            for i in range(8)]
    out = io.StringIO()
    assert hch.report(ledger=ledger(tmp_path, rows), now=NOW,
                      runner=fake_nodes(), out=out) == 0
    assert "busiest 0.80 core(s)" in out.getvalue()
