"""The arithmetic behind "when does this loop go dark".

Every case here is built from numbers the test itself states, never from
the module's own constants -- a fixture assembled out of the thing under
test agrees with itself no matter what, which is how three checks passed
in one night on Cycle 253.
"""

import datetime as dt
import json

from tools import quota_runway
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
    monkeypatch.setattr(quota_runway, "live_cadence_minutes", lambda: (120, True))
    quota_runway.main_argv(["--snapshot", str(snap), "--history", str(_history(tmp_path))])
    out = capsys.readouterr().out
    # 43h dark at 120-minute cadence is 21 wake-ups, not the 64 that a
    # hardcoded 40 would give.
    assert "21 heartbeats" in out
    assert "64 heartbeats" not in out


def test_the_cadence_lookup_falls_back_rather_than_raising(monkeypatch):
    import agora_runner.cycle_health as ch

    monkeypatch.setattr(ch, "nova_cadence_minutes", lambda: None)
    assert quota_runway.live_cadence_minutes() == (quota_runway.CADENCE_MINUTES, False)

    def boom():
        raise RuntimeError("agora unreachable")

    monkeypatch.setattr(ch, "nova_cadence_minutes", boom)
    assert quota_runway.live_cadence_minutes() == (quota_runway.CADENCE_MINUTES, False)

    monkeypatch.setattr(ch, "nova_cadence_minutes", lambda: 60)
    assert quota_runway.live_cadence_minutes() == (60, True)


def test_an_assumed_cadence_says_so_and_a_measured_one_does_not(tmp_path, capsys, monkeypatch):
    """The silent fallback was the normal path from the bridge pod, and it
    reported a wake-up count off a stale 40 while the truth was 60."""
    reset = dt.datetime.fromtimestamp(1000.0 + 58 * 3600, dt.timezone.utc)
    snap = _snapshot(tmp_path, resets_at=reset.isoformat())
    argv = ["--snapshot", str(snap), "--history", str(_history(tmp_path))]

    monkeypatch.setattr(quota_runway, "live_cadence_minutes", lambda: (40, False))
    quota_runway.main_argv(argv)
    assert "NOTE: could not reach Agora" in capsys.readouterr().out

    monkeypatch.setattr(quota_runway, "live_cadence_minutes", lambda: (60, True))
    quota_runway.main_argv(argv)
    assert "NOTE: could not reach Agora" not in capsys.readouterr().out
