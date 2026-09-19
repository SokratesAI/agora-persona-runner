import io
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agora_runner import nova_chat_ratings as cr

NOON = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def test_parse_needs_both_ids_and_a_known_rating():
    assert cr.parse({"conversationId": "c", "rating": "up"}) is None
    assert cr.parse({"messageId": "m", "rating": "up"}) is None
    assert cr.parse({"conversationId": "c", "messageId": "m", "rating": "meh"}) is None
    assert cr.parse(["c", "m"]) is None
    assert cr.parse({"conversationId": "c", "messageId": "m", "rating": "down", "text": "x" * 999}) == (
        "c", "m", "down", "x" * cr.MAX_TEXT)
    # null is a real value: it takes the rating back.
    assert cr.parse({"conversationId": "c", "messageId": "m", "rating": None}) == ("c", "m", None, "")


def test_the_newest_tap_wins_and_the_same_tap_again_clears_it():
    rows = cr.fold([], "c", "m1", "up", "an answer", NOON)
    rows = cr.fold(rows, "c", "m2", "up", "another", NOON)
    rows = cr.fold(rows, "c", "m1", "down", "an answer", NOON)
    assert [(r["messageId"], r["rating"]) for r in rows] == [("m2", "up"), ("m1", "down")]
    rows = cr.fold(rows, "c", "m1", None, "", NOON)
    assert [(r["messageId"], r["rating"]) for r in rows] == [("m2", "up")]


def test_the_same_message_id_in_another_conversation_is_its_own_row():
    rows = cr.fold(cr.fold([], "a", "m", "up", "", NOON), "b", "m", "down", "", NOON)
    assert [(r["conversationId"], r["rating"]) for r in rows] == [("a", "up"), ("b", "down")]


def test_record_writes_on_the_revision_it_read_and_keeps_other_rows():
    store = {"text": cr.dumps(cr.fold([], "c", "old", "up", "", NOON)), "rev": "7"}
    writes = []

    def read(path):
        assert path == cr.CHAT_RATINGS_PATH
        return store["text"], store["rev"]

    def write(path, text, if_rev=None):
        writes.append(if_rev)
        store["text"] = text

    cr.record("c", "new", "down", "hm", read=read, write=write, now=NOON)
    rows = json.loads(store["text"])["ratings"]
    assert [(r["messageId"], r["rating"], r["text"]) for r in rows] == [("old", "up", ""), ("new", "down", "hm")]
    assert writes == ["7"]


def test_a_malformed_ledger_raises_instead_of_being_overwritten():
    with pytest.raises(ValueError):
        cr.load('{"opens": []}')
    assert cr.load("") == []


def _post(body, record_error=None):
    from agora_runner import nova_site
    raw = json.dumps(body).encode()
    handler = nova_site.NovaSiteHandler.__new__(nova_site.NovaSiteHandler)
    handler.path = "/api/chat/rate"
    handler.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json"}
    handler.rfile = io.BytesIO(raw)
    handler._send_json = MagicMock()
    with patch.object(nova_site.nova_chat_ratings, "record", side_effect=record_error) as record:
        handler._handle_post()
    return handler._send_json.call_args[0], record


def test_the_route_records_the_rating():
    (status, body), record = _post({"conversationId": "c", "messageId": "m", "rating": "up", "text": "t"})
    assert (status, body) == (200, {"ok": True, "rating": "up"})
    record.assert_called_once_with("c", "m", "up", "t")


def test_the_route_refuses_a_body_that_is_not_a_rating():
    (status, _), record = _post({"conversationId": "c", "rating": "up"})
    assert status == 400
    record.assert_not_called()


def test_a_failed_save_is_reported_not_swallowed():
    (status, body), _ = _post({"conversationId": "c", "messageId": "m", "rating": "down"},
                              record_error=RuntimeError("vault down"))
    assert status == 502 and body["ok"] is False
