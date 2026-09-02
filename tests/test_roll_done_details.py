"""`tools.roll_done_details` moves a finished row's write-up, and only that."""

import pytest

from agora_runner.nova_boards import parse_board, parse_notes
from tools import roll_done_details
from tools.rolling import RollError

LIVE = """\
---
type: log
---

# Nova — Issues

## Entries

- 2026-09-02 (Cycle 800) — a capture
- 2026-09-01 (Cycle 700) — an older capture

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#3 — Third\\|3]] | Third | ✅ Done | 09-02 |  |
| [[#2 — Second\\|2]] | Second | ⚪ Backlog | 09-01 | 🟠 High |
| [[#1 — First\\|1]] | First | ✅ Done | 08-30 |  |

# Details

### #3 — Third

Third body.

### #2 — Second

Second body.

### #1 — First

First body.
"""

ARCHIVE = """\
---
type: log
---

# Nova — Issues Archive

## Entries

- 2026-08-01 (Cycle 100) — an archived capture
"""


def test_it_moves_the_done_write_ups_and_leaves_the_open_one():
    new_live, new_archive, blocks = roll_done_details.plan(LIVE, ARCHIVE)
    assert sorted(blocks) == [1, 3]
    assert sorted(parse_board(new_live)["details"]) == [2]
    assert sorted(parse_board(new_archive)["details"]) == [1, 3]
    assert parse_board(new_archive)["details"][3] == "Third body."


def test_the_rows_stay_on_the_live_board():
    """The write-up moves; the row does not. An archived row is still a row."""
    new_live, new_archive, _ = roll_done_details.plan(LIVE, ARCHIVE)
    assert parse_board(new_live)["items"] == parse_board(LIVE)["items"]
    assert parse_board(new_archive)["items"] == []


def test_the_page_draws_exactly_what_it_drew_before():
    """The merge `board_payload` does, asserted end to end rather than assumed."""
    new_live, new_archive, _ = roll_done_details.plan(LIVE, ARCHIVE)
    assert roll_done_details._merged(new_live, new_archive) == roll_done_details._merged(
        LIVE, ARCHIVE
    )


def test_without_the_move_the_archive_would_draw_nothing():
    """Asserts the precondition, so the test above cannot pass on a no-op.

    A merge test passes whether or not anything moved if the live file
    still carries every body. This is the guard that makes the previous
    test mean something -- copied from
    `test_without_the_archive_that_row_would_draw_an_empty_body` on #652.
    """
    assert parse_board(ARCHIVE)["details"] == {}
    new_live, _, _ = roll_done_details.plan(LIVE, ARCHIVE)
    assert parse_board(new_live)["details"] == {2: "Second body."}


def test_the_capture_bullets_are_untouched_on_both_sides():
    new_live, new_archive, _ = roll_done_details.plan(LIVE, ARCHIVE)
    assert parse_notes(new_live) == parse_notes(LIVE)
    assert parse_notes(new_archive) == parse_notes(ARCHIVE)


def test_a_second_run_is_a_no_op():
    new_live, new_archive, _ = roll_done_details.plan(LIVE, ARCHIVE)
    again_live, again_archive, blocks = roll_done_details.plan(new_live, new_archive)
    assert blocks == {}
    assert (again_live, again_archive) == (new_live, new_archive)


def test_a_number_the_archive_already_carries_is_replaced_not_duplicated():
    """`_detail_spans` keeps the first block for a repeated number.

    Two blocks for one number would mean the page drew whichever sat
    higher, which is a coin flip rather than redundancy.
    """
    stale = ARCHIVE.rstrip() + "\n\n# Details\n\n### #3 — Third\n\nStale body.\n"
    new_live, new_archive, blocks = roll_done_details.plan(LIVE, stale)
    assert new_archive.count("### #3 — Third") == 1
    assert parse_board(new_archive)["details"][3] == "Third body."


def test_it_refuses_when_a_write_up_would_stop_rendering():
    """The render guard, asked directly: a body dropped on the way over."""
    new_live, _ = roll_done_details._cut_blocks(LIVE, [1, 3])
    with pytest.raises(RollError, match="write-up"):
        roll_done_details._check(LIVE, ARCHIVE, new_live, ARCHIVE, {1: "", 3: ""})


def test_it_refuses_when_a_row_would_move_with_its_write_up():
    """Rows are the thing this must never touch, so the guard is asserted."""
    new_live, blocks = roll_done_details._cut_blocks(LIVE, [1, 3])
    without_row = new_live.replace("| [[#1 — First\\|1]] | First | ✅ Done | 08-30 |  |\n", "")
    archive = roll_done_details._splice_into_archive(ARCHIVE, blocks)
    assert parse_board(without_row)["items"] != parse_board(LIVE)["items"]
    with pytest.raises(RollError, match="board rows changed"):
        roll_done_details._check(LIVE, ARCHIVE, without_row, archive, blocks)


def test_it_refuses_when_a_capture_bullet_would_travel():
    new_live, blocks = roll_done_details._cut_blocks(LIVE, [1, 3])
    archive = roll_done_details._splice_into_archive(ARCHIVE, blocks)
    lost = new_live.replace("- 2026-09-01 (Cycle 700) — an older capture\n", "")
    with pytest.raises(RollError, match="capture bullets changed"):
        roll_done_details._check(LIVE, ARCHIVE, lost, archive, blocks)
