"""The `Milestone` cell: `set_row_milestone` and the parser round-trip.

Milestone M4 of idea #260's picking redesign. A milestone is a named group
of tasks inside one project, and `nova_next.milestone_ranks` is what reads
this cell -- so what has to hold here is that an ungrouped row stays
visibly ungrouped rather than acquiring a default, that a name which would
break the owner's table is refused rather than written, and that setting
one touches exactly one cell of his file.

Assertions are on `parse_board` output wherever the question is "what does
the system now believe", the same reason `test_board_size` gives: a shifted
cell is still a well-formed table and reads as plausible right up until the
page draws a size in the milestone column.
"""

from agora_runner.nova_boards import parse_board, set_row_milestone, set_row_size

BOARD = """# Nova — Ideas

## Entries

- 2026-09-06 (Cycle 1094) — a bullet nothing here may touch

## Board

| # | Idea | Status | Updated | Priority | Project | Size |
|---|---|---|---|---|---|---|
| [[#7 — Open one\\|7]] | Open one | 🟡 In progress | 09-05 | 🟠 High | Marcus | M |
| [[#8 — Closed one\\|8]] | Closed one | ✅ Done | 09-04 |  | Nova |
| [[#9 — Narrow one\\|9]] | Narrow one | ⚪ Backlog | 09-03 |

## Done

| # | Item | Landed | Where |
|---|---|---|---|
| [[#3 — Shipped\\|3]] | Shipped | 09-01 | #700 |

# Details

### #7 — Open one

Body text I must not touch.
"""


def rows(markdown):
    return {item["number"]: item for item in parse_board(markdown)["items"]}


def test_an_ungrouped_row_stays_ungrouped_and_is_not_defaulted():
    """Same call as `Size`, and the opposite one to `Project`.

    A blank `Project` cell means `DEFAULT_PROJECT` because every row on
    both live boards predates that column. A blank milestone cannot take
    the same treatment: there is no name that would be right, and inventing
    one would put every row of a project into a single phantom milestone
    and make the ranking below it meaningless.
    """
    assert rows(BOARD)[7]["milestone"] == ""
    assert rows(BOARD)[9]["milestone"] == ""


def test_setting_a_milestone_moves_that_cell_and_nothing_else():
    after = set_row_milestone(BOARD, 7, "Picking redesign")
    assert after is not None
    was, now = rows(BOARD)[7], rows(after)[7]
    assert now["milestone"] == "Picking redesign"
    assert {k: v for k, v in was.items() if k != "milestone"} == {
        k: v for k, v in now.items() if k != "milestone"
    }
    # Every other row, and the write-up, untouched.
    assert rows(after)[8] == rows(BOARD)[8]
    assert rows(after)[9] == rows(BOARD)[9]
    assert parse_board(after)["details"] == parse_board(BOARD)["details"]


def test_a_narrow_row_is_padded_rather_than_refused():
    """#9 predates `Priority`, `Project` and `Size` all three."""
    after = set_row_milestone(BOARD, 9, "Backup")
    assert after is not None
    now = rows(after)[9]
    assert now["milestone"] == "Backup"
    # The cells it grew are blank, not invented.
    assert now["size"] == ""
    assert now["priority"] == rows(BOARD)[9]["priority"]
    # `Project` keeps its own documented default rather than a blank.
    assert now["project"] == rows(BOARD)[9]["project"]


def test_the_header_grows_a_milestone_column():
    after = set_row_milestone(BOARD, 7, "Picking redesign")
    header = [
        line for line in after.split("\n")
        if line.startswith("| # |")
    ][0]
    assert header.rstrip().endswith("| Milestone | Order |")
    # The owner's own second column is never renamed.
    assert "| Idea |" in header


def test_a_pipe_is_refused_because_it_would_widen_the_row():
    """The refusal that matters: this text lands in his file verbatim."""
    assert set_row_milestone(BOARD, 7, "Picking | redesign") is None
    # And the file is genuinely untouched, not merely reported as refused.
    assert rows(BOARD)[7]["milestone"] == ""


def test_a_closed_row_is_refused():
    """Same boundary `set_row_size` draws, for the same reason."""
    assert set_row_milestone(BOARD, 8, "Picking redesign") is None


def test_an_absent_row_is_refused():
    assert set_row_milestone(BOARD, 404, "Picking redesign") is None


def test_a_blank_clears_the_cell_back_to_ungrouped():
    grouped = set_row_milestone(BOARD, 7, "Picking redesign")
    cleared = set_row_milestone(grouped, 7, "")
    assert rows(cleared)[7]["milestone"] == ""
    assert rows(cleared)[7]["size"] == rows(BOARD)[7]["size"]


def test_a_name_is_stripped_not_stored_with_its_padding():
    """Asserted on the written line, not on the parsed row.

    `parse_board` strips the cell on the way back in, so a round-trip
    through it cannot tell a padded cell from a clean one -- the test would
    pass with the strip deleted. This text lands in his file, so the check
    has to be on the bytes.
    """
    after = set_row_milestone(BOARD, 7, "  Picking redesign  ")
    written = [line for line in after.split("\n") if line.startswith("| [[#7")][0]
    # The trailing empty cell is the `Order` column, which every row grows
    # the moment any cell past it is written and which nothing has placed.
    assert written.endswith("| Picking redesign |  |")
    assert rows(after)[7]["milestone"] == "Picking redesign"


def test_size_and_milestone_are_independent_cells():
    """The pair M4 divides one by the other, so a write must not swap them."""
    after = set_row_milestone(set_row_size(BOARD, 9, "l"), 9, "Backup")
    assert rows(after)[9]["size"] == "L"
    assert rows(after)[9]["milestone"] == "Backup"
