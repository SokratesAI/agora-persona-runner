"""`agora_runner.ticket_docs` -- a ticket survives being a CouchDB document.

Slice 1's test proved a ticket survives the markdown. This one proves it
survives the shape it is stored in: records -> documents -> JSON -> back
to records -> the board file, byte for byte. The JSON hop in the middle is
not decoration -- it is where a tuple becomes a list, and `to_markdown`
unpacks layout blocks, so a round trip that skipped it would pass on a
shape CouchDB never returns.

The fixture is imported from the slice 1 test rather than written again.
That file is the one place the awkward shapes of his real boards are
written down (an escaped `\\|` in a wikilink, both write-up heading
shapes, `## Discarded`, trailing blank lines); a second copy here would
drift the moment either is edited.
"""

import json

import pytest

from agora_runner import ticket_docs, ticket_store

from tests.test_ticket_store import BOARD


PATH = "projects/sokrates/projects/nova/Issues.md"


def _through_json(docs):
    """What CouchDB gives back: the same documents, via JSON."""
    return json.loads(json.dumps(docs))


def test_the_board_file_survives_the_documents():
    records = ticket_store.to_records(BOARD)
    docs = _through_json(ticket_docs.to_documents(PATH, records))
    assert ticket_store.to_markdown(ticket_docs.from_documents(docs)) == BOARD


def test_one_document_per_ticket_plus_one_for_the_board():
    records = ticket_store.to_records(BOARD)
    docs = ticket_docs.to_documents(PATH, records)
    tickets = [doc for doc in docs if doc["type"] == "ticket"]
    boards = [doc for doc in docs if doc["type"] == "board"]
    assert len(tickets) == len(records["tickets"]) > 0
    assert len(boards) == 1
    # The point of the whole slice: a status change is one small document.
    assert {doc["_id"] for doc in tickets} == {
        ticket_docs.ticket_doc_id(PATH, ticket["number"])
        for ticket in records["tickets"]
    }


def test_the_id_is_the_lowercased_board_path():
    # Not a slug kept in a table here. A table would be a second copy of
    # the truth and would go stale the way every other one has.
    assert ticket_docs.ticket_doc_id(PATH, 3) == (
        "ticket:projects/sokrates/projects/nova/issues.md:3")
    assert ticket_docs.layout_doc_id(PATH) == (
        "board:projects/sokrates/projects/nova/issues.md")


def test_two_boards_do_not_collide():
    other = "projects/sokrates/projects/agora/nova/resources/issues.md"
    assert ticket_docs.ticket_doc_id(PATH, 3) != ticket_docs.ticket_doc_id(other, 3)


def test_the_layout_document_is_required_to_render():
    # A read that lost the board document must fail loudly. Rendering the
    # tickets without a layout would silently produce a file missing his
    # frontmatter, his captures and every heading.
    records = ticket_store.to_records(BOARD)
    docs = _through_json(ticket_docs.to_documents(PATH, records))
    without = [doc for doc in docs if doc["type"] != "board"]
    with pytest.raises(KeyError):
        ticket_docs.from_documents(without)


def test_tickets_come_back_in_the_same_order_to_records_hands_them_out():
    records = ticket_store.to_records(BOARD)
    docs = _through_json(ticket_docs.to_documents(PATH, records))
    back = ticket_docs.from_documents(docs)
    assert [t["number"] for t in back["tickets"]] == [
        t["number"] for t in records["tickets"]]


def test_a_ticket_that_left_the_markdown_is_tombstoned(monkeypatch):
    """A write that only ever adds leaves a deleted ticket readable."""
    calls = {}

    def fake_req(method, path, body=None, timeout=60):
        if method == "POST":
            calls["body"] = body
            return 201, [{"ok": True} for _ in body["docs"]]
        raise AssertionError(f"unexpected {method} {path}")

    monkeypatch.setattr(ticket_docs, "_req", fake_req)
    monkeypatch.setattr(ticket_docs, "_existing", lambda path: {
        ticket_docs.ticket_doc_id(PATH, 3): "1-aaa",
        ticket_docs.ticket_doc_id(PATH, 99): "1-gone",
        ticket_docs.layout_doc_id(PATH): "1-bbb",
    })

    summary = ticket_docs.write_board(PATH, ticket_store.to_records(BOARD))

    sent = {doc["_id"]: doc for doc in calls["body"]["docs"]}
    dead = ticket_docs.ticket_doc_id(PATH, 99)
    assert sent[dead] == {"_id": dead, "_rev": "1-gone", "_deleted": True}
    assert summary["deleted"] == 1
    # And an id that IS still produced carries the stored revision, so a
    # second run updates instead of conflicting.
    assert sent[ticket_docs.ticket_doc_id(PATH, 3)]["_rev"] == "1-aaa"
    assert "_rev" not in sent[ticket_docs.ticket_doc_id(PATH, 1)]


def test_a_bulk_write_that_reports_an_error_is_not_reported_as_clean(monkeypatch):
    monkeypatch.setattr(ticket_docs, "_existing", lambda path: {})
    monkeypatch.setattr(
        ticket_docs, "_req",
        lambda method, path, body=None, timeout=60: (
            201, [{"id": "x", "error": "conflict"}]))
    summary = ticket_docs.write_board(PATH, ticket_store.to_records(BOARD))
    assert summary["failures"] == [{"id": "x", "error": "conflict"}]


def test_a_refused_bulk_write_raises(monkeypatch):
    monkeypatch.setattr(ticket_docs, "_existing", lambda path: {})
    monkeypatch.setattr(
        ticket_docs, "_req",
        lambda method, path, body=None, timeout=60: (401, {"error": "unauthorized"}))
    with pytest.raises(RuntimeError):
        ticket_docs.write_board(PATH, ticket_store.to_records(BOARD))


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


def test_read_board_reassembles_what_write_board_sent(monkeypatch):
    """The two halves have to agree, and only a paired test says so."""
    records = ticket_store.to_records(BOARD)
    stored = {doc["_id"]: doc for doc in _through_json(
        ticket_docs.to_documents(PATH, records))}

    def fake_req(method, path, body=None, timeout=60):
        if path.startswith(f"{ticket_docs.TICKET_DB}/_all_docs"):
            rows = [{"id": doc_id, "doc": doc} for doc_id, doc in stored.items()
                    if doc["type"] == "ticket"]
            return 200, {"rows": rows}
        return 200, stored[ticket_docs.layout_doc_id(PATH)]

    monkeypatch.setattr(ticket_docs, "_req", fake_req)
    assert ticket_docs.render_from_couch(PATH) == BOARD
