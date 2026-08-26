"""`tools.board_row` -- boarding one of my own items adds a row and nothing else.

The failure this guards is the same one `test_roll_done_captures` guards
one file over: these documents are rendered through
`nova_boards.parse_board`, so an edit that lands the row in the wrong
table, or shifts a `### #N` write-up out from under its heading, breaks
the page with the string still looking fine. Every assertion below is on
`parse_board`'s output except where the point is that the file refused.
"""

import pytest

from agora_runner.nova_boards import add_row, parse_board, parse_notes
from tools.board_row import check, main

BOARD = """---
type: log
---

# Nova — Issues

## Entries

- 2026-08-26 (Cycle 480) — something I noticed and did not fix

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
    argv = ["--file", str(path), "--title", "A third thing",
            "--priority", "medium", "--dated", "08-26"]
    for flag, value in overrides.items():
        argv += ["--" + flag.replace("_", "-")] + ([value] if value is not None else [])
    return main(argv), path


def test_boards_the_next_number_at_the_top(tmp_path):
    code, path = _run(tmp_path)
    assert code == 0
    board = parse_board(path.read_text(encoding="utf-8"))
    numbers = [item["number"] for item in board["items"]]
    # 3, not 1: `next_row_number` takes the highest in use and adds one,
    # and the newest row goes directly under the header rule.
    assert numbers == [3, 2, 1]
    new = board["items"][0]
    assert new["title"] == "A third thing"
    assert new["status"] == "⚪ Backlog"
    assert new["priority"] == "🔵 Medium"
    assert new["updated"] == "08-26"


def test_the_write_up_lands_under_its_own_heading(tmp_path):
    body = tmp_path / "w.md"
    body.write_text("The reason this is worth tracking.", encoding="utf-8")
    code, path = _run(tmp_path, write_up_file=str(body))
    assert code == 0
    board = parse_board(path.read_text(encoding="utf-8"))
    assert board["details"][3] == "The reason this is worth tracking."
    # The two that were already there are untouched, heading and body.
    assert board["details"][2] == "Why the second thing matters."
    assert board["details"][1] == "Why the first thing mattered."


def test_the_capture_stream_is_untouched(tmp_path):
    code, path = _run(tmp_path)
    assert code == 0
    text = path.read_text(encoding="utf-8")
    assert "- 2026-08-26 (Cycle 480) — something I noticed and did not fix" in text
    assert "## Entries" in text


def test_a_blank_rating_is_refused(tmp_path):
    # `add_row` accepts "" for the owner's board; here it is the state that
    # means nobody has looked, so the tool refuses it (ideas.md #69).
    code, path = _run(tmp_path, priority="")
    assert code == 1
    assert parse_board(path.read_text(encoding="utf-8"))["items"] == \
        parse_board(BOARD)["items"]


def test_an_unknown_rating_is_refused(tmp_path):
    code, _ = _run(tmp_path, priority="urgent-ish")
    assert code == 1


def test_a_file_with_no_board_table_is_refused(tmp_path):
    code, path = _run(tmp_path, board="---\ntype: log\n---\n\n## Entries\n\n- a note\n")
    assert code == 1
    assert "## Board" not in path.read_text(encoding="utf-8")


def test_a_title_with_a_pipe_is_refused(tmp_path):
    code, _ = _run(tmp_path, title="a | b")
    assert code == 1


def test_dry_run_does_not_write(tmp_path):
    path = tmp_path / "issues.md"
    path.write_text(BOARD, encoding="utf-8")
    code = main(["--file", str(path), "--title", "A third thing",
                 "--priority", "high", "--dated", "08-26", "--dry-run"])
    assert code == 0
    assert path.read_text(encoding="utf-8") == BOARD


@pytest.mark.parametrize("damage,expected", [
    # A row that silently dropped off, and a write-up torn loose from its
    # heading -- the two ways an edit here breaks the page while the new
    # row still looks right.
    (lambda text: text.replace(
        "| [[#1 — The first thing\\|1]] | The first thing | ✅ Done | 08-25 |  |\n", ""),
     "fell off the board"),
    (lambda text: text.replace("Why the second thing matters.", "something else"),
     "the write-up for #2 changed"),
])
def test_check_catches_collateral_damage(damage, expected):
    after, _ = __import__(
        "agora_runner.nova_boards", fromlist=["add_row"]
    ).add_row(BOARD, "A third thing", "08-26", "medium")
    problems = check(BOARD, damage(after), 3, "A third thing")
    assert any(expected in problem for problem in problems)


def test_check_passes_on_a_clean_add():
    after, number = __import__(
        "agora_runner.nova_boards", fromlist=["add_row"]
    ).add_row(BOARD, "A third thing", "08-26", "medium")
    assert check(BOARD, after, number, "A third thing") == []


BOARD_WITH_TWO_NOTES = BOARD.replace(
    "- 2026-08-26 (Cycle 480) — something I noticed and did not fix",
    "- 2026-08-26 (Cycle 480) — something I noticed and did not fix\n"
    "- 2026-08-25 (Cycle 470) — an older thing I noticed",
)


def test_a_dated_with_a_pipe_is_refused(tmp_path):
    # `add_row` drops `dated` into a table cell unescaped, so a pipe shifts
    # every cell right of it and `parse_board` reads the tail of the date
    # as the rating -- with `check` reporting no problems, because the rows
    # that were already there are untouched. Reviewer finding.
    code, path = _run(tmp_path, dated="08-26 | 🔴 Immediately")
    assert code == 1
    assert parse_board(path.read_text(encoding="utf-8"))["items"] == \
        parse_board(BOARD)["items"]


def test_a_dated_with_a_newline_is_refused(tmp_path):
    code, path = _run(tmp_path, dated="08-26\n| evil | row | x | y |")
    assert code == 1
    assert len(parse_board(path.read_text(encoding="utf-8"))["items"]) == 2


def test_a_blank_dated_is_refused(tmp_path):
    code, _ = _run(tmp_path, dated="  ")
    assert code == 1


def test_a_write_up_carrying_a_heading_is_refused(tmp_path):
    # `_detail_spans` ends a `### #N` block at the next heading, so the
    # prose after it is dropped from the page while the raw file keeps it.
    body = tmp_path / "w.md"
    body.write_text(
        "Some real prose.\n\n## Not actually a new section\n\nMore prose that would spill out.",
        encoding="utf-8",
    )
    code, path = _run(tmp_path, write_up_file=str(body))
    assert code == 1
    assert parse_board(path.read_text(encoding="utf-8"))["details"] == \
        parse_board(BOARD)["details"]


def test_check_sees_the_entries_stream_being_damaged():
    # The guard this replaced asked `parse_board`'s `captures`, which is
    # `[]` on every real file here -- they open with an H1 before the first
    # bullet, so `capture_entries` stops on line one and the branch could
    # never fire. `parse_notes` is the instrument that answers.
    after, number = add_row(BOARD_WITH_TWO_NOTES, "A third thing", "08-26", "medium")
    assert check(BOARD_WITH_TWO_NOTES, after, number, "A third thing") == []
    eaten = after.replace("- 2026-08-25 (Cycle 470) — an older thing I noticed\n", "")
    problems = check(BOARD_WITH_TWO_NOTES, eaten, number, "A third thing")
    assert any("bullet stream changed: 2 -> 1" in problem for problem in problems)


def test_the_real_files_have_no_parse_board_captures():
    # Pins the reason the guard above uses `parse_notes`: a file shaped like
    # mine yields no `captures` at all, so a comparison on them is dead code.
    assert parse_board(BOARD_WITH_TWO_NOTES)["captures"] == []
    assert len(parse_notes(BOARD_WITH_TWO_NOTES)) == 2
