"""The join is only worth having if a whole board survives it.

So the anchor test is a full round trip: real markdown -> `parse_board` ->
record documents -> a fake store -> `board_records.contents`, compared
against `parse_board`'s own answer. Every one of the twenty-one modules
issue #203 moves is asking that exact question, so a green here is the
thing that lets them stop parsing.

The failure to guard against is not an exception. It is a board that comes
back looking right with a capture filed as a numberless row, a project name
resolved to the wrong id, or `details` quietly empty -- each of which
renders without complaint.
"""

import pytest

from agora_runner import (
    board_document,
    board_records,
    board_store,
    entity_id,
    nova_boards,
    rank_key,
)
from tools import board_migration_preflight as preflight

BOARD = """- His first capture
  - Nova, cycle 1: answered.
- His second capture

## Board

| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |
|---|---|---|---|---|---|---|---|---|
| [[#41]] | Give the boards a schema | 🟡 In progress | 2026-09-08 | 🔴 Immediately | Nova | M | Records | 3 |
| [[#42]] | A row nobody has filed | ⚪ Backlog | 2026-09-07 | | | | | |
| [[#43]] | Done in the board table | ✅ Done | 2026-09-06 | 🟠 High | Marcus | S | | |

## Done

| # | Item | Updated | Where |
|---|---|---|---|
| [[#40]] | An older thing | 2026-09-01 | shipped |

# Details

## #41 — Give the boards a schema

The prose body of forty-one.
"""


class FakeStore:
    """`read_rows` and `read_registry` against a list, no CouchDB.

    `read_rows` sorts through `board_store.in_order` rather than restating
    the order, so a test cannot pass by agreeing with a rule the real store
    has since changed.
    """

    def __init__(self, docs, registry):
        self.docs = list(docs)
        self.registry = registry

    def read_rows(self, board):
        return board_store.in_order(
            [doc for doc in self.docs if doc.get("board") == board])

    def read_registry(self):
        return self.registry


def migrated(board="issue", markdown=BOARD):
    """`(parse_board's answer, a store holding the same board as records)`."""
    parsed = nova_boards.parse_board(markdown)
    registry = entity_id.new_registry()
    docs, _projects, _milestones = preflight.records(
        parsed["items"], board, registry, details=parsed["details"])
    keys = rank_key.sequence(len(parsed["captures"]))
    for index, (text, replies) in enumerate(
            zip(parsed["captures"], parsed["captureReplies"])):
        docs.append(board_document.to_capture_document(
            text, board, f"cap_{index + 1}", rank=keys[index], replies=replies))
    return parsed, FakeStore(docs, registry)


def test_a_whole_board_survives_the_round_trip():
    """In through the records, out through the join, identical."""
    parsed, store = migrated()
    assert board_records.contents("issue", store=store) == parsed


def test_a_capture_does_not_come_back_as_a_numberless_row():
    """A capture's id sits inside the row key range on purpose, so
    `read_rows` hands both kinds over and only `type` separates them. A
    capture read as a row is a row with no number, which the next
    `write_rows` would tombstone."""
    _parsed, store = migrated()
    result = board_records.contents("issue", store=store)
    assert [row["number"] for row in result["items"]] == [41, 42, 43, 40]
    assert result["captures"] == ["His first capture", "His second capture"]


def test_details_come_back_keyed_by_number():
    _parsed, store = migrated()
    assert board_records.contents("issue", store=store)["details"] == {
        41: "The prose body of forty-one."}


def test_names_are_resolved_through_the_registry_not_the_document():
    """A rename must reach every row that points at the project. If the
    name were read off the row, this would still say `Nova`."""
    _parsed, store = migrated()
    pid = entity_id.resolve_project(store.registry, "Nova")
    entity_id.rename_project(store.registry, pid, "Aurora")
    result = board_records.contents("issue", store=store)
    assert [row["project"] for row in result["items"] if row["number"] == 41] \
        == ["Aurora"]


def test_a_dangling_project_id_raises_rather_than_reading_as_unfiled():
    """The orphan stable ids exist to prevent. Falling back to the default
    project would put it on his board looking re-filed."""
    _parsed, store = migrated()
    store.registry["projects"].clear()
    with pytest.raises(board_records.RecordError):
        board_records.contents("issue", store=store)


def test_a_dangling_milestone_id_raises_too():
    _parsed, store = migrated()
    store.registry["milestones"].clear()
    with pytest.raises(board_records.RecordError):
        board_records.contents("issue", store=store)


def test_a_row_with_no_project_is_not_a_dangling_one():
    """`#42` carries no project at all, which is a row nobody has filed --
    `from_document`'s default, not an error."""
    _parsed, store = migrated()
    result = board_records.contents("issue", store=store)
    row = next(r for r in result["items"] if r["number"] == 42)
    assert row["project"] == nova_boards.DEFAULT_PROJECT


@pytest.mark.parametrize("kind", [None, "row-v2", "board-registry", ""])
def test_an_unknown_document_type_is_refused(kind):
    """`read_rows` promises everything in the board's key range, so a third
    kind appearing there is a naming mistake, not a row."""
    with pytest.raises(board_records.RecordError):
        board_records.split_documents([{"_id": "board:issue:9", "type": kind}])


def test_split_keeps_the_order_it_was_given():
    """`contents` leans on it: `read_rows` has already sorted."""
    _parsed, store = migrated()
    docs = store.read_rows("issue")
    rows, captures = board_records.split_documents(docs)
    assert [doc["_id"] for doc in rows + captures] != []
    assert rows == [d for d in docs if d["type"] == board_document.DOCUMENT_TYPE]
    assert captures == [
        d for d in docs if d["type"] == board_document.CAPTURE_DOCUMENT_TYPE]
