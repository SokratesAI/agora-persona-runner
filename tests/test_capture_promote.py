"""Promoting one of the owner's captures into a numbered board row.

His capture, 2026-08-26: *"they do no seem to just stay forever in the
'not boarded yet' box as unrated. Thats not what the box is for. This a
re ideas you have not seen before and you pick it up, prioritised them
and make them as their own nice item like the rest."*

The thing worth pinning is not that a row appears -- it is that his own
text survives the move whole, in one write, and that a second tap cannot
board the same bullet twice.
"""

import agora_runner.nova_capture as nc
from agora_runner.nova_boards import (
    add_row, capture_entries, next_row_number, parse_board)

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


def _fake_vault(monkeypatch, markdown):
    """A one-file vault whose contents the test can read back."""
    store = {"text": markdown, "writes": 0}
    monkeypatch.setattr(nc, "vault_read_path_rev", lambda path: (store["text"], "rev-1"))

    def write(path, text, if_rev=None):
        store["writes"] += 1
        store["text"] = text
        return "written"

    monkeypatch.setattr(nc, "vault_write_path", write)
    return store


def test_next_number_clears_the_done_table_too():
    """#12 is finished and its number is still spoken for."""
    assert next_row_number(BOARD) == 13


def test_promote_writes_the_row_and_takes_the_bullet(monkeypatch):
    store = _fake_vault(monkeypatch, BOARD)
    original = capture_entries(BOARD)[0][2]

    ok, message = nc.promote_capture("issues", 0, original)

    assert ok, message
    assert message == "boarded as #13"
    # One file, one revision, one write -- the whole reason this is not
    # shaped like convert_capture.
    assert store["writes"] == 1

    after = store["text"]
    rows = {row["number"]: row for row in parse_board(after)["items"]}
    assert 13 in rows
    assert rows[13]["priority"] == "🟠 High", "his own rating rides across"
    assert rows[13]["status"] == "⚪ Backlog"
    # The title is his first sentence, not the whole paragraph.
    assert rows[13]["title"] == "The menu runs off the screen."
    # ...and none of his text is lost to make that true.
    assert "It has thirteen links and no scrolling at all." in after
    assert "Cycle 400, 06:16 — the drawer scrolls now." in after

    left = [text for _, _, text, _ in capture_entries(after)]
    assert original not in left, "the bullet left the not-boarded box"
    assert "A second, unrelated capture." in left


def test_promote_overrides_the_rating_when_asked(monkeypatch):
    store = _fake_vault(monkeypatch, BOARD)
    original = capture_entries(BOARD)[1][2]

    ok, message = nc.promote_capture("issues", 1, original, "immediate")

    assert ok, message
    rows = {row["number"]: row for row in parse_board(store["text"])["items"]}
    assert rows[13]["priority"] == "🔴 Immediately"


def test_a_stale_address_is_refused_and_writes_nothing(monkeypatch):
    """A second tap, or a bullet something else boarded first."""
    store = _fake_vault(monkeypatch, BOARD)

    ok, message = nc.promote_capture("issues", 0, "text he never typed")

    assert not ok
    assert nc.STALE_CAPTURE in message
    assert store["writes"] == 0
    assert store["text"] == BOARD


def test_the_index_and_the_text_must_agree(monkeypatch):
    """Capture 1's text at capture 0's position addresses nothing."""
    store = _fake_vault(monkeypatch, BOARD)
    second = capture_entries(BOARD)[1][2]

    ok, message = nc.promote_capture("issues", 0, second)

    assert not ok
    assert store["writes"] == 0


def test_an_unknown_priority_is_refused(monkeypatch):
    store = _fake_vault(monkeypatch, BOARD)
    original = capture_entries(BOARD)[0][2]

    ok, message = nc.promote_capture("issues", 0, original, "urgent-ish")

    assert not ok
    assert "priority" in message
    assert store["writes"] == 0


def test_a_pipe_in_his_first_sentence_does_not_break_the_table(monkeypatch):
    """A table cell ends at a pipe, so the row is refused, not folded."""
    board = BOARD.replace(
        "The menu runs off the screen.", "The a|b split is wrong.")
    store = _fake_vault(monkeypatch, board)
    original = capture_entries(board)[0][2]

    ok, _ = nc.promote_capture("issues", 0, original)

    assert not ok
    assert store["writes"] == 0


def test_add_row_refuses_a_file_with_no_board_table():
    assert add_row("---\ntype: board\n---\n\n- a capture\n", "T", "08-26") == (None, None)
