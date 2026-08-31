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


CALM = (0.03, 0.02, 104934927170.0)  # a live calm reading off server1


def calm_host(monkeypatch, pressure=CALM, oom_kills=627.0, uptime_days=136.0):
    """Pin the two host readings so a test asserts on the ledger, not on CI's box.

    Without this every `main` test below would read the real `/proc` of
    whatever machine it runs on, and a GitHub runner under memory pressure
    would turn `exit 1, not enough history` into `exit 2, stalling`.
    """
    monkeypatch.setattr(hmt, "read_pressure", lambda *a, **k: (pressure, None))
    monkeypatch.setattr(hmt, "read_oom_kills", lambda *a, **k: (oom_kills, None))
    monkeypatch.setattr(hmt, "read_uptime_days", lambda *a, **k: (uptime_days, None))


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
    calm_host(monkeypatch)
    ledger = tmp_path / "l.jsonl"
    assert hmt.main(["--ledger", str(ledger)]) == 1
    out = capsys.readouterr().out
    assert "NOT ENOUGH HISTORY" in out
    assert ledger.exists()


def test_main_exits_2_on_a_leak(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hmt, "read_meminfo",
                        lambda *a: (meminfo(swap_free_kb=617 * 1024), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    calm_host(monkeypatch)
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
    calm_host(monkeypatch)
    assert hmt.main(["--ledger", str(tmp_path / "l.jsonl")]) == 1
    assert "CANNOT ATTRIBUTE MEMORY" in capsys.readouterr().out


def test_main_exits_1_when_no_node_capacity_can_be_read(tmp_path, monkeypatch, capsys):
    """An empty node list is no instrument, not a matchless reading."""
    monkeypatch.setattr(hmt, "read_meminfo", lambda *a: (meminfo(), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({}, None))
    calm_host(monkeypatch)
    assert hmt.main(["--ledger", str(tmp_path / "l.jsonl")]) == 1
    assert "no node capacities" in capsys.readouterr().out


def test_no_record_judges_without_appending(tmp_path, monkeypatch):
    monkeypatch.setattr(hmt, "read_meminfo", lambda *a: (meminfo(), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    calm_host(monkeypatch)
    ledger = tmp_path / "l.jsonl"
    hmt.main(["--ledger", str(ledger), "--no-record"])
    assert not ledger.exists()


# --- harm already done, which needs no history -----------------------


def harm_row(at=BASE, host="server1", full=0.9, some=1.4, kills=627.0,
             uptime=136.0):
    return {"_at": at, "at": at.isoformat(), "host": host,
            "psi_full_avg300": full, "psi_some_avg300": some,
            "oom_kills": kills, "uptime_days": uptime}


def test_pressure_is_parsed_out_of_the_kernels_two_lines(tmp_path):
    path = tmp_path / "pressure"
    path.write_text(
        "some avg10=0.00 avg60=0.08 avg300=0.03 total=166846997019\n"
        "full avg10=0.00 avg60=0.01 avg300=0.02 total=104934927170\n")
    (some, full, total), why = hmt.read_pressure(str(path))
    assert why is None
    assert (some, full, total) == (0.03, 0.02, 104934927170.0)


def test_a_kernel_without_psi_is_reported_not_assumed_calm(tmp_path):
    reading, why = hmt.read_pressure(str(tmp_path / "absent"))
    assert reading is None and "could not be read" in why


def test_a_pressure_file_missing_full_is_refused(tmp_path):
    path = tmp_path / "pressure"
    path.write_text("some avg10=0.00 avg60=0.08 avg300=0.03 total=1\n")
    reading, why = hmt.read_pressure(str(path))
    assert reading is None and "full_avg300" in why


def test_oom_kills_come_off_vmstat(tmp_path):
    path = tmp_path / "vmstat"
    path.write_text("pgfault 1\noom_kill 627\npgmajfault 2\n")
    assert hmt.read_oom_kills(str(path)) == (627.0, None)


def test_a_vmstat_without_the_counter_is_reported(tmp_path):
    path = tmp_path / "vmstat"
    path.write_text("pgfault 1\n")
    kills, why = hmt.read_oom_kills(str(path))
    assert kills is None and "does not publish one" in why


def test_a_stalling_box_is_a_finding_on_the_very_first_reading():
    """The point of PSI here: no ledger, no slope, and still a real answer."""
    current = harm_row(full=12.0)
    lines, actionable = hmt.judge_harm(current, [])
    assert actionable
    assert any(line.startswith("STALLING") for line in lines)


def test_a_calm_box_is_not_a_finding():
    lines, actionable = hmt.judge_harm(harm_row(full=0.9), [])
    assert not actionable
    assert any("stall:" in line for line in lines)


def test_the_stall_line_is_full_not_some():
    """`some` runs at 1.44% on this box while healthy; judging it would fire daily."""
    lines, actionable = hmt.judge_harm(harm_row(full=1.0, some=95.0), [])
    assert not actionable


def test_a_burst_of_oom_kills_above_the_boxs_own_rate_is_a_finding():
    earlier = harm_row(at=BASE, kills=627.0)
    current = harm_row(at=BASE + timedelta(hours=6), kills=640.0)
    lines, actionable = hmt.judge_harm(current, [earlier, current])
    assert actionable
    assert any(line.startswith("OOM KILLING") for line in lines)


def test_the_ordinary_background_kill_rate_is_not_a_finding():
    """4.6/day is this box's normal; a check that fired on it fires forever."""
    earlier = harm_row(at=BASE, kills=627.0)
    current = harm_row(at=BASE + timedelta(hours=24), kills=632.0)
    lines, actionable = hmt.judge_harm(current, [earlier, current])
    assert not actionable


def test_two_kills_never_raise_however_quiet_the_box():
    """The floor exists so a near-zero baseline cannot be tripped by noise."""
    earlier = harm_row(at=BASE, kills=1.0, uptime=136.0)
    current = harm_row(at=BASE + timedelta(hours=1), kills=3.0, uptime=136.0)
    lines, actionable = hmt.judge_harm(current, [earlier, current])
    assert not actionable


def test_a_reboot_is_reported_rather_than_read_as_negative_kills():
    earlier = harm_row(at=BASE, kills=627.0)
    current = harm_row(at=BASE + timedelta(hours=6), kills=4.0)
    lines, actionable = hmt.judge_harm(current, [earlier, current])
    assert not actionable
    assert any("went backwards" in line for line in lines)


def test_a_missing_psi_reading_says_so_rather_than_passing_quietly():
    current = {"_at": BASE, "host": "server1"}
    lines, actionable = hmt.judge_harm(current, [])
    assert not actionable
    assert any(line.startswith("CANNOT READ PRESSURE") for line in lines)
    assert any(line.startswith("CANNOT READ OOM KILLS") for line in lines)


def test_the_new_fields_reach_the_ledger_row():
    row, why = hmt.reading_now(meminfo(), {"server1": 7745.7}, at=BASE,
                               pressure=CALM, oom_kills=627.0, uptime_days=136.0)
    assert why is None
    assert row["psi_full_avg300"] == 0.02
    assert row["oom_kills"] == 627.0
    assert row["uptime_days"] == 136.0


def test_each_reading_is_omitted_only_when_that_one_kernel_file_is_absent():
    """The slope worked before PSI existed and must not start failing on it.

    Asserting only the absent case pinned nothing: the pre-diff `reading_now`
    took the same three arguments and produced a row with neither key, so that
    assertion agreed with the code either way. The complement is what pins it
    -- one reading present and the other absent, in the same test.
    """
    neither, why = hmt.reading_now(meminfo(), {"server1": 7745.7}, at=BASE)
    assert why is None
    assert "psi_full_avg300" not in neither and "oom_kills" not in neither

    psi_only, why = hmt.reading_now(meminfo(), {"server1": 7745.7}, at=BASE,
                                    pressure=CALM)
    assert why is None
    assert psi_only["psi_full_avg300"] == 0.02 and "oom_kills" not in psi_only

    oom_only, why = hmt.reading_now(meminfo(), {"server1": 7745.7}, at=BASE,
                                    oom_kills=627.0, uptime_days=136.0)
    assert why is None
    assert oom_only["oom_kills"] == 627.0 and "psi_full_avg300" not in oom_only


# --- the uptime reader, which is the OOM baseline's denominator ------


def test_uptime_is_read_as_days_off_the_first_field(tmp_path):
    path = tmp_path / "uptime"
    path.write_text("11750400.42 98765.43\n")
    days, why = hmt.read_uptime_days(str(path))
    assert why is None and abs(days - 136.0) < 0.01


def test_a_missing_uptime_file_reports_why_rather_than_a_bare_none(tmp_path):
    days, why = hmt.read_uptime_days(str(tmp_path / "absent"))
    assert days is None and "no denominator" in why


def test_a_nonsense_uptime_file_reports_why(tmp_path):
    path = tmp_path / "uptime"
    path.write_text("not-a-number\n")
    days, why = hmt.read_uptime_days(str(path))
    assert days is None and "no denominator" in why


def test_a_burst_with_no_uptime_says_it_cannot_judge_rather_than_passing():
    """Losing the denominator turns the burst detector off; it must say so."""
    earlier = harm_row(at=BASE, kills=627.0, uptime=None)
    current = harm_row(at=BASE + timedelta(hours=6), kills=700.0, uptime=None)
    lines, actionable = hmt.judge_harm(current, [earlier, current])
    assert not actionable
    assert any(line.startswith("CANNOT READ OOM RATE") for line in lines)


def test_every_blind_line_starts_with_a_marker_preflight_actually_matches():
    """A caveat preflight cannot match is dropped from the collapsed report."""
    from tools.preflight import CAVEAT_MARKERS
    blind = []
    blind += hmt.judge_harm({"_at": BASE, "host": "server1"}, [])[0]
    blind += hmt.judge_harm(harm_row(uptime=None), [])[0]
    stated = [line for line in blind if line.startswith("CANNOT")]
    assert stated
    for line in stated:
        assert any(line.startswith(marker) for marker in CAVEAT_MARKERS), line


def test_main_prints_why_the_uptime_could_not_be_read(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hmt, "read_meminfo", lambda *a: (meminfo(), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    calm_host(monkeypatch)
    monkeypatch.setattr(hmt, "read_uptime_days",
                        lambda *a, **k: (None, "/proc/uptime could not be read, so "
                                               "the OOM-kill baseline has no "
                                               "denominator."))
    hmt.main(["--ledger", str(tmp_path / "l.jsonl")])
    assert "no denominator" in capsys.readouterr().out


def test_main_exits_2_on_a_stall_rather_than_1_on_a_fresh_ledger(tmp_path, monkeypatch,
                                                                 capsys):
    """A real incident must never be reported as "not enough history"."""
    monkeypatch.setattr(hmt, "read_meminfo", lambda *a: (meminfo(), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    calm_host(monkeypatch, pressure=(40.0, 25.0, 1.0))
    assert hmt.main(["--ledger", str(tmp_path / "l.jsonl")]) == 2
    out = capsys.readouterr().out
    assert "STALLING" in out and "NOT ENOUGH HISTORY" in out


def test_a_box_that_has_never_oom_killed_can_still_raise():
    """No multiple of a zero baseline is ever exceeded; the floor is the judgement."""
    earlier = harm_row(at=BASE, kills=0.0, uptime=136.0)
    current = harm_row(at=BASE + timedelta(hours=6), kills=4.0, uptime=136.0)
    lines, actionable = hmt.judge_harm(current, [earlier, current])
    assert actionable
    assert any(line.startswith("OOM KILLING") for line in lines)


def test_a_zero_baseline_still_respects_the_floor():
    earlier = harm_row(at=BASE, kills=0.0, uptime=136.0)
    current = harm_row(at=BASE + timedelta(hours=6), kills=2.0, uptime=136.0)
    lines, actionable = hmt.judge_harm(current, [earlier, current])
    assert not actionable
