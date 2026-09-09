"""The arithmetic behind "when does this loop go dark".

Every case here is built from numbers the test itself states, never from
the module's own constants -- a fixture assembled out of the thing under
test agrees with itself no matter what, which is how three checks passed
in one night on Cycle 253.
"""

import datetime as dt
import json

from tools import quota_runway
from tools.quota_runway import (
    ASSUMED,
    DARK,
    HEALTHY,
    OBSERVED,
    SCHEDULE,
    TIGHT,
    burn_rate,
    observed_cadence_minutes,
    runway,
)


def test_budget_outliving_the_window_is_healthy():
    state, hours, dark, lost, lines = runway(
        remaining_pct=50, hours_to_reset=24, pct_per_day=10
    )
    assert state == HEALTHY
    # 50% at 10%/day is 5 days, far past a one-day window.
    assert hours == 120
    assert dark == 0
    assert lost == 0
    assert "outlives" in lines[0]


def test_the_live_cycle_259_reading_goes_dark():
    """12% left, 18%/day, 58h to reset -- the numbers that prompted this."""
    state, hours, dark, lost, lines = runway(
        remaining_pct=12, hours_to_reset=58, pct_per_day=18
    )
    assert state == DARK
    assert round(hours, 1) == 16.0
    assert round(dark) == 42
    # 42h of dark at one wake-up every 40 minutes.
    assert lost == 63
    assert "63 heartbeats" in " ".join(lines)


def test_running_out_just_before_the_reset_is_tight_not_dark():
    # 10% at 24%/day lasts 10h against 10.5h to the reset: 30 minutes
    # short, which is less than one 40-minute interval.
    state, _, dark, lost, lines = runway(
        remaining_pct=10, hours_to_reset=10.5, pct_per_day=24
    )
    assert state == TIGHT
    assert lost == 0
    assert round(dark * 60) == 30
    assert "Not worth acting on" in lines[0]


def test_an_already_spent_window_reports_the_whole_gap():
    state, hours, dark, lost, lines = runway(
        remaining_pct=0, hours_to_reset=20, pct_per_day=18
    )
    assert state == DARK
    assert hours == 0
    assert dark == 20
    assert lost == 30  # 20h at 40-minute cadence
    assert "already spent" in lines[0]


def test_no_measurable_burn_is_reported_not_divided_by():
    state, hours, dark, lost, _ = runway(
        remaining_pct=40, hours_to_reset=48, pct_per_day=0
    )
    assert state == HEALTHY
    assert hours == float("inf")
    assert (dark, lost) == (0.0, 0)


def test_the_suggested_cadence_would_actually_last():
    """The advice line has to be checkable, not plausible."""
    _, _, _, _, lines = runway(remaining_pct=12, hours_to_reset=58, pct_per_day=18)
    advice = [ln for ln in lines if "minutes between cycles" in ln]
    assert len(advice) == 1
    # 12% over 58h is 4.966%/day; 40 min * 18 / 4.966 = 145 min.
    suggested = float(advice[0].split("about ")[1].split(" ")[0])
    assert round(suggested) == 145
    # Feeding the answer straight back in lands exactly on the dark_hours
    # == 0 boundary, so it would pass under a `< 0` / `<= 0` mutation
    # either way -- the code agreeing with its own algebra. Step just
    # inside and just outside instead, which no rounding can move.
    assert runway(12, 58, 18 * 40 / (suggested * 1.05))[0] == HEALTHY
    assert runway(12, 58, 18 * 40 / (suggested * 0.95))[0] == DARK


def test_a_shorter_cadence_loses_proportionally_more_wake_ups():
    _, _, _, at_40, _ = runway(12, 58, 18, cadence_minutes=40)
    _, _, _, at_20, _ = runway(12, 58, 18, cadence_minutes=20)
    assert (at_40, at_20) == (63, 126)  # the same 42h dark, twice the wake-ups


def _row(at, pct):
    return {"at": at, "seven_day": pct}


HOUR = 3600


def test_burn_rate_is_measured_off_the_slope():
    now = 100 * HOUR
    rows = [_row(now - 24 * HOUR, 50), _row(now - 12 * HOUR, 59), _row(now, 68)]
    # 18 points over 24 hours.
    assert burn_rate(rows, now, window_hours=24) == 18


def test_a_reset_inside_the_sample_is_dropped_not_averaged_through():
    now = 100 * HOUR
    rows = [
        _row(now - 30 * HOUR, 90),  # old window, nearly spent
        _row(now - 24 * HOUR, 4),  # reset: a 86-point drop
        _row(now, 22),
    ]
    # Averaging through the reset would give a negative rate. Only the
    # post-reset samples count: 18 points over 24 hours.
    assert burn_rate(rows, now, window_hours=48) == 18


def test_burn_rate_ignores_samples_outside_the_window():
    now = 100 * HOUR
    rows = [_row(now - 48 * HOUR, 0), _row(now - 6 * HOUR, 60), _row(now, 63)]
    # Only the last two are inside 6h: 3 points over 6 hours = 12/day.
    assert burn_rate(rows, now, window_hours=6) == 12


def test_burn_rate_is_none_when_it_cannot_be_measured():
    now = 100 * HOUR
    assert burn_rate([], now) is None
    assert burn_rate([_row(now, 50)], now) is None
    # Two samples at the same instant have no slope to read.
    assert burn_rate([_row(now, 50), _row(now, 51)], now) is None
    # Samples exist but all fall outside the window.
    assert burn_rate([_row(now - 9 * HOUR, 1), _row(now - 8 * HOUR, 2)], now, 1) is None


def test_rows_missing_the_key_are_skipped_rather_than_crashing():
    now = 100 * HOUR
    rows = [{"at": now - 24 * HOUR}, _row(now - 24 * HOUR, 50), _row(now, 68)]
    assert burn_rate(rows, now, window_hours=24) == 18


def test_unordered_rows_give_the_same_answer():
    now = 100 * HOUR
    ordered = [_row(now - 24 * HOUR, 50), _row(now - 12 * HOUR, 59), _row(now, 68)]
    assert burn_rate(list(reversed(ordered)), now, 24) == burn_rate(ordered, now, 24)


# --- main(), which had no coverage at all until the reviewer said so ---


def _snapshot(tmp_path, resets_at, remaining=12.0, fetched_at=1000.0):
    p = tmp_path / "snap.json"
    p.write_text(
        json.dumps(
            {
                "windows": [
                    {
                        "window": "seven_day",
                        "used_pct": 100 - remaining,
                        "remaining_pct": remaining,
                        "resets_at": resets_at,
                        "pace": 1.344,
                    }
                ],
                "fetched_at": fetched_at,
            }
        )
    )
    return p


def _history(tmp_path, fetched_at=1000.0):
    p = tmp_path / "hist.jsonl"
    rows = [
        {"at": fetched_at - 24 * 3600, "seven_day": 69.0},
        {"at": fetched_at, "seven_day": 88.0},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_an_empty_reset_time_is_reported_not_raised(tmp_path, capsys):
    """The bridge writes "" at the reset instant. There is one such row in
    the real quota-history.jsonl, logged during the 2026-08-12 reset."""
    rc = main_with(_snapshot(tmp_path, resets_at=""), _history(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    # The generic parse failure below would also catch "" and also exit 1,
    # so asserting on the exit code alone pins nothing -- this test passed
    # with the guard deleted. What the guard buys is telling the reader
    # the window just rolled over instead of implying a corrupt file.
    assert "just rolled over" in out
    assert "cannot parse" not in out


def test_an_unparseable_reset_time_is_reported_not_raised(tmp_path, capsys):
    rc = main_with(_snapshot(tmp_path, resets_at="not a date"), _history(tmp_path))
    assert rc == 1
    assert "cannot parse" in capsys.readouterr().out


def test_a_missing_seven_day_window_is_reported(tmp_path, capsys):
    p = tmp_path / "snap.json"
    p.write_text(json.dumps({"windows": [{"window": "five_hour"}]}))
    assert main_with(p, _history(tmp_path)) == 1
    assert "no seven_day window" in capsys.readouterr().out


def test_too_little_history_to_measure_is_reported(tmp_path, capsys):
    snap = _snapshot(tmp_path, resets_at="1970-01-01T02:00:00+00:00")
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert main_with(snap, empty) == 1
    assert "not enough history" in capsys.readouterr().out


def test_a_good_snapshot_prints_the_verdict_and_exits_nonzero_when_dark(
    tmp_path, capsys
):
    # fetched_at 1000.0, reset 58h later; 19%/day burn over the history.
    reset = dt.datetime.fromtimestamp(1000.0 + 58 * 3600, dt.timezone.utc)
    snap = _snapshot(tmp_path, resets_at=reset.isoformat())
    rc = main_with(snap, _history(tmp_path))
    out = capsys.readouterr().out
    assert rc == 2
    assert out.startswith("DARK:")
    assert "minutes between cycles" in out


def main_with(snapshot, history):
    return quota_runway.main_argv(
        ["--snapshot", str(snapshot), "--history", str(history), "--cadence-minutes", "40"]
    )


def test_main_asks_agora_for_the_cadence_rather_than_assuming_it(tmp_path, capsys, monkeypatch):
    """The cadence has moved five times since 08-08; a constant would be
    the very mistake this module's docstring accuses prompt.md of."""
    reset = dt.datetime.fromtimestamp(1000.0 + 58 * 3600, dt.timezone.utc)
    snap = _snapshot(tmp_path, resets_at=reset.isoformat())
    monkeypatch.setattr(
        quota_runway, "live_cadence_minutes", lambda *a, **k: (120, SCHEDULE)
    )
    quota_runway.main_argv(["--snapshot", str(snap), "--history", str(_history(tmp_path))])
    out = capsys.readouterr().out
    # 43h dark at 120-minute cadence is 21 wake-ups, not the 64 that a
    # hardcoded 40 would give.
    assert "21 heartbeats" in out
    assert "64 heartbeats" not in out


def test_the_cadence_lookup_falls_back_rather_than_raising(monkeypatch):
    import agora_runner.cycle_health as ch

    monkeypatch.setattr(ch, "nova_cadence_minutes", lambda: None)
    assert quota_runway.live_cadence_minutes() == (quota_runway.CADENCE_MINUTES, ASSUMED)

    def boom():
        raise RuntimeError("agora unreachable")

    monkeypatch.setattr(ch, "nova_cadence_minutes", boom)
    assert quota_runway.live_cadence_minutes() == (quota_runway.CADENCE_MINUTES, ASSUMED)

    monkeypatch.setattr(ch, "nova_cadence_minutes", lambda: 60)
    assert quota_runway.live_cadence_minutes() == (60, SCHEDULE)


def test_an_assumed_cadence_says_so_and_a_measured_one_does_not(tmp_path, capsys, monkeypatch):
    """The silent fallback was the normal path from the bridge pod, and it
    reported a wake-up count off a stale 40 while the truth was 60."""
    reset = dt.datetime.fromtimestamp(1000.0 + 58 * 3600, dt.timezone.utc)
    snap = _snapshot(tmp_path, resets_at=reset.isoformat())
    argv = ["--snapshot", str(snap), "--history", str(_history(tmp_path))]

    monkeypatch.setattr(
        quota_runway, "live_cadence_minutes", lambda *a, **k: (40, ASSUMED)
    )
    quota_runway.main_argv(argv)
    out = capsys.readouterr().out
    assert "NOTE: could not reach Agora" in out
    assert "assume 40 minutes" in out

    monkeypatch.setattr(
        quota_runway, "live_cadence_minutes", lambda *a, **k: (60, SCHEDULE)
    )
    quota_runway.main_argv(argv)
    assert "NOTE: could not reach Agora" not in capsys.readouterr().out


# --- the cadence measured off the loop's own wake-ups (Cycle 260) ---


def _starts(*minutes_from_zero):
    """A wake-up log at the stated minute offsets, plus the burn rows that
    share the file, so the filter has something to reject."""
    rows = [{"at": 0.0, "seven_day": 10.0}, {"at": 3600.0, "seven_day": 11.0}]
    rows += [{"at": m * 60.0, "boundary": "start"} for m in minutes_from_zero]
    return rows


def test_a_clean_hourly_log_measures_sixty():
    rows = _starts(0, 60, 120, 180, 240)
    assert observed_cadence_minutes(rows, now=240 * 60) == (60, 4, 4)


def test_a_manual_start_splits_an_interval_and_the_mode_survives_it():
    """The owner talking to Nova opens a session too, which inserts a start
    partway through an interval. That splits one 60 into 31 + 29 without
    changing their sum, so the low buckets fill up with halves."""
    rows = _starts(0, 60, 120, 151, 180, 240, 244, 300)
    minutes, support, sampled = observed_cadence_minutes(rows, now=300 * 60)
    assert (minutes, support, sampled) == (60, 3, 7)

    # The point of using the mode: on this same log the median is 30,
    # which would halve the suggested interval and double the wake-up
    # count. Asserted here so a future switch back to the median fails.
    gaps = sorted([60, 60, 31, 29, 60, 4, 56])
    assert gaps[len(gaps) // 2] == 56 and minutes != 56


def test_two_starts_in_the_same_minute_are_one_wake_up_not_two():
    # 0 and 1 are a re-entry inside one session: no cadence the owner has
    # ever set is under 40 minutes. Dropping them leaves four real gaps.
    rows = _starts(0, 1, 60, 120, 180, 240)
    assert observed_cadence_minutes(rows, now=240 * 60) == (60, 4, 4)


def test_a_sample_too_small_for_a_mode_measures_nothing():
    assert observed_cadence_minutes(_starts(0, 60, 120), now=120 * 60) == (None, 0, 2)


def test_a_mode_backed_by_a_single_gap_is_not_a_measurement():
    """Four gaps, all different: a winner with one vote is a coin toss."""
    rows = _starts(0, 20, 65, 140, 240)
    assert observed_cadence_minutes(rows, now=240 * 60) == (None, 0, 4)


def test_wake_ups_outside_the_window_do_not_vote():
    """A cadence change has to show up, so the old interval must age out."""
    old = [-720, -680, -640, -600, -560]  # a 40-minute era, 9-12h ago
    new = [0, 120, 240, 360, 480]  # every two hours since
    rows = _starts(*(old + new))
    assert observed_cadence_minutes(rows, now=480 * 60, window_hours=9) == (120, 4, 4)
    # Widen the window until the old era outnumbers the new one and it
    # wins on votes -- which is why this window is 48h and not a week.
    rows = _starts(*([-800, -760] + old + new))
    assert observed_cadence_minutes(rows, now=480 * 60, window_hours=48)[0] == 40


def test_a_tie_between_two_eras_reports_the_recent_one():
    """The days after a cadence change are exactly when this gets asked,
    and for part of that stretch the two intervals draw."""
    rows = _starts(-720, -680, -640, -600, 0, 120, 240, 360)
    minutes, support, sampled = observed_cadence_minutes(
        rows, now=360 * 60, window_hours=48
    )
    assert support == 3 and sampled == 7  # three 40s, three 120s, one gap between
    assert minutes == 120


def test_rows_that_are_not_wake_ups_are_ignored():
    """The burn rate reads the same file; its rows carry no boundary."""
    rows = [{"at": m * 60.0, "seven_day": 10.0} for m in (0, 5, 10, 15, 20)]
    assert observed_cadence_minutes(rows, now=20 * 60) == (None, 0, 0)


def test_the_lookup_measures_the_cadence_when_agora_is_unreachable(monkeypatch):
    """The bridge pod's normal path: no Agora, but a wake-up log."""
    import agora_runner.cycle_health as ch

    monkeypatch.setattr(ch, "nova_cadence_minutes", lambda: None)
    rows = _starts(0, 90, 180, 270, 360)
    assert quota_runway.live_cadence_minutes(rows, now=360 * 60) == (90, OBSERVED)

    # Agora answering still wins: it says what the schedule *is*.
    monkeypatch.setattr(ch, "nova_cadence_minutes", lambda: 60)
    assert quota_runway.live_cadence_minutes(rows, now=360 * 60) == (60, SCHEDULE)


def test_an_observed_cadence_says_so_and_shows_its_sample(tmp_path, capsys):
    """The note has to be distinguishable from the assumed one, and it has
    to carry the counts -- a measurement whose sample is hidden reads
    exactly like the constant it replaced."""
    reset = dt.datetime.fromtimestamp(1000.0 + 58 * 3600, dt.timezone.utc)
    snap = _snapshot(tmp_path, resets_at=reset.isoformat())

    hist = tmp_path / "hist.jsonl"
    rows = [
        {"at": 1000.0 - 24 * 3600, "seven_day": 69.0},
        {"at": 1000.0, "seven_day": 88.0},
    ]
    # Five wake-ups two hours apart, ending at the snapshot instant.
    rows += [
        {"at": 1000.0 - h * 3600, "boundary": "start"} for h in (8, 6, 4, 2, 0)
    ]
    hist.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    quota_runway.main_argv(["--snapshot", str(snap), "--history", str(hist)])
    out = capsys.readouterr().out
    assert "measured from this loop's own wake-ups" in out
    assert "the most common gap in 4 of 4 starts" in out
    assert "assume" not in out
    # 43h dark at the measured 120 minutes, not the 64 a stale 40 gives.
    assert "21 heartbeats" in out


# A cadence change makes the two cadences in this tool disagree, and the
# disagreement is invisible in the output unless it is said out loud.
# Measured 2026-08-27, Cycle 523: the heartbeat moved 20 -> 30 minutes, and
# the moment `nova_heartbeat_snapshot` learned to read it the suggestion
# jumped 29 -> 43 with no new measurement behind it -- the rate from the old
# cadence scaled by the new interval.


def test_the_suggested_interval_scales_from_the_cadence_that_earned_the_rate():
    """40%/day over a 20-minute sample needs 58 minutes. Reading the new
    30-minute schedule must not turn that into 87.

    This is the invariant the projection has to preserve: scaling the rate
    down by 20/30 and then dividing by 30 gives back the number that came
    out of dividing the unscaled rate by 20.
    """
    at_twenty = runway(90.0, 155.9, 40.0, 20)[4]
    after_change = runway(90.0, 155.9, 40.0, 30, spend_cadence_minutes=20)[4]
    assert "about 58 minutes" in at_twenty[-1]
    assert "about 58 minutes" in after_change[-1]


def test_a_slower_schedule_stretches_the_runway_as_well_as_the_divisor():
    """Both halves move, and until Cycle 1275 only one of them did.

    Running 30 minutes apart instead of 20 means two thirds as many cold
    sessions per hour, so the budget lasts half again as long *and* fewer
    wake-ups fall in the gap. Holding the runway fixed across a cadence
    change says spending is unaffected by running fewer cycles, which
    contradicts the premise `_needed_cadence` stands on.
    """
    _, hours_20, dark_20, lost_20, _ = runway(90.0, 155.9, 40.0, 20)
    _, hours_30, dark_30, lost_30, _ = runway(
        90.0, 155.9, 40.0, 30, spend_cadence_minutes=20
    )
    assert hours_30 > hours_20, "a slower heartbeat buys more runway"
    assert lost_20 > lost_30, "a slower heartbeat wastes fewer wake-ups"
    # Worked by hand, not restated from the source. At 20 the rate is
    # unscaled: 90% at 40%/day is 54h of runway against 155.9h, so 101.9h
    # dark = 6114 minutes, // 20 = 305 wake-ups. At 30 the rate projects to
    # 40 * 20/30 = 26.67%/day: 81h of runway, 74.9h dark = 4494 minutes,
    # // 30 = 149.
    assert (round(hours_20), round(hours_30)) == (54, 81)
    assert (lost_20, lost_30) == (305, 149)


def test_a_faster_schedule_can_turn_healthy_into_dark():
    """The live 2026-09-09 reading, which is why this was found.

    10% left, 9.1h to the reset, and a 17.0%/day rate earned at about 25
    minutes with the heartbeat now set to 15. Unprojected that is 14.1h of
    runway and prints HEALTHY. Projected onto 15 minutes the rate is
    17.0 * 25/15 = 28.3%/day, which is 8.5h -- 38 minutes short, two
    heartbeats that wake with nothing.
    """
    state, hours, dark, lost, lines = runway(
        10.0, 9.1, 17.0, 15, spend_cadence_minutes=25
    )
    assert state == DARK
    assert round(hours, 1) == 8.5
    assert lost == 2
    assert "28.3%/day" in lines[0]

    unprojected, _, _, _, _ = runway(10.0, 9.1, 17.0, 15)
    assert unprojected == HEALTHY, "the old reading, kept as the control"


def test_an_unchanged_cadence_needs_no_second_number():
    """The default has to be the old behaviour exactly, or every existing
    caller silently changes meaning."""
    assert runway(90.0, 155.9, 20.0, 30) == runway(
        90.0, 155.9, 20.0, 30, spend_cadence_minutes=30
    )


def test_a_moved_schedule_says_which_number_the_advice_stands_on(tmp_path, capsys):
    reset = dt.datetime.fromtimestamp(1000.0 + 58 * 3600, dt.timezone.utc)
    snap = _snapshot(tmp_path, resets_at=reset.isoformat())
    hist = tmp_path / "hist.jsonl"
    rows = [
        {"at": 1000.0 - 24 * 3600, "seven_day": 69.0},
        {"at": 1000.0, "seven_day": 88.0},
    ]
    rows += [
        {"at": 1000.0 - h * 3600, "boundary": "start"} for h in (8, 6, 4, 2, 0)
    ]
    hist.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    # Agora answers, and says something other than the observed 120.
    quota_runway.main_argv(
        ["--snapshot", str(snap), "--history", str(hist), "--cadence-minutes", "180"]
    )
    out = capsys.readouterr().out
    assert "the heartbeat is set to 180 minutes" in out
    assert "measured at about 120" in out
    assert "moved slower inside the sample" in out
    # Both numbers, because the rate in the verdict is not the rate anyone
    # reading quota-history.jsonl by hand would get: 19.0%/day over the
    # sample, scaled by 120/180 onto the schedule now in force.
    assert "12.7%/day above is the measured 19.0%/day scaled onto the 180" in out


def test_an_unmoved_schedule_stays_quiet(tmp_path, capsys):
    reset = dt.datetime.fromtimestamp(1000.0 + 58 * 3600, dt.timezone.utc)
    snap = _snapshot(tmp_path, resets_at=reset.isoformat())
    hist = tmp_path / "hist.jsonl"
    rows = [
        {"at": 1000.0 - 24 * 3600, "seven_day": 69.0},
        {"at": 1000.0, "seven_day": 88.0},
    ]
    rows += [
        {"at": 1000.0 - h * 3600, "boundary": "start"} for h in (8, 6, 4, 2, 0)
    ]
    hist.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    quota_runway.main_argv(
        ["--snapshot", str(snap), "--history", str(hist), "--cadence-minutes", "120"]
    )
    out = capsys.readouterr().out
    assert "the heartbeat is set to" not in out
