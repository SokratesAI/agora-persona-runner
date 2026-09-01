"""Covers the policy in `tools.cadence_control` -- the part that decides
whether to move Nova's own heartbeat, and to what.

The arithmetic that produces the target interval is not tested here: it
lives in `tools.quota_runway` and `tests/test_quota_runway.py` owns it.
What is new in this module is the *policy* -- the floor, the ceiling, the
deadband, the anti-thrash guard, and the snap onto an interval Agora will
actually accept -- so that is what these assert.

Two of these are regression tests against something measured live rather
than reasoned about:

  * `test_snaps_to_an_interval_agora_accepts` -- the first live PATCH asked
    for 37 minutes and came back `400 ... the interval must divide 24h
    evenly`. 37 was the honest target and an illegal schedule.
  * `test_holds_while_a_change_is_still_rolling_through_the_sample` -- the
    run right after that PATCH landed had the heartbeat at 40 and the burn
    rate still earned at 30. Without the guard the controller would have
    read its own change as new evidence and moved again, every 40 minutes,
    forever.
"""

import pytest

from tools import cadence_control as cc


def test_legal_intervals_are_exactly_the_divisors_of_a_day():
    legal = cc.legal_intervals()
    assert legal[0] >= cc.FLOOR_MINUTES and legal[-1] <= cc.CEILING_MINUTES
    assert all(1440 % n == 0 for n in legal)
    # Spot-check the two that bracket the live cadence, so a change to the
    # floor or ceiling that silently drops them fails here.
    assert 30 in legal and 40 in legal
    assert 37 not in legal


def test_snaps_away_from_the_current_interval():
    # Slowing down: 37 is illegal, and snapping down to 36 would leave the
    # loop still going dark. Up.
    assert cc.snap(37, 30) == 40
    # Speeding up: snapping up would leave quota unspent. Down.
    assert cc.snap(37, 60) == 36


def test_moves_when_the_window_is_running_hot():
    action, minutes, _reason = cc.decide(30, 37, spend_minutes=30)
    assert action == "move"
    assert minutes == 40


def test_moves_faster_when_the_window_would_reset_unspent():
    """the owner's ask is to spend all of it, so under-spending is a finding too."""
    action, minutes, _reason = cc.decide(40, 22, spend_minutes=40)
    assert action == "move"
    assert minutes == 20
    assert minutes < 40


def test_holds_inside_the_deadband():
    action, minutes, reason = cc.decide(30, 32, spend_minutes=30)
    assert action == "hold"
    assert minutes == 30
    assert "deadband" in reason


def test_holds_while_a_change_is_still_rolling_through_the_sample():
    """The live state right after the first real PATCH: set to 40, rate earned at 30."""
    action, minutes, reason = cc.decide(40, 37, spend_minutes=30)
    assert action == "hold"
    assert minutes == 40
    assert "rolling through the sample" in reason


def test_the_anti_thrash_guard_outranks_a_move():
    """A stale sample must win even when the target is far from the schedule."""
    action, _minutes, reason = cc.decide(40, 90, spend_minutes=20)
    assert action == "hold"
    assert "rolling through the sample" in reason


def test_clamps_at_the_floor_and_says_so():
    action, minutes, reason = cc.decide(30, 4, spend_minutes=30)
    assert action == "floor"
    assert minutes == cc.FLOOR_MINUTES
    assert "may not spend on its own" in reason


def test_clamps_at_the_ceiling_and_says_so():
    action, minutes, reason = cc.decide(30, 400, spend_minutes=30)
    assert action == "ceiling"
    assert minutes == cc.CEILING_MINUTES
    assert "go dark" in reason


@pytest.mark.parametrize("schedule,minutes,expected", [
    ("every@30m@16:00", 40, "every@40m@16:00"),
    ("every@30m", 40, "every@40m"),
    ("every@2h@06:30", 90, "every@90m@06:30"),
])
def test_rewrite_schedule_keeps_the_anchor(schedule, minutes, expected):
    """Dropping the anchor would re-phase the loop on every adjustment."""
    assert cc.rewrite_schedule(schedule, minutes) == expected
