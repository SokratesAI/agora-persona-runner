"""Unread marks on the conversations list (his capture, ideas.md 2026-08-29).

The rules worth guarding are the two that decide whether the page is usable:
a conversation nobody has ever opened here must not count as unread unless it
has spoken since the store was first written, and stamping a marker must be a
no-op when no new message has arrived -- otherwise the dock's poller writes to
the vault every few seconds forever.
"""

import json

import pytest

from agora_runner import nova_conversation_reads as reads
from agora_runner import nova_conversations


def test_parse_reads_tolerates_rubbish():
    assert reads.parse_reads("") == ("", {})
    assert reads.parse_reads("not json") == ("", {})
    assert reads.parse_reads("[1, 2]") == ("", {})
    assert reads.parse_reads(json.dumps({"since": "S", "seen": "nope"})) == ("S", {})


def test_unread_needs_a_message_newer_than_the_marker():
    seen = {"c1": "2026-08-29T10:00:00Z"}
    assert reads.is_unread("c1", "2026-08-29T11:00:00Z", "", seen) is True
    assert reads.is_unread("c1", "2026-08-29T10:00:00Z", "", seen) is False
    assert reads.is_unread("c1", "2026-08-29T09:00:00Z", "", seen) is False


def test_an_unmarked_conversation_is_read_before_the_store_existed():
    """The 653-row wall of highlights this design exists to avoid."""
    since = "2026-08-29T12:00:00Z"
    assert reads.is_unread("never-opened", "2026-08-28T09:00:00Z", since, {}) is False
    assert reads.is_unread("never-opened", "2026-08-29T13:00:00Z", since, {}) is True
    # No store at all: nothing is unread, rather than everything.
    assert reads.is_unread("never-opened", "2026-08-29T13:00:00Z", "", {}) is False


def test_an_undated_conversation_is_never_unread():
    assert reads.is_unread("c1", "", "2026-01-01T00:00:00Z", {}) is False


def test_mark_seen_writes_nothing_when_nothing_new_arrived(monkeypatch):
    stored = json.dumps({"since": "2026-08-29T12:00:00Z",
                         "seen": {"c1": "2026-08-29T13:00:00Z"}})
    monkeypatch.setattr(reads, "vault_read_path_rev", lambda p: (stored, "3-abc"))

    def refuse(*a, **k):
        pytest.fail("mark_seen wrote to the vault with no new message")

    monkeypatch.setattr(reads, "vault_write_path", refuse)
    assert reads.mark_seen("c1", "2026-08-29T13:00:00Z", "now") is False
    assert reads.mark_seen("c1", "2026-08-29T12:30:00Z", "now") is False
    assert reads.mark_seen("", "2026-08-29T14:00:00Z", "now") is False
    assert reads.mark_seen("c1", "", "now") is False


def test_mark_seen_stores_the_newest_message_and_seeds_since(monkeypatch):
    written = {}
    monkeypatch.setattr(reads, "vault_read_path_rev", lambda p: ("", None))

    def capture(path, content, if_rev=None):
        written["path"] = path
        written["doc"] = json.loads(content)
        return "written"

    monkeypatch.setattr(reads, "vault_write_path", capture)
    assert reads.mark_seen("c1", "2026-08-29T14:00:00Z", "2026-08-29T15:00:00Z") is True
    assert written["path"] == reads.READS_PATH
    assert written["doc"]["seen"] == {"c1": "2026-08-29T14:00:00Z"}
    # `since` is seeded once, from the first write, so everything older than
    # the feature stays quiet.
    assert written["doc"]["since"] == "2026-08-29T15:00:00Z"


def test_since_is_not_overwritten_by_a_later_write(monkeypatch):
    stored = json.dumps({"since": "2026-08-01T00:00:00Z", "seen": {}})
    written = {}
    monkeypatch.setattr(reads, "vault_read_path_rev", lambda p: (stored, "9-x"))
    monkeypatch.setattr(
        reads, "vault_write_path",
        lambda path, content, if_rev=None: (written.update(json.loads(content)), "written")[1])
    assert reads.mark_seen("c2", "2026-08-29T14:00:00Z", "2026-08-29T15:00:00Z") is True
    assert written["since"] == "2026-08-01T00:00:00Z"


def _listing(rows):
    return 200, {"conversations": rows}


def test_unread_rows_sort_above_the_rest(monkeypatch):
    rows = [
        {"id": "old-unread", "name": "old unread", "lastMessageAt": "2026-08-29T10:00:00Z"},
        {"id": "new-read", "name": "new read", "lastMessageAt": "2026-08-29T20:00:00Z"},
        {"id": "new-unread", "name": "new unread", "lastMessageAt": "2026-08-29T19:00:00Z"},
    ]
    monkeypatch.setattr(nova_conversations, "agora_get", lambda p: _listing(rows))
    monkeypatch.setattr(nova_conversations, "_folder_rows", lambda: [])
    monkeypatch.setattr(nova_conversations, "_model_rows", lambda: [])
    monkeypatch.setattr(nova_conversations, "load_reads", lambda: (
        "2026-08-01T00:00:00Z",
        {"new-read": "2026-08-29T20:00:00Z", "old-unread": "2026-08-29T09:00:00Z"}))

    out = nova_conversations.conversations()["conversations"]
    assert [r["id"] for r in out] == ["new-unread", "old-unread", "new-read"]
    assert [r["unread"] for r in out] == [True, True, False]


def test_thread_reports_the_newest_settled_message(monkeypatch):
    detail = {"messages": [
        {"id": "1", "sender": "Edvard", "text": "hi", "ts": "2026-08-29T10:00:00Z"},
        {"id": "2", "sender": "Nova", "text": "there", "ts": "2026-08-29T11:00:00Z"},
    ]}
    monkeypatch.setattr(nova_conversations, "agora_get", lambda p: (200, detail))
    assert nova_conversations.thread("c1")["newestAt"] == "2026-08-29T11:00:00Z"


def test_a_mid_turn_passage_is_not_the_newest(monkeypatch):
    """The reply still being written must not clear the highlight.

    A passage is pushed into the thread the moment it is written, so the
    newest row in a running turn is the steps-only one, which carries
    `partial`. Stamping that as seen would mark the answer read before the
    answer exists.
    """
    detail = {"messages": [
        {"id": "1", "sender": "Edvard", "text": "hi", "ts": "2026-08-29T10:00:00Z"},
        {"id": "2", "sender": "Nova", "text": "assistant_text: half an answer",
         "ts": "2026-08-29T11:00:00Z",
         "activity": {"capability": "assistant_text", "detail": "half an answer"}},
    ]}
    monkeypatch.setattr(nova_conversations, "agora_get", lambda p: (200, detail))
    out = nova_conversations.thread("c1")
    assert [m["partial"] for m in out["messages"]] == [False, True]
    assert out["messages"][1]["steps"] == [
        {"kind": "thought", "text": "half an answer"}]
    assert out["newestAt"] == "2026-08-29T10:00:00Z"


def test_a_thread_with_nothing_settled_reports_no_newest(monkeypatch):
    """A reply still being written must not clear the highlight."""
    monkeypatch.setattr(nova_conversations, "agora_get", lambda p: (200, {"messages": []}))
    assert nova_conversations.thread("c1")["newestAt"] == ""


def test_an_unreadable_marker_store_costs_highlights_not_the_page(monkeypatch):
    """The listing is strict about Agora and soft about markers, on purpose."""
    def explode(path):
        raise RuntimeError("couchdb is not answering")

    monkeypatch.setattr(reads, "vault_read_path_rev", explode)
    assert reads.load_reads() == ("", {})

    rows = [{"id": "c1", "name": "one", "lastMessageAt": "2026-08-29T10:00:00Z"}]
    monkeypatch.setattr(nova_conversations, "agora_get", lambda p: _listing(rows))
    monkeypatch.setattr(nova_conversations, "_folder_rows", lambda: [])
    monkeypatch.setattr(nova_conversations, "_model_rows", lambda: [])
    out = nova_conversations.conversations()["conversations"]
    assert [r["id"] for r in out] == ["c1"]
    assert out[0]["unread"] is False
