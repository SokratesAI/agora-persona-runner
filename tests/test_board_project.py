"""The `Project` column -- idea #92's phase 2, the sixth cell on `## Board`.

Every test here is pointed at one of the two ways a sixth column goes
wrong quietly. **A row can gain a cell the header does not have**, which
Obsidian renders by dropping the value while `parse_board` keeps
reporting it -- so the owner's screen and the page disagree and neither
looks broken. And **the two boards do not share a header**: `issues.md`
says `Item` and `ideas.md` says `Idea`, so a writer that stamps a full
six-cell header renames his column while adding mine. The first draft of
`_ensure_project_column` did exactly that and a diff against the live
`ideas.md` is what caught it, which is why the header text is asserted
here rather than only the width.
"""

from agora_runner.nova_boards import (
    DEFAULT_PROJECT,
    board_projects,
    parse_board,
    set_row_project,
)

BOARD = """---
type: board
---

## Board

| # | Idea | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#57 — More pages\\|57]] | More pages | 🟡 In progress | 08-11 | 🔵 Medium |
| [[#59 — Small pickings\\|59]] | Small pickings | ⚪ Backlog | 08-11 |

## Done

| # | Item | Landed | Where |
|---|------|--------|-------|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

## #57 — More pages

Body text I must not touch.
"""


def rows(markdown):
    return {item["number"]: item for item in parse_board(markdown)["items"]}


def test_a_board_with_no_project_column_reads_as_the_default():
    """The state both live files are in today: five columns, no migration."""
    parsed = rows(BOARD)
    assert parsed[57]["project"] == DEFAULT_PROJECT
    assert parsed[59]["project"] == DEFAULT_PROJECT
    # A `## Done` row has its own four-column shape and never carries one.
    assert parsed[51]["project"] == DEFAULT_PROJECT


def test_setting_a_project_adds_the_header_without_renaming_the_others():
    written = set_row_project(BOARD, 57, "Sokrates Post")
    header = [
        line for line in written.split("\n")
        if line.startswith("| # |")
    ][0]
    # `Idea`, not `Item`: this file's own second column survives.
    assert header == "| # | Idea | Status | Updated | Priority | Project |"
    assert rows(written)[57]["project"] == "Sokrates Post"
    # The `## Done` header is a different table and must not have moved.
    assert "| # | Item | Landed | Where |" in written
    assert "Body text I must not touch." in written


def test_a_short_row_is_padded_rather_than_refused():
    """#59 has four cells; the column was appended so it still parses."""
    written = set_row_project(BOARD, 59, "Agora")
    assert rows(written)[59]["project"] == "Agora"
    assert rows(written)[57]["project"] == DEFAULT_PROJECT


def test_writing_twice_does_not_add_the_column_twice():
    once = set_row_project(BOARD, 57, "Agora")
    twice = set_row_project(once, 59, "Nova")
    assert twice.count("| Project |") == 1
    assert len(twice.split("\n")) == len(BOARD.split("\n"))


def test_a_pipe_or_emphasis_is_refused():
    assert set_row_project(BOARD, 57, "a|b") is None
    assert set_row_project(BOARD, 57, "**bold") is None
    assert set_row_project(BOARD, 57, "two\nlines") is None
    assert set_row_project(BOARD, 57, "   ") is None
    assert set_row_project(BOARD, 57, "x" * 41) is None


def test_a_done_row_is_out_of_reach():
    """The two tables do not share a layout; a sixth cell there is past `Where`."""
    assert set_row_project(BOARD, 51, "Agora") is None


def test_a_missing_row_is_not_written():
    assert set_row_project(BOARD, 9999, "Agora") is None


def test_board_projects_reads_the_names_off_the_rows():
    written = set_row_project(set_row_project(BOARD, 57, "Sokrates Post"), 59, "Agora")
    names = board_projects(parse_board(written)["items"])
    # First-seen order, and `Nova` is there because the `## Done` row
    # defaults to it -- the list is what the rows say, not a constant.
    assert names == ["Sokrates Post", "Agora", "Nova"]
