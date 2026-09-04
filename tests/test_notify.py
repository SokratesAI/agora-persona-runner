"""The gate in front of the owner's phone: quiet hours, dedupe, urgency."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tools import notify

OSLO = ZoneInfo("Europe/Oslo")


def at(hour, minute=0, day=4):
    return datetime(2026, 9, day, hour, minute, tzinfo=OSLO)


class FakeSend:
    def __init__(self, result=(0, "sent, 12 character(s)")):
        self.result = result
        self.calls = []

    def __call__(self, text, url=None):
        self.calls.append((text, url))
        return self.result


# --- the window itself -------------------------------------------------

@pytest.mark.parametrize("hour", [22, 23, 0, 3, 6])
def test_quiet_hours_covers_the_night_and_wraps_midnight(hour):
    assert notify.in_quiet_hours(at(hour)) is True


@pytest.mark.parametrize("hour", [7, 9, 12, 21])
def test_daytime_is_not_quiet_hours(hour):
    assert notify.in_quiet_hours(at(hour)) is False


def test_the_boundaries_are_where_he_put_them():
    # 21:59 sends, 22:00 does not; 06:59 does not, 07:00 does. Written as
    # minutes rather than hours because an off-by-one here is an hour of
    # his sleep either way.
    assert notify.in_quiet_hours(at(21, 59)) is False
    assert notify.in_quiet_hours(at(22, 0)) is True
    assert notify.in_quiet_hours(at(6, 59)) is True
    assert notify.in_quiet_hours(at(7, 0)) is False


# --- the decision ------------------------------------------------------

def test_routine_message_at_night_is_held():
    send, line = notify.decide("k", False, at(2), {})
    assert send is False
    assert "quiet hours" in line


def test_urgent_message_at_night_is_held_too():
    # His answer on Telegram, 2026-09-04: quiet hours fully respected. The
    # override this module shipped with was my call and he reversed it, so
    # the one class I built the breakthrough for -- a dead server -- waits.
    assert notify.QUIET_HOURS_BREAKTHROUGH is False
    send, line = notify.decide("k", True, at(2), {})
    assert send is False
    assert "quiet hours" in line


def test_the_breakthrough_switch_still_works_when_it_is_on(monkeypatch):
    # He scoped the answer to "for now ... as we are not hosting production
    # code", so this path is off rather than deleted. Tested at both settings
    # so flipping one constant back cannot land on code nothing has run.
    monkeypatch.setattr(notify, "QUIET_HOURS_BREAKTHROUGH", True)
    send, line = notify.decide("k", True, at(2), {})
    assert send is True
    assert "urgent" in line


def test_routine_message_at_night_is_held_even_with_the_breakthrough_on(monkeypatch):
    # The switch governs urgency, never the window itself.
    monkeypatch.setattr(notify, "QUIET_HOURS_BREAKTHROUGH", True)
    assert notify.decide("k", False, at(2), {})[0] is False


def test_routine_message_in_the_daytime_goes_through():
    send, line = notify.decide("k", False, at(10), {})
    assert send is True


def test_the_same_key_inside_the_window_is_held_even_in_the_daytime():
    state = {"k": {"last_sent": at(10).isoformat()}}
    send, line = notify.decide("k", False, at(13), state, dedupe_hours=6)
    assert send is False
    assert "already sent" in line


def test_dedupe_holds_an_urgent_repeat_too():
    # An outage that is still an outage eighteen minutes later must not page
    # again -- urgency decides whether to wake him, not how often.
    state = {"k": {"last_sent": at(2).isoformat()}}
    send, _ = notify.decide("k", True, at(2, 20), state, dedupe_hours=6)
    assert send is False


def test_the_same_key_past_the_window_sends_again():
    state = {"k": {"last_sent": at(2).isoformat()}}
    send, _ = notify.decide("k", False, at(10), state, dedupe_hours=6)
    assert send is True


def test_a_different_key_is_a_different_message():
    state = {"other": {"last_sent": at(10).isoformat()}}
    send, _ = notify.decide("k", False, at(10, 5), state)
    assert send is True


def test_an_unparseable_stamp_does_not_block_the_send():
    state = {"k": {"last_sent": "some time last tuesday"}}
    send, _ = notify.decide("k", False, at(10), state)
    assert send is True


def test_an_unknown_hour_holds_everything():
    # Without a clock quiet hours cannot be ruled out, and nothing breaks
    # through them, so both halves wait.
    assert notify.decide("k", False, None, {})[0] is False
    assert notify.decide("k", True, None, {})[0] is False


def test_an_unknown_hour_still_sends_urgent_with_the_breakthrough_on(monkeypatch):
    monkeypatch.setattr(notify, "QUIET_HOURS_BREAKTHROUGH", True)
    assert notify.decide("k", False, None, {})[0] is False
    assert notify.decide("k", True, None, {})[0] is True


# --- end to end --------------------------------------------------------

def test_notify_sends_and_records_the_key(tmp_path):
    state_path = tmp_path / "state.json"
    send = FakeSend()
    status, line = notify.notify(
        "the box is down", "nas-down", urgent=True,
        state_path=str(state_path), now=at(10), send=send,
    )
    assert status == 0
    assert send.calls and send.calls[0][0] == "the box is down"
    assert json.loads(state_path.read_text())["nas-down"]["last_sent"] == at(10).isoformat()


def test_a_held_message_never_reaches_the_bridge(tmp_path):
    send = FakeSend()
    status, line = notify.notify(
        "routine thing", "alerts", urgent=False,
        state_path=str(tmp_path / "s.json"), now=at(2), send=send,
    )
    assert status == notify.HELD
    assert send.calls == []


def test_a_held_message_does_not_write_state(tmp_path):
    # Otherwise a held message would dedupe the real send that follows it
    # at 07:00, and the alert would be lost rather than delayed.
    state_path = tmp_path / "s.json"
    notify.notify(
        "routine", "alerts", state_path=str(state_path), now=at(2), send=FakeSend()
    )
    assert not state_path.exists()


def test_a_failed_send_does_not_record_the_key(tmp_path):
    state_path = tmp_path / "s.json"
    send = FakeSend((1, "could not reach the Telegram bridge"))
    status, line = notify.notify(
        "x", "k", urgent=True, state_path=str(state_path), now=at(10), send=send,
    )
    assert status == 1
    assert not state_path.exists()


def test_an_empty_message_is_refused_before_any_decision(tmp_path):
    send = FakeSend()
    status, line = notify.notify(
        "   ", "k", urgent=True, state_path=str(tmp_path / "s.json"), now=at(10), send=send,
    )
    assert status == 2
    assert send.calls == []


def test_an_unreadable_state_file_is_an_empty_memory(tmp_path):
    bad = tmp_path / "s.json"
    bad.write_text("{not json")
    assert notify.load_state(str(bad)) == {}


def test_state_round_trips_through_a_real_file(tmp_path):
    path = str(tmp_path / "deep" / "s.json")
    assert notify.save_state(path, {"k": {"last_sent": at(10).isoformat()}}) is None
    assert notify.recently_sent(notify.load_state(path), "k", at(11), 6) is not None


def test_cli_dry_run_sends_nothing_and_reports_the_verdict(tmp_path, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(notify.telegram, "send", lambda *a, **k: called.append(a) or (0, "sent"))
    code = notify.main([
        "--key", "k", "--text", "hello", "--dry-run", "--state", str(tmp_path / "s.json"),
    ])
    assert called == []
    assert code in (0, notify.HELD)
    assert "would send" in capsys.readouterr().out or code == notify.HELD


def test_the_send_client_is_resolved_at_call_time_not_at_import(monkeypatch, tmp_path):
    # A `send=telegram.send` default argument binds once, at import, so this
    # monkeypatch would be invisible and the real bridge would be called from
    # a test run. The assertion is that the patched client is the one used.
    called = []
    monkeypatch.setattr(notify.telegram, "send", lambda text, url=None: called.append(text) or (0, "sent"))
    status, _ = notify.notify(
        "x", "k", urgent=True, state_path=str(tmp_path / "s.json"), now=at(10),
    )
    assert status == 0
    assert called == ["x"]


def test_an_unknown_hour_still_records_and_still_dedupes(tmp_path, monkeypatch):
    # The branch nobody would test: no Oslo clock, urgent, so it sends. If it
    # recorded nothing, the same outage would page every eighteen minutes for
    # ever -- worse than the routine case, and silent. Only reachable with the
    # breakthrough on, which is why it is set here rather than assumed.
    monkeypatch.setattr(notify, "QUIET_HOURS_BREAKTHROUGH", True)
    state_path = tmp_path / "s.json"
    send = FakeSend()
    assert notify.notify("down", "k", urgent=True, state_path=str(state_path),
                         now=None, send=send)[0] == 0
    status, line = notify.notify("down", "k", urgent=True, state_path=str(state_path),
                                 now=None, send=send)
    assert status == notify.HELD
    assert len(send.calls) == 1


def test_no_urgent_message_reaches_the_bridge_at_night(tmp_path):
    # The end-to-end half of his answer: not just a False from `decide`, but
    # nothing handed to the transport, and no dedupe state written -- so the
    # first cycle after 07:00 still sends it if the alert is still firing.
    state_path = tmp_path / "s.json"
    send = FakeSend()
    status, line = notify.notify(
        "the box is down", "nas-down", urgent=True,
        state_path=str(state_path), now=at(3), send=send,
    )
    assert status == notify.HELD
    assert send.calls == []
    assert not state_path.exists()


# --- length: "messages to telegram must be shorter" (Telegram, 2026-09-04) ---

def test_his_own_example_of_enough_is_short_enough():
    example = (
        "Yes - done, and it was the nameapace cap that was holding it, "
        "not an oversight"
    )
    assert notify.over_guideline(example) is None


def test_a_long_message_gets_a_note_that_says_both_numbers():
    note = notify.over_guideline("x" * (notify.GUIDELINE_CHARS + 1))
    assert note is not None
    assert str(notify.GUIDELINE_CHARS + 1) in note
    assert str(notify.GUIDELINE_CHARS) in note


def test_the_guideline_is_measured_on_the_stripped_text():
    padded = "x" * notify.GUIDELINE_CHARS + "\n\n   "
    assert notify.over_guideline(padded) is None


def test_a_long_message_still_reaches_the_bridge(tmp_path):
    # His correction at 17:21 on 2026-09-04. The policy layer holds a message
    # for quiet hours and for dedupe; it does not hold one for being long.
    send = FakeSend()
    status, _ = notify.notify(
        "x" * (notify.GUIDELINE_CHARS + 1),
        key="wall-of-text",
        now=at(12),
        state_path=str(tmp_path / "s.json"),
        send=send,
    )
    assert status == 0
    assert len(send.calls) == 1


def test_length_is_not_a_reason_to_hold_and_quiet_hours_still_is(tmp_path):
    status, line = notify.notify(
        "x" * (notify.GUIDELINE_CHARS + 1),
        key="wall-of-text",
        now=at(3),
        state_path=str(tmp_path / "s.json"),
        send=FakeSend(),
    )
    assert status == notify.HELD
    assert "quiet hours" in line


def test_a_long_message_is_not_refused_in_dry_run_either(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(notify, "oslo_now", lambda: at(12))
    code = notify.main([
        "--key", "wall", "--text", "x" * (notify.GUIDELINE_CHARS + 1),
        "--dry-run", "--state", str(tmp_path / "s.json"),
    ])
    assert code == 0
    assert "would send" in capsys.readouterr().out
