"""A document is only worth having if a row survives the trip through it.

So most of these are a round trip: `parse_board` -> `to_document` ->
`from_document`, compared against the row that went in. The failure to
guard against is not an exception -- it is a document that looks right and
gives back a row with a lost project, a position that came back `None`, or
a status key computed from a stale copy of a rule that has since moved.
"""

import pytest

from agora_runner import board_document, nova_boards, rank_key
from agora_runner.board_document import (
    BOARDS,
    CAPTURE_DOCUMENT_TYPE,
    DOCUMENT_TYPE,
    DocumentError,
    capture_document_id,
    capture_replies_of,
    capture_text_of,
    captures_map,
    details_map,
    detail_of,
    document_id,
    from_document,
    to_capture_document,
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


# --- Captures ---------------------------------------------------------

CAPTURE_BOARD = """---
type: log
---

- Move the goals out of my obsidian vault and into yours.
  - Done in cycle 789, all ten documents verified byte-identical first.
- Be more humble in tone.

## Board

| # | Title |
|---|---|
"""


def parsed_captures(markdown=CAPTURE_BOARD):
    parsed = nova_boards.parse_board(markdown)
    return parsed["captures"], parsed["captureReplies"]


def test_a_capture_and_its_replies_survive_the_round_trip_in_order():
    """The pair `parse_board` hands back is what six modules read, and they
    index one list by the other's position -- so the two coming back the
    same length and in the same order is the contract, not a detail."""
    captures, replies = parsed_captures()
    assert len(captures) == 2 and replies[0] and not replies[1]

    ranks = rank_key.sequence(len(captures))
    docs = [
        to_capture_document(text, "issue", f"cap_{i}", rank=ranks[i], replies=reply)
        for i, (text, reply) in enumerate(zip(captures, replies))
    ]
    # Handed back in the order CouchDB's `_all_docs` actually answers in --
    # lexical by id, which is not insertion order once there are ten.
    assert captures_map(reversed(docs)) == {
        "captures": captures, "captureReplies": replies,
    }


def test_an_unranked_capture_lands_after_the_ranked_ones_not_before():
    """`sorted` on `rank or ""` would read an unplaced capture as the
    smallest key and print it at the top of his board -- the most visible
    seat in the file, for the one bullet nobody has placed. The migration
    reads a file that states an order and nothing else, so `None` has to
    stay a different answer from first, the same as a row's `order`."""
    placed = to_capture_document("placed", "issue", "cap_1", rank="0|b:")
    unplaced = to_capture_document("unplaced", "issue", "cap_2")
    assert "rank" not in unplaced
    assert captures_map([unplaced, placed])["captures"] == ["placed", "unplaced"]


def test_a_captures_id_is_minted_and_never_derived_from_its_text():
    """Editing a capture changes its text, and today the Edit route
    addresses a capture *by* its text -- which is how a welded-on reply
    made the route answer "no longer in the list" and lose an edit he had
    just typed. So the id has to survive the edit that uses it."""
    before = to_capture_document("Be more humble.", "issue", "cap_7")
    after = to_capture_document("Be more humble in tone.", "issue", "cap_7")
    assert before["_id"] == after["_id"] == "capture:issue:cap_7"
    assert capture_text_of(after) == "Be more humble in tone."


def test_a_capture_is_not_a_row_and_a_row_view_must_not_see_it():
    """One database for the whole vault: a view asking for `type == "row"`
    would otherwise be handed bullets with no number."""
    assert CAPTURE_DOCUMENT_TYPE != DOCUMENT_TYPE
    assert to_capture_document("hi", "idea", "cap_1")["type"] == CAPTURE_DOCUMENT_TYPE


def test_an_empty_reply_list_is_absent_rather_than_stored_empty():
    """`parse_board` gives `[]` for an unanswered bullet. Storing `[]` puts
    the same fact in CouchDB one way and everywhere else another way."""
    doc = to_capture_document("hi", "issue", "cap_1", replies=[])
    assert "replies" not in doc and capture_replies_of(doc) == []


def test_a_malformed_capture_is_refused_rather_than_stored():
    for bad in ("", "   "):
        with pytest.raises(DocumentError):
            to_capture_document(bad, "issue", "cap_1")
    with pytest.raises(DocumentError):
        to_capture_document(["not", "text"], "issue", "cap_1")
    with pytest.raises(DocumentError):
        to_capture_document("hi", "issue", "cap_1", replies=[7])
    for bad_id in ("", "   ", "cap:1", None):
        with pytest.raises(DocumentError):
            to_capture_document("hi", "issue", bad_id)
    with pytest.raises(DocumentError):
        to_capture_document("hi", "roadmap", "cap_1")


def test_a_capture_whose_id_disagrees_with_its_fields_is_refused():
    """Same rule as a row's: `_id` is composed from `board` and the minted
    id, so a document where they disagree has no winner to pick."""
    doc = to_capture_document("hi", "issue", "cap_1")
    for broken in (dict(doc, board="idea"), dict(doc, captureId="cap_2")):
        with pytest.raises(DocumentError):
            capture_text_of(broken)
    missing = dict(doc)
    missing.pop("captureId")
    with pytest.raises(DocumentError):
        capture_replies_of(missing)
    with pytest.raises(DocumentError):
        captures_map([dict(doc, board="idea")])


def test_a_capture_id_sits_outside_the_range_the_store_reads_rows_with():
    """`board_store` selects a board's rows with an `_all_docs` range over
    the literal prefix `board:<board>:`, so an id under that prefix is a
    row as far as every store call is concerned however its `type` reads.
    A capture filed there comes back from `read_rows` as a row with no
    number, and the next `write_rows(board, rows)` tombstones it, because
    `prune=True` deletes what the caller did not send.

    The bounds are read off the store rather than spelled out again here:
    a test that restates the prefix would still pass on the day somebody
    changes it."""
    import json
    import urllib.parse

    from agora_runner import board_store

    query = urllib.parse.parse_qs(board_store._range_query("issue"))
    start = json.loads(query["startkey"][0])
    end = json.loads(query["endkey"][0])

    row_id = document_id("issue", 41)
    assert start <= row_id < end

    capture_id = capture_document_id("issue", "cap_1")
    assert not (start <= capture_id < end)


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------


def test_the_layout_id_is_outside_both_of_a_boards_key_ranges():
    """The id decision, asserted rather than left to the reader.

    `board_store` selects a board's rows with an `_all_docs` range over the
    literal prefix `board:<board>:` and its captures over `capture:<board>:`.
    A layout id inside either one would be handed back as a row and
    tombstoned by `write_rows`' prune -- so this is the guard, and it is
    written as the two prefixes rather than as the literal string, so
    renaming a range breaks it.
    """
    for board in board_document.BOARDS:
        doc_id = board_document.layout_document_id(board)
        assert not doc_id.startswith(f"board:{board}:")
        assert not doc_id.startswith(f"capture:{board}:")
    assert (board_document.layout_document_id("issue")
            != board_document.layout_document_id("idea"))


def test_a_layout_round_trips_through_its_document():
    blocks = [
        {"kind": "board", "columns": ["#", "Idea"]},
        {"kind": "verbatim", "markdown": "## Discarded\n\nnothing yet"},
        {"kind": "detail", "number": 7},
    ]
    doc = board_document.to_layout_document(blocks, "idea")

    assert doc["_id"] == "board:layout:idea"
    assert doc["type"] == board_document.LAYOUT_DOCUMENT_TYPE
    assert doc["board"] == "idea"
    assert board_document.layout_blocks_of(doc) == blocks


def test_the_layout_type_is_not_the_row_type():
    """The CouchDB views key on `doc.type`; a layout is not a row."""
    assert (board_document.LAYOUT_DOCUMENT_TYPE
            != board_document.DOCUMENT_TYPE)
    assert (board_document.LAYOUT_DOCUMENT_TYPE
            != board_document.CAPTURE_DOCUMENT_TYPE)


def test_every_kind_board_view_mints_is_accepted():
    """The accepted kinds are `board_view`'s own tuple, not a second copy.

    A layout block whose kind this module does not know is refused, and the
    list of known kinds lives where the blocks are minted. If that tuple
    grows a kind and this module carried its own copy, every layout
    containing the new kind would be unstorable.
    """
    from agora_runner import board_view

    for kind in board_view.LAYOUT_KINDS:
        block = {"kind": kind}
        if kind == "detail":
            block["number"] = 1
        if kind == "verbatim":
            block["markdown"] = "x"
        assert board_document.to_layout_document([block], "issue")


@pytest.mark.parametrize("blocks", [
    "not a list",
    [{"kind": "prose", "markdown": "x"}],
    [{"kind": "detail", "number": "7"}],
    [{"kind": "verbatim"}],
    [["kind", "board"]],
])
def test_a_layout_that_would_delete_his_archive_is_refused(blocks):
    """`render_document` drops a block it does not recognise silently.

    So a layout that lost its `verbatim` blocks -- a JSON load of the wrong
    file, a `number` that arrived as a string -- renders a board with the
    `## Processed captures` archive deleted and no error anywhere. That is
    the same 19,653 words this whole piece exists to keep, so the shape is
    refused at the one place that mints the document.
    """
    with pytest.raises(board_document.DocumentError):
        board_document.to_layout_document(blocks, "issue")


def test_a_layout_document_served_under_the_wrong_board_is_refused():
    doc = board_document.to_layout_document([{"kind": "board"}], "issue")
    doc["board"] = "idea"

    with pytest.raises(board_document.DocumentError):
        board_document.layout_blocks_of(doc)


def test_a_layout_for_a_board_that_is_not_his_is_refused():
    with pytest.raises(board_document.DocumentError):
        board_document.layout_document_id("roadmap")


def test_the_source_document_id_sits_outside_both_board_ranges():
    """The same prefix decision `layout_document_id` makes.

    `board_store` selects a board's rows with an `_all_docs` range over the
    literal prefix `board:<board>:`, and `write_rows` prunes everything in
    it that the caller did not send. A source stamp under that prefix would
    be tombstoned by the first migration that wrote the rows alone.
    """
    for board in board_document.BOARDS:
        doc_id = board_document.source_document_id(board)
        assert not doc_id.startswith(f"board:{board}:")
        assert not doc_id.startswith(f"capture:{board}:")

    assert (board_document.source_document_id("issue")
            != board_document.source_document_id("idea"))
    assert (board_document.source_document_id("issue")
            != board_document.layout_document_id("issue"))


def test_a_source_document_round_trips_its_revision():
    doc = board_document.to_source_document("42-abc", "issue")

    assert board_document.source_rev_of(doc) == "42-abc"
    assert doc["_id"] == board_document.source_document_id("issue")


def test_a_source_document_refuses_an_id_that_disagrees_with_its_board():
    """A stamp served under the wrong board would certify one board's
    records as current from the other board's write."""
    doc = board_document.to_source_document("42-abc", "issue")
    doc["board"] = "idea"

    with pytest.raises(board_document.DocumentError):
        board_document.source_rev_of(doc)


def test_a_source_revision_must_be_a_non_empty_string():
    """The only thing anybody does with this value is compare it for
    equality against a live revision, so a value that arrived as an int
    would compare unequal to the same revision read back as text -- and
    `currency` would answer `stale` on records that are current."""
    for bad in (None, "", "   ", 42, ["42-abc"]):
        with pytest.raises(board_document.DocumentError):
            board_document.to_source_document(bad, "issue")

    for bad in (None, "", "   ", 42):
        doc = dict(board_document.to_source_document("42-abc", "issue"),
                   sourceRev=bad)
        with pytest.raises(board_document.DocumentError):
            board_document.source_rev_of(doc)
