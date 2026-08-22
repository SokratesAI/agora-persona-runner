"""`tools.roll_done_captures` -- finished captures leave, nothing else does.

The failure this guards is silent: these two files are Edvard's own, the
app renders them through `nova_boards.parse_board`, and a rewrite that
tears a `### #N` write-up out of its heading makes it stop appearing on
his phone with every test still green. So the assertions below are on
`parse_board`'s output rather than on the text, except where the point
is specifically that the text moved verbatim.
"""

from agora_runner.nova_boards import parse_board
from tools.roll_done_captures import PROCESSED_HEADING, check, plan, rewrite

BOARD = """---
type: board
---

- DONE (Cycle 4): shipped it — the header is bold now
- 🟠 High: the search bar closes my keyboard
- DONE (Cycle 9): answered on the row
-

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| #2 | The search bar closes my keyboard | ⚪ Backlog | 08-20 | 🟠 High |

# Details

### #2 — The search bar closes my keyboard
Every letter dismisses it.
"""


def test_only_the_done_bullets_are_planned_for_the_move():
    kept, moved = plan(BOARD)
    # The bare `-` is his cursor, not a capture; it rides along in `kept`
    # and `rewrite` is what re-lays it as the single trailing bullet.
    assert [b[0] for b in kept] == [
        "- 🟠 High: the search bar closes my keyboard",
        "-",
    ]
    assert len(moved) == 2


def test_the_finished_captures_leave_the_top_and_the_live_one_stays():
    after, moved = rewrite(BOARD)
    assert moved == 2
    assert parse_board(after)["captures"] == [
        "🟠 High: the search bar closes my keyboard"
    ]


def test_rows_and_write_ups_come_back_identical():
    after, _ = rewrite(BOARD)
    before, now = parse_board(BOARD), parse_board(after)
    assert before["items"] == now["items"]
    assert before["details"] == now["details"]


def test_every_moved_bullet_is_still_in_the_file_word_for_word():
    after, _ = rewrite(BOARD)
    head, _, archive = after.partition(PROCESSED_HEADING)
    assert "DONE (Cycle 4): shipped it — the header is bold now" in archive
    assert "DONE (Cycle 9): answered on the row" in archive
    assert "DONE (Cycle 4)" not in head


def test_the_cursor_bullet_survives_a_list_that_was_entirely_done():
    all_done = BOARD.replace("- 🟠 High: the search bar closes my keyboard\n", "")
    after, moved = rewrite(all_done)
    assert moved == 2
    assert parse_board(after)["captures"] == []
    assert "\n- \n" in after


def test_a_second_run_moves_nothing():
    once, _ = rewrite(BOARD)
    twice, moved = rewrite(once)
    assert moved == 0
    assert twice == once


def test_a_wrapped_capture_travels_with_its_own_bullet():
    wrapped = BOARD.replace(
        "- DONE (Cycle 9): answered on the row\n",
        "- DONE (Cycle 9): answered on the row\n  and here is the rest of it\n",
    )
    after, _ = rewrite(wrapped)
    head, _, archive = after.partition(PROCESSED_HEADING)
    assert "and here is the rest of it" in archive
    assert "and here is the rest of it" not in head


def test_a_file_with_no_finished_captures_is_returned_untouched():
    live = BOARD.replace("DONE (Cycle 4): ", "").replace("DONE (Cycle 9): ", "")
    after, moved = rewrite(live)
    assert moved == 0
    assert after == live


def test_check_catches_a_rewrite_that_dropped_a_write_up():
    after, moved = rewrite(BOARD)
    assert check(BOARD, after, moved) == []
    mangled = after.replace("### #2 — The search bar closes my keyboard\n", "")
    assert check(BOARD, mangled, moved)


def test_check_catches_a_rewrite_that_lost_a_live_capture():
    after, moved = rewrite(BOARD)
    mangled = after.replace("- 🟠 High: the search bar closes my keyboard\n", "")
    problems = check(BOARD, mangled, moved)
    assert any("expected" in p for p in problems)


def test_trailing_blank_lines_are_not_eaten():
    """Both real files end in ~100 blank lines. They are his, not mine."""
    padded = BOARD + "\n" * 40
    after, _ = rewrite(padded)
    assert len(after) - len(after.rstrip("\n")) == 41  # 40 + the file's own last newline
    plain, _ = rewrite(BOARD)
    assert len(plain) - len(plain.rstrip("\n")) == 1


def test_the_heading_guard_is_a_heading_not_a_substring():
    prose = BOARD.replace(
        "Every letter dismisses it.",
        "Every letter dismisses it. See the processed captures section.",
    )
    after, _ = rewrite(prose)
    assert after.count(PROCESSED_HEADING) == 1
    head, _, archive = after.partition("\n" + PROCESSED_HEADING)
    assert "DONE (Cycle 4)" in archive


def test_a_line_the_reader_ignores_is_not_dragged_along():
    """`_captures` only folds a non-blank line that is not `-`/`*`/`|`."""
    odd = BOARD.replace(
        "- DONE (Cycle 9): answered on the row\n",
        "- DONE (Cycle 9): answered on the row\n* a starred line\n",
    )
    after, _ = rewrite(odd)
    head, _, archive = after.partition("\n" + PROCESSED_HEADING)
    assert "* a starred line" in head
    assert "* a starred line" not in archive
