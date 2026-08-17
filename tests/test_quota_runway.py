"""The arithmetic behind "when does this loop go dark".

Every case here is built from numbers the test itself states, never from
the module's own constants -- a fixture assembled out of the thing under
test agrees with itself no matter what, which is how three checks passed
in one night on Cycle 253.
"""

from tools.quota_runway import DARK, HEALTHY, TIGHT, burn_rate, runway


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
    # Feed the slowed rate back in and the window must now hold out.
    slowed = 18 * 40 / suggested
    state, _, _, _, _ = runway(12, 58, slowed)
    assert state == HEALTHY


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
