"""The deadline check: quiet before the cutoff, loud and actionable after it."""

from datetime import datetime, timedelta, timezone

from tools import shutdown_due


ROWS = [
    {"id": "aaa", "name": "K3s Sentinel", "enabled": True},
    {"id": "bbb", "name": "Workflow trial (disabled, manual only)",
     "enabled": False},
    {"id": "ccc", "name": "Nova"},
]

BEFORE = shutdown_due.CUTOFF - timedelta(hours=3)
AFTER = shutdown_due.CUTOFF + timedelta(minutes=5)


def test_before_the_cutoff_is_clean_and_says_how_long_is_left():
    report, status = shutdown_due.format_report(BEFORE, ROWS, None)
    assert status == 0
    assert "3.0h until" in report
    # The heartbeats are not judged at all before the deadline, so a row that
    # is on must not appear as a problem.
    assert "STOP THESE NOW" not in report


def test_main_makes_no_network_call_before_the_cutoff(monkeypatch, capsys):
    def boom(*a, **k):  # pragma: no cover - the test fails if it runs
        raise AssertionError("asked Agora before the deadline")
    monkeypatch.setattr(shutdown_due, "_fetch", boom)
    monkeypatch.setattr(shutdown_due, "CUTOFF",
                        datetime.now(timezone.utc) + timedelta(days=1))
    assert shutdown_due.main([]) == 0
    assert "not due" in capsys.readouterr().out


def test_after_the_cutoff_names_every_enabled_heartbeat_and_the_command():
    report, status = shutdown_due.format_report(AFTER, ROWS, None)
    assert status == 2
    # A row with no `enabled` field counts as on; the one explicitly off does not.
    assert "2 heartbeat(s) are still enabled" in report
    assert "aaa" in report and "ccc" in report
    assert "bbb" not in report
    # The action is printed, not described.
    assert 'PATCH' in report and '{"enabled": false}' in report


def test_after_the_cutoff_with_nothing_enabled_is_clean():
    off = [dict(row, enabled=False) for row in ROWS]
    report, status = shutdown_due.format_report(AFTER, off, None)
    assert status == 0
    assert "already off" in report


def test_unreadable_after_the_cutoff_is_not_clean():
    report, status = shutdown_due.format_report(AFTER, [], "connection refused")
    assert status == 1
    assert "CANNOT SEE THE HEARTBEATS" in report
    assert "connection refused" in report


def test_preflight_runs_it():
    from tools import preflight
    assert "shutdown_due" in preflight.CHECKS
    assert "shutdown_due" in preflight.SUBJECT
