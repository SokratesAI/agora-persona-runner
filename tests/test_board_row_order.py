"""The `Order` cell: `set_row_order`, the parser round-trip, and the tier.

His capture of 2026-09-08 -- *"Lets me organise/sort the milestones and
tasks aswell. Convert the old priority to the ordered list so high is at
the top and low is at the bottom."* Milestones already reorder; a row had
no position at all, so inside a milestone the rating was the only ordering.

Three things have to hold and each has a failure this file exists to catch.
A board he has never dragged must rank exactly as it does today, or the
column is a silent reshuffle of his queue. The first placement has to
number the whole group off the ratings, because a single numbered row
beside seven that still rank by something else is a control whose effect
he cannot see. And `nova_next.rank` has to actually read the cell -- the
comment in `open_rows` records that M4's first run shipped a tier reading a
field that function dropped, which is a tier doing nothing with no symptom.
"""

from agora_runner.nova_boards import parse_board, set_row_order
from agora_runner import nova_next

BOARD = """# Nova — Ideas

## Entries

- 2026-09-06 (Cycle 1094) — a bullet nothing here may touch

## Board

| # | Idea | Status | Updated | Priority | Project | Size | Milestone |
|---|---|---|---|---|---|---|---|
| [[#7 — Low one\\|7]] | Low one | ⚪ Backlog | 09-05 | ⚪ Low | Marcus | M | Push |
| [[#8 — High one\\|8]] | High one | ⚪ Backlog | 09-04 | 🟠 High | Marcus | S | Push |
| [[#9 — Medium one\\|9]] | Medium one | ⚪ Backlog | 09-03 | 🔵 Medium | Marcus |  | Push |
| [[#10 — Other milestone\\|10]] | Other milestone | ⚪ Backlog | 09-02 | 🟠 High | Marcus |  | Video |
| [[#11 — Other project\\|11]] | Other project | ⚪ Backlog | 09-01 | 🟠 High | Nova |  | Push |
| [[#12 — Closed one\\|12]] | Closed one | ✅ Done | 09-04 | 🟠 High | Marcus |  | Push |

## Done

| # | Item | Landed | Where |
|---|---|---|---|
| [[#3 — Shipped\\|3]] | Shipped | 09-01 | #700 |

# Details

### #7 — Low one

Body text I must not touch.
"""


def rows(markdown):
    return {item["number"]: item for item in parse_board(markdown)["items"]}


def test_a_board_nobody_has_ordered_reports_no_position():
    # The whole of "this ships without changing a single row": every live
    # row predates the column, and `None` rather than `0` is what makes the
    # rating still decide the order for them.
    assert [item["order"] for item in parse_board(BOARD)["items"]] == [
        None] * len(parse_board(BOARD)["items"])


def test_placing_one_row_numbers_its_whole_milestone_by_rating():
    # High, Medium, Low -- "high at the top and low at the bottom", which is
    # the migration his capture asks for, performed on the group he touched.
    after = set_row_order(BOARD, 9, 1)
    assert after is not None
    placed = rows(after)
    assert placed[9]["order"] == 1
    assert placed[8]["order"] == 2
    assert placed[7]["order"] == 3


def test_the_group_is_the_milestone_and_not_the_board():
    after = set_row_order(BOARD, 8, 1)
    placed = rows(after)
    # Same project, different milestone; and same milestone name, different
    # project. Neither is in the group, so neither is renumbered.
    assert placed[10]["order"] is None
    assert placed[11]["order"] is None


def test_a_second_move_reorders_the_seats_already_written():
    after = set_row_order(set_row_order(BOARD, 9, 1), 7, 1)
    placed = rows(after)
    assert (placed[7]["order"], placed[9]["order"], placed[8]["order"]) == (1, 2, 3)


def test_a_closed_row_is_refused_and_is_not_in_the_group():
    assert set_row_order(BOARD, 12, 1) is None
    # Three open rows in Push/Marcus, so 4 is past the end of the group --
    # which is the check that the closed row was left out of the count.
    assert set_row_order(BOARD, 8, 4) is None
    assert rows(set_row_order(BOARD, 8, 1))[12]["order"] is None


def test_a_position_outside_the_group_and_a_nonsense_one_are_refused():
    assert set_row_order(BOARD, 8, 0) is None
    assert set_row_order(BOARD, 8, -1) is None
    assert set_row_order(BOARD, 8, "top") is None
    assert set_row_order(BOARD, 999, 1) is None


def test_the_write_touches_the_table_and_nothing_else():
    after = set_row_order(BOARD, 9, 1)
    assert "a bullet nothing here may touch" in after
    assert "Body text I must not touch." in after
    # The header grows a labelled ninth column rather than an unnamed one.
    assert "| Milestone | Order |" in after
    changed = [(a, b) for a, b in zip(BOARD.split("\n"), after.split("\n")) if a != b]
    assert len(changed) == 5, changed


def _ranked(markdown):
    # Both real callers -- `next_payload` and `tools.top_board_rows` -- pass
    # the milestone tier, and it is what separates one group's seats from
    # another's. Ranking flat here would be testing a call shape nothing
    # makes on board rows, and the seats would interleave across groups.
    rows = nova_next.open_rows_from_contents(
        parse_board(markdown), "idea")
    return [row["number"] for row in nova_next.rank(
        rows, None, nova_next.milestone_ranks(rows))]


def test_rank_follows_the_hand_order_ahead_of_the_rating():
    # Untouched, the rating decides and High leads its milestone.
    assert _ranked(BOARD)[:3] == [8, 9, 7]
    # Placed, his order decides -- Low first, against its own rating.
    after = set_row_order(BOARD, 7, 1)
    assert _ranked(after)[:3] == [7, 8, 9]


def test_an_unplaced_row_sinks_below_the_placed_ones_in_its_milestone():
    # #10 is Marcus/Video and untouched; placing the Push rows must not
    # push an unplaced row of another milestone around.
    before = _ranked(BOARD).index(10)
    after = _ranked(set_row_order(BOARD, 7, 1)).index(10)
    assert before == after
