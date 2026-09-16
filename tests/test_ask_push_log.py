"""nova-kpi-push-delivered: every ask and nudge writes whether his phone buzzed.

Before this, Agora's answer to a post was the only place that fact existed, and
`push_held` read it once and dropped it -- so two goal threads opened in quiet
hours on 2026-09-15 never buzzed his phone and nothing on record said so.
"""

import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from agora_runner import ask_push_log, needs_input, nudge_ask
from tools import goal_measures

NOW = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)
AUDIBLE = datetime(2026, 9, 16, 5, 30, tzinfo=timezone.utc)   # 07:30 Oslo


def rec(kind, cid, held, hours_ago):
    return ask_push_log.record_line(kind, cid, "m", held,
                                    now=NOW - timedelta(hours=hours_ago))


def share(*lines):
    return ask_push_log.delivery_share(ask_push_log.parse("\n\n".join(lines)), until=NOW)


def _capture(monkeypatch, result="written"):
    written = []

    def fake(path, content, after_marker):
        written.append((path, content, after_marker))
        return result
    monkeypatch.setattr(ask_push_log, "_append", fake)
    return written


def test_an_ask_records_the_push_agora_withheld(monkeypatch):
    written = _capture(monkeypatch)

    def fake(method, path, payload=None):
        if path == "/conversations":
            return 201, {"conversation": {"id": "c1"}}
        if path.endswith("/notify"):
            return 200, {"status": "recorded", "quietHours": True, "message": {"id": "m1"}}
        return 200, {}
    monkeypatch.setattr(needs_input, "agora_internal", fake)
    ok, info = needs_input.ask("Yes or no, should X?", "because", cycle="1690")
    assert ok and info["unrecorded"] is None
    [(path, content, marker)] = written
    assert (path, marker) == (ask_push_log.PATH, ask_push_log.MARKER)
    [row] = ask_push_log.parse(content)
    assert (row["kind"], row["conversationId"], row["messageId"], row["pushed"],
            row["cycle"]) == ("ask", "c1", "m1", False, "1690")
    assert "quiet hours" in row["held"]


def test_a_failed_record_never_fails_the_ask(monkeypatch):
    _capture(monkeypatch, result="FAILED(409 conflict)")
    monkeypatch.setattr(needs_input, "agora_internal", lambda m, p, b=None: (
        (201, {"conversation": {"id": "c1"}}) if p == "/conversations"
        else (200, {"status": "sent", "message": {"id": "m1"}})))
    ok, info = needs_input.ask("Yes or no, should X?", "because")
    assert ok and info["pushed"]
    assert "409" in info["unrecorded"]


def test_a_nudge_records_that_his_phone_buzzed(monkeypatch):
    written = _capture(monkeypatch)
    monkeypatch.setattr(nudge_ask, "agora_internal", lambda m, p, b=None: (
        200, {"status": "sent", "message": {"id": "m2"}}))
    newest = {"sender": "Nova", "ts": "2026-09-16T03:28:00.000Z", "text": "the ask"}
    ok, detail = nudge_ask.nudge("c1", now=AUDIBLE, newest=newest)
    assert ok, detail
    [row] = ask_push_log.parse(written[0][1])
    assert (row["kind"], row["conversationId"], row["pushed"]) == ("nudge", "c1", True)


def test_a_quiet_hours_ask_counts_once_a_later_nudge_went_out():
    value, detail = share(rec("ask", "c1", "quiet hours", 10), rec("nudge", "c1", None, 5))
    assert value == 100.0, detail


def test_an_ask_that_never_reached_him_is_named():
    value, detail = share(rec("ask", "c1", None, 2), rec("ask", "c2", "quiet hours", 3))
    assert value == 50.0
    assert "c2 (quiet hours)" in detail and "c1" not in detail.split("never reached")[1]


def test_a_nudge_before_the_ask_does_not_deliver_it():
    value, _ = share(rec("nudge", "c1", None, 5), rec("ask", "c1", "quiet hours", 2))
    assert value == 0.0


def test_a_push_held_because_he_was_watching_reached_him():
    value, _ = share(rec("ask", "c1", "he had the thread on screen in another app", 1))
    assert value == 100.0


def test_no_ask_in_seven_days_is_no_reading_not_a_share():
    value, detail = share(rec("ask", "c1", "quiet hours", 24 * 8))
    assert value is None and "records begin" in detail


def test_the_measurer_reads_the_log_through_the_vault_tool():
    text = "## Records\n\n" + ask_push_log.record_line("ask", "c1", "m", None)

    def runner(cmd, **kwargs):
        assert cmd[-2:] == ["get", ask_push_log.PATH]
        return subprocess.CompletedProcess(cmd, 0, text, "")
    value, detail = goal_measures.measure_nova_push_delivered(None, None, runner=runner)
    assert value == 100.0, detail
    assert goal_measures.KPI_MEASURERS["nova-kpi-push-delivered"] is goal_measures.measure_nova_push_delivered


def test_an_unreadable_log_is_no_reading():
    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "[not found: x]", "")
    value, _ = goal_measures.measure_nova_push_delivered(None, None, runner=runner)
    assert value is None
