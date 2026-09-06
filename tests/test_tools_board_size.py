"""`tools.board_size` -- estimating one row moves exactly that cell.

`tests/test_board_size.py` one file over covers `set_row_size` itself. What
is tested here is the CLI around it, and specifically `check`, which
re-parses the whole document and refuses the write unless the size named is
the only thing that moved.
"""

import pytest

from agora_runner.nova_boards import parse_board, parse_notes
from tools.board_size import check, main, resolve_size

BOARD = """# Nova — Ideas

## Entries

- 2026-09-06 (Cycle 1090) — a bullet nothing here may touch

## Board

| # | Idea | Status | Updated | Priority | Project |
|---|---|---|---|---|---|
| [[#7 — Open one\\|7]] | Open one | 🟡 In progress | 09-05 | 🟠 High | Marcus |
| [[#8 — Closed one\\|8]] | Closed one | ✅ Done | 09-04 |  | Nova |

# Details

### #7 — Open one

Body text I must not touch.
"""


def _board(tmp_path, text=BOARD):
    path = tmp_path / "ideas.md"
    path.write_text(text, encoding="utf-8")
    return path


def rows(markdown):
    return {item["number"]: item for item in parse_board(markdown)["items"]}


def test_it_writes_the_size_and_says_what_moved(tmp_path, capsys):
    path = _board(tmp_path)
    assert main(["--file", str(path), "--number", "7", "--size", "l"]) == 0
    text = path.read_text(encoding="utf-8")
    assert rows(text)[7]["size"] == "L"
    assert "(unsized) -> L" in capsys.readouterr().out


def test_a_dry_run_writes_nothing(tmp_path):
    path = _board(tmp_path)
    assert main(["--file", str(path), "--number", "7", "--size", "l", "--dry-run"]) == 0
    assert path.read_text(encoding="utf-8") == BOARD


def test_a_note_lands_in_the_write_up_and_stamps_the_date(tmp_path):
    """The one field `check` forgives, and only to the date given.

    `append_detail_note` moves `Updated` on the target row, which is the
    ordinary case here -- a row is re-estimated *because* a cycle learned
    something about it -- so `check` has to allow that move without opening
    a hole for any other date.
    """
    path = _board(tmp_path)
    code = main([
        "--file", str(path), "--number", "7", "--size", "xl",
        "--dated", "09-06", "--note", "bigger than it looked", "--cycle", "1090",
    ])
    assert code == 0
    text = path.read_text(encoding="utf-8")
    assert rows(text)[7]["size"] == "XL"
    assert rows(text)[7]["updated"] == "09-06"
    detail = parse_board(text)["details"][7]
    assert detail.startswith("Body text I must not touch.")
    assert "bigger than it looked" in detail
    # The bullet stream is his and is never touched by a re-estimate.
    assert [n["text"] for n in parse_notes(text)] == [n["text"] for n in parse_notes(BOARD)]


@pytest.mark.parametrize("size", ["", "huge", "   "])
def test_a_size_that_is_not_one_of_the_four_is_refused(tmp_path, size, capsys):
    """The blank is in here on purpose.

    `set_row_size` accepts `""` because clearing the cell has to stay
    reachable; a *caller* that passed nothing meant something else, and
    letting it through would silently unsize a row it was trying to size.
    """
    path = _board(tmp_path)
    assert main(["--file", str(path), "--number", "7", "--size", size]) == 1
    assert path.read_text(encoding="utf-8") == BOARD
    assert "is not a size" in capsys.readouterr().err


def test_a_closed_row_is_refused_by_the_cli_too(tmp_path, capsys):
    path = _board(tmp_path)
    assert main(["--file", str(path), "--number", "8", "--size", "s"]) == 1
    assert path.read_text(encoding="utf-8") == BOARD
    assert "not an open row" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["a | b", "a\nb", "   "])
def test_a_note_that_would_break_the_table_is_refused(tmp_path, bad, capsys):
    path = _board(tmp_path)
    assert main([
        "--file", str(path), "--number", "7", "--size", "s",
        "--dated", "09-06", "--note", bad,
    ]) == 1
    assert path.read_text(encoding="utf-8") == BOARD
    assert "--note" in capsys.readouterr().err


def test_a_note_without_a_date_is_refused(tmp_path, capsys):
    """`append_detail_note` takes a date rather than reading a clock, and a
    module that formats its own would format it in UTC."""
    path = _board(tmp_path)
    assert main([
        "--file", str(path), "--number", "7", "--size", "s", "--note", "why",
    ]) == 1
    assert "needs --dated" in capsys.readouterr().err


def test_check_catches_a_second_row_moving_underneath_the_write():
    """The assertion `check` exists for, driven by a hand-made 'after'.

    A real `set_row_size` cannot produce this, which is the point: `check`
    is the guard against the write path being wrong, so testing it through
    the write path only would make it assert its own implementation.
    """
    after = BOARD.replace("| Open one | 🟡 In progress | 09-05 | 🟠 High | Marcus |",
                          "| Open one | 🟡 In progress | 09-05 | 🟠 High | Marcus | S |")
    after = after.replace("| Closed one | ✅ Done | 09-04 |", "| Renamed | ✅ Done | 09-04 |")
    problems = check(BOARD, after, 7, "S", noted=False)
    assert any("#8" in problem for problem in problems), problems


def test_check_catches_the_wrong_size_landing():
    after = BOARD.replace("| 🟠 High | Marcus |", "| 🟠 High | Marcus | M |")
    problems = check(BOARD, after, 7, "S", noted=False)
    assert any("asked for 'S'" in problem for problem in problems), problems


def test_check_refuses_a_date_the_caller_did_not_ask_for():
    """The forgiveness is to exactly `dated`, not to any date.

    A plain exclusion of `updated` would let a write stamp whatever it
    liked on the target row, which is the hole this asserts is shut.
    """
    after = BOARD.replace("| Open one | 🟡 In progress | 09-05 | 🟠 High | Marcus |",
                          "| Open one | 🟡 In progress | 01-01 | 🟠 High | Marcus | S |")
    problems = check(BOARD, after, 7, "S", noted=True, dated="09-06")
    assert any("came back updated" in problem for problem in problems), problems


def test_resolve_size_accepts_every_spelling_but_the_blank():
    assert resolve_size("xl") == "XL"
    assert resolve_size("XL") == "XL"
    assert resolve_size("x-large") == "XL"
    assert resolve_size("") is None
    assert resolve_size("enormous") is None
