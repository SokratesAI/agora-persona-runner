"""The notes record store, against the same fake CouchDB the board store uses."""

import pytest

from agora_runner import nova_notes_store as store, ticket_docs
from tests.test_board_store import FakeCouch


@pytest.fixture
def couch(monkeypatch):
    fake = FakeCouch()
    monkeypatch.setattr(ticket_docs, "_req", fake)
    return fake


def test_multi_paragraph_text_comes_back_byte_for_byte(couch):
    text = "first line\n  indented continuation\n\nsecond paragraph\n"
    note = store.create_note("edvard", text)
    assert store.read_note(note["_id"])["text"] == text


def test_a_note_id_is_note_colon_hex_and_the_note_starts_live(couch):
    note = store.create_note("nova", "x")
    assert note["_id"].startswith("note:") and len(note["_id"]) == 11
    assert note["archived"] is False and note["type"] == "note"


def test_an_unknown_author_is_refused_before_anything_is_written(couch):
    with pytest.raises(ValueError):
        store.create_note("Edvard (typed by anyone)", "x")
    assert couch.docs == {}


def test_empty_text_is_refused(couch):
    with pytest.raises(ValueError):
        store.create_note("nova", "  \n ")


def test_list_is_newest_first_and_archive_moves_a_note_between_lists(couch, monkeypatch):
    stamps = iter(["2026-09-21T08:00:00Z", "2026-09-21T09:00:00Z", "2026-09-21T10:00:00Z"])
    monkeypatch.setattr(store, "_now", lambda: next(stamps))
    old = store.create_note("edvard", "old")
    new = store.create_note("sokrates", "new")
    assert [n["text"] for n in store.list_notes()] == ["new", "old"]
    store.set_archived(old, True)
    assert [n["text"] for n in store.list_notes()] == ["new"]
    assert [n["text"] for n in store.list_notes(archived=True)] == ["old"]
    assert new["_id"] != old["_id"]


def test_edit_needs_the_rev_it_was_read_at(couch):
    note = store.create_note("edvard", "v1")
    edited = store.edit_note(note, "v2")
    assert store.read_note(note["_id"])["text"] == "v2"
    with pytest.raises(store.NoteConflict):
        store.edit_note(note, "stale write")
    assert store.read_note(note["_id"])["text"] == "v2"
    assert edited["_rev"] != note["_rev"]


def test_comments_come_back_in_written_order_past_nine(couch):
    note = store.create_note("edvard", "n")
    for i in range(11):
        store.add_comment(note["_id"], "nova" if i % 2 else "sokrates", f"c{i}")
    assert [c["text"] for c in store.read_comments(note["_id"])] == [f"c{i}" for i in range(11)]


def test_a_comment_on_a_missing_note_is_refused(couch):
    with pytest.raises(store.StoreError):
        store.add_comment("note:abcdef", "nova", "x")


def test_a_comment_race_takes_the_next_number(couch, monkeypatch):
    note = store.create_note("edvard", "n")
    key = note["_id"][5:]
    # Another writer took 000001 after this one listed the comments.
    real = couch.__call__
    def racing(method, path, body=None, timeout=60):
        if method == "PUT" and path.endswith(f"comment%3A{key}%3A000001") and not couch.docs.get(f"comment:{key}:000001"):
            couch.docs[f"comment:{key}:000001"] = {"_id": f"comment:{key}:000001", "type": "comment", "text": "theirs", "_rev": "1-z"}
        return real(method, path, body, timeout)
    monkeypatch.setattr(ticket_docs, "_req", racing)
    mine = store.add_comment(note["_id"], "nova", "mine")
    assert mine["_id"] == f"comment:{key}:000002"


def test_delete_removes_the_note_and_its_comments_only(couch):
    keep = store.create_note("edvard", "keep")
    store.add_comment(keep["_id"], "nova", "stays")
    gone = store.create_note("edvard", "gone")
    store.add_comment(gone["_id"], "nova", "goes")
    assert store.delete_note(gone) is True
    assert store.read_note(gone["_id"]) is None
    assert store.read_comments(gone["_id"]) == []
    assert [c["text"] for c in store.read_comments(keep["_id"])] == ["stays"]


def test_note_ids_cannot_reach_into_another_key_range(couch):
    for bad in ("board:issue:1", "note:", "note:a:b", None):
        with pytest.raises(ValueError):
            store.read_comments(bad)
