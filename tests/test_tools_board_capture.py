"""`tools.board_capture` -- a capture becomes a row and leaves the box.

The failure this guards is the one that made the tool necessary: boarding
a capture by hand is an add and a delete on one document, and a cycle
that does the add and not the delete leaves the item in his "not boarded
yet" box *and* on the board at the same time. So every test below asserts
on both halves -- `parse_board`'s rows and `capture_entries`'s bullets --
because either one alone passes on a half-done edit.

The other half is the span. A capture's replies are indented bullets that
`capture_entries` folds into the capture above them, so an off-by-one in
the lines being cut silently takes a neighbour's answer with it and both
documents still render.
"""

import pytest

from agora_runner.nova_boards import capture_entries, parse_board
from tools.board_capture import check, first_sentence, main, promote

BOARD = """---
type: board
---

- The first thing he typed. It goes on for a second sentence.
  - Cycle 500 answered this one.
- 🟠 High: A rated capture.
- DONE (Cycle 501): A capture a cycle already closed.
- 

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#2 — The second thing\\|2]] | The second thing | 🟡 In progress | 08-25 | 🟠 High |
| [[#1 — The first thing\\|1]] | The first thing | ✅ Done | 08-25 |  |

# Details

### #2 — The second thing

Why the second thing matters.

### #1 — The first thing

Why the first thing mattered.
"""


def _run(tmp_path, board=BOARD, **overrides):
    path = tmp_path / "issues.md"
    path.write_text(board, encoding="utf-8")
    argv = ["--file", str(path), "--index", "0", "--dated", "08-27"]
    for flag, value in overrides.items():
        argv += ["--" + flag.replace("_", "-")] + ([value] if value is not None else [])
    return main(argv), path


def test_the_capture_leaves_the_box_and_arrives_as_a_row(tmp_path):
    code, path = _run(tmp_path, priority="medium")
    assert code == 0
    after = path.read_text(encoding="utf-8")
    board = parse_board(after)
    assert [item["number"] for item in board["items"]] == [3, 2, 1]
    new = board["items"][0]
    # The title is his first sentence; the write-up is everything he wrote.
    assert new["title"] == "The first thing he typed."
    assert new["status"] == "⚪ Backlog"
    assert new["priority"] == "🔵 Medium"
    assert board["details"][3].startswith(
        "The first thing he typed. It goes on for a second sentence."
    )
    # And it is gone from the box, without taking its neighbours.
    texts = [text for _, _, text, _ in capture_entries(after)]
    assert texts == ["🟠 High: A rated capture.",
                     "DONE (Cycle 501): A capture a cycle already closed."]


def test_the_reply_written_under_it_rides_across(tmp_path):
    code, path = _run(tmp_path, priority="medium")
    assert code == 0
    assert "Cycle 500 answered this one." in parse_board(
        path.read_text(encoding="utf-8")
    )["details"][3]


def test_the_empty_cursor_bullet_stays(tmp_path):
    """He types into it. `capture_entries` ignores it and so must the cut."""
    code, path = _run(tmp_path, priority="medium")
    assert code == 0
    head = path.read_text(encoding="utf-8").split("## Board")[0]
    assert "\n- \n" in head


def test_a_rated_capture_keeps_its_own_rating(tmp_path):
    code, path = _run(tmp_path, index="1")
    assert code == 0
    board = parse_board(path.read_text(encoding="utf-8"))
    assert board["items"][0]["priority"] == "🟠 High"
    # The prefix is a cell now, so it is off the title and off the write-up.
    assert board["items"][0]["title"] == "A rated capture."
    assert not board["details"][3].startswith("🟠")


def test_an_explicit_priority_beats_the_bullets_own(tmp_path):
    code, path = _run(tmp_path, index="1", priority="low")
    assert code == 0
    assert parse_board(path.read_text(encoding="utf-8"))["items"][0]["priority"] == "⚪ Low"


def test_a_done_marker_lands_done_not_backlog(tmp_path):
    """His complaint in as many words: finished items in the unstaged box."""
    code, path = _run(tmp_path, index="2")
    assert code == 0
    board = parse_board(path.read_text(encoding="utf-8"))
    assert board["items"][0]["status"] == "✅ Done"
    assert board["items"][0]["title"] == "A capture a cycle already closed."
    # A closed row takes no rating -- `set_row_status` clears it.
    assert board["items"][0]["priority"] == ""


@pytest.mark.parametrize(
    "status,cell",
    [("in-progress", "🟡 In progress"),
     ("blocked-on-edvard", "⏸ Blocked on Edvard"),
     ("done", "✅ Done")],
)
def test_every_status_reaches_the_cell(tmp_path, status, cell):
    code, path = _run(tmp_path, priority="medium", status=status)
    assert code == 0
    assert parse_board(path.read_text(encoding="utf-8"))["items"][0]["status"] == cell


def test_outdated_is_not_a_status_a_cycle_may_set(tmp_path):
    """He deletes those himself; nothing he typed this week arrives written off."""
    with pytest.raises(SystemExit):
        _run(tmp_path, priority="medium", status="outdated")


def test_it_refuses_an_index_that_is_not_there(tmp_path, capsys):
    code, path = _run(tmp_path, index="9", priority="medium")
    assert code == 1
    assert "no capture at index 9" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == BOARD


def test_it_refuses_a_pipe_in_the_date(tmp_path, capsys):
    """A stray `|` shifts every column right of it -- `parse_board` then
    reads the tail of the date as the rating."""
    code, path = _run(tmp_path, priority="medium", dated="08|27")
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_it_refuses_a_rating_that_is_not_one(tmp_path, capsys):
    code, path = _run(tmp_path, priority="urgentish")
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_dry_run_writes_nothing(tmp_path):
    code, path = _run(tmp_path, priority="medium", dry_run=None)
    assert code == 0
    assert path.read_text(encoding="utf-8") == BOARD


def test_first_sentence_keeps_a_long_one_whole(tmp_path):
    """No character count: a truncated title reads as a different item."""
    long = "A" * 300 + ". And then a second sentence."
    assert first_sentence(long) == "A" * 300 + "."
    assert first_sentence("No full stop here") == "No full stop here"
    assert first_sentence("Is it? Yes.") == "Is it?"


def test_check_catches_a_capture_lost_beside_the_one_boarded():
    """The off-by-one this guard exists for, forced by hand."""
    after, number, title = promote(BOARD, 0, "medium", "backlog", "08-27")
    assert not check(BOARD, after, number, title,
                     "The first thing he typed. It goes on for a second sentence.")
    damaged = after.replace("- 🟠 High: A rated capture.\n", "")
    problems = check(BOARD, damaged, number, title,
                     "The first thing he typed. It goes on for a second sentence.")
    assert any("capture count went" in p for p in problems)


def test_check_catches_a_reply_taken_with_the_cut():
    after, number, title = promote(BOARD, 1, "medium", "backlog", "08-27")
    damaged = after.replace("  - Cycle 500 answered this one.\n", "")
    problems = check(BOARD, damaged, number, title, "🟠 High: A rated capture.")
    assert any("a capture changed underneath" in p for p in problems)


def test_check_catches_a_row_that_changed_underneath():
    after, number, title = promote(BOARD, 0, "medium", "backlog", "08-27")
    damaged = after.replace("| The second thing | 🟡 In progress |",
                            "| The second thing | ✅ Done |")
    problems = check(BOARD, damaged, number, title,
                     "The first thing he typed. It goes on for a second sentence.")
    assert any("#2 changed underneath" in p for p in problems)
