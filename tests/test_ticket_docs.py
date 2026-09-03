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
import re
import urllib.parse

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
    # Stored contents that do not match what this board now produces, so
    # every id here is genuinely due a write and the skip cannot hide the
    # tombstone this test is about.
    monkeypatch.setattr(ticket_docs, "_existing", lambda path: {
        ticket_docs.ticket_doc_id(PATH, 3): {
            "_id": ticket_docs.ticket_doc_id(PATH, 3), "_rev": "1-aaa",
            "type": "ticket", "board": PATH, "number": 3, "ticket": {"stale": True}},
        ticket_docs.ticket_doc_id(PATH, 99): {
            "_id": ticket_docs.ticket_doc_id(PATH, 99), "_rev": "1-gone",
            "type": "ticket", "board": PATH, "number": 99, "ticket": {}},
        ticket_docs.layout_doc_id(PATH): {
            "_id": ticket_docs.layout_doc_id(PATH), "_rev": "1-bbb",
            "type": "board", "board": PATH, "layout": [], "tickets": 0},
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


def _bulk_recorder(monkeypatch, stored):
    """Stub `_existing` with `stored` and capture what `_bulk_docs` is sent."""
    calls = {}

    def fake_req(method, path, body=None, timeout=60):
        if method == "POST":
            calls["body"] = body
            return 201, [{"ok": True} for _ in body["docs"]]
        raise AssertionError(f"unexpected {method} {path}")

    monkeypatch.setattr(ticket_docs, "_req", fake_req)
    monkeypatch.setattr(ticket_docs, "_existing", lambda path: stored)
    return calls


def test_a_second_write_of_an_unchanged_board_sends_nothing(monkeypatch):
    """The point of the store: writing the same board again costs no revisions.

    Slice 2 sent all 445 documents every time, so keeping the store in
    step with the markdown would have cost the same write amplification as
    the 1.15 MB single document it replaces.
    """
    records = ticket_store.to_records(BOARD)
    stored = {doc["_id"]: dict(doc, _rev="1-held") for doc in _through_json(
        ticket_docs.to_documents(PATH, records))}
    calls = _bulk_recorder(monkeypatch, stored)

    summary = ticket_docs.write_board(PATH, records)

    assert summary == {"written": 0, "deleted": 0,
                       "unchanged": len(stored), "failures": []}
    # Not "it sent an empty list" -- it made no request at all.
    assert "body" not in calls


def test_changing_one_ticket_writes_one_document(monkeypatch):
    records = ticket_store.to_records(BOARD)
    stored = {doc["_id"]: dict(doc, _rev="1-held") for doc in _through_json(
        ticket_docs.to_documents(PATH, records))}
    moved = records["tickets"][0]
    stale = ticket_docs.ticket_doc_id(PATH, moved["number"])
    stored[stale] = dict(stored[stale], ticket=dict(moved, status="something else"))
    calls = _bulk_recorder(monkeypatch, stored)

    summary = ticket_docs.write_board(PATH, records)

    assert summary["written"] == 1
    assert summary["unchanged"] == len(stored) - 1
    sent = calls["body"]["docs"]
    assert [doc["_id"] for doc in sent] == [stale]
    # It carries the stored revision, so the update does not conflict.
    assert sent[0]["_rev"] == "1-held"


def test_a_ticket_missing_from_the_store_is_written(monkeypatch):
    """An unchanged-skip must not swallow a document that is not there."""
    records = ticket_store.to_records(BOARD)
    stored = {doc["_id"]: dict(doc, _rev="1-held") for doc in _through_json(
        ticket_docs.to_documents(PATH, records))}
    absent = ticket_docs.ticket_doc_id(PATH, records["tickets"][0]["number"])
    del stored[absent]
    calls = _bulk_recorder(monkeypatch, stored)

    summary = ticket_docs.write_board(PATH, records)

    assert summary["written"] == 1
    sent = calls["body"]["docs"]
    assert [doc["_id"] for doc in sent] == [absent]
    assert "_rev" not in sent[0]


def test_push_markdown_ignores_a_path_that_is_not_a_board(monkeypatch):
    """Every other vault write must reach this store's network layer never."""
    monkeypatch.setattr(ticket_docs, "_req", _refuse)
    assert ticket_docs.push_markdown(
        "projects/sokrates/projects/agora/nova/journal/900-cycle-900.md",
        BOARD) is None


def _refuse(method, path, body=None, timeout=60):
    raise AssertionError(f"a non-board write reached CouchDB: {method} {path}")


def test_push_markdown_writes_a_board_it_recognises(monkeypatch):
    seen = {}
    monkeypatch.setattr(ticket_docs, "ensure_database", lambda: (True, 201))
    monkeypatch.setattr(ticket_docs, "ensure_views", lambda: "unchanged")

    def fake_write(path, records):
        seen["call"] = (path, records)
        return {"written": 1}

    monkeypatch.setattr(ticket_docs, "write_board", fake_write)

    board = ticket_docs.BOARDS[0]
    assert ticket_docs.push_markdown(board, BOARD) == {"written": 1}
    assert seen["call"][0] == board
    assert seen["call"][1] == ticket_store.to_records(BOARD)


def test_is_board_is_case_insensitive_the_way_vault_paths_are():
    # `_vault_put_raw` lowercases the id and the stored path, so a mixed
    # case write lands on the same document and has to reach the same
    # tickets. PATH itself is spelled `Issues.md` for exactly this reason.
    assert ticket_docs.is_board(PATH)
    assert ticket_docs.is_board(ticket_docs.BOARDS[0].upper())
    assert not ticket_docs.is_board("projects/sokrates/projects/nova/notes.md")
    assert not ticket_docs.is_board(None)


# --- the row index: a board list without the write-ups ---


def _row_index_docs(records):
    """`{doc id: document}` the way the store holds them, via JSON."""
    return {doc["_id"]: doc
            for doc in _through_json(ticket_docs.to_documents(PATH, records))}


def test_every_row_field_is_actually_on_a_ticket():
    """The drift guard the view itself cannot give.

    A CouchDB map emitting `t.somethingElse` yields `undefined`, which
    comes back as a row with a missing key rather than as an error. So the
    day `ticket_store` renames a field, the view would quietly serve nulls
    and every test that only reads the view would still pass. This asserts
    against the real fixture instead.
    """
    records = ticket_store.to_records(BOARD)
    assert records["tickets"], "the fixture must carry tickets for this to mean anything"
    for ticket in records["tickets"]:
        missing = [field for field in ticket_docs.ROW_FIELDS if field not in ticket]
        assert not missing, f"ticket {ticket['number']} carries no {missing}"


def test_the_map_function_is_generated_from_the_field_list():
    js = ticket_docs._rows_map_js()
    for field in ticket_docs.ROW_FIELDS:
        assert f"{field}: t.{field}" in js
    # And nothing else: a field emitted but not declared is a copy of the
    # truth this generation exists to prevent.
    assert len(re.findall(r"\w+: t\.\w+", js)) == len(ticket_docs.ROW_FIELDS)


def test_a_row_carries_the_list_fields_and_none_of_the_write_up():
    """What the view emits, applied in Python to the same documents.

    The whole point of the index is that a list read does not ship the
    prose, so the assertion that matters is the *absence* of `details`.
    """
    records = ticket_store.to_records(BOARD)
    for doc in _row_index_docs(records).values():
        if doc.get("type") != "ticket":
            continue
        row = {field: doc["ticket"][field] for field in ticket_docs.ROW_FIELDS}
        assert set(row) == set(ticket_docs.ROW_FIELDS)
        assert "details" not in row and "cells" not in row
        assert row["number"] == doc["number"]


def test_read_rows_asks_for_one_board_and_orders_it_newest_first(monkeypatch):
    seen = {}

    def fake_req(method, path, body=None, timeout=60):
        seen["method"], seen["path"] = method, path
        return 200, {"rows": [
            {"value": {"number": 4, "title": "older"}},
            {"value": {"number": 17, "title": "newer"}},
        ]}

    monkeypatch.setattr(ticket_docs, "_req", fake_req)
    rows = ticket_docs.read_rows(PATH)
    assert [row["number"] for row in rows] == [17, 4]
    assert seen["method"] == "GET"
    # A key range over one board, not a scan of every board in the store.
    assert "_design/rows/_view/by_board" in urllib.parse.unquote(seen["path"])
    query = urllib.parse.parse_qs(seen["path"].split("?", 1)[1])
    assert json.loads(query["startkey"][0]) == [PATH]
    assert json.loads(query["endkey"][0])[0] == PATH


def test_read_rows_raises_rather_than_reporting_an_empty_board(monkeypatch):
    monkeypatch.setattr(ticket_docs, "_req",
                        lambda *a, **k: (500, {"error": "boom"}))
    with pytest.raises(RuntimeError):
        ticket_docs.read_rows(PATH)


def test_an_unchanged_design_document_is_not_rewritten(monkeypatch):
    """Rewriting it invalidates the index, so this is the load-bearing half.

    `push_markdown` calls `ensure_views` on every board write. If that PUT
    fired each time, a one-ticket status change would rebuild the whole
    index -- the write amplification the store exists to end, moved one
    layer down.
    """
    held = dict(ticket_docs.rows_design_document(), _rev="7-abc")
    calls = []

    def fake_req(method, path, body=None, timeout=60):
        calls.append(method)
        return (200, held) if method == "GET" else (201, {"ok": True})

    monkeypatch.setattr(ticket_docs, "_req", fake_req)
    assert ticket_docs.ensure_views() == "unchanged"
    assert calls == ["GET"]


def test_a_changed_map_function_is_written_with_the_held_revision(monkeypatch):
    stale = {"_id": ticket_docs.ROWS_DDOC_ID, "_rev": "7-abc",
             "views": {ticket_docs.ROWS_VIEW: {"map": "function (doc) {}"}}}
    sent = {}

    def fake_req(method, path, body=None, timeout=60):
        if method == "GET":
            return 200, stale
        sent["body"] = body
        return 201, {"ok": True}

    monkeypatch.setattr(ticket_docs, "_req", fake_req)
    assert ticket_docs.ensure_views() == "written"
    assert sent["body"]["_rev"] == "7-abc"
    assert sent["body"]["views"][ticket_docs.ROWS_VIEW]["map"] == ticket_docs._rows_map_js()


def test_a_missing_design_document_is_created_without_a_revision(monkeypatch):
    sent = {}

    def fake_req(method, path, body=None, timeout=60):
        if method == "GET":
            return 404, {"error": "not_found"}
        sent["body"] = body
        return 201, {"ok": True}

    monkeypatch.setattr(ticket_docs, "_req", fake_req)
    assert ticket_docs.ensure_views() == "written"
    assert "_rev" not in sent["body"]


def test_a_board_write_builds_the_view_beside_the_tickets(monkeypatch):
    """A store with documents in it always has the index over them."""
    order = []
    monkeypatch.setattr(ticket_docs, "ensure_database",
                        lambda: order.append("db") or (True, 201))
    monkeypatch.setattr(ticket_docs, "ensure_views",
                        lambda: order.append("views") or "unchanged")
    monkeypatch.setattr(ticket_docs, "write_board",
                        lambda path, records: order.append("write") or {"written": 1})
    ticket_docs.push_markdown(ticket_docs.BOARDS[0], BOARD)
    assert order == ["db", "views", "write"]
    # And a write that is not a board still touches nothing at all.
    order.clear()
    assert ticket_docs.push_markdown("projects/somewhere/else.md", BOARD) is None
    assert order == []
