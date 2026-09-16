"""The nudge primitive, and the duplicate it exists to stop.

On 2026-09-16 both goal threads carried two re-announcements 17 seconds apart.
`tools.ask_watch --nudge` could not be run from either pod -- the tool is not on
the runner pod and the bridge pod has no token -- so the second one was
hand-rolled through `agora_internal`, with none of the tool's guards. These
tests are about the guard travelling with the primitive.
"""

import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, ".")

from agora_runner import http_util, nudge_ask

AUDIBLE = datetime(2026, 9, 16, 5, 30, tzinfo=timezone.utc)   # 07:30 Oslo
QUIET = datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc)      # 05:00 Oslo


def msg(sender="Nova", ts="2026-09-16T03:28:00.000Z", text="the ask"):
    return {"sender": sender, "ts": ts, "text": text}


def _posts(monkeypatch, status=200, body=None):
    calls = []

    def fake(method, path, payload=None):
        calls.append((method, path, payload))
        return status, body if body is not None else {"status": "sent"}

    monkeypatch.setattr(nudge_ask, "agora_internal", fake)
    return calls


def _thread(monkeypatch, rows, status=200):
    monkeypatch.setattr(
        nudge_ask, "agora_get",
        lambda path: (status, {"messages": rows}))


def test_a_quiet_ask_is_re_announced(monkeypatch):
    calls = _posts(monkeypatch)
    _thread(monkeypatch, [msg(ts="2026-09-16T03:28:00.000Z")])  # 05:28 Oslo
    ok, detail = nudge_ask.nudge("c1", now=AUDIBLE)
    assert ok is True
    assert detail == "his phone buzzed"
    assert calls == [("POST", "/conversations/c1/notify",
                      {"text": nudge_ask.NUDGE_TEXT, "sender": "Nova",
                       "system": False})]


def test_an_already_audible_message_is_not_re_announced_again(monkeypatch):
    """The duplicate. The first nudge landed at 07:02 Oslo, outside quiet
    hours, so its own push went out -- and a second nudge seventeen seconds
    later has nothing to retry. The guard reads the thread's state, so it holds
    whether or not the audible message carries the nudge wording."""
    calls = _posts(monkeypatch)
    _thread(monkeypatch, [msg(ts="2026-09-16T05:02:03.000Z",
                              text="Nudging this one by hand: ...")])
    ok, detail = nudge_ask.nudge("c1", now=AUDIBLE)
    assert ok is False
    assert "outside quiet hours" in detail
    assert "07:02 Oslo" in detail
    assert calls == []


def test_his_answer_is_not_talked_over(monkeypatch):
    calls = _posts(monkeypatch)
    _thread(monkeypatch, [msg(), msg(sender="Edvard", text="2 is fine, redo 1")])
    ok, detail = nudge_ask.nudge("c1", now=AUDIBLE)
    assert ok is False
    assert "Edvard wrote the newest message" in detail
    assert calls == []


def test_nothing_is_posted_during_quiet_hours(monkeypatch):
    calls = _posts(monkeypatch)
    _thread(monkeypatch, [msg()])
    ok, detail = nudge_ask.nudge("c1", now=QUIET)
    assert ok is False
    assert "05:00 Oslo" in detail and "quiet hours" in detail
    assert calls == []


def test_an_unreadable_thread_refuses_rather_than_posting_blind(monkeypatch):
    calls = _posts(monkeypatch)
    _thread(monkeypatch, None, status=502)
    ok, detail = nudge_ask.nudge("c1", now=AUDIBLE)
    assert ok is False
    assert "HTTP 502" in detail
    assert calls == []


def test_an_empty_thread_refuses(monkeypatch):
    calls = _posts(monkeypatch)
    _thread(monkeypatch, [])
    ok, detail = nudge_ask.nudge("c1", now=AUDIBLE)
    assert ok is False
    assert "empty" in detail
    assert calls == []


def test_a_withheld_push_is_not_reported_as_delivered(monkeypatch):
    _posts(monkeypatch, body={"status": "recorded", "quietHours": True})
    _thread(monkeypatch, [msg()])
    ok, detail = nudge_ask.nudge("c1", now=AUDIBLE)
    assert ok is False
    assert "posted but the push was withheld again" in detail


def test_a_401_with_no_token_names_the_runner_pod(monkeypatch):
    _posts(monkeypatch, status=401, body={})
    _thread(monkeypatch, [msg()])
    monkeypatch.setattr(http_util, "AGORA_TOKEN", "")
    ok, detail = nudge_ask.nudge("c1", now=AUDIBLE)
    assert ok is False
    assert "HTTP 401" in detail and "runner pod" in detail


def test_a_caller_that_already_read_the_thread_pays_for_no_second_read(monkeypatch):
    calls = _posts(monkeypatch)

    def refuse_get(path):
        raise AssertionError("read the thread twice")

    monkeypatch.setattr(nudge_ask, "agora_get", refuse_get)
    ok, _detail = nudge_ask.nudge("c1", now=AUDIBLE, newest=msg())
    assert ok is True
    assert len(calls) == 1


def test_a_message_with_no_sender_refuses(monkeypatch):
    calls = _posts(monkeypatch)
    _thread(monkeypatch, [msg(sender="")])
    ok, detail = nudge_ask.nudge("c1", now=AUDIBLE)
    assert ok is False
    assert "names no sender" in detail
    assert calls == []


def test_a_message_with_an_unreadable_timestamp_refuses(monkeypatch):
    calls = _posts(monkeypatch)
    _thread(monkeypatch, [msg(ts="not-a-date")])
    ok, detail = nudge_ask.nudge("c1", now=AUDIBLE)
    assert ok is False
    assert "no readable timestamp" in detail
    assert calls == []


@pytest.mark.parametrize("when,quiet", [
    ("2026-09-16T19:59:00+00:00", False),  # 21:59 Oslo
    ("2026-09-16T20:00:00+00:00", True),   # 22:00 Oslo
    ("2026-09-16T04:59:00+00:00", True),   # 06:59 Oslo
    ("2026-09-16T05:00:00+00:00", False),  # 07:00 Oslo
])
def test_the_window_is_half_open_at_both_ends(when, quiet):
    assert nudge_ask.in_quiet_hours(nudge_ask.parse_ts(when)) is quiet


def test_main_takes_ids_from_the_shell_and_reports_the_worst(monkeypatch, capsys):
    """`main()` with no argv drops every flag the shell passed, which is how a
    new entry point ships unreachable and still exits 0."""
    monkeypatch.setattr(sys, "argv", ["nudge_ask", "c1", "c2"])
    monkeypatch.setattr(nudge_ask, "nudge",
                        lambda cid, **k: (cid == "c1", f"detail for {cid}"))
    code = nudge_ask.main()
    text = capsys.readouterr().out
    assert code == 1
    assert "re-announced — c1 — detail for c1" in text
    assert "NOT re-announced — c2 — detail for c2" in text


def test_ask_watch_uses_this_primitive_rather_than_its_own_copy():
    """One window, one wording, one guard. A second copy in `tools/` is a
    second thing to keep true, and the runner pod cannot read it at all."""
    from tools import ask_watch
    assert ask_watch.NUDGE_TEXT is nudge_ask.NUDGE_TEXT
    assert ask_watch.in_quiet_hours is nudge_ask.in_quiet_hours
