"""A document is only worth having if a row survives the trip through it.

So most of these are a round trip: `parse_board` -> `to_document` ->
`from_document`, compared against the row that went in. The failure to
guard against is not an exception -- it is a document that looks right and
gives back a row with a lost project, a position that came back `None`, or
a status key computed from a stale copy of a rule that has since moved.
"""

import pytest

from agora_runner import nova_boards
from agora_runner.board_document import (
    BOARDS,
    DOCUMENT_TYPE,
    DocumentError,
    details_map,
    detail_of,
    document_id,
    from_document,
    to_document,
)

BOARD = """## Board

| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |
|---|---|---|---|---|---|---|---|---|
| [[#41]] | Give the boards a schema | 🟡 In progress | 2026-09-08 | 🔴 Immediately | Nova | M | Records | 3 |
| [[#42]] | A row nobody has filed | ⚪ Backlog | 2026-09-07 | | | | | |
| [[#43]] | Done in the board table | ✅ Done | 2026-09-06 | 🟠 High | Marcus | S | | |

## Done

| # | Item | Updated | Where |
|---|---|---|---|
| [[#40]] | An older thing | 2026-09-01 | shipped |
"""


def rows():
    return nova_boards.parse_board(BOARD)["items"]


def by_number(number):
    return next(row for row in rows() if row["number"] == number)


def test_a_parsed_row_survives_the_document_unchanged():
    """The whole point: in, out, identical -- on every row of a real shape."""
    for row in rows():
        doc = to_document(row, "issue")
        assert from_document(doc, project_name=row["project"],
                             milestone_name=row["milestone"]) == row


def test_the_id_names_the_board_as_well_as_the_number():
    """Both boards number from 1, so a bare number names two rows."""
    assert document_id("issue", 41) == "board:issue:41"
    assert document_id("idea", 41) == "board:idea:41"
    assert document_id("issue", 41) != document_id("idea", 41)


@pytest.mark.parametrize("board, number", [
    ("project", 41),      # not one of the owner's two boards
    ("issue", "41"),      # a string would mint the same id from a second parse
    ("issue", 0),
    ("issue", -1),
    ("issue", True),      # bool is an int in Python and is not a row number
])
def test_a_bad_identity_is_refused_rather_than_coerced(board, number):
    with pytest.raises(DocumentError):
        document_id(board, number)


def test_derived_keys_are_not_stored():
    """A stored `statusKey` is the split brain this issue is about, one
    field wide: a change to the rule would fix the pages and leave every
    document spelled the old way."""
    doc = to_document(by_number(41), "issue")
    for derived in ("statusKey", "priorityKey", "sizeKey", "project", "milestone"):
        assert derived not in doc


def test_the_keys_are_recomputed_through_nova_boards():
    """One spelling of each rule -- not a copy that drifts from it."""
    row = by_number(41)
    out = from_document(to_document(row, "issue"), project_name="Nova")
    assert out["priorityKey"] == nova_boards.priority_key(row["priority"])
    assert out["sizeKey"] == nova_boards.size_key(row["size"])
    assert out["statusKey"] == nova_boards.status_key(row["status"])


def test_a_done_row_keeps_where_and_carries_no_position():
    row = by_number(40)
    doc = to_document(row, "issue")
    assert doc["done"] is True
    assert doc["where"] == "shipped"
    assert from_document(doc)["order"] is None


def test_done_is_which_table_and_status_is_the_cell():
    """Row #43 sits in `## Board` reading `✅ Done`. Keying off the status
    would move it into the other table on the next render."""
    doc = to_document(by_number(43), "issue")
    assert doc["status"] == "✅ Done"
    assert doc["done"] is False
    assert from_document(doc)["statusKey"] == nova_boards.status_key("✅ Done")


def test_an_unplaced_row_stays_unplaced_and_zero_stays_zero():
    """`None` and `0` are different answers -- `board_view`'s finding."""
    row = dict(by_number(42), order=None)
    assert from_document(to_document(row, "issue"))["order"] is None
    assert from_document(to_document(dict(row, order=0), "issue"))["order"] == 0


def test_the_ids_are_only_written_when_the_caller_has_them():
    """A `## Done` row carries no project, milestone or rank in the markdown
    it came from, and inventing one during the migration would be writing a
    fact nobody stated."""
    doc = to_document(by_number(40), "issue")
    for absent in ("projectId", "milestoneId", "rank"):
        assert absent not in doc
    full = to_document(by_number(41), "issue", project_id="prj_nova",
                       milestone_id="ms_7", rank="0|hzzzzz:")
    assert full["projectId"] == "prj_nova"
    assert full["milestoneId"] == "ms_7"
    assert full["rank"] == "0|hzzzzz:"


def test_an_unresolved_project_reads_as_the_default_not_as_nothing():
    """`parse_board`'s own rule: a row that predates the column is a row
    nobody has re-filed, not a row that belongs nowhere."""
    assert from_document(to_document(by_number(42), "issue"))["project"] == (
        nova_boards.DEFAULT_PROJECT)


def test_an_id_that_disagrees_with_its_own_fields_is_refused():
    """Not resolved in favour of either side -- a document that says two
    things about which row it is has no right answer to pick."""
    doc = to_document(by_number(41), "issue")
    with pytest.raises(DocumentError):
        from_document(dict(doc, _id="board:idea:41"))
    with pytest.raises(DocumentError):
        from_document(dict(doc, number=42))
    doc.pop("board")
    with pytest.raises(DocumentError):
        from_document(doc)


def test_every_document_says_what_kind_it_is():
    """One database for the whole vault, so a view has to be able to ask."""
    assert to_document(by_number(41), "issue")["type"] == DOCUMENT_TYPE
    assert set(BOARDS) == {"issue", "idea"}


def test_a_done_row_reads_as_done_whatever_its_status_cell_says():
    """`parse_board` hard-codes `statusKey` for a `## Done` row rather than
    deriving it, and this mirrors that. Every document `to_document` makes
    from a parsed row has `status == "✅ Done"` whenever `done` is true, so
    the two spellings agree on all 472 live rows and a mutation swapping
    them survives -- this hands it the pair only the store can produce: a
    row moved to `## Done` whose status cell was never re-saved."""
    doc = to_document(dict(by_number(40), status="🟡 In progress"), "issue")
    assert doc["done"] is True and doc["status"] == "🟡 In progress"
    assert from_document(doc)["statusKey"] == "done"
    assert nova_boards.status_key("🟡 In progress") != "done"


def test_a_detail_body_survives_the_document_and_an_absent_one_stays_absent():
    """A row's prose body is `parse_board`'s `details[number]`, a separate
    dict rather than a key on the row, so nothing carried it into a record
    until now -- and a document written without one loses it silently,
    because `board_view` renders the two tables and no prose at all.

    The pair matters as much as the survival: an absent body and an empty
    one are the same thing to `parse_board`, which simply has no entry for
    that number, so storing `""` would make the round trip mint a detail
    section nobody wrote."""
    with_body = to_document(by_number(41), "issue", detail="Why this matters.\n")
    assert detail_of(with_body) == "Why this matters.\n"
    for empty in (None, ""):
        doc = to_document(by_number(41), "issue", detail=empty)
        assert "detail" not in doc and detail_of(doc) == ""


def test_details_map_gives_back_exactly_parse_boards_details_dict():
    """The inverse the 15 modules reading `parse_board(...)["details"]` need.
    Only rows that carry a body appear, so `details.get(n)` still tells
    "no detail section" from "one that is empty"."""
    docs = [
        to_document(by_number(41), "issue", detail="first\n"),
        to_document(by_number(40), "issue"),
    ]
    assert details_map(docs) == {41: "first\n"}


def test_a_detail_that_is_not_text_is_refused_rather_than_stored():
    """Stored unchecked it would come back out of CouchDB as JSON of some
    other shape and be rendered into a board as its repr."""
    with pytest.raises(DocumentError):
        to_document(by_number(41), "issue", detail=["not", "text"])
