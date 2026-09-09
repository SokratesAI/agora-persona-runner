"""The two board readers in `nova_next`, fed records instead of a file.

Issue #203. `open_rows` and `unboarded_captures` used to *be* their first
line -- `parse_board(markdown)` -- and everything after it was already a
rule about `parse_board`'s return value, which is the same four keys
`board_records.contents` answers out of CouchDB. The split gives the
switchover a door to walk through without a facade over the parser.

Every contents dict here is built by hand and **no markdown exists in this
file at all**. That is the point of the file rather than a style choice: a
test that builds a board file and parses it cannot tell a converted reader
from an unconverted one, because both agree on text the parser produced.
These hand-built dicts carry shapes a board file cannot express -- a
detail body for a row that is not in `items`, an `order` integer, a
capture list with no bullet syntax -- so a reader that quietly went back to
parsing would have nothing to parse.
"""

import pytest

from agora_runner.nova_next import (
    open_rows, open_rows_from_contents,
    unboarded_captures, unboarded_captures_from_contents,
)


def row(number, **over):
    item = {"number": number, "title": f"row {number}", "status": "Backlog",
            "statusKey": "backlog", "priority": "🟡 Soon", "priorityKey": "soon",
            "updated": "09-09", "done": False, "project": "Nova",
            "size": "", "sizeKey": "", "milestone": "", "order": None}
    item.update(over)
    return item


def contents(items=(), captures=(), details=None):
    return {"items": list(items), "captures": list(captures),
            "captureReplies": {}, "details": dict(details or {})}


def test_rows_come_back_with_no_file_to_parse():
    rows = open_rows_from_contents(contents([row(7)]), "issue")
    assert [r["number"] for r in rows] == [7]
    assert rows[0]["board"] == "issue"
    assert rows[0]["slug"] == "issue-7"


def test_a_closed_row_is_not_open():
    got = open_rows_from_contents(
        contents([row(7, statusKey="done", status="✅ Done"), row(8)]), "issue")
    assert [r["number"] for r in got] == [8]


def test_waiting_is_read_out_of_the_details_the_caller_handed_in():
    """The half that would silently vanish if `details` were dropped.

    A row is `waiting` because of text in `details`, which lives nowhere in
    `items`. Reading it from the same dict is what keeps `open_rows`'
    promise that a row and its thread cannot come from two different reads
    -- true for free on one string, and something a records reader has to
    actually do, since two queries against a live store can straddle a
    write.
    """
    body = "**Edvard, 09-09:** is this still needed?"
    got = open_rows_from_contents(contents([row(7)], details={7: body}), "issue")
    assert got[0]["waiting"] is True
    assert got[0]["replySlug"] is not None


def test_an_answered_thread_is_not_waiting():
    body = ("**Edvard, 09-09:** is this still needed?\n"
            "\n**Nova, 09-09:** yes, it is.")
    got = open_rows_from_contents(contents([row(7)], details={7: body}), "issue")
    assert got[0]["waiting"] is False
    assert got[0]["replySlug"] is None


def test_a_row_with_no_thread_is_not_waiting():
    got = open_rows_from_contents(contents([row(7)]), "issue")
    assert got[0]["waiting"] is False


def test_a_detail_body_for_a_row_that_is_not_on_the_board_moves_nothing():
    """A shape no board file can hold, so nothing here can be parsed into.

    `details` is keyed by row number and the store answers the two ranges
    separately, so a detail document can outlive its row. It must not mint
    a row, and it must not make some other row wait.
    """
    got = open_rows_from_contents(
        contents([row(7)], details={99: "**Edvard, 09-09:** hello?"}), "issue")
    assert [r["number"] for r in got] == [7]
    assert got[0]["waiting"] is False


def test_the_hand_set_seat_survives_the_handover():
    got = open_rows_from_contents(contents([row(7, order=3)]), "issue")
    assert got[0]["order"] == 3


def test_captures_come_back_with_no_file_to_parse():
    got = unboarded_captures_from_contents(
        contents(captures=["🔴 Immediately: fix the thing"]), "issue")
    assert [c["text"] for c in got] == ["fix the thing"]
    assert got[0]["priority"] == "🔴 Immediately"
    assert got[0]["board"] == "issue"


def test_a_finished_capture_is_not_unprocessed_but_still_holds_its_address():
    got = unboarded_captures_from_contents(
        contents(captures=["DONE (Cycle 3): old one", "the live one"]), "issue")
    assert [c["text"] for c in got] == ["the live one"]
    # The index is the address `/api/capture/comment` resolves against, so it
    # counts the finished bullet it skipped.
    assert got[0]["index"] == 1


def test_a_capture_already_closed_as_a_row_drops_out():
    items = [row(7, title="fix the thing", statusKey="done", status="✅ Done")]
    got = unboarded_captures_from_contents(
        contents(items, captures=["fix the thing"]), "issue")
    assert got == []


@pytest.mark.parametrize("markdown_door,contents_door", [
    (open_rows, open_rows_from_contents),
    (unboarded_captures, unboarded_captures_from_contents),
])
def test_the_markdown_door_is_only_a_door(markdown_door, contents_door):
    """The file-shaped function is the parse and nothing else.

    Pinned rather than assumed: the whole value of the split is that the
    rule stopped being duplicated, so a fix applied to one shape and not
    the other is the failure to catch. `parse_board` on an empty string is
    the contents of an empty board, and both must answer it the same way.
    """
    from agora_runner.nova_boards import parse_board
    assert markdown_door("", "issue") == contents_door(parse_board(""), "issue")
