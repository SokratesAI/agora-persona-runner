"""Tests for tools.host_memory_trend.

The one that matters is `test_a_leak_every_level_check_passes_is_caught`.
Every reading in that fixture is comfortably above `workload_health`'s two
level thresholds, which is the whole point: the 2026-08-29 outage was
eleven days of individually fine readings. A trend check that only fires
once the level is already bad would be a second copy of the check we have.

The mirror of it is `test_a_flat_series_is_not_a_finding` -- a check that
cannot come back clean is as useless as one that cannot fire.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from tools import host_memory_trend as hmt


# The default start for a fabricated ledger, and it has to move with the clock.
# `main` windows the ledger to the last 72h **measured from the reading it takes
# right now**, so a fixed calendar date is a fixture with an expiry: this was
# `datetime(2026, 8, 29, 6, 0)`, and at 08:15 Oslo on 1 September it fell 15
# minutes out the back of that window. Two tests that had passed for days began
# asserting on an empty window, red on main, with nothing in the diff that
# caused it. Anchoring to the run makes the fixture mean what it always meant --
# "recent readings" -- instead of meaning it only until a Tuesday.
BASE = (datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        - timedelta(hours=12))


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


def calm_host(monkeypatch, pressure=CALM, oom_kills=627.0, uptime_days=136.0,
              pod_working_set=None, pods_why="kubectl top failed: no kubectl here",
              cgroup_split=None, cgroup_why="no kubectl here"):
    """Pin the two host readings so a test asserts on the ledger, not on CI's box.

    Without this every `main` test below would read the real `/proc` of
    whatever machine it runs on, and a GitHub runner under memory pressure
    would turn `exit 1, not enough history` into `exit 2, stalling`.
    """
    monkeypatch.setattr(hmt, "read_pressure", lambda *a, **k: (pressure, None))
    monkeypatch.setattr(hmt, "read_oom_kills", lambda *a, **k: (oom_kills, None))
    monkeypatch.setattr(hmt, "read_uptime_days", lambda *a, **k: (uptime_days, None))
    # ...and the Pod split, for the same reason: unpinned it shells out to a
    # kubectl that CI has not got, which is slow and machine-dependent rather
    # than wrong. Tests that want the split pass one in.
    monkeypatch.setattr(hmt, "read_pod_working_set",
                        lambda *a, **k: (pod_working_set, pods_why))
    # ...and the cAdvisor read, which shells out to the same absent kubectl.
    monkeypatch.setattr(hmt, "read_cgroup_split",
                        lambda *a, **k: (cgroup_split, cgroup_why))


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
    from tools.preflight import is_caveat
    blind = []
    blind += hmt.judge_harm({"_at": BASE, "host": "server1"}, [])[0]
    blind += hmt.judge_harm(harm_row(uptime=None), [])[0]
    stated = [line for line in blind if line.startswith("CANNOT")]
    assert stated
    for line in stated:
        assert is_caveat(line), line


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


# --- which side of the Pod boundary ----------------------------------


def split_series(host_values, pod_values, start=BASE, step_hours=1.0):
    """Readings carrying both halves of the split, oldest first."""
    rows = []
    for i, (host_mib, pods_mib) in enumerate(zip(host_values, pod_values)):
        at = start + timedelta(hours=step_hours * i)
        rows.append({
            "_at": at,
            "at": at.isoformat(),
            "host": "server1",
            "mem_total_mib": 7745.7,
            "mem_available_mib": 1800.0,
            "swap_total_mib": 2048.0,
            "swap_free_mib": 1800.0,
            "anon_mib": float(host_mib) + float(pods_mib),
            "pods_working_set_mib": float(pods_mib),
            "pod_count": 43,
            "host_anon_mib": float(host_mib),
        })
    return rows


def test_a_reading_records_which_side_of_the_pod_boundary_the_memory_is_on():
    info = dict(meminfo(), AnonPages=5045000.0)
    reading, why = hmt.reading_now(info, {"server1": 7745.7}, at=BASE,
                                   pod_working_set=(3020.0, 43))
    assert why is None
    assert reading["anon_mib"] == pytest.approx(4926.8, abs=0.2)
    assert reading["pods_working_set_mib"] == 3020.0
    assert reading["pod_count"] == 43
    assert reading["host_anon_mib"] == pytest.approx(1906.8, abs=0.2)


def test_a_reading_without_a_pod_read_still_records_the_rest():
    """The slope worked before the split existed and must not start failing."""
    info = dict(meminfo(), AnonPages=5045000.0)
    reading, why = hmt.reading_now(info, {"server1": 7745.7}, at=BASE,
                                   pod_working_set=None)
    assert why is None
    assert reading["anon_mib"] == pytest.approx(4926.8, abs=0.2)
    assert "host_anon_mib" not in reading
    assert reading["mem_available_mib"] > 0


def test_a_meminfo_without_anonpages_records_no_split_rather_than_a_wrong_one():
    reading, why = hmt.reading_now(meminfo(), {"server1": 7745.7}, at=BASE,
                                   pod_working_set=(3020.0, 43))
    assert why is None
    assert "anon_mib" not in reading
    assert "host_anon_mib" not in reading


def test_a_negative_split_is_recorded_as_measured_rather_than_clamped():
    """The two instruments overlap and can cross; clamping would hide that."""
    info = dict(meminfo(), AnonPages=1024000.0)
    reading, _ = hmt.reading_now(info, {"server1": 7745.7}, at=BASE,
                                 pod_working_set=(2000.0, 43))
    assert reading["host_anon_mib"] < 0


def test_a_leak_outside_every_pod_cgroup_is_named_as_the_host_side():
    """The 2026-08-29 shape: Pods flat, host climbing, headroom falling."""
    rows = split_series([2000, 2100, 2200, 2300, 2400, 2500, 2600],
                        [3020, 3020, 3020, 3020, 3020, 3020, 3020])
    lines = hmt.attribute_slope(rows, current_from(rows))
    assert any(line.startswith("ATTRIBUTION") for line in lines)
    assert any("Only the host is growing" in line for line in lines)
    assert not any("Only the Pods" in line for line in lines)


def test_a_leak_inside_a_pod_is_named_as_the_pod_side():
    rows = split_series([2000, 2000, 2000, 2000, 2000, 2000, 2000],
                        [3020, 3120, 3220, 3320, 3420, 3520, 3620])
    lines = hmt.attribute_slope(rows, current_from(rows))
    assert any("Only the Pods are growing" in line for line in lines)
    assert not any("Only the host" in line for line in lines)


def test_both_sides_growing_picks_neither():
    """A comparison of two positive rates needs a threshold I did not measure."""
    rows = split_series([2000, 2050, 2100, 2150, 2200, 2250, 2300],
                        [3020, 3070, 3120, 3170, 3220, 3270, 3320])
    lines = hmt.attribute_slope(rows, current_from(rows))
    assert any("Both sides are growing" in line for line in lines)
    assert not any("Only the" in line for line in lines)


def test_a_flat_split_names_no_side():
    rows = split_series([2000] * 7, [3020] * 7)
    lines = hmt.attribute_slope(rows, current_from(rows))
    assert any("Neither side is growing" in line for line in lines)


def test_attribution_is_silent_when_the_window_is_too_short_to_trend():
    """judge() already says NOT ENOUGH HISTORY; a second copy is noise."""
    rows = split_series([2000, 2100, 2200], [3020, 3020, 3020])
    assert hmt.attribute_slope(rows, current_from(rows)) == []


def test_a_window_without_the_split_says_so_rather_than_going_quiet():
    rows = series([1800] * 7, field="mem_available_mib", step_hours=1.0)
    lines = hmt.attribute_slope(rows, current_from(rows))
    assert lines and lines[0].startswith("CANNOT SEE")


def test_a_crossed_split_says_it_is_not_measurable_rather_than_naming_a_side():
    rows = split_series([-50] * 7, [3020] * 7)
    lines = hmt.attribute_slope(rows, current_from(rows))
    assert any("the split is not measurable on this reading" in line for line in lines)
    assert not any("Only the" in line for line in lines)


def test_attribution_never_raises_the_exit_status(tmp_path, monkeypatch, capsys):
    """It explains a finding judge() already made; a second alarm is noise."""
    monkeypatch.setattr(hmt, "read_meminfo",
                        lambda *a: (dict(meminfo(), AnonPages=5045000.0), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    calm_host(monkeypatch, pod_working_set=(3020.0, 43), pods_why=None)
    ledger = tmp_path / "l.jsonl"
    rows = split_series([2000, 2100, 2200, 2300, 2400, 2500, 2600],
                        [3020] * 7, start=BASE, step_hours=1.0)
    hmt.save(str(ledger), rows, 2000)
    code = hmt.main(["--ledger", str(ledger), "--no-record"])
    out = capsys.readouterr().out
    assert "ATTRIBUTION" in out
    assert "outside every Pod cgroup" in out
    assert code == 0


def test_main_says_why_it_could_not_split_by_pod(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hmt, "read_meminfo",
                        lambda *a: (dict(meminfo(), AnonPages=5045000.0), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    calm_host(monkeypatch, pod_working_set=None,
              pods_why="kubectl top returned no Pod rows")
    hmt.main(["--ledger", str(tmp_path / "l.jsonl")])
    out = capsys.readouterr().out
    assert "CANNOT SEE the Pod split" in out
    assert "kubectl top returned no Pod rows" in out


def test_every_attribution_blind_line_starts_with_a_marker_preflight_matches():
    from tools.preflight import is_caveat
    short = series([1800] * 7, field="mem_available_mib")
    blind = hmt.attribute_slope(short, current_from(short))
    no_current = split_series([2000] * 7, [3020] * 7)
    blind += hmt.attribute_slope(no_current, {"host": "server1"})
    stated = [line for line in blind if line.startswith("CANNOT")]
    assert len(stated) == 2
    for line in stated:
        assert is_caveat(line), line


def test_a_window_carrying_only_one_half_of_the_split_says_so():
    """A half-present window formatted a `None` rate into a sentence and crashed.

    The ledger is a file rather than a schema, so a window can hold rows from
    more than one writer; both halves have to be there before either is used.
    """
    rows = split_series([2000] * 7, [3020] * 7)
    for row in rows[:-1]:
        del row["pods_working_set_mib"]
    lines = hmt.attribute_slope(rows, current_from(rows))
    assert lines[0] == ("ATTRIBUTION  outside every Pod cgroup: 2000Mi now. "
                        "Pods: 3020Mi now.")
    assert lines[1].startswith("CANNOT SEE which side is moving")
    assert "pods_working_set_mib on 1 spanning 0.0h" in lines[1]
    assert len(lines) == 2
    assert not any("Mi/day" in line for line in lines)


def test_a_rate_is_never_fitted_to_a_thinner_series_than_the_gate_allows():
    """The window passed the gate; the field's own sub-series did not.

    Measured on the live ledger 2026-08-31: 19 readings over 6.0h in the
    window, `host_anon_mib` on 4 of them spanning 49 minutes, and the
    printed attribution was -7207Mi/day against a 1912Mi value. The gate
    was on the window, and `slope_per_day` silently drops the rows that do
    not carry the field, so it was never on the sample being fitted.
    """
    rows = split_series([2000, 2100, 2200, 2300, 2400, 2500, 2600],
                        [3020] * 7)
    for row in rows[:-4]:
        del row["host_anon_mib"]
        del row["pods_working_set_mib"]
    lines = hmt.attribute_slope(rows, current_from(rows))
    assert not any("Mi/day" in line for line in lines)
    assert any(line.startswith("CANNOT SEE which side is moving") for line in lines)
    assert any("host_anon_mib is on 4 reading(s) spanning 3.0h" in line
               for line in lines)
    # The levels were measured on this reading and are not thrown away with
    # the rate that could not be fitted.
    assert lines[0].startswith("ATTRIBUTION")
    assert "2600Mi now" in lines[0]


def test_a_sparse_tracked_field_is_not_projected_to_zero():
    """`judge` has the same hole and the same fix; its output is the loud one."""
    rows = series([1800] * 7, field="swap_free_mib", other=1800.0)
    for row in rows[:-3]:
        del row["mem_available_mib"]
    rows[-3]["mem_available_mib"] = 900.0
    rows[-2]["mem_available_mib"] = 600.0
    rows[-1]["mem_available_mib"] = 300.0
    lines, actionable, judged = hmt.judge(rows, current_from(rows), 7.0)
    assert judged
    assert not actionable
    assert any(line.startswith("CANNOT TREND available memory") for line in lines)
    assert any("3 of the 7 reading(s) in the window carry it" in line
               for line in lines)
    assert not any(line.startswith("FALLING") and "available memory" in line
                   for line in lines)


def test_a_tracked_field_missing_from_this_reading_says_which_half_is_missing():
    rows = series([1800] * 7, field="mem_available_mib", other=1800.0)
    current = current_from(rows)
    del current["mem_available_mib"]
    lines, _, _ = hmt.judge(rows, current, 7.0)
    assert any(line == "CANNOT TREND available memory — this reading does not carry it."
               for line in lines)


def test_gated_slope_reports_the_series_it_actually_fitted():
    rows = split_series([2000, 2100, 2200, 2300, 2400, 2500, 2600], [3020] * 7)
    rate, count, span = hmt.gated_slope(rows, "host_anon_mib")
    assert count == 7
    assert span == pytest.approx(6.0)
    assert rate == pytest.approx(2400.0)


# --- whose swap is it (issue #131) -----------------------------------


def swap_series(k3s_values, unowned_values, start=BASE, step_hours=1.0):
    """Readings carrying the cgroup swap split, oldest first."""
    rows = []
    for i, (k3s, unowned) in enumerate(zip(k3s_values, unowned_values)):
        at = start + timedelta(hours=step_hours * i)
        rows.append({
            "_at": at,
            "at": at.isoformat(),
            "host": "server1",
            "mem_total_mib": 7745.7,
            "mem_available_mib": 1800.0,
            "swap_total_mib": 2048.0,
            "swap_free_mib": 1800.0,
            "k3s_rss_mib": float(k3s),
            "k3s_swap_mib": 168.0,
            "unowned_swap_mib": float(unowned),
            "swap_used_mib": 1664.0,
        })
    return rows


def cadvisor(root_swap=1664.0, k3s_swap=168.0, kubepods_swap=1.0, k3s_rss=2123.0):
    return {
        hmt.CADVISOR_RSS_SERIES: {"/": 4637.0, hmt.K3S_CGROUP: k3s_rss},
        hmt.CADVISOR_SWAP_SERIES: {
            "/": root_swap,
            hmt.K3S_CGROUP: k3s_swap,
            hmt.KUBEPODS: kubepods_swap,
        },
    }


def test_the_remainder_is_the_root_minus_the_two_cgroups_that_have_a_name():
    """The live 2026-08-31 numbers: 1664 root, 168 k3s, 1 kubepods."""
    split, why = hmt.read_cgroup_split("server1", reader=lambda h: (cadvisor(), None))
    assert why is None
    assert split["k3s_rss_mib"] == 2123.0
    assert split["k3s_swap_mib"] == 168.0
    assert split["unowned_swap_mib"] == 1495.0
    assert split["swap_used_mib"] == 1664.0


def test_kubepods_is_subtracted_rather_than_assumed_to_be_nothing():
    """It is ~1Mi today. A remainder that ignores it stops being a remainder."""
    split, _ = hmt.read_cgroup_split(
        "server1", reader=lambda h: (cadvisor(kubepods_swap=400.0), None))
    assert split["unowned_swap_mib"] == 1096.0


def test_a_read_carrying_no_swap_series_is_a_missing_instrument_not_a_zero():
    series = cadvisor()
    del series[hmt.CADVISOR_SWAP_SERIES]
    split, why = hmt.read_cgroup_split("server1", reader=lambda h: (series, None))
    assert split is None
    assert "container_memory_swap" in why


def test_a_read_carrying_no_k3s_row_says_so_rather_than_reporting_a_split():
    series = cadvisor()
    del series[hmt.CADVISOR_SWAP_SERIES][hmt.K3S_CGROUP]
    del series[hmt.CADVISOR_RSS_SERIES][hmt.K3S_CGROUP]
    split, why = hmt.read_cgroup_split("server1", reader=lambda h: (series, None))
    assert split is None
    assert hmt.K3S_CGROUP in why


def test_a_refused_cadvisor_read_carries_its_own_reason_through():
    split, why = hmt.read_cgroup_split("server1", reader=lambda h: (None, "403 Forbidden"))
    assert split is None
    assert why == "403 Forbidden"


def test_a_growing_unowned_remainder_is_named_as_the_2026_08_29_shape():
    rows = swap_series([2123] * 7, [1200, 1250, 1300, 1350, 1400, 1450, 1495])
    lines = hmt.attribute_swap(rows, current_from(rows))
    assert lines[0].startswith("SWAP OWNERS")
    assert "Only the unowned swap is growing" in lines[-1]
    assert "SWAP HOLDERS" in lines[-1]


def test_a_growing_k3s_is_named_as_a_control_plane_not_as_the_unowned_half():
    rows = swap_series([1800, 1850, 1900, 1950, 2000, 2050, 2123], [1495] * 7)
    lines = hmt.attribute_swap(rows, current_from(rows))
    assert "Only k3s.service is growing" in lines[-1]
    assert "unowned" not in lines[-1]


def test_a_flat_pair_names_neither():
    rows = swap_series([2123] * 7, [1495] * 7)
    lines = hmt.attribute_swap(rows, current_from(rows))
    assert lines[-1] == "  Neither is growing over this window."


def test_both_growing_picks_neither():
    rows = swap_series([1800, 1850, 1900, 1950, 2000, 2050, 2123],
                       [1200, 1250, 1300, 1350, 1400, 1450, 1495])
    lines = hmt.attribute_swap(rows, current_from(rows))
    assert lines[-1] == "  Both are growing; this does not pick between them."


def test_a_window_too_thin_to_fit_says_so_rather_than_printing_a_rate():
    rows = swap_series([2123] * 7, [1495] * 7)
    for row in rows[:-1]:
        del row["unowned_swap_mib"]
    lines = hmt.attribute_swap(rows, current_from(rows))
    assert lines[0].startswith("SWAP OWNERS")
    assert lines[1].startswith("  CANNOT SEE which of them is moving")
    assert "unowned_swap_mib on 1 spanning 0.0h" in lines[1]
    assert not any("Mi/day" in line for line in lines)


def test_a_reading_without_the_cgroup_keys_says_so_rather_than_going_quiet():
    rows = swap_series([2123] * 7, [1495] * 7)
    lines = hmt.attribute_swap(rows, {"host": "server1"})
    assert len(lines) == 1
    assert lines[0].startswith("CANNOT SEE whose swap this is")


def test_every_swap_blind_line_starts_with_a_marker_preflight_matches():
    from tools.preflight import is_caveat
    rows = swap_series([2123] * 7, [1495] * 7)
    blind = hmt.attribute_swap(rows, {"host": "server1"})
    thin = swap_series([2123] * 7, [1495] * 7)
    for row in thin[:-1]:
        del row["unowned_swap_mib"]
    blind += hmt.attribute_swap(thin, current_from(thin))
    stated = [line for line in blind if line.strip().startswith("CANNOT")]
    assert len(stated) == 2
    for line in stated:
        assert is_caveat(line), line


def test_reading_now_records_the_split_it_is_handed():
    reading, why = hmt.reading_now(meminfo(), {"server1": 7745.7}, at=BASE,
                                   cgroup_split={"k3s_rss_mib": 2123.0,
                                                 "unowned_swap_mib": 1495.0})
    assert why is None
    assert reading["k3s_rss_mib"] == 2123.0
    assert reading["unowned_swap_mib"] == 1495.0


def test_main_says_why_it_could_not_name_the_swap(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hmt, "read_meminfo",
                        lambda *a: (dict(meminfo(), AnonPages=5045000.0), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    calm_host(monkeypatch, cgroup_split=None,
              cgroup_why="nodes/proxy is Forbidden for this account")
    hmt.main(["--ledger", str(tmp_path / "l.jsonl")])
    out = capsys.readouterr().out
    assert "CANNOT SEE whose swap it is" in out
    assert "nodes/proxy is Forbidden" in out


def test_naming_the_swap_owner_never_raises_the_exit_status(tmp_path, monkeypatch,
                                                            capsys):
    """judge() already raises on free swap falling; a second alarm is noise."""
    monkeypatch.setattr(hmt, "read_meminfo",
                        lambda *a: (dict(meminfo(), AnonPages=5045000.0), None))
    monkeypatch.setattr(hmt, "read_node_capacity", lambda: ({"server1": 7745.7}, None))
    calm_host(monkeypatch, pod_working_set=(3020.0, 43), pods_why=None,
              cgroup_split={"k3s_rss_mib": 2123.0, "k3s_swap_mib": 168.0,
                            "unowned_swap_mib": 1495.0, "swap_used_mib": 1664.0},
              cgroup_why=None)
    ledger = tmp_path / "l.jsonl"
    rows = swap_series([1800, 1850, 1900, 1950, 2000, 2050, 2123],
                       [1200, 1250, 1300, 1350, 1400, 1450, 1495])
    hmt.save(str(ledger), rows, 2000)
    code = hmt.main(["--ledger", str(ledger), "--no-record"])
    out = capsys.readouterr().out
    assert "SWAP OWNERS" in out
    assert "Both are growing" in out
    assert code == 0


SWEEP_LOG = """HOST PROCESS MEMORY -- 367 process(es) read, 0 exited mid-sweep
  total    rss   6213Mi  swap   1634Mi
  cgroup paths below are read through this container's own cgroup namespace and may be relativized -- context, not a verdict.
TOP 20 BY SWAP
  3848650 claude.exe           rss     68Mi  swap    407Mi  /../../../../system.slice/claude-remote.
  2621643 claude.exe           rss     66Mi  swap    350Mi  /../../../../system.slice/claude-remote.
   242153 k3s-server           rss   1878Mi  swap    115Mi  /../../../../system.slice/k3s.service
  2460615 claude               rss    101Mi  swap     60Mi  /../../../../system.slice/claude-remote.
   242185 containerd           rss    142Mi  swap     10Mi  /../../../../system.slice/k3s.service
   999999 someapp              rss     20Mi  swap      4Mi  /../../kubepods-burstable-podfeedface
TOP 20 BY RSS
   242153 k3s-server           rss   1878Mi  swap    115Mi  /../../../../system.slice/k3s.service
  2630610 argocd-applicat      rss    372Mi  swap      0Mi  /../../../kubepods-besteffort.slice/kube
"""


class FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def sweep_runner(pods=None, log=SWEEP_LOG, logs_rc=0, nodes=("server1",),
                 nodes_rc=0, logs=None):
    """A `subprocess.run` that answers the three calls `read_swap_holders` makes.

    `pods` rows are `(name, phase, creationTimestamp)` and optionally a fourth
    element naming the node the Pod ran on; it defaults to the first entry of
    `nodes`, which keeps a one-node cluster the simple case it used to be.
    `logs` maps a Pod name to its own log when a test needs the two Pods of one
    run to say different things.
    """
    if pods is None:
        pods = [("host-process-memory-29803110-t79dw", "Succeeded",
                 "2026-08-31T14:30:00Z")]
    body = {"items": [
        {"metadata": {"name": row[0], "creationTimestamp": row[2]},
         "spec": {"nodeName": row[3] if len(row) > 3 else nodes[0]},
         "status": {"phase": row[1]}} for row in pods]}
    node_body = {"items": [{"metadata": {"name": n}} for n in nodes]}
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        if argv[1] == "get" and argv[2] == "nodes":
            return FakeProc(stdout=json.dumps(node_body), returncode=nodes_rc,
                            stderr="" if nodes_rc == 0 else "no nodes for you")
        if argv[1] == "get":
            return FakeProc(stdout=json.dumps(body))
        return FakeProc(stdout=(logs or {}).get(argv[-1], log),
                        returncode=logs_rc,
                        stderr="" if logs_rc == 0 else "not found")

    run.seen = seen
    return run


NOW = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)


def test_the_sweep_report_is_read_off_the_newest_completed_pod():
    run = sweep_runner(pods=[
        ("host-process-memory-29803000-old", "Succeeded", "2026-08-31T13:30:00Z"),
        ("host-process-memory-29803110-t79dw", "Succeeded", "2026-08-31T14:30:00Z"),
    ])
    report, why = hmt.read_swap_holders(runner=run, now=NOW)
    assert why is None
    assert [pod["pod"] for pod in report["pods"]] == ["host-process-memory-29803110-t79dw"]
    assert ["kubectl", "logs", "-n", "infra",
            "host-process-memory-29803110-t79dw"] in run.seen
    assert report["age_hours"] == pytest.approx(0.5)
    assert report["total_swap_mib"] == 1634.0


def test_a_running_pod_is_not_read_because_half_a_top_n_is_not_a_top_n():
    run = sweep_runner(pods=[
        ("host-process-memory-29803000-old", "Succeeded", "2026-08-31T13:30:00Z"),
        ("host-process-memory-29803110-t79dw", "Running", "2026-08-31T14:59:00Z"),
    ])
    report, _ = hmt.read_swap_holders(runner=run, now=NOW)
    assert [pod["pod"] for pod in report["pods"]] == ["host-process-memory-29803000-old"]


def test_only_the_swap_section_is_parsed_so_the_rss_table_is_not_counted_twice():
    report, _ = hmt.read_swap_holders(runner=sweep_runner(), now=NOW)
    pids = [row["pid"] for row in report["rows"]]
    assert pids.count(242153) == 1
    assert 2630610 not in pids
    assert len(report["rows"]) == 6


def test_a_reaped_sweep_is_a_missing_instrument_not_an_empty_list():
    report, why = hmt.read_swap_holders(
        runner=sweep_runner(pods=[("some-other-pod", "Succeeded",
                                   "2026-08-31T14:30:00Z")]), now=NOW)
    assert report is None
    assert "host-process-memory" in why


def test_a_report_whose_format_moved_says_so_rather_than_reporting_nothing():
    report, why = hmt.read_swap_holders(
        runner=sweep_runner(log="HOST PROCESS MEMORY -- 0 read\nTOP 20 BY SWAP\n"),
        now=NOW)
    assert report is None
    assert "did not say it found none" in why


def test_a_failed_logs_call_carries_its_own_reason_through():
    report, why = hmt.read_swap_holders(runner=sweep_runner(logs_rc=1), now=NOW)
    assert report is None
    assert "not found" in why


def test_the_named_processes_are_split_by_the_same_three_buckets_as_the_remainder():
    report, _ = hmt.read_swap_holders(runner=sweep_runner(), now=NOW)
    lines = hmt.name_swap_holders(report, top=2)
    # 407 + 350 + 60 outside k3s.service and outside every Pod.
    assert "817Mi of that is outside k3s.service" in lines[1]
    assert lines[0].startswith("SWAP HOLDERS")
    assert "946Mi swapped of 1634Mi on them (58%)" in lines[0]


def test_the_biggest_holder_is_named_first_and_the_rest_are_summed():
    report, _ = hmt.read_swap_holders(runner=sweep_runner(), now=NOW)
    lines = hmt.name_swap_holders(report, top=2)
    assert ("407Mi swapped, 68Mi resident — claude.exe on server1 (pid 3848650, unowned)") in lines[2]
    assert "350Mi swapped" in lines[3]
    # 115 + 60 + 10 + 4 left over, and the log that holds them is named.
    assert "the other 4 named process(es) hold 189Mi" in lines[-1]
    assert "kubectl logs -n infra host-process-memory-29803110-t79dw" in lines[-1]


def test_a_k3s_process_is_not_counted_against_the_unowned_remainder():
    assert hmt.holder_scope("/../../../../system.slice/k3s.service") == "k3s"
    assert hmt.holder_scope("/../../kubepods-burstable-podfeedface") == "pods"
    assert hmt.holder_scope("/../../../../system.slice/claude-remote.") == "unowned"


def test_naming_the_holders_never_raises_because_judge_already_does():
    report, _ = hmt.read_swap_holders(runner=sweep_runner(), now=NOW)
    lines = hmt.name_swap_holders(report)
    assert not any(line.startswith(("SWAP FALLING", "MEMORY")) for line in lines)
    assert all("exit 2" not in line for line in lines)


#: The shape platform-config#594 deploys as of 2026-09-01 -- an `age N.Nd`
#: column between the swap figure and the cgroup path, and a `SWAP BY CGROUP`
#: roll-up above the per-process table. Copied from a real
#: `kubectl logs -n infra host-process-memory-29804670-822v4`, trimmed.
SWEEP_LOG_WITH_AGE = """HOST PROCESS MEMORY -- 346 process(es) read, 1 exited mid-sweep
  total    rss   5921Mi  swap   1606Mi
  cgroup paths below are read through this container's own cgroup namespace and may be relativized -- context, not a verdict.
SWAP BY CGROUP -- 67 bucket(s) holding 1606Mi; cgroup strings in full.
  swap   1379Mi  85.8%    7 process(es)  oldest  14.0d  /../../../../system.slice/claude-remote.service
  swap    165Mi  10.3%   46 process(es)  oldest  43.8d  /../../../../system.slice/k3s.service
TOP 20 BY SWAP
  1410665 claude.exe           rss     49Mi  swap    282Mi  age   0.4d  /../../../../system.slice/claude-remote.service
  3435586 claude.exe           rss     44Mi  swap    236Mi  age   2.4d  /../../../../system.slice/claude-remote.service
   242153 k3s-server           rss   2093Mi  swap    122Mi  age  29.6d  /../../../../system.slice/k3s.service
TOP 20 BY RSS
   242153 k3s-server           rss   2093Mi  swap    122Mi  age  29.6d  /../../../../system.slice/k3s.service
"""


def test_the_current_sweep_format_parses_because_the_age_column_is_read():
    report, why = hmt.read_swap_holders(
        runner=sweep_runner(log=SWEEP_LOG_WITH_AGE), now=NOW)
    assert why is None
    assert [row["pid"] for row in report["rows"]] == [1410665, 3435586, 242153]
    assert report["rows"][0]["swap_mib"] == 282.0
    assert report["rows"][0]["rss_mib"] == 49.0
    assert (report["rows"][0]["cgroup"]
            == "/../../../../system.slice/claude-remote.service")
    assert report["rows"][0]["age_days"] == pytest.approx(0.4)
    assert report["rows"][2]["age_days"] == pytest.approx(29.6)


def test_the_cgroup_rollup_lines_are_not_read_as_processes():
    # `SWAP BY CGROUP` sits above `TOP 20 BY SWAP` and its rows carry no pid,
    # so counting them would double every figure the roll-up already totals.
    report, _ = hmt.read_swap_holders(
        runner=sweep_runner(log=SWEEP_LOG_WITH_AGE), now=NOW)
    assert len(report["rows"]) == 3


def test_a_sweep_without_the_age_column_still_parses_and_says_it_has_no_age():
    report, why = hmt.read_swap_holders(runner=sweep_runner(), now=NOW)
    assert why is None
    assert all(row["age_days"] is None for row in report["rows"])


def test_the_age_is_printed_beside_a_named_holder_when_the_sweep_supplied_one():
    report, _ = hmt.read_swap_holders(
        runner=sweep_runner(log=SWEEP_LOG_WITH_AGE), now=NOW)
    lines = hmt.name_swap_holders(report, top=2)
    assert ("282Mi swapped, 49Mi resident — claude.exe on server1 "
            "(pid 1410665, unowned, 0.4d old)") in lines[2]


def test_an_ageless_sweep_prints_no_age_rather_than_a_placeholder():
    # This one does NOT fail if the age line is reverted -- the output is
    # byte-identical either way, which the reviewer caught. It guards the
    # opposite direction, and that is checked rather than asserted: mutating
    # the conditional to an unconditional f", {row.get('age_days')}d old"
    # fails it, because an ageless sweep then prints "None d old".
    report, _ = hmt.read_swap_holders(runner=sweep_runner(), now=NOW)
    lines = hmt.name_swap_holders(report, top=1)
    assert ("407Mi swapped, 68Mi resident — claude.exe on server1 "
            "(pid 3848650, unowned)") in lines[2]
    assert "None" not in lines[2]
    assert "d old" not in lines[2]


#: A real, complete report from a box with no swap at all -- copied from
#: `kubectl logs -n infra host-process-memory-29807850-j4cd8`, trimmed. server2
#: has no swap, so its honest answer is an empty top-N list and the sweep says
#: so in words. Reading that as a broken parser is what sent Cycle 863 looking
#: for a format change that had not happened.
SWEEP_LOG_NO_SWAP = """HOST PROCESS MEMORY on server2 -- 193 process(es) read, 1 exited mid-sweep
  total    rss   1719Mi  swap      0Mi
  cgroup paths below are read through this container's own cgroup namespace and may be relativized -- context, not a verdict.
SWAP BY CGROUP -- 0 bucket(s) holding 0Mi; cgroup strings in full.
  swap      0Mi   0.0%  in the other 0 bucket(s)
TOP 20 BY SWAP
  none -- every process read reported zero swap
TOP 20 BY RSS
    32737 grafana              rss    325Mi  swap      0Mi  age   0.0d  /../../kubepods-burstable-pod8a091491.slice
"""


def test_a_swapless_box_is_a_result_not_a_format_this_reader_cannot_parse():
    report, why = hmt.read_swap_holders(
        runner=sweep_runner(log=SWEEP_LOG_NO_SWAP, nodes=("server2",)), now=NOW)
    assert why is None
    assert report["rows"] == []
    assert report["total_swap_mib"] == 0.0


def test_every_pod_of_the_newest_run_is_read_so_one_node_cannot_stand_for_two():
    run = sweep_runner(
        pods=[("host-process-memory-29803110-aaaaa", "Succeeded",
               "2026-08-31T14:30:00Z", "server1"),
              ("host-process-memory-29803110-bbbbb", "Succeeded",
               "2026-08-31T14:30:00Z", "server2"),
              ("host-process-memory-29803000-old", "Succeeded",
               "2026-08-31T13:30:00Z", "server1")],
        nodes=("server1", "server2"),
        logs={"host-process-memory-29803110-aaaaa": SWEEP_LOG,
              "host-process-memory-29803110-bbbbb": SWEEP_LOG_NO_SWAP})
    report, why = hmt.read_swap_holders(runner=run, now=NOW)
    assert why is None
    assert report["nodes"] == ["server1", "server2"]
    assert report["unswept"] == []
    # The older run is a different Job and is not mixed in with the newest one.
    assert [pod["pod"] for pod in report["pods"]] == [
        "host-process-memory-29803110-aaaaa",
        "host-process-memory-29803110-bbbbb"]
    assert all(row["node"] == "server1" for row in report["rows"])
    assert report["total_swap_mib"] == 1634.0


def test_a_node_the_run_never_reached_is_named_rather_than_left_out():
    # The live failure: every retained sweep ran on server2, so server1's swap
    # holders were unnamed and the output said nothing about it at all.
    report, _ = hmt.read_swap_holders(
        runner=sweep_runner(log=SWEEP_LOG_NO_SWAP,
                            nodes=("server1", "server2"),
                            pods=[("host-process-memory-29803110-t79dw",
                                   "Succeeded", "2026-08-31T14:30:00Z",
                                   "server2")]),
        now=NOW)
    assert report["unswept"] == ["server1"]
    lines = hmt.name_swap_holders(report)
    assert any(line.startswith("  NOT SWEPT  server1") for line in lines)
    assert "1 node(s) (server2)" in lines[0]


def test_the_pods_own_node_name_outranks_the_one_printed_in_the_log():
    # An older image prints no node in its header. The scheduler's answer is on
    # the Pod either way, so a rollback of the manifest does not blind this.
    report, _ = hmt.read_swap_holders(
        runner=sweep_runner(log=SWEEP_LOG, nodes=("server1",)), now=NOW)
    assert report["nodes"] == ["server1"]
    assert report["rows"][0]["node"] == "server1"


def test_an_unreadable_node_list_says_it_cannot_say_what_was_missed():
    report, why = hmt.read_swap_holders(
        runner=sweep_runner(nodes_rc=1, nodes=("server1", "server2")), now=NOW)
    assert why is None
    assert report["unswept"] == []
    lines = hmt.name_swap_holders(report)
    assert any("CANNOT SAY which nodes were missed" in line for line in lines)
    assert not any(line.startswith("  NOT SWEPT") for line in lines)


def test_one_unreadable_pod_does_not_discard_the_node_that_did_report():
    run = sweep_runner(
        pods=[("host-process-memory-29803110-aaaaa", "Succeeded",
               "2026-08-31T14:30:00Z", "server1"),
              ("host-process-memory-29803110-bbbbb", "Succeeded",
               "2026-08-31T14:30:00Z", "server2")],
        nodes=("server1", "server2"),
        logs={"host-process-memory-29803110-aaaaa": SWEEP_LOG,
              "host-process-memory-29803110-bbbbb": "nothing like a report"})
    report, why = hmt.read_swap_holders(runner=run, now=NOW)
    assert why is None
    assert report["nodes"] == ["server1"]
    assert report["unswept"] == ["server2"]
    lines = hmt.name_swap_holders(report)
    assert any(line.startswith("  UNREADABLE  host-process-memory-29803110-bbbbb")
               for line in lines)
