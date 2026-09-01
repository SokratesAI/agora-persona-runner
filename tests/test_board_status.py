"""Marking a row's status, which Cycle 202 did by hand and nearly got wrong.

That cycle split each row on `|` itself, and the first cell is a wiki-link
containing an escaped `\\|`, so every column shifted one to the right and the
title landed in the status column. `set_row_status` exists so no cycle has to
write that split again; these tests are pointed at the shift specifically,
because a shifted row is still a well-formed table and reads as plausible.
"""

from agora_runner.nova_boards import (
    OUTDATED_STATUS,
    STATUS_LABELS,
    parse_board,
    set_row_priority,
    set_row_status,
    status_key,
)

BOARD = """---
type: board
---

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#57 — More pages\\|57]] | More pages | 🟡 In progress | 08-11 | 🔵 Medium |
| [[#59 — Small pickings\\|59]] | Small pickings | ⚪ Backlog | 08-11 |
| [[#63 — Green tick\\|63]] | Green tick | 🟢 Done | 08-12 |

## Done

| # | Item | Landed | Where |
|---|------|--------|-------|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

## #57 — More pages

Body text I must not touch.
"""


def _row(markdown, number):
    return [i for i in parse_board(markdown)["items"] if i["number"] == number][0]


def test_marks_a_row_and_parse_board_reads_the_cells_back_unshifted():
    updated = set_row_status(BOARD, 57, OUTDATED_STATUS)
    row = _row(updated, 57)
    assert row["status"] == OUTDATED_STATUS and row["statusKey"] == "outdated"
    # The shift Cycle 202 nearly wrote puts the title here and the status
    # into `updated`, so asserting the status alone would not have caught it.
    assert row["title"] == "More pages"
    assert row["updated"] == "08-11"


def test_stamps_the_updated_cell_only_when_asked():
    assert _row(set_row_status(BOARD, 57, "✅ Done"), 57)["updated"] == "08-11"
    stamped = set_row_status(BOARD, 57, "✅ Done", updated="08-15")
    assert _row(stamped, 57)["updated"] == "08-15"


def test_closing_a_row_clears_a_rating_set_row_priority_could_never_clear():
    closed = set_row_status(BOARD, 57, OUTDATED_STATUS)
    assert _row(closed, 57)["priority"] == ""
    # The invariant that makes that necessary: the rating setter refuses a
    # closed row, so a chip left behind here would be unreachable forever.
    assert set_row_priority(closed, 57, "🟠 High") is None


def test_moving_between_two_open_statuses_keeps_the_rating():
    moved = set_row_status(BOARD, 57, "⚪ Backlog")
    assert _row(moved, 57)["status"] == "⚪ Backlog"
    assert _row(moved, 57)["priority"] == "🔵 Medium"


def test_reopening_a_closed_row_does_not_bring_its_rating_back():
    # This test was named "reopening" and never closed the row first, so it
    # only ever went In progress -> Backlog and could not reach the
    # clearing branch at all. Doing it properly pins the consequence that
    # actually matters: closing is lossy, so a reopened row comes back
    # unrated and somebody has to rate it again.
    closed = set_row_status(BOARD, 57, "✅ Done")
    assert _row(closed, 57)["priority"] == ""
    reopened = set_row_status(closed, 57, "🟡 In progress")
    assert _row(reopened, 57)["status"] == "🟡 In progress"
    assert _row(reopened, 57)["priority"] == ""
    # ...and it is rateable again, which a closed row is not.
    assert set_row_priority(reopened, 57, "🟠 High") is not None


def test_touches_nothing_but_the_one_row():
    updated = set_row_status(BOARD, 59, "✅ Done", updated="08-15")
    before, after = BOARD.split("\n"), updated.split("\n")
    assert len(before) == len(after)
    differing = [i for i in range(len(before)) if before[i] != after[i]]
    assert len(differing) == 1 and "#59" in before[differing[0]]


def test_a_row_with_no_rating_cell_is_not_given_an_empty_one():
    updated = set_row_status(BOARD, 59, OUTDATED_STATUS)
    assert updated.count("| [[#59 — Small pickings\\|59]] | Small pickings"
                         " | ⚫ Outdated | 08-11 |") == 1


def test_refuses_a_done_table_row_whose_third_column_is_a_date():
    # This test was written asserting the opposite -- that reopening a
    # `## Done` row should work -- and it is what found the bug. `## Done`
    # is `# | Item | Landed | Where`, so the write would have replaced the
    # landed date with `⚪ Backlog` and `parse_board`, which derives that
    # row's status from the table it sits in, would have gone on reporting
    # `✅ Done`. A silent corruption on the owner's own file.
    assert set_row_status(BOARD, 51, "⚪ Backlog") is None
    assert "| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |" in BOARD


def test_refuses_a_missing_row_and_a_status_i_did_not_offer():
    assert set_row_status(BOARD, 999, "✅ Done") is None
    assert set_row_status(BOARD, 57, "🟣 Maybe") is None
    assert set_row_status(BOARD, 57, "done") is None
    assert set_row_status(BOARD, 57, "") is None


def test_refuses_an_updated_stamp_carrying_a_table_delimiter():
    # `status` comes off a whitelist so it cannot carry one; `updated` is
    # free text. A `|` gives the row an extra column and a newline splits
    # it into two, and both land in the owner's file looking deliberate.
    assert set_row_status(BOARD, 57, "✅ Done", updated="08-15 | 🟠 High") is None
    assert set_row_status(BOARD, 57, "✅ Done", updated="08-15\n| oops |") is None
    assert set_row_status(BOARD, 57, "✅ Done", updated="08-15") is not None


def test_the_live_green_tick_spelling_is_rewritten_to_the_one_everything_uses():
    # `🟢 Done` is on issue #3 in the live file. It already reduces to
    # `done`, so the only thing at stake is that setting it again lands on
    # the ✅ spelling rather than adding a third one.
    assert status_key("🟢 Done") == "done"
    assert _row(set_row_status(BOARD, 63, STATUS_LABELS["done"]), 63)["status"] == "✅ Done"


def test_labels_round_trip_through_status_key():
    for key, label in STATUS_LABELS.items():
        assert status_key(label) == key
