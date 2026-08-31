"""Tests for tools.host_memory_trend.

The one that matters is `test_a_leak_every_level_check_passes_is_caught`.
Every reading in that fixture is comfortably above `workload_health`'s two
level thresholds, which is the whole point: the 2026-08-29 outage was
eleven days of individually fine readings. A trend check that only fires
once the level is already bad would be a second copy of the check we have.

The mirror of it is `test_a_flat_series_is_not_a_finding` -- a check that
cannot come back clean is as useless as one that cannot fire.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tools import host_memory_trend as hmt


BASE = datetime(2026, 8, 29, 6, 0, tzinfo=timezone.utc)


def meminfo(total_kb=7931600, available_kb=2692208, swap_total_kb=2097148,
            swap_free_kb=631268):
    return {
        "MemTotal": float(total_kb),
        "MemAvailable": float(available_kb),
        "SwapTotal": float(swap_total_kb),
        "SwapFree": float(swap_free_kb),
    }


def series(values, field="swap_free_mib", host="server1", step_hours=1.0,
           start=BASE, other=1800.0):
    rows = []
    for i, value in enumerate(values):
        at = start + timedelta(hours=step_hours * i)
        row = {
            "_at": at,
            "at": at.isoformat(),
            "host": host,
            "mem_total_mib": 7745.7,
            "mem_available_mib": other,
            "swap_total_mib": 2048.0,
            "swap_free_mib": other,
        }
        row[field] = float(value)
        rows.append(row)
    return rows


def current_from(rows):
    return dict(rows[-1])


# --- attribution -----------------------------------------------------


def test_meminfo_matching_a_node_is_attributed_to_it():
    reading, why = hmt.reading_now(meminfo(), {"server1": 7745.7}, at=BASE)
    assert why is None
    assert reading["host"] == "server1"
    assert reading["mem_available_mib"] == pytest.approx(2629.1, abs=0.2)
    assert reading["swap_free_mib"] == pytest.approx(616.5, abs=0.2)


def test_meminfo_matching_no_node_is_refused_rather_than_recorded():
    """A container's view recorded as a host's poisons every later slope."""
    reading, why = hmt.reading_now(meminfo(total_kb=2097152), {"server1": 7745.7})
    assert reading is None
    assert "matches no node" in why


def test_a_host_without_memavailable_cannot_be_read():
    fields = meminfo()
    del fields["MemAvailable"]
    reading, why = hmt.reading_now(fields, {"server1": 7745.7})
    assert reading is None
    assert "MemAvailable" in why


# --- the ledger ------------------------------------------------------


def test_a_missing_ledger_is_no_readings_not_an_error(tmp_path):
    rows, skipped = hmt.load(tmp_path / "absent.jsonl")
    assert rows == [] and skipped == 0


def test_a_corrupt_line_is_skipped_and_counted(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_text(
        '{"at": "2026-08-29T06:00:00+00:00", "host": "server1", "swap_free_mib": 1800}\n'
        "not json at all\n"
        '{"host": "server1"}\n',
        encoding="utf-8",
    )
    rows, skipped = hmt.load(path)
    assert len(rows) == 1 and skipped == 2


def test_save_keeps_the_newest_and_drops_the_private_key(tmp_path):
    path = tmp_path / "l.jsonl"
    rows = series([1800, 1700, 1600, 1500])
    assert hmt.save(path, rows, keep=2) == 2
    body = path.read_text(encoding="utf-8")
    assert "_at" not in body
    reloaded, _ = hmt.load(path)
    assert [r["swap_free_mib"] for r in reloaded] == [1600.0, 1500.0]


def test_an_unwritable_ledger_reports_rather_than_raising(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file", encoding="utf-8")
    assert hmt.save(blocker / "l.jsonl", series([1800]), keep=10) is None


# --- the slope -------------------------------------------------------


def test_slope_is_mib_per_day_not_per_reading():
    """Six hourly readings losing 10Mi each is 240Mi/day, not 10."""
    rows = series([1800, 1790, 1780, 1770, 1760, 1750])
    assert hmt.slope_per_day(rows, "swap_free_mib") == pytest.approx(-240.0)


def test_a_single_point_has_no_slope():
    assert hmt.slope_per_day(series([1800]), "swap_free_mib") is None


def test_readings_all_at_one_instant_have_no_slope():
    rows = series([1800, 1700, 1600], step_hours=0.0)
    assert hmt.slope_per_day(rows, "swap_free_mib") is None


def test_window_drops_older_readings_and_other_hosts():
    rows = series([1800, 1700, 1600, 1500], step_hours=24.0)
    rows += series([9, 9], host="server2", start=BASE + timedelta(hours=72))
    now = BASE + timedelta(hours=72)
    inside = hmt.window(rows, "server1", now, hours=36)
    assert [r["swap_free_mib"] for r in inside] == [1600.0, 1500.0]
    # The cutoff is inclusive: a reading landing exactly on it is inside.
    assert len(hmt.window(rows, "server1", now, hours=48)) == 3


# --- the judgement ---------------------------------------------------


def test_a_leak_every_level_check_passes_is_caught():
    """The 08-29 shape: swap draining ~600Mi/day, still at 30% free.

    `workload_health` exits 0 on every one of these readings -- its swap
    threshold is 10% of total and 617Mi is 30%. That is the gap.
    """
    rows = series([1800, 1650, 1500, 1350, 1100, 900, 617], step_hours=6.0)
    lines, actionable, judged = hmt.judge(rows, current_from(rows), horizon_days=7.0)
    assert judged and actionable
    assert any(line.startswith("FALLING") and "free swap" in line for line in lines)


def test_a_flat_series_is_not_a_finding():
    rows = series([1800, 1801, 1799, 1800, 1802, 1798, 1800])
    lines, actionable, judged = hmt.judge(rows, current_from(rows), horizon_days=7.0)
    assert judged and not actionable
    assert any("not falling" in line for line in lines)


def test_a_slow_drift_beyond_the_horizon_is_not_a_finding():
    """1Mi/hour off 1800Mi is 75 days out. Real, and not this week's problem."""
    rows = series([1800 - i for i in range(0, 12, 2)], step_hours=2.0)
    lines, actionable, judged = hmt.judge(rows, current_from(rows), horizon_days=7.0)
    assert judged and not actionable
    assert any("beyond the 7-day horizon" in line for line in lines)


def test_too_few_readings_is_not_judged_and_says_so():
    rows = series([1800, 1000, 400], step_hours=12.0)
    lines, actionable, judged = hmt.judge(rows, current_from(rows), horizon_days=7.0)
    assert not judged and not actionable
    assert any("NOT ENOUGH HISTORY" in line for line in lines)
    assert any("not a healthy host" in line for line in lines)


def test_enough_readings_over_too_little_time_is_not_judged():
    """Eight readings four minutes apart describe any line you like."""
    rows = series([1800, 1700, 1600, 1500, 1400, 1300, 1200, 1100],
                  step_hours=1.0 / 15)
    _, actionable, judged = hmt.judge(rows, current_from(rows), horizon_days=7.0)
    assert not judged and not actionable


def test_falling_memory_is_reported_as_well_as_falling_swap():
    rows = series([2600, 2200, 1800, 1400, 1000, 600], field="mem_available_mib",
                  step_hours=6.0)
    lines, actionable, judged = hmt.judge(rows, current_from(rows), horizon_days=7.0)
    assert judged and actionable
    assert any(line.startswith("FALLING") and "available memory" in line
               for line in lines)


# --- the exit contract -----------------------------------------------


def test_main_records_and_exits_1_on_a_fresh_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hmt, "read_meminfo", lambda *a: (meminfo(), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    ledger = tmp_path / "l.jsonl"
    assert hmt.main(["--ledger", str(ledger)]) == 1
    out = capsys.readouterr().out
    assert "NOT ENOUGH HISTORY" in out
    assert ledger.exists()


def test_main_exits_2_on_a_leak(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hmt, "read_meminfo",
                        lambda *a: (meminfo(swap_free_kb=617 * 1024), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    ledger = tmp_path / "l.jsonl"
    now = datetime.now(timezone.utc)
    hmt.save(ledger, series([1800, 1650, 1500, 1350, 1100, 900], step_hours=6.0,
                            start=now - timedelta(hours=36)), keep=100)
    assert hmt.main(["--ledger", str(ledger)]) == 2
    assert "FALLING" in capsys.readouterr().out


def test_main_exits_1_when_the_reading_cannot_be_attributed(tmp_path, monkeypatch,
                                                            capsys):
    monkeypatch.setattr(hmt, "read_meminfo",
                        lambda *a: (meminfo(total_kb=2097152), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    assert hmt.main(["--ledger", str(tmp_path / "l.jsonl")]) == 1
    assert "CANNOT ATTRIBUTE MEMORY" in capsys.readouterr().out


def test_main_exits_1_when_no_node_capacity_can_be_read(tmp_path, monkeypatch, capsys):
    """An empty node list is no instrument, not a matchless reading."""
    monkeypatch.setattr(hmt, "read_meminfo", lambda *a: (meminfo(), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({}, None))
    assert hmt.main(["--ledger", str(tmp_path / "l.jsonl")]) == 1
    assert "no node capacities" in capsys.readouterr().out


def test_no_record_judges_without_appending(tmp_path, monkeypatch):
    monkeypatch.setattr(hmt, "read_meminfo", lambda *a: (meminfo(), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    ledger = tmp_path / "l.jsonl"
    hmt.main(["--ledger", str(ledger), "--no-record"])
    assert not ledger.exists()
