"""The decision path of the off-box deadman, as a pure function.

Every branch here runs with no network, no ref and no GitHub: an outage
alarm whose own tests need the thing being watched would be untestable
during exactly the failure it exists for.
"""
from datetime import datetime, timedelta, timezone

from tools.deadman_check import assess

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
