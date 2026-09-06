"""The `board_milestone` CLI: what it refuses, and that `check` is real.

Same shape as `test_tools_board_size`, and here for the same reason: this
writes into the owner's own file, so the interesting tests are the ones
where it must not.
"""

from tools import board_milestone

BOARD = """## Board

| # | Idea | Status | Updated | Priority | Project | Size |
|---|---|---|---|---|---|---|
| [[#7 — Open one\\|7]] | Open one | 🟡 In progress | 09-05 | 🟠 High | Marcus | M |
| [[#8 — Closed one\\|8]] | Closed one | ✅ Done | 09-04 |  | Nova |

## Done

| # | Item | Landed | Where |
|---|---|---|---|

# Details

### #7 — Open one

Body text.
"""


def write(tmp_path, text=BOARD):
    path = tmp_path / "ideas.md"
    path.write_text(text, encoding="utf-8")
    return path


def run(path, *args):
    return board_milestone.main(["--file", str(path), *args])


def test_it_writes_the_cell(tmp_path):
    path = write(tmp_path)
    assert run(path, "--number", "7", "--milestone", "Goal setting") == 0
    assert "| Goal setting |" in path.read_text(encoding="utf-8")


def test_a_dry_run_leaves_the_file_alone(tmp_path):
    path = write(tmp_path)
    assert run(path, "--number", "7", "--milestone", "Goal setting",
               "--dry-run") == 0
    assert path.read_text(encoding="utf-8") == BOARD


def test_a_pipe_is_refused_and_nothing_is_written(tmp_path):
    path = write(tmp_path)
    assert run(path, "--number", "7", "--milestone", "a | b") == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_a_closed_row_is_refused(tmp_path):
    path = write(tmp_path)
    assert run(path, "--number", "8", "--milestone", "Goal setting") == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_a_missing_row_is_refused(tmp_path):
    path = write(tmp_path)
    assert run(path, "--number", "404", "--milestone", "x") == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_a_note_without_a_date_is_refused(tmp_path):
    """`append_detail_note` stamps `Updated` with the date it is given, and
    a module that formats its own would format it in UTC."""
    path = write(tmp_path)
    assert run(path, "--number", "7", "--milestone", "x", "--note", "why") == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_a_note_lands_in_the_write_up_and_moves_only_that_date(tmp_path):
    path = write(tmp_path)
    assert run(path, "--number", "7", "--milestone", "Goal setting",
               "--dated", "09-06", "--note", "it belongs here",
               "--cycle", "1094") == 0
    text = path.read_text(encoding="utf-8")
    assert "it belongs here" in text
    assert "Body text." in text
    assert "| 09-06 |" in text


def test_clearing_it_back_to_ungrouped_is_reachable(tmp_path):
    path = write(tmp_path)
    assert run(path, "--number", "7", "--milestone", "Goal setting") == 0
    assert run(path, "--number", "7", "--milestone", "") == 0
    from agora_runner.nova_boards import parse_board
    rows = {i["number"]: i for i in parse_board(
        path.read_text(encoding="utf-8"))["items"]}
    assert rows[7]["milestone"] == ""
    assert rows[7]["size"] == "M"
