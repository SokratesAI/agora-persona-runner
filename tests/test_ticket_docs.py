"""`agora_runner.ticket_docs` -- the connection `board_store` is built on.

The mirror this module used to be is deleted (Cycle 1380); what is left is
the credentials, the database name, `ensure_database` and the four-board
roster `tools.board_put` refuses everything else by. The credential tests
moved here from `tests/test_ticket_drift.py`, which went with the mirror.
"""

import pytest

from agora_runner import ticket_docs


PATH = "projects/sokrates/projects/nova/Issues.md"


def test_ensure_database_treats_already_exists_as_success(monkeypatch):
    monkeypatch.setattr(
        ticket_docs, "_req",
        lambda method, path, body=None, timeout=60: (412, {"error": "file_exists"}))
    assert ticket_docs.ensure_database() == (True, 412)


def test_ensure_database_reports_a_refusal(monkeypatch):
    monkeypatch.setattr(
        ticket_docs, "_req",
        lambda method, path, body=None, timeout=60: (401, {"error": "unauthorized"}))
    ok, detail = ticket_docs.ensure_database()
    assert ok is False and "401" in detail


def test_is_board_is_case_insensitive_the_way_vault_paths_are():
    # `_vault_put_raw` lowercases the id and the stored path, so a mixed
    # case write lands on the same document. PATH itself is spelled
    # `Issues.md` for exactly this reason.
    assert ticket_docs.is_board(PATH)
    assert ticket_docs.is_board(ticket_docs.BOARDS[0].upper())
    assert not ticket_docs.is_board("projects/sokrates/projects/nova/notes.md")
    assert not ticket_docs.is_board(None)


def test_the_store_answers_from_either_pod(monkeypatch):
    """The bridge pod spells CouchDB `CDB_*`; a tool must still answer there."""
    for name in ("COUCHDB_URL", "COUCHDB_USER", "COUCHDB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CDB_BASE", "http://couch:5984")
    monkeypatch.setenv("CDB_USER", "bridge")
    monkeypatch.setenv("CDB_PASS", "secret")
    assert ticket_docs.credentials() == ("http://couch:5984", "bridge", "secret")


def test_the_runner_spelling_wins_where_both_are_set(monkeypatch):
    monkeypatch.setenv("COUCHDB_URL", "http://runner:5984")
    monkeypatch.setenv("COUCHDB_USER", "runner")
    monkeypatch.setenv("COUCHDB_PASSWORD", "runner-pass")
    monkeypatch.setenv("CDB_BASE", "http://bridge:5984")
    monkeypatch.setenv("CDB_USER", "bridge")
    monkeypatch.setenv("CDB_PASS", "bridge-pass")
    assert ticket_docs.credentials() == (
        "http://runner:5984", "runner", "runner-pass")


@pytest.mark.parametrize("gone", [
    "push_markdown", "write_board", "read_board", "read_rows", "read_details",
    "read_head", "row_order", "currency", "stamp_source_rev",
    "render_from_couch", "ensure_views",
])
def test_the_mirror_is_gone(gone):
    """Nothing may write the old ticket documents for nobody to read."""
    assert not hasattr(ticket_docs, gone)
