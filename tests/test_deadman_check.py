"""The decision path of the off-box deadman, as a pure function.

Every branch here runs with no network, no ref and no GitHub: an outage
alarm whose own tests need the thing being watched would be untestable
during exactly the failure it exists for.
"""
from datetime import datetime, timedelta, timezone

from tools.deadman_check import alarm_body, assess, assess_channel

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
GRACE = timedelta(minutes=60)


def test_a_fresh_ping_is_ok():
    verdict, reason = assess(NOW - timedelta(minutes=4), NOW, GRACE)
    assert verdict == "OK"
    assert "4 minute(s)" in reason


def test_a_ping_inside_the_grace_is_still_ok():
    # 59 minutes is twelve missed pings and deliberately not an alarm: a
    # node reboot with image pulls eats that without anything being wrong.
    assert assess(NOW - timedelta(minutes=59), NOW, GRACE)[0] == "OK"


def test_a_ping_past_the_grace_is_stale():
    verdict, reason = assess(NOW - timedelta(minutes=61), NOW, GRACE)
    assert verdict == "STALE"
    assert "61 minute(s) ago" in reason
    assert "grace is 60" in reason


def test_the_boundary_belongs_to_ok():
    assert assess(NOW - GRACE, NOW, GRACE)[0] == "OK"


def test_a_missing_ref_is_never_rather_than_stale():
    # These are opposite findings — never deployed vs the box is gone —
    # and merging them sends whoever reads the alarm to the wrong half.
    verdict, reason = assess(None, NOW, GRACE)
    assert verdict == "NEVER"
    assert "never run" in reason


def test_a_ping_from_the_future_does_not_alarm():
    # Clock skew between the cluster and GitHub is small but real, and a
    # negative age must not wrap into a stale verdict.
    assert assess(NOW + timedelta(minutes=2), NOW, GRACE)[0] == "OK"


def test_the_two_verdicts_do_not_share_a_body():
    from tools.deadman_check import alarm_body

    never = alarm_body("NEVER", "x")
    stale = alarm_body("STALE", "x")
    assert "not** evidence that the cluster is down" in never
    assert "github-bot-token" in never
    assert "It has stopped" in stale
    assert "nova-alive-ping -n obsidian" in stale


# --- the alarm channel itself -------------------------------------------
#
# Added Cycle 534. Every test above this line exercises the decision about
# the ping; none of them ask whether the alarm could be delivered, and that
# is exactly the half that was broken. Issues were disabled on this repo the
# whole time slice 1 was "shipped", so `POST /issues` answers 410 -- and the
# only scheduled run so far went green because the ping was fresh and the
# alarm branch never executed.

def test_issues_disabled_is_a_broken_channel():
    ok, reason = assess_channel(False, ["EdvardGB"])
    assert ok is False
    assert "DISABLED" in reason
    assert "410" in reason


def test_unknown_issue_setting_is_broken_not_assumed_fine():
    ok, reason = assess_channel(None, ["EdvardGB"])
    assert ok is False
    assert "could not read" in reason


def test_an_unassignable_addressee_is_a_broken_channel():
    # Issues on, but the alarm would fall back to notifying whoever watches
    # the repo -- and nobody does: subscribers_count was 0 on 2026-08-27.
    ok, reason = assess_channel(True, ["sokrates-ai-user"])
    assert ok is False
    assert "EdvardGB" in reason


def test_issues_on_and_addressee_assignable_is_usable():
    ok, reason = assess_channel(True, ["EdvardGB", "sokrates-ai-user"])
    assert ok is True
    assert "EdvardGB" in reason


def test_the_alarm_body_addresses_him_by_name():
    # A bot-authored issue in an unwatched repo notifies nobody; the @mention
    # is a participating notification, which is on by default everywhere.
    for verdict in ("NEVER", "STALE"):
        assert alarm_body(verdict, "because").startswith("@EdvardGB ")
