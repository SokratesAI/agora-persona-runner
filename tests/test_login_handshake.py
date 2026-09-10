"""The read side of the login handshake he asked for on 2026-09-10."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools import credential_recovery, login_handshake


NOW = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
PAGED = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)


def _row(number, at, text):
    return {"id": number, "at": at, "text": text}


def _inside():
    """An expiry inside the lead window he is paged in."""
    return NOW + timedelta(hours=credential_recovery.LOGIN_LEAD_HOURS - 24)


def test_a_bare_yes_in_any_of_his_spellings_is_a_go_ahead():
    for text in ("ok", "OK", "Ok!", "okay", "yes", "Yes please", "ja", "ja takk",
                 "go ahead", "send it", "kjør", "greit"):
        assert login_handshake.is_go_ahead(text), text


def test_a_sentence_containing_ok_is_not_a_go_ahead():
    # The safety rule: this answer mints a login link, and he writes long
    # captures on the same channel.
    for text in ("ok so the board is still wrong",
                 "yes I saw the journal entry, fix the pool page",
                 "not ok", "ok?? what happened to marcus", "", None):
        assert not login_handshake.is_go_ahead(text), text


def test_a_reply_older_than_the_page_is_not_an_answer_to_it():
    rows = [_row(1, "2026-09-12T07:30:00Z", "ok")]
    assert login_handshake.go_ahead_since(rows, PAGED) is None


def test_the_newest_yes_after_the_page_is_the_one_that_counts():
    rows = [_row(1, "2026-09-12T08:10:00Z", "ok"),
            _row(2, "2026-09-12T08:40:00Z", "yes"),
            _row(3, "2026-09-12T08:50:00Z", "and the board is still wrong")]
    assert login_handshake.go_ahead_since(rows, PAGED)["id"] == 2


def test_without_a_page_stamp_no_reply_can_be_consent():
    # Every row on this channel is unread by construction, so an "ok" he sent
    # about something else last week would otherwise mint a link.
    rows = [_row(1, "2026-09-12T08:10:00Z", "ok")]
    assert login_handshake.go_ahead_since(rows, None) is None


def test_a_row_with_an_unreadable_timestamp_is_skipped_not_accepted():
    rows = [_row(1, "not a timestamp", "ok")]
    assert login_handshake.go_ahead_since(rows, PAGED) is None


def test_no_link_is_owed_while_the_deadline_is_far_away():
    far = NOW + timedelta(hours=credential_recovery.LOGIN_LEAD_HOURS + 48)
    status, lines = login_handshake.judge(far, NOW, PAGED, [_row(1, "2026-09-12T08:10:00Z", "ok")])
    assert status == 0
    assert "No login is due" in lines[0]


def test_inside_the_window_with_no_page_yet_names_the_sender():
    status, lines = login_handshake.judge(_inside(), NOW, None, [])
    assert status == 0
    assert "credential_recovery --notify" in lines[0]


def test_inside_the_window_with_no_yes_of_his_is_not_a_link():
    rows = [_row(7, "2026-09-12T08:30:00Z", "what does the pool page do again")]
    status, lines = login_handshake.judge(_inside(), NOW, PAGED, rows)
    assert status == 0
    assert "no link is owed" in lines[0]


def test_his_yes_raises_and_prints_the_mint_and_the_ack():
    rows = [_row(42, "2026-09-12T08:30:00Z", "ok")]
    status, lines = login_handshake.judge(_inside(), NOW, PAGED, rows)
    assert status == 2
    body = "\n".join(lines)
    assert "HE SAID GO" in body and "#42" in body
    # The mint and the wait have to be one run -- the code lives minutes.
    assert "claude_login_link start --notify --force" in body
    assert "claude_login_link wait" in body
    # And the ack is named with the right number, or the next cycle answers
    # the same message again.
    assert "telegram_inbox --ack 42" in body


def test_an_unreadable_expiry_is_an_instrument_failure_not_a_clean_answer():
    status, lines = login_handshake.judge(None, NOW, PAGED, [])
    assert status == 1
    assert "NOT JUDGED" in lines[0]


def test_last_paged_reads_the_key_the_pager_actually_writes():
    state = {credential_recovery.LOGIN_NOTIFY_KEY: {"last_sent": "2026-09-12T08:00:00+00:00"}}
    assert login_handshake.last_paged(state) == PAGED
    # A different alert's stamp is not this one's.
    assert login_handshake.last_paged({"some-other-alert": {"last_sent": "2026-09-12T08:00:00+00:00"}}) is None
    assert login_handshake.last_paged({}) is None


def test_a_naive_stamp_is_read_as_utc_rather_than_raising():
    state = {credential_recovery.LOGIN_NOTIFY_KEY: {"last_sent": "2026-09-12T08:00:00"}}
    assert login_handshake.last_paged(state) == PAGED


def test_due_is_false_while_the_deadline_is_far_off():
    # The gate on reaching for the Telegram bridge at all. The page stamp is
    # never cleared, so without this the day after a renewal still looks like a
    # day he might have answered, and a bridge outage becomes a daily exit 1.
    far = NOW + timedelta(hours=credential_recovery.LOGIN_LEAD_HOURS + 1)
    assert not login_handshake.due(far, NOW)
    assert not login_handshake.due(None, NOW)
    assert login_handshake.due(_inside(), NOW)
    # Past the deadline is still due -- the login is dead, not irrelevant.
    assert login_handshake.due(NOW - timedelta(days=2), NOW)
