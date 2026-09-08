"""The `Size` cell: `set_row_size`, `canonical_size`, and the parser round-trip.

Milestone M2 of idea #260's picking redesign. The field is a t-shirt size on
a `## Board` row, S/M/L/XL, and milestone M4 ranks on importance divided by
it -- so what has to hold here is that an unsized row stays visibly unsized
rather than acquiring a default, and that setting a size touches exactly one
cell of the owner's own file.

Assertions are on `parse_board` output rather than on the raw string
wherever the question is "what does the system now believe", for the reason
`test_tools_board_status` gives: a shifted cell is still a well-formed table
and reads as plausible right up until the page draws a title in the size
column.
"""

from agora_runner.nova_boards import (
    SIZE_LABELS,
    canonical_size,
    parse_board,
    set_row_project,
    set_row_size,
    size_key,
)

BOARD = """# Nova — Ideas

## Entries

- 2026-09-06 (Cycle 1090) — a bullet nothing here may touch

## Board

| # | Idea | Status | Updated | Priority | Project |
|---|---|---|---|---|---|
| [[#7 — Open one\\|7]] | Open one | 🟡 In progress | 09-05 | 🟠 High | Marcus |
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


def test_an_unsized_row_stays_unsized_and_is_not_defaulted():
    """The opposite call to `project`, and it is deliberate.

    A blank `Project` cell means `DEFAULT_PROJECT`, because every row
    predates that column and all of them really are Nova's. A blank `Size`
    means nobody has estimated this, which is a state the picker has to be
    able to see: defaulting it to `M` would hide every row still owing an
    estimate behind a number I invented.
    """
    parsed = rows(BOARD)
    assert parsed[7]["size"] == ""
    assert parsed[7]["sizeKey"] == ""
    # And the row that is too narrow to have the cell at all reads the same
    # way, rather than raising -- that is what "appended, never inserted"
    # buys, and both live boards are that shape today.
    assert parsed[9]["size"] == ""


def test_setting_a_size_moves_that_cell_and_nothing_else():
    written = set_row_size(BOARD, 7, "xl")
    before, after = rows(BOARD)[7], rows(written)[7]
    assert after["size"] == "XL"
    assert after["sizeKey"] == "xl"
    assert {k: v for k, v in before.items() if k not in ("size", "sizeKey")} == {
        k: v for k, v in after.items() if k not in ("size", "sizeKey")
    }
    # The other rows, the `## Done` table and the write-up are untouched.
    assert rows(written)[8] == rows(BOARD)[8]
    assert rows(written)[9] == rows(BOARD)[9]
    assert "| [[#3 — Shipped\\|3]] | Shipped | 09-01 | #700 |" in written
    assert "Body text I must not touch." in written


def test_the_header_gains_the_size_column():
    """A seven-cell row under a six-cell header is dropped by Obsidian.

    Asserted by name and not by a `|` count, because a count passes on a
    header that grew an unlabelled cell -- the value would be on his screen
    with no word saying what it is, which is the same failure the project
    column's widener was written for.
    """
    written = set_row_size(BOARD, 7, "s")
    header = [line for line in written.split("\n") if line.startswith("| # |")][0]
    assert header == ("| # | Idea | Status | Updated | Priority | Project "
                      "| Size | Milestone | Order |")
    # `## Done` is a different table with a different shape and must not
    # have grown anything.
    assert "| # | Item | Landed | Where |" in written


def test_a_narrow_row_is_padded_rather_than_refused():
    """#9 has never carried a project, let alone a size."""
    written = set_row_size(BOARD, 9, "m")
    assert rows(written)[9]["size"] == "M"
    # The padding fills the project cell with a blank, which `parse_board`
    # reads as the default -- the same answer it gave before the write, so
    # sizing a row does not silently re-file it.
    assert rows(written)[9]["project"] == rows(BOARD)[9]["project"]


def test_a_closed_row_is_refused():
    """An estimate of the work left on a finished row is not a fact.

    #8 is `✅ Done` and still sits in `## Board`, which is where most
    finished rows live -- so refusing the `## Done` table would not be
    enough, and the status cell is what decides.
    """
    assert set_row_size(BOARD, 8, "s") is None


def test_an_unknown_size_and_a_missing_row_are_both_refused():
    assert set_row_size(BOARD, 7, "huge") is None
    assert set_row_size(BOARD, 404, "s") is None


def test_a_blank_clears_the_cell_back_to_unsized():
    """A size is a guess, so "I no longer have one" has to be sayable."""
    sized = set_row_size(BOARD, 7, "l")
    assert rows(sized)[7]["size"] == "L"
    cleared = set_row_size(sized, 7, "")
    assert cleared is not None
    assert rows(cleared)[7]["size"] == ""
    assert rows(cleared)[7]["sizeKey"] == ""


def test_the_written_spellings_are_the_ones_the_parser_reads_back():
    """The producer and the reader agree, for every size.

    `PRIORITY_LABELS` needed exactly this test one column over: an
    un-normalised cell rendered as a glyph with a dead CSS class, and
    nothing failed. Driving every label through a real write and back out
    through `parse_board` is what would catch a label the parser cannot
    return.
    """
    for key, label in SIZE_LABELS.items():
        if not key:
            continue
        written = set_row_size(BOARD, 7, key)
        assert rows(written)[7]["size"] == label
        assert rows(written)[7]["sizeKey"] == key


def test_a_hand_typed_synonym_lands_in_the_right_bucket():
    """He writes these cells in Obsidian, by hand, so `Large` has to work."""
    assert canonical_size("Large") == "L"
    assert canonical_size("x-large") == "XL"
    assert canonical_size("  SMALL ") == "S"
    assert size_key("Extra-Large") == "xl"
    # And a word that is not a size is `None`, not a silent bucket.
    assert canonical_size("enormous") is None
    # `""` is the real unsized answer and not a rejection, which is the one
    # distinction `None` cannot make for itself.
    assert canonical_size("") == ""
    assert canonical_size(None) == ""


def test_setting_a_project_first_does_not_lose_the_size():
    """The two writers share a width, so they must not overwrite each other."""
    written = set_row_project(set_row_size(BOARD, 9, "xl"), 9, "Marcus")
    assert rows(written)[9]["size"] == "XL"
    assert rows(written)[9]["project"] == "Marcus"
