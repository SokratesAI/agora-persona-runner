"""Promoting one of the owner's captures into a numbered board row.

His capture, 2026-08-26: *"they do no seem to just stay forever in the
'not boarded yet' box as unrated. Thats not what the box is for. This a
re ideas you have not seen before and you pick it up, prioritised them
and make them as their own nice item like the rest."*

The thing worth pinning is not that a row appears -- it is that his own
text survives the move whole, and that a second tap cannot board the same
bullet twice. Since #203 both writes land in the record store: the row
through `board_write.add_row`, then the bullet's own document is deleted.
"""

import agora_runner.nova_capture as nc
from agora_runner import board_records
from agora_runner.board_store import CaptureConflict
from agora_runner.nova_boards import add_row, next_row_number
from tests.test_board_records import writable

BOARD = """---
type: board
---

- 🟠 High: The menu runs off the screen. It has thirteen links and no scrolling at all.
  - Cycle 400, 06:16 — the drawer scrolls now.
- A second, unrelated capture.

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#7 — An older row\\|7]] | An older row | ⚪ Backlog | 08-20 | 🔵 Medium |

## Done

| # | Item | Landed | Where |
|---|------|--------|---------|
| [[#12 — A finished row\\|12]] | A finished row | 08-19 | runner#1 |

# Details

### #7 — An older row

Something I wrote earlier.
"""

FIRST = ("🟠 High: The menu runs off the screen. It has thirteen links and "
         "no scrolling at all.")
SECOND = "A second, unrelated capture."


def _store(markdown=BOARD):
    return writable(board="issue", markdown=markdown)[1]


def _writes(store):
    return [call for call in store.calls if call[0] != "write_layout"]


def _board(store):
    return board_records.contents("issue", store=store)


def test_next_number_clears_the_done_table_too():
    """#12 is finished and its number is still spoken for."""
    assert next_row_number(BOARD) == 13


def test_the_fixture_holds_both_captures_and_the_reply():
    board = _board(_store())
    assert board["captures"] == [FIRST, SECOND]
    assert board["captureReplies"][0] == ["Cycle 400, 06:16 — the drawer scrolls now."]


def test_promote_writes_the_row_and_takes_the_bullet():
    store = _store()

    ok, message = nc.promote_capture("issues", 0, FIRST, store=store)

    assert ok, message
    assert message == "boarded as #13"
    after = _board(store)
    rows = {row["number"]: row for row in after["items"]}
    assert 13 in rows
    assert rows[13]["priority"] == "🟠 High", "his own rating rides across"
    assert rows[13]["status"] == "⚪ Backlog"
    # The title is his first sentence, not the whole paragraph.
    assert rows[13]["title"] == "The menu runs off the screen."
    # ...and none of his text is lost to make that true.
    assert "It has thirteen links and no scrolling at all." in after["details"][13]
    assert "the drawer scrolls now." in after["details"][13], \
        "the reply under his bullet rides across as a note"
    assert after["captures"] == [SECOND], "the bullet left the not-boarded box"


def test_the_row_is_written_before_the_bullet_is_deleted():
    """Row first: a failure between the two leaves his text in both places,
    never in neither."""
    store = _store()
    nc.promote_capture("issues", 0, FIRST, store=store)
    kinds = [call[0] for call in _writes(store)]
    assert "delete_capture" in kinds and "write_row" in kinds
    assert kinds.index("write_row") < kinds.index("delete_capture")


def test_promote_overrides_the_rating_when_asked():
    store = _store()

    ok, message = nc.promote_capture("issues", 1, SECOND, "immediate", store=store)

    assert ok, message
    rows = {row["number"]: row for row in _board(store)["items"]}
    assert rows[13]["priority"] == "🔴 Immediately"
    assert _board(store)["captures"] == [FIRST]


def test_a_stale_address_is_refused_and_writes_nothing():
    """A bullet something else boarded first, or words he never typed."""
    store = _store()

    ok, message = nc.promote_capture("issues", 0, "text he never typed", store=store)

    assert not ok
    assert nc.STALE_CAPTURE in message
    assert _writes(store) == []


def test_the_index_and_the_text_must_agree():
    """Capture 1's text at capture 0's position addresses nothing."""
    store = _store()

    ok, message = nc.promote_capture("issues", 0, SECOND, store=store)

    assert not ok
    assert nc.STALE_CAPTURE in message
    assert _writes(store) == []


def test_a_second_tap_does_not_board_the_bullet_twice():
    store = _store()
    assert nc.promote_capture("issues", 0, FIRST, store=store)[0] is True

    ok, message = nc.promote_capture("issues", 0, FIRST, store=store)

    assert not ok
    assert nc.STALE_CAPTURE in message
    assert [row["number"] for row in _board(store)["items"]].count(13) == 1
    assert 14 not in [row["number"] for row in _board(store)["items"]]


def test_an_unknown_priority_is_refused():
    store = _store()

    ok, message = nc.promote_capture("issues", 0, FIRST, "urgent-ish", store=store)

    assert not ok
    assert "priority" in message
    assert _writes(store) == []


def test_a_pipe_in_his_first_sentence_does_not_break_the_table():
    """A table cell ends at a pipe, so the row is refused, not folded."""
    board = BOARD.replace(
        "The menu runs off the screen.", "The a|b split is wrong.")
    store = _store(board)
    original = _board(store)["captures"][0]

    ok, message = nc.promote_capture("issues", 0, original, store=store)

    assert not ok
    assert "could not board" in message
    assert _writes(store) == []
    assert _board(store)["captures"][0] == original


def test_a_bullet_that_cannot_be_deleted_is_reported_not_hidden():
    """The one half-done state this order can leave: the row landed, the
    bullet is still in the box, and the message sends him to the box."""
    store = _store()

    def conflict(doc):
        raise CaptureConflict("a reply landed on this bullet meanwhile")

    store.delete_capture = conflict
    ok, message = nc.promote_capture("issues", 0, FIRST, store=store)

    assert not ok
    assert "boarded as #13" in message
    assert "check the box" in message
    assert 13 in [row["number"] for row in _board(store)["items"]]
    assert _board(store)["captures"] == [FIRST, SECOND]


def test_notes_has_no_board_to_promote_onto():
    store = _store()
    ok, message = nc.promote_capture("notes", 0, FIRST, store=store)
    assert not ok
    assert "unknown target" in message
    assert _writes(store) == []


def test_add_row_refuses_a_file_with_no_board_table():
    assert add_row("---\ntype: board\n---\n\n- a capture\n", "T", "08-26") == (None, None)
