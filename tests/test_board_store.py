"""`agora_runner.board_store` -- board records survive CouchDB and come back in order.

The store is the fifth #203 primitive and the first one that talks to a
database, so the fixture here is a fake CouchDB rather than a fake
`board_store`: it implements the three requests the module actually makes
(`_all_docs` over an id range, a single-document `GET`, `_bulk_docs`) and
returns ids in the lexical order CouchDB returns them in. A fake that
handed back documents already sorted would make every ordering test here
pass against a module that did no sorting at all.

Two of these cases cannot be built from the live boards, which is why they
are here. Every live row today carries a rank, so an unranked row -- a
`## Done` row, which `board_document` deliberately leaves the field off --
only exists in this file; and the live numbers happen not to expose the
`board:issue:100 < board:issue:2` lexical trap on the boards' current
sizes.
"""

import json
import urllib.parse

import pytest

from agora_runner import board_document, board_store, entity_id, ticket_docs


class FakeCouch:
    """Just enough CouchDB: an id-keyed dict behind the three real requests."""

    def __init__(self, docs=()):
        self.docs = {doc["_id"]: dict(doc, _rev="1-a") for doc in docs}
        self.bulk_calls = []
        self.revs = 1

    @staticmethod
    def _wire(doc):
        """A round trip through JSON, because a real request is one.

        Without it the fake hands back the caller's own nested dicts, so a
        test that mutates a registry it already wrote silently mutates the
        stored copy as well -- and a conditional write then compares a
        document against itself and finds it unchanged.
        """
        return json.loads(json.dumps(doc))

    def __call__(self, method, path, body=None, timeout=60):
        if method == "POST" and path.endswith("_bulk_docs"):
            self.bulk_calls.append(body["docs"])
            answer = []
            for doc in body["docs"]:
                if doc.get("_deleted"):
                    self.docs.pop(doc["_id"], None)
                else:
                    self.docs[doc["_id"]] = self._wire(dict(doc, _rev="2-b"))
                answer.append({"ok": True, "id": doc["_id"]})
            return 200, answer
        if method == "GET" and "_all_docs?" in path:
            query = urllib.parse.parse_qs(path.split("?", 1)[1])
            start = json.loads(query["startkey"][0])
            end = json.loads(query["endkey"][0])
            rows = [{"id": doc_id, "doc": self._wire(doc)}
                    # CouchDB answers in lexical id order, not numeric.
                    for doc_id, doc in sorted(self.docs.items())
                    if start <= doc_id <= end]
            return 200, {"rows": rows}
        if method == "PUT":
            doc_id = urllib.parse.unquote(path.split("/", 1)[1])
            held = self.docs.get(doc_id)
            # CouchDB's own rule, and the registry tests turn on it: a PUT
            # must carry the stored revision, and a mismatch is a 409 rather
            # than a write.
            if (held or {}).get("_rev") != body.get("_rev"):
                return 409, {"error": "conflict"}
            self.revs += 1
            stored = self._wire(dict(body, _rev=f"{self.revs}-r"))
            self.docs[doc_id] = stored
            return 201, {"ok": True, "id": doc_id, "rev": stored["_rev"]}
        if method == "GET":
            doc_id = urllib.parse.unquote(path.split("/", 1)[1])
            if doc_id in self.docs:
                return 200, self._wire(self.docs[doc_id])
            return 404, {"error": "not_found"}
        raise AssertionError(f"unexpected request: {method} {path}")


def _row(board, number, **fields):
    doc = {"_id": board_document.document_id(board, number),
           "type": board_document.DOCUMENT_TYPE,
           "board": board, "number": number, "done": False}
    doc.update(fields)
    return doc


@pytest.fixture
def couch(monkeypatch):
    fake = FakeCouch()
    monkeypatch.setattr(ticket_docs, "_req", fake)
    return fake


def test_reads_come_back_in_rank_order_not_the_order_couch_returns():
    docs = [_row("issue", 2, rank="z"), _row("issue", 100, rank="a")]
    assert [doc["number"] for doc in board_store.in_order(docs)] == [100, 2]


def test_an_unranked_row_sorts_last_not_first():
    """The empty-string default would put every `## Done` row at the top."""
    ranked = _row("issue", 7, rank="V")
    unranked = _row("issue", 3, done=True)
    assert board_store.in_order([unranked, ranked]) == [ranked, unranked]


def test_unranked_rows_are_ordered_among_themselves_by_number():
    rows = [_row("issue", 30, done=True), _row("issue", 4, done=True)]
    assert [doc["number"] for doc in board_store.in_order(rows)] == [4, 30]


def test_read_rows_sorts_what_the_database_hands_back(couch):
    couch.docs = {doc["_id"]: doc for doc in
                  (_row("issue", 2, rank="z"), _row("issue", 100, rank="a"),
                   _row("issue", 9, done=True))}
    assert [doc["number"] for doc in board_store.read_rows("issue")] == [100, 2, 9]


def test_one_board_never_reads_the_other(couch):
    couch.docs = {doc["_id"]: doc for doc in
                  (_row("issue", 1, rank="a"), _row("idea", 1, rank="a"))}
    assert [doc["board"] for doc in board_store.read_rows("idea")] == ["idea"]


def test_read_row_answers_none_for_a_row_that_is_not_stored(couch):
    assert board_store.read_row("issue", 41) is None
    couch.docs = {doc["_id"]: doc for doc in (_row("issue", 41, rank="a"),)}
    assert board_store.read_row("issue", 41)["number"] == 41


def test_an_unchanged_document_is_not_written_again(couch):
    doc = _row("issue", 41, rank="a", title="a row")
    board_store.write_rows("issue", [doc])
    summary = board_store.write_rows("issue", [doc])
    assert summary["written"] == 0 and summary["unchanged"] == 1
    assert len(couch.bulk_calls) == 1


def test_a_changed_document_is_written_with_the_stored_revision(couch):
    doc = _row("issue", 41, rank="a", title="a row")
    board_store.write_rows("issue", [doc])
    board_store.write_rows("issue", [dict(doc, title="renamed")])
    assert couch.bulk_calls[-1][0]["_rev"] == "2-b"
    assert couch.docs[doc["_id"]]["title"] == "renamed"


def test_a_row_that_left_the_board_is_tombstoned(couch):
    kept, gone = _row("issue", 1, rank="a"), _row("issue", 2, rank="b")
    board_store.write_rows("issue", [kept, gone])
    summary = board_store.write_rows("issue", [kept])
    assert summary["deleted"] == 1
    assert [doc["number"] for doc in board_store.read_rows("issue")] == [1]


def test_prune_false_leaves_a_row_the_caller_did_not_send(couch):
    kept, other = _row("issue", 1, rank="a"), _row("issue", 2, rank="b")
    board_store.write_rows("issue", [kept, other])
    summary = board_store.write_rows("issue", [dict(kept, title="moved")], prune=False)
    assert summary["deleted"] == 0
    assert [doc["number"] for doc in board_store.read_rows("issue")] == [1, 2]


def test_a_write_never_prunes_the_other_board(couch):
    board_store.write_rows("idea", [_row("idea", 1, rank="a")])
    board_store.write_rows("issue", [_row("issue", 1, rank="a")])
    assert len(board_store.read_rows("idea")) == 1


def test_a_document_from_the_wrong_board_is_refused(couch):
    with pytest.raises(board_document.DocumentError):
        board_store.write_rows("idea", [_row("issue", 1, rank="a")])
    assert couch.bulk_calls == []


def test_a_document_whose_id_disagrees_with_its_fields_is_refused(couch):
    bad = dict(_row("issue", 1, rank="a"), _id="board:issue:2")
    with pytest.raises(board_document.DocumentError):
        board_store.write_rows("issue", [bad])
    assert couch.bulk_calls == []


def test_an_unknown_board_is_refused_on_both_paths(couch):
    with pytest.raises(board_document.DocumentError):
        board_store.read_rows("bug")
    with pytest.raises(board_document.DocumentError):
        board_store.write_rows("bug", [])


def test_a_refused_read_raises_rather_than_reading_as_empty(monkeypatch):
    monkeypatch.setattr(ticket_docs, "_req",
                        lambda *a, **k: (500, {"error": "internal"}))
    with pytest.raises(board_store.StoreError):
        board_store.read_rows("issue")


def test_a_refused_write_raises(monkeypatch):
    monkeypatch.setattr(ticket_docs, "_req", FakeCouch())
    monkeypatch.setattr(board_store, "stored_documents", lambda board: {})
    monkeypatch.setattr(ticket_docs, "_req",
                        lambda *a, **k: (409, {"error": "conflict"}))
    with pytest.raises(board_store.StoreError):
        board_store.write_rows("issue", [_row("issue", 1, rank="a")])


def test_a_bulk_write_that_reports_an_error_is_not_reported_as_clean(monkeypatch):
    monkeypatch.setattr(board_store, "stored_documents", lambda board: {})
    monkeypatch.setattr(
        ticket_docs, "_req",
        lambda *a, **k: (200, [{"id": "board:issue:1", "error": "conflict"}]))
    summary = board_store.write_rows("issue", [_row("issue", 1, rank="a")])
    assert summary["failures"]


def test_a_round_trip_through_json_keeps_the_order(couch):
    """CouchDB answers JSON, so the sort must not lean on a Python type."""
    docs = [_row("issue", 3, rank="b"), _row("issue", 12, rank="a"),
            _row("issue", 5, done=True)]
    board_store.write_rows("issue", json.loads(json.dumps(docs)))
    assert [doc["number"] for doc in board_store.read_rows("issue")] == [12, 3, 5]


# --- the registry document -------------------------------------------------
#
# `entity_id` mints ids and touches no database; `board_store` is the only
# layer that does. These cases are about the join between them, and two of
# them cannot be reached from the live boards at all: an absent registry
# (there is one now) and a lost conflict (one cycle at a time never sees it).


def test_absent_registry_reads_as_an_empty_one(couch):
    registry = board_store.read_registry()

    assert registry["projects"] == {}
    assert registry["milestones"] == {}
    assert registry["_id"] == board_store.REGISTRY_ID
    assert "_rev" not in registry


def test_the_document_read_back_is_a_registry_entity_id_can_mint_into(couch):
    registry = board_store.read_registry()
    pid = entity_id.ensure_project(registry, "Nova")
    entity_id.ensure_milestone(registry, pid, "Board records")
    board_store.write_registry(registry)

    stored = board_store.read_registry()

    assert entity_id.resolve_project(stored, "nova") == pid
    assert entity_id.resolve_milestone(stored, pid, "Board Records") is not None


def test_the_registry_is_not_a_row_of_any_board(couch):
    registry = board_store.read_registry()
    entity_id.ensure_project(registry, "Nova")
    board_store.write_registry(registry)
    couch.docs.update({doc["_id"]: doc for doc in
                       [dict(_row("issue", 7), _rev="1-a")]})

    for board in board_document.BOARDS:
        assert board_store.REGISTRY_ID not in board_store.stored_documents(board)
    assert [doc["number"] for doc in board_store.read_rows("issue")] == [7]


def test_an_unchanged_registry_is_not_written_again(couch):
    registry = board_store.read_registry()
    entity_id.ensure_project(registry, "Nova")
    written = board_store.write_registry(registry)

    again = board_store.write_registry(dict(written))

    assert again["_rev"] == written["_rev"]


def test_a_write_against_a_stale_revision_is_refused(couch):
    first = board_store.read_registry()
    entity_id.ensure_project(first, "Nova")
    board_store.write_registry(first)

    stale = dict(first)
    entity_id.ensure_project(stale, "Marcus")
    with pytest.raises(board_store.RegistryConflict):
        board_store.write_registry(stale)

    # And the winner's ids are still there, unchanged.
    assert entity_id.resolve_project(board_store.read_registry(), "Marcus") is None


def test_a_first_write_over_an_existing_registry_is_refused(couch):
    board_store.write_registry(entity_id.new_registry())

    with pytest.raises(board_store.RegistryConflict):
        board_store.write_registry(entity_id.new_registry() | {"projects": {"prj_a": {}}})


@pytest.mark.parametrize("bad", [
    {},
    {"projects": {}},
    {"projects": {}, "milestones": []},
    "projects",
])
def test_a_thing_that_is_not_a_registry_never_reaches_couchdb(couch, bad):
    keep = board_store.read_registry()
    entity_id.ensure_project(keep, "Nova")
    board_store.write_registry(keep)
    before = dict(couch.docs[board_store.REGISTRY_ID])

    with pytest.raises(board_document.DocumentError):
        board_store.write_registry(bad)

    assert couch.docs[board_store.REGISTRY_ID] == before
