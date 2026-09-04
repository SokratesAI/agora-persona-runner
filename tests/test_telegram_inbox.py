"""The inbox reader's exit contract, which is what preflight acts on.

The one that matters is the negative: an unreachable bridge and a bridge with
no `/inbox` endpoint must not read as "he has not written". Both would be a
positive result guaranteed in advance -- an empty list either way.
"""

import io
import json
import urllib.error

import pytest

from tools import telegram_inbox


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(payload, status=200):
    seen = []

    def opener(request, timeout=None):
        seen.append((request.get_method(), request.full_url, request.data))
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "boom", {},
                                         io.BytesIO(json.dumps(payload).encode()))
        return FakeResponse(payload, status)

    opener.seen = seen
    return opener


def test_empty_inbox_is_exit_zero():
    opener = opener_returning({"messages": [], "unread": 0, "total": 4, "acked_through": 4})
    code, lines = telegram_inbox.fetch(opener=opener)
    assert code == 0
    assert "nothing waiting" in lines[0]


def test_unread_messages_are_exit_two_and_printed_whole():
    body = {
        "messages": [
            {"id": 7, "at": "2026-09-04T08:00:00Z", "text": "move couchdb tonight"},
            {"id": 9, "at": "2026-09-04T08:02:00Z", "text": "two\nlines"},
        ],
        "unread": 2, "total": 9, "acked_through": 5,
    }
    code, lines = telegram_inbox.fetch(opener=opener_returning(body))
    assert code == 2
    joined = "\n".join(lines)
    assert "2 message(s) from the owner" in lines[0]
    # The ack hint names the newest id, or a cycle acks the wrong watermark.
    assert "--ack 9" in lines[0]
    assert "move couchdb tonight" in joined
    # A multi-line message keeps both of its lines.
    assert "two" in joined and "lines" in joined


def test_unreachable_bridge_is_exit_one_not_exit_zero():
    def opener(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    code, lines = telegram_inbox.fetch(opener=opener)
    assert code == 1
    assert "could not reach" in lines[0]


def test_missing_endpoint_is_exit_one_and_names_the_reason():
    """A ConfigMap older than the endpoint 404s. That is not an empty inbox."""
    code, lines = telegram_inbox.fetch(opener=opener_returning({"error": "not found"}, status=404))
    assert code == 1
    assert "no /inbox endpoint" in lines[0]


def test_a_payload_without_a_messages_list_is_an_instrument_failure():
    code, lines = telegram_inbox.fetch(opener=opener_returning({"unread": 0}))
    assert code == 1
    assert "messages list" in lines[0]


def test_reading_does_not_ack():
    """Preflight reads every cycle; only an explicit --ack may consume."""
    opener = opener_returning({"messages": [{"id": 3, "at": "x", "text": "hi"}],
                               "unread": 1, "total": 3, "acked_through": 2})
    telegram_inbox.fetch(opener=opener)
    assert [method for method, _, _ in opener.seen] == ["GET"]
    assert all("/inbox/ack" not in url for _, url, _ in opener.seen)


def test_all_flag_asks_for_everything_and_is_never_a_finding():
    opener = opener_returning({"messages": [{"id": 1, "at": "x", "text": "hi"}],
                               "unread": 0, "total": 1, "acked_through": 1})
    code, lines = telegram_inbox.fetch(opener=opener, everything=True)
    assert code == 0
    assert opener.seen[0][1].endswith("/inbox?all=1")
    assert "1 message(s) ever" in lines[0]


def test_ack_posts_the_id_and_reports_the_watermark():
    opener = opener_returning({"acked_through": 9})
    code, line = telegram_inbox.ack(9, opener=opener)
    assert code == 0
    method, url, data = opener.seen[0]
    assert method == "POST" and url.endswith("/inbox/ack")
    assert json.loads(data) == {"through": 9}
    assert "#9" in line


def test_a_refused_ack_is_not_a_success():
    code, line = telegram_inbox.ack(9, opener=opener_returning({"error": "nope"}, status=400))
    assert code == 1
    assert "nope" in line


def test_it_is_registered_in_preflight():
    """A reader nothing runs is a channel that still does not exist."""
    from tools import preflight

    assert "telegram_inbox" in preflight.CHECKS
    assert "telegram_inbox" in preflight.SUBJECT
