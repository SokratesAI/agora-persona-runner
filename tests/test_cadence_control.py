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

from tools import ci_minutes
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


# --- the second budget: the Actions-minute allowance ------------------------
# Cycle 977. This controller sets the interval that decides how many pull
# requests this loop opens, and a private-repo pull request bills two whole
# Actions minutes. Until now it read the Claude window only, so it could
# speed the loop up into an allowance that was already projected to overrun.

CI_BLOCK = "86 minute(s)/day projects to 2582 against the 2000-minute allowance"


def test_speed_up_is_held_while_the_actions_allowance_is_over():
    action, minutes, reason = cc.decide(60, 20, ci_block=CI_BLOCK)
    assert action == "hold", (action, reason)
    assert minutes == 60
    assert CI_BLOCK in reason


def test_the_same_speed_up_is_taken_when_the_allowance_is_clear():
    action, minutes, _ = cc.decide(60, 20, ci_block=None)
    assert action == "move"
    assert minutes < 60


def test_slowing_down_is_never_held_for_the_actions_allowance():
    # A longer interval opens fewer pull requests, so it helps both budgets.
    # This one and the deadband test below still pass with the guard deleted
    # outright, and that is written down rather than hidden: what they pin
    # is that the guard is not *over-broad* -- a version that held on any
    # `ci_block` at all, or one placed before the deadband, fails here.
    action, minutes, _ = cc.decide(20, 60, ci_block=CI_BLOCK)
    assert action == "move"
    assert minutes > 20


def test_the_floor_clamp_is_a_speed_up_and_is_held_too():
    # `needed` below the floor asks to run faster than 15 minutes; from 30
    # the clamp itself would still shorten the interval, so it is blocked.
    action, minutes, reason = cc.decide(30, 4, ci_block=CI_BLOCK)
    assert action == "hold", (action, reason)
    assert minutes == 30
    assert CI_BLOCK in reason


def test_the_floor_still_reports_when_already_at_the_floor():
    # At 15 minutes the clamp is not a speed-up, so the allowance cannot
    # block anything and the tool still tells a cycle the window has
    # unspent quota -- but it says what that quota would cost, because the
    # bare sentence reads as an invitation to lower the floor.
    action, minutes, reason = cc.decide(15, 4, ci_block=CI_BLOCK)
    assert action == "floor"
    assert minutes == cc.FLOOR_MINUTES
    assert CI_BLOCK in reason, reason


def test_the_floor_says_nothing_about_ci_when_the_allowance_is_clear():
    _action, _minutes, reason = cc.decide(15, 4, ci_block=None)
    assert "allowance" not in reason, reason


def test_deadband_still_wins_over_the_ci_block():
    # 48 is the legal interval below 45+3, and it is 3 minutes from 48-now,
    # inside that interval's own 4.8-minute band. The allowance never gets
    # asked, because there is no move to block.
    action, _minutes, reason = cc.decide(48, 47, ci_block=CI_BLOCK)
    assert action == "hold"
    assert "deadband" in reason, reason


def test_an_unreadable_allowance_blocks_a_speed_up():
    def gh_that_fails(*_a, **_k):
        raise RuntimeError("gh api: 502")

    blocked, why = ci_minutes.allowance_pressure(gh=gh_that_fails)
    assert blocked is True
    assert "could not be read" in why
