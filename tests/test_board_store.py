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
        # Every request, so a test can assert what a write *did not* fetch.
        # `write_row` exists to write one document without listing the
        # board, and nothing in its return value shows that.
        self.calls = []
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
        self.calls.append((method, path))
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
        if method == "DELETE":
            head, _, query = path.partition("?")
            doc_id = urllib.parse.unquote(head.split("/", 1)[1])
            held = self.docs.get(doc_id)
            if held is None:
                return 404, {"error": "not_found"}
            # CouchDB's own rule again, and the reason this branch carries
            # it rather than just popping: a delete that ignored `?rev=`
            # would let a caller remove a document somebody else had since
            # rewritten, and no test could tell.
            sent = urllib.parse.parse_qs(query).get("rev", [None])[0]
            if sent != held.get("_rev"):
                return 409, {"error": "conflict"}
            self.docs.pop(doc_id)
            return 200, {"ok": True, "id": doc_id}
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
    {"projects": {}, "milestones": {}, "captures": []},
    {"projects": {}, "milestones": {}, "captures": None},
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


def test_a_registry_stored_before_captures_existed_is_still_writable(couch):
    """The asymmetry in `_check_registry`, and the reason for it.

    Every registry written before `entity_id.mint_capture` existed carries
    `projects` and `milestones` and no `captures`. Requiring the third map
    the way the first two are required would refuse to write the one
    document every `projectId` on every row points at, and the recovery is
    a hand-edit of the live database.
    """
    couch.docs[board_store.REGISTRY_ID] = {
        "_id": board_store.REGISTRY_ID,
        "_rev": "1-old",
        "type": board_store.REGISTRY_TYPE,
        "projects": {"prj_nova": {"name": "Nova", "key": "nova", "aliases": []}},
        "milestones": {},
    }
    held = board_store.read_registry()
    assert "captures" not in held

    # The write that must not be refused, and it is an ordinary one: a
    # cycle renaming a project touches no capture and so never mints the
    # map into existence. Minting first would put a `captures` back on the
    # document and test nothing -- the mutation `if not isinstance(
    # registry.get("captures"), dict)` survives that version.
    entity_id.rename_project(held, "prj_nova", "Aurora")
    stored = board_store.write_registry(held)
    assert "captures" not in stored
    assert stored["projects"]["prj_nova"]["name"] == "Aurora"

    assert entity_id.mint_capture(stored, "issue") == "cap_1"
    stored = board_store.write_registry(stored)
    assert stored["captures"] == {"issue": 1}
    assert stored["projects"]["prj_nova"]["name"] == "Aurora"


def test_write_row_writes_one_document_without_listing_the_board(couch):
    """The whole point of it beside `write_rows`, and the return value hides it.

    `write_rows(board, [doc], prune=False)` writes the same document and
    lists every row of the board with `include_docs=true` first, which on
    the live issues board drags back every `# Details` write-up to find one
    revision. Asserted on the requests made rather than on the result,
    because both calls produce the same stored document.
    """
    couch.docs = {doc["_id"]: doc for doc in (_row("issue", 1, rank="a"),
                                              _row("issue", 2, rank="b"))}
    couch.calls.clear()
    written = board_store.write_row(_row("issue", 3, rank="c"))
    assert written["number"] == 3
    assert couch.docs[board_document.document_id("issue", 3)]["rank"] == "c"
    assert not [path for method, path in couch.calls if "_all_docs" in path]


def test_an_update_must_carry_the_revision_it_was_read_at(couch):
    """The clobber this refuses: two cycles editing two cells of one row.

    `board_document.to_document` mints a document with no `_rev`, so a
    caller that read a row, changed a cell and re-minted it has one --
    and writing it against the *stored* revision would succeed whatever
    happened in between.
    """
    couch.docs = {doc["_id"]: doc for doc in (_row("issue", 5, rank="a"),)}
    with pytest.raises(board_document.DocumentError):
        board_store.write_row(_row("issue", 5, rank="z"))
    assert couch.docs[board_document.document_id("issue", 5)]["rank"] == "a"


def test_a_row_that_is_not_stored_needs_no_revision(couch):
    """The same missing `_rev` is a create, not a clobber -- nothing to lose."""
    stored = board_store.write_row(_row("idea", 9, rank="m"))
    assert stored["_rev"] == couch.docs[board_document.document_id("idea", 9)]["_rev"]


def test_an_unchanged_row_costs_no_revision(couch):
    """A re-run must be free, and it must not need a `_rev` to be free."""
    board_store.write_row(_row("issue", 7, rank="a"))
    before = couch.docs[board_document.document_id("issue", 7)]["_rev"]
    again = board_store.write_row(_row("issue", 7, rank="a"))
    assert again["_rev"] == before
    assert couch.docs[board_document.document_id("issue", 7)]["_rev"] == before


def test_a_lost_revision_raises_rather_than_overwriting_the_winner(couch):
    """A 409 is the other cycle's write, so this one's body is stale."""
    stale = board_store.write_row(_row("issue", 4, rank="a"))
    board_store.write_row(dict(stale, rank="b"))
    with pytest.raises(board_store.RowConflict):
        board_store.write_row(dict(stale, rank="c"))
    assert couch.docs[board_document.document_id("issue", 4)]["rank"] == "b"


def test_write_row_refuses_a_board_it_does_not_know(couch):
    """Spelled out by hand: `_row` would raise before `write_row` was called.

    The first version of this test built its document through `_row`, whose
    `document_id` refuses the plural board itself -- so it passed against a
    `write_row` that did no checking at all.
    """
    with pytest.raises(board_document.DocumentError):
        board_store.write_row({"_id": "board:issues:1",
                               "type": board_document.DOCUMENT_TYPE,
                               "board": "issues", "number": 1, "done": False})
    assert not couch.docs


def _range_of(query):
    """`(startkey, endkey)` decoded back out of an `_all_docs` query string."""
    parsed = urllib.parse.parse_qs(query)
    return json.loads(parsed["startkey"][0]), json.loads(parsed["endkey"][0])


def test_a_capture_id_falls_in_the_capture_range_and_not_the_row_range():
    """The two ranges are what `board_records.contents` has to query
    separately, and the reason it has to is that a capture id is *outside*
    the row range. Held against `board_document.capture_document_id`'s own
    output rather than a re-spelled prefix: if that function ever moves
    captures under `board:`, this fails here rather than silently making
    `write_rows`' prune eat every capture the owner has written.
    """
    doc_id = board_document.capture_document_id("issue", "cap_1")
    row_start, row_end = _range_of(board_store._range_query("issue"))
    cap_start, cap_end = _range_of(board_store._capture_range_query("issue"))

    assert cap_start <= doc_id <= cap_end
    assert not (row_start <= doc_id <= row_end)

    # And the row id is in the row range only, so the two are disjoint over
    # the ids that actually exist rather than merely different strings.
    row_id = board_document.document_id("issue", 41)
    assert row_start <= row_id <= row_end
    assert not (cap_start <= row_id <= cap_end)


def test_the_capture_range_is_scoped_to_one_board():
    """Both boards' captures share the `capture:` prefix, so a range that
    stopped at it would hand the issue board the idea board's captures."""
    cap_start, cap_end = _range_of(board_store._capture_range_query("issue"))
    other = board_document.capture_document_id("idea", "cap_1")
    assert not (cap_start <= other <= cap_end)


def test_read_captures_fetches_the_capture_range_from_the_database(couch):
    """Against the fake CouchDB, which answers a key range rather than a
    field, so this fails if `read_captures` queries the row prefix.

    A row of the same board is in the store too: the assertion is that the
    capture came back *and* the row did not, which a query returning
    everything would also fail.
    """
    capture = board_document.to_capture_document(
        "His capture", "issue", "cap_1", rank="V")
    row = _row("issue", 41, rank="V")
    couch.docs = {doc["_id"]: doc for doc in (capture, row)}
    assert [doc["_id"] for doc in board_store.read_captures("issue")] == [
        capture["_id"]]


def test_read_captures_does_not_reach_the_other_board(couch):
    mine = board_document.to_capture_document("Mine", "issue", "cap_1")
    theirs = board_document.to_capture_document("Theirs", "idea", "cap_1")
    couch.docs = {doc["_id"]: doc for doc in (mine, theirs)}
    assert [doc["text"] for doc in board_store.read_captures("idea")] == ["Theirs"]


def test_a_refused_capture_read_raises_rather_than_reading_as_empty(monkeypatch):
    """Empty is what a board with no captures looks like, so a 500 that
    returned `[]` would render as "he has written nothing" on every reader
    below `board_records.contents`."""
    monkeypatch.setattr(ticket_docs, "_req",
                        lambda *a, **k: (500, {"error": "boom"}))
    with pytest.raises(board_store.StoreError):
        board_store.read_captures("issue")


def _capture(board, capture_id, text="His capture", **fields):
    return board_document.to_capture_document(text, board, capture_id, **fields)


def test_write_captures_stores_a_capture_in_the_capture_range(couch):
    """The migration's write path. Asserted through the fake's key range
    rather than its dict, because the whole point of the second function is
    which range the document lands in."""
    doc = _capture("issue", "cap_1")
    assert board_store.write_captures("issue", [doc])["written"] == 1

    stored = list(couch.docs)
    assert stored == [board_document.capture_document_id("issue", "cap_1")]
    cap_start, cap_end = _range_of(board_store._capture_range_query("issue"))
    assert cap_start <= stored[0] <= cap_end
    assert board_store.read_captures("issue")[0]["text"] == "His capture"


def test_writing_the_rows_does_not_tombstone_the_captures(couch):
    """`write_rows`' prune is the reason captures got their own key range,
    and this is that reason as a test: a migration that writes the rows of
    a board the owner has written captures on must leave them alone."""
    capture = _capture("issue", "cap_1")
    couch.docs = {capture["_id"]: dict(capture, _rev="1-a")}

    board_store.write_rows("issue", [_row("issue", 41, rank="V")])

    assert capture["_id"] in couch.docs


def test_writing_the_captures_does_not_tombstone_the_rows(couch):
    """And the other way, which is the half a shared writer would break:
    `write_captures` prunes its own range only."""
    row = _row("issue", 41, rank="V")
    couch.docs = {row["_id"]: dict(row, _rev="1-a")}

    board_store.write_captures("issue", [_capture("issue", "cap_1")])

    assert row["_id"] in couch.docs


def test_a_capture_the_owner_deleted_is_tombstoned(couch):
    gone = _capture("issue", "cap_1", text="Deleted from his board")
    kept = _capture("issue", "cap_2", text="Still there")
    couch.docs = {doc["_id"]: dict(doc, _rev="1-a") for doc in (gone, kept)}

    summary = board_store.write_captures("issue", [kept])

    assert summary["deleted"] == 1
    assert list(couch.docs) == [kept["_id"]]


def test_write_captures_prune_false_leaves_one_the_caller_did_not_send(couch):
    held = _capture("issue", "cap_1")
    couch.docs = {held["_id"]: dict(held, _rev="1-a")}

    board_store.write_captures("issue", [_capture("issue", "cap_2")], prune=False)

    assert held["_id"] in couch.docs


def test_a_capture_write_never_prunes_the_other_board(couch):
    theirs = _capture("idea", "cap_1", text="On the idea board")
    couch.docs = {theirs["_id"]: dict(theirs, _rev="1-a")}

    board_store.write_captures("issue", [_capture("issue", "cap_1")])

    assert theirs["_id"] in couch.docs


def test_an_unchanged_capture_is_not_written_again(couch):
    doc = _capture("issue", "cap_1")
    board_store.write_captures("issue", [doc])
    couch.bulk_calls.clear()

    summary = board_store.write_captures("issue", [doc])

    assert (summary["written"], summary["unchanged"]) == (0, 1)
    assert couch.bulk_calls == []


def test_a_changed_capture_is_written_with_the_stored_revision(couch):
    """He edits a capture's words; the id stays and the revision must ride
    along, or CouchDB refuses the update as a conflict."""
    board_store.write_captures("issue", [_capture("issue", "cap_1")])

    board_store.write_captures(
        "issue", [_capture("issue", "cap_1", text="What he meant")])

    assert couch.bulk_calls[-1][0]["_rev"] == "2-b"
    assert board_store.read_captures("issue")[0]["text"] == "What he meant"


def test_a_row_document_is_refused_by_the_capture_writer(couch):
    """It would land under `board:` and `write_rows` would then tombstone
    it, so the loss would happen a whole migration later."""
    with pytest.raises(board_document.DocumentError):
        board_store.write_captures("issue", [_row("issue", 41)])
    assert not couch.docs


def test_a_capture_document_is_refused_by_the_row_writer(couch):
    with pytest.raises(board_document.DocumentError):
        board_store.write_rows("issue", [_capture("issue", "cap_1")])
    assert not couch.docs


def test_a_capture_from_the_wrong_board_is_refused(couch):
    with pytest.raises(board_document.DocumentError):
        board_store.write_captures("issue", [_capture("idea", "cap_1")])
    assert not couch.docs


def test_write_captures_refuses_a_board_it_does_not_know(couch):
    """`issues` is the filename; `issue` is the board. The plural is the
    mistake every converted reader made, and here it must not open a third
    key range."""
    with pytest.raises(board_document.DocumentError):
        board_store.write_captures("issues", [])
    assert not couch.docs


@pytest.mark.parametrize("writer, docs", [
    ("write_captures", lambda: [_capture("issue", "cap_1", text="First"),
                                _capture("issue", "cap_1", text="Second")]),
    ("write_rows", lambda: [_row("issue", 41, rank="V"),
                            _row("issue", 41, rank="M")]),
])
def test_one_id_twice_in_a_batch_is_refused_rather_than_half_stored(
        couch, writer, docs):
    """`_bulk_docs` given the same id twice keeps one of the two and the
    caller cannot tell which, so the batch is refused before the request.
    For a capture that is one of his bullets vanishing inside the migration
    that exists to preserve it."""
    with pytest.raises(board_store.StoreError) as caught:
        getattr(board_store, writer)("issue", docs())

    assert "duplicate" in str(caught.value)
    assert couch.bulk_calls == []
    assert not couch.docs


def test_a_refused_capture_write_raises(monkeypatch):
    monkeypatch.setattr(ticket_docs, "_req",
                        lambda *a, **k: (500, {"error": "boom"}))
    with pytest.raises(board_store.StoreError):
        board_store.write_captures("issue", [_capture("issue", "cap_1")])


def test_a_capture_bulk_write_that_reports_an_error_is_not_reported_as_clean(
        couch, monkeypatch):
    """`failures` is what `board_migrate` refuses a partial migration on, so
    a 200 carrying a per-document conflict must not read as written."""
    real = couch.__call__

    def failing(method, path, body=None, timeout=60):
        if method == "POST" and path.endswith("_bulk_docs"):
            return 200, [{"id": doc["_id"], "error": "conflict"}
                         for doc in body["docs"]]
        return real(method, path, body, timeout)

    monkeypatch.setattr(ticket_docs, "_req", failing)
    summary = board_store.write_captures("issue", [_capture("issue", "cap_1")])
    assert len(summary["failures"]) == 1


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------

LAYOUT = [
    {"kind": "board", "columns": ["#", "Idea"]},
    {"kind": "verbatim", "markdown": "## Discarded\n\n| # | Item |"},
    {"kind": "detail", "number": 7},
]


def test_an_unstored_layout_reads_as_none_and_never_as_an_empty_list(couch):
    """Absent and empty are opposite instructions to `render_document`.

    `layout=None` means "draw the fixed default order" and `layout=[]` means
    "this document has no blocks", which renders a board file holding its
    frontmatter, his capture box and nothing else. A reader that flattened
    the absent case to `[]` would delete every row, every write-up and the
    whole `## Processed captures` archive the first time it rendered a board
    that had never been migrated -- with no error, because an empty layout
    is a valid layout.
    """
    assert board_store.read_layout("issue") is None
    assert board_store.read_layout("issue") != []

    board_store.write_layout("issue", [])
    assert board_store.read_layout("issue") == []


def test_a_layout_survives_the_database_and_comes_back_in_order(couch):
    board_store.write_layout("idea", LAYOUT)

    assert board_store.read_layout("idea") == LAYOUT


def test_one_boards_layout_is_not_the_others(couch):
    board_store.write_layout("issue", [{"kind": "board"}])
    board_store.write_layout("idea", LAYOUT)

    assert board_store.read_layout("issue") == [{"kind": "board"}]
    assert board_store.read_layout("idea") == LAYOUT


def test_the_layout_is_not_handed_back_as_a_row_or_a_capture(couch):
    """The prefix guard, measured through the store rather than the id.

    `test_board_document.py` asserts the id sits outside both prefixes. This
    is the consequence: a layout inside the row range would arrive in
    `read_rows`, reach `from_document` and fail there -- or worse, be
    tombstoned by the next `write_rows` prune. Both ranges are checked
    because the layout is written under `board:` and the captures are not.
    """
    board_store.write_layout("issue", LAYOUT)
    board_store.write_rows("issue", [_row("issue", 1, rank="0|a:")])

    assert sorted(dict(board_store.stored_documents("issue"))) == [
        "board:issue:1"]
    assert dict(board_store.stored_capture_documents("issue")) == {}
    assert board_store.read_layout("issue") == LAYOUT


def test_a_rewrite_of_the_rows_does_not_tombstone_the_layout(couch):
    """`write_rows(board, [])` is the restore path `board_migrate.verify`
    runs in a `finally`, and it prunes everything in the row range. The
    layout must survive it, or a verify run would delete the block order of
    a board it was only supposed to read."""
    board_store.write_layout("issue", LAYOUT)
    board_store.write_rows("issue", [])
    board_store.write_captures("issue", [])

    assert board_store.read_layout("issue") == LAYOUT


def test_an_unchanged_layout_costs_no_revision(couch):
    """A migration re-run computes the same blocks from the same markdown."""
    first = board_store.write_layout("issue", LAYOUT)
    again = board_store.write_layout("issue", list(LAYOUT))

    assert again["_rev"] == first["_rev"]


def test_a_changed_layout_is_written(couch):
    first = board_store.write_layout("issue", LAYOUT)
    changed = board_store.write_layout("issue", LAYOUT[:2])

    assert changed["_rev"] != first["_rev"]
    assert board_store.read_layout("issue") == LAYOUT[:2]


def test_a_layout_that_lost_the_race_is_written_over(couch, monkeypatch):
    """The one place this deliberately differs from `write_registry`.

    A registry holds minted ids, so a caller that lost a race and resent its
    body would orphan rows permanently -- `write_registry` raises instead. A
    layout holds no identity: it is a function of the source markdown, and
    two cycles migrating the same board compute the same blocks. So the 409
    is retried against the winner's revision rather than raised.
    """
    board_store.write_layout("issue", [{"kind": "board"}])
    real = ticket_docs._req
    stale = {"n": 0}

    def conflict_once(method, path, body=None, timeout=60):
        if (method == "PUT" and "layout" in path and stale["n"] == 0):
            stale["n"] = 1
            return 409, {"error": "conflict"}
        return real(method, path, body=body, timeout=timeout)

    monkeypatch.setattr(ticket_docs, "_req", conflict_once)
    board_store.write_layout("issue", LAYOUT)

    monkeypatch.setattr(ticket_docs, "_req", real)
    assert stale["n"] == 1
    assert board_store.read_layout("issue") == LAYOUT


def test_a_layout_for_a_board_that_is_not_his_is_refused(couch):
    with pytest.raises(board_document.DocumentError):
        board_store.write_layout("roadmap", LAYOUT)
    with pytest.raises(board_document.DocumentError):
        board_store.read_layout("roadmap")


def test_a_re_migration_of_the_same_markdown_costs_no_revision(couch):
    """The tuple trap, pinned where it bit.

    `board_view.document_layout` puts the owner's own table header in the
    block as a **tuple**, because `_header_cells` returns one, and JSON has
    no tuple -- so the stored copy comes back as a list. Comparing a fresh
    layout against the stored one without normalising means they never
    compare equal, and every migration re-run burns a revision on a document
    that did not change. `LAYOUT` above uses plain lists and cannot see it.
    """
    with_tuple = [{"kind": "board", "columns": ("#", "Idea")}]
    first = board_store.write_layout("idea", with_tuple)
    again = board_store.write_layout("idea", list(with_tuple))

    assert again["_rev"] == first["_rev"]
    assert board_store.read_layout("idea") == [
        {"kind": "board", "columns": ["#", "Idea"]}]


def test_delete_layout_removes_it_and_says_it_did(couch):
    board_store.write_layout("issue", [{"kind": "board"}])

    assert board_store.delete_layout("issue") is True
    assert board_store.read_layout("issue") is None


def test_delete_layout_on_a_board_with_none_is_not_an_error(couch):
    """A caller restoring a store it may or may not have written to should
    not have to look first."""
    assert board_store.delete_layout("issue") is False


def test_deleting_one_boards_layout_leaves_the_others(couch):
    board_store.write_layout("issue", [{"kind": "board"}])
    board_store.write_layout("idea", [{"kind": "board"}])

    board_store.delete_layout("issue")

    assert board_store.read_layout("idea") == [{"kind": "board"}]


def test_delete_layout_is_absence_and_not_an_empty_layout(couch):
    """The distinction the whole layout API turns on. `read_layout` hands
    `[]` straight to `board_view.render_document`, which draws a board file
    holding its frontmatter, the capture box and nothing else -- so a
    delete that stored an empty list instead of removing the document would
    erase every row and every write-up on the next render, silently."""
    board_store.write_layout("issue", [{"kind": "board"}])

    board_store.delete_layout("issue")

    assert board_store.read_layout("issue") is not []
    assert board_store.read_layout("issue") is None
    assert board_document.layout_document_id("issue") not in couch.docs


def test_delete_layout_reaches_for_no_other_document(couch):
    """It is the only delete here that is not a prune, so the thing to pin
    is that it names one id rather than sweeping a range."""
    board_store.write_layout("issue", [{"kind": "board"}])
    couch.calls.clear()

    board_store.delete_layout("issue")

    assert not any("_all_docs" in path for _method, path in couch.calls)


def test_write_capture_creates_one_bullet_without_touching_the_others(couch):
    """The whole point of the single-document form over `write_captures`.

    `write_captures` lists the range with `include_docs=true` and prunes what
    it was not passed, so writing one bullet through it fetches every bullet
    and every reply and tombstones the rest.
    """
    board_store.write_captures("issue", [_capture("issue", "cap_1"),
                                         _capture("issue", "cap_2")])
    held = board_store.read_capture("issue", "cap_1")

    board_store.write_capture(dict(held, text="Edited"))

    assert board_store.read_capture("issue", "cap_1")["text"] == "Edited"
    assert board_store.read_capture("issue", "cap_2") is not None


def test_an_unstored_capture_reads_as_none_rather_than_raising(couch):
    """`read_row`'s contract over the other range: absent is not an error."""
    assert board_store.read_capture("issue", "cap_9") is None


def test_a_capture_update_must_carry_the_revision_it_was_read_at(couch):
    """The rule `_write_one` holds for both ranges, asked of the capture."""
    board_store.write_capture(_capture("issue", "cap_1", text="His words"))
    with pytest.raises(board_document.DocumentError):
        board_store.write_capture(_capture("issue", "cap_1", text="Clobbered"))
    assert board_store.read_capture("issue", "cap_1")["text"] == "His words"


def test_an_unchanged_capture_costs_no_revision(couch):
    """A re-run must be free here too, and free without a `_rev`."""
    stored = board_store.write_capture(_capture("issue", "cap_1"))
    again = board_store.write_capture(_capture("issue", "cap_1"))
    assert again["_rev"] == stored["_rev"]


def test_a_lost_capture_revision_raises_its_own_conflict(couch):
    """Not `RowConflict`: a caller recovering from one is not recovering the other."""
    stale = board_store.write_capture(_capture("issue", "cap_1", text="a"))
    board_store.write_capture(dict(stale, text="b"))
    with pytest.raises(board_store.CaptureConflict):
        board_store.write_capture(dict(stale, text="c"))
    assert board_store.read_capture("issue", "cap_1")["text"] == "b"


def test_write_capture_refuses_a_document_that_is_not_a_capture(couch):
    """A row handed to the capture writer would land outside its own range."""
    with pytest.raises(board_document.DocumentError):
        board_store.write_capture(_row("issue", 5))
    assert couch.docs == {}


def test_delete_capture_removes_it_and_says_it_did(couch):
    """The half of boarding a capture that `write_capture` cannot do."""
    stored = board_store.write_capture(_capture("issue", "cap_1"))
    assert board_store.delete_capture(stored) is True
    assert board_store.read_capture("issue", "cap_1") is None


def test_delete_capture_leaves_the_other_bullets_alone(couch):
    """`write_captures(prune=True)` is the spelling this replaces, and it
    expresses a deletion as an absence from the caller's list -- so a list
    built from a stale read takes every bullet the winner added with it."""
    board_store.write_captures("issue", [_capture("issue", "cap_1"),
                                         _capture("issue", "cap_2")])
    board_store.delete_capture(board_store.read_capture("issue", "cap_1"))

    assert board_store.read_capture("issue", "cap_2") is not None


def test_a_capture_delete_must_carry_the_revision_it_was_read_at(couch):
    """`_write_one`'s middle rule pointed at a delete.

    A document straight out of `to_capture_document` has no revision, so
    deleting on it would remove whatever is stored *now* -- including a
    reply written under his words while I was deciding to board the bullet.
    """
    board_store.write_capture(_capture("issue", "cap_1", text="His words"))
    with pytest.raises(board_document.DocumentError):
        board_store.delete_capture(_capture("issue", "cap_1"))
    assert board_store.read_capture("issue", "cap_1")["text"] == "His words"


def test_a_capture_delete_on_a_stale_revision_raises_its_own_conflict(couch):
    """Not `RowConflict`, and not a silent success: the bullet moved."""
    stale = board_store.write_capture(_capture("issue", "cap_1", text="a"))
    board_store.write_capture(dict(stale, text="b"))
    with pytest.raises(board_store.CaptureConflict):
        board_store.delete_capture(stale)
    assert board_store.read_capture("issue", "cap_1")["text"] == "b"


def test_deleting_a_capture_that_has_already_gone_is_not_an_error(couch):
    """`delete_layout`'s contract, and a re-run is reachable here: the caller
    adds the row and then removes the bullet, so a cycle that died between
    the two comes back to a board holding both."""
    stored = board_store.write_capture(_capture("issue", "cap_1"))
    assert board_store.delete_capture(stored) is True
    assert board_store.delete_capture(stored) is False


def test_delete_capture_refuses_a_row_document(couch):
    """A row handed here would name an id outside the capture range."""
    with pytest.raises(board_document.DocumentError):
        board_store.delete_capture(dict(_row("issue", 5), _rev="1-a"))


def test_delete_capture_reaches_for_no_other_document(couch):
    """One bullet out of the box is one request. `write_captures` lists the
    whole range with `include_docs=true` to do the same thing."""
    stored = board_store.write_capture(_capture("issue", "cap_1"))
    couch.calls.clear()
    board_store.delete_capture(stored)
    assert [method for method, _ in couch.calls] == ["DELETE"]
