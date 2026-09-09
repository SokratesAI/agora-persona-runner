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
    """`read_rows`, `read_captures` and `read_registry`, no CouchDB.

    **Routed on the document's own `_id` prefix, the way `_all_docs` does
    it** -- not on the `board` field. That is the difference between this
    fake and the one this file shipped with, and it is the whole bug:
    the old one answered `read_rows` with every document of the board,
    captures included, so it agreed with a join no real store can perform.
    `contents` never called for the capture range at all and returned zero
    captures against CouchDB with this file green.

    A fake that cannot say *"that id is not in the range you asked for"*
    cannot catch that, however many assertions are written against it.

    `read_rows` sorts through `board_store.in_order` rather than restating
    the order, so a test cannot pass by agreeing with a rule the real store
    has since changed. `read_captures` deliberately does *not* sort, for the
    same reason the real one does not: `board_document.captures_map` owns
    the capture order and deciding it twice is deciding it in two places.
    """

    def __init__(self, docs, registry):
        self.docs = list(docs)
        self.registry = registry

    def _in_range(self, prefix):
        return [doc for doc in self.docs
                if str(doc.get("_id", "")).startswith(prefix)]

    def read_rows(self, board):
        return board_store.in_order(self._in_range(f"board:{board}:"))

    def read_captures(self, board):
        return self._in_range(f"capture:{board}:")

    def read_registry(self):
        return self.registry


def migrated(board="issue", markdown=BOARD):
    """`(parse_board's answer, a store holding the same board as records)`."""
    parsed = nova_boards.parse_board(markdown)
    # `_rev` because a store that has been migrated has a *stored* registry,
    # and `contents` reads exactly that to tell an unmigrated store from a
    # clean board. A fake without it is not a migrated store.
    registry = dict(entity_id.new_registry(), _rev="1-abc")
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
    """Captures live in their own key range, so the rows come back as rows
    and the captures still arrive -- from the second query, not from
    sorting the first one's answer by `type`."""
    _parsed, store = migrated()
    result = board_records.contents("issue", store=store)
    assert [row["number"] for row in result["items"]] == [41, 42, 43, 40]
    assert result["captures"] == ["His first capture", "His second capture"]


def test_captures_come_from_the_capture_range_and_not_the_row_range():
    """The guard on the bug this fake used to hide.

    A store that answers the row range and nothing else must produce an
    empty capture list -- if `contents` ever goes back to sorting captures
    out of `read_rows`, the captures reappear here and this fails. Asserted
    on a store that genuinely holds them, so a green cannot mean "there
    were no captures to find".
    """
    _parsed, store = migrated()
    assert store.read_captures("issue"), "the fixture must hold captures"

    class RowsOnly:
        def read_rows(self, board):
            return store.read_rows(board)

        def read_captures(self, board):
            return []

        def read_registry(self):
            return store.registry

    result = board_records.contents("issue", store=RowsOnly())
    assert result["captures"] == []
    assert result["captureReplies"] == []
    # The rows are untouched, so this is the capture query going missing
    # and not the whole read failing.
    assert [row["number"] for row in result["items"]] == [41, 42, 43, 40]


def test_a_capture_inside_the_row_range_raises():
    """`write_rows`' default `prune=True` tombstones anything in the row
    range it was not sent, so a capture stored there is about to be
    deleted. Reading it as a capture would make this reader the last thing
    ever to see it, which is why it raises instead."""
    _parsed, store = migrated()
    stray = dict(board_document.to_capture_document(
        "Filed in the wrong range", "issue", "cap_stray"),
        _id="board:issue:capture:cap_stray")
    store.docs.append(stray)
    with pytest.raises(board_records.RecordError) as caught:
        board_records.contents("issue", store=store)
    assert "board:issue:capture:cap_stray" in str(caught.value)


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


def test_an_unmigrated_store_raises_instead_of_reading_as_a_clean_board():
    """The trap this guard exists for.

    `read_rows` cannot tell "never written" from "every row closed" -- both
    are `[]` -- so without this every reader below the seam exits 0 saying
    the board is fine. The registry is the signal, so the store here holds
    the real documents and only the registry is absent: a guard that keyed
    off the row count would pass this test while missing the case it names.
    """
    _parsed, store = migrated()
    store.registry = entity_id.new_registry()
    with pytest.raises(board_records.UnmigratedStore):
        board_records.contents("issue", store=store)


def test_the_unmigrated_guard_is_a_record_error():
    """Because that is how it reaches the readers.

    Each converted reader catches `RecordError` to mean "this sweep did not
    see that board" and exits non-zero. If this were a sibling exception,
    every one of them would need editing and the ones not yet converted
    would inherit the silent answer instead of the safe one.
    """
    assert issubclass(board_records.UnmigratedStore, board_records.RecordError)


def test_a_genuinely_empty_board_under_a_stored_registry_still_returns():
    """The other half, and the reason the guard is not "no rows".

    A board whose rows are all gone is a real state a reader is entitled to
    report on. Only the registry decides.
    """
    _parsed, store = migrated()
    store.docs = []
    assert board_records.contents("issue", store=store) == {
        "captures": [], "captureReplies": [], "items": [], "details": {}}


def test_the_registry_is_checked_before_the_documents_are_read():
    """An unmigrated store is the more fundamental failure of the two.

    A store with no registry *and* a junk document must name the migration,
    not the document -- otherwise the first run against an empty store
    reports whichever unrelated thing it happened to trip over.
    """
    _parsed, store = migrated()
    store.registry = entity_id.new_registry()
    store.docs = [{"_id": "board:issue:row:1", "board": "issue", "type": "nonsense"}]
    with pytest.raises(board_records.UnmigratedStore):
        board_records.contents("issue", store=store)
