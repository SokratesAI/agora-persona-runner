"""`document_layout` is the record home for what `parse_board` does not model.

The failure these guard against is the one the four-key comparison could
never see: a renderer built out of `parse_board`'s four keys deletes every
section the parser has no word for, and both sides of any check written in
the parser's vocabulary agree that nothing happened. So the assertions here
are about *the document* -- which blocks came out, in which order, and
whether the owner's own words came back -- and never about the four keys
alone.
"""

import pytest

from agora_runner.board_view import (
    BOARD_COLUMNS,
    document_layout,
    render_document,
)
from agora_runner.nova_boards import parse_board


#: A board with a section in the middle and a section at the end, which is
#: the shape both live boards have: `ideas.md` carries `## Discarded`
#: between the table and `# Details`, and both carry `## Processed captures`
#: after every write-up.
BOARD = """---
type: log
---

- a capture he typed
- 

## Board

| # | Idea | Status | Updated |
|---|---|---|---|
| [[#2 — Second\\|2]] | Second | 🟡 In progress | 09-01 |
| [[#1 — First\\|1]] | First | ⚪ Backlog | 09-02 |

## Discarded

| # | Idea | Why |
|---|---|---|
| 9 | An idea he threw away | it was already done |

# Details

### #2 — Second

The write-up for the second row.

# Done — detail

### #1 — First

The write-up for the first row.

## Processed captures

- 2026-09-01 — something he wrote and I filed
"""


def kinds(layout):
    return [block["kind"] for block in layout]


def rendered(markdown):
    """The generated view of a document, through its own layout."""
    contents = parse_board(markdown)
    frontmatter = markdown.split("---")[1].join(("---", "---"))
    return render_document(
        contents, frontmatter, layout=document_layout(markdown))


def test_every_block_of_the_document_is_named_in_order():
    """`## Discarded` and `# Details` are one block, and that is right.

    Two headings with no modelled block between them are one run of
    verbatim markdown -- splitting them would be a claim about structure
    the residue does not have. What has to hold is the *order*, which is
    the next test.
    """
    assert kinds(document_layout(BOARD)) == [
        "board", "verbatim", "detail", "verbatim", "detail", "verbatim",
    ]


def test_the_sections_the_parser_cannot_model_come_back_word_for_word():
    document = rendered(BOARD)
    for section in ("## Discarded", "An idea he threw away",
                    "# Done — detail", "## Processed captures",
                    "something he wrote and I filed"):
        assert section in document


def test_a_middle_section_stays_in_the_middle():
    document = rendered(BOARD)
    assert (document.index("## Board")
            < document.index("## Discarded")
            < document.index("# Details")
            < document.index("# Done — detail")
            < document.index("## Processed captures"))


def test_without_a_layout_the_sections_are_gone():
    """The state this whole module exists to leave, pinned so it stays gone."""
    contents = parse_board(BOARD)
    document = render_document(contents, "")
    assert "## Discarded" not in document
    assert "## Processed captures" not in document
    assert "An idea he threw away" not in document


def test_the_four_keys_still_survive_the_layout_render():
    was = parse_board(BOARD)
    now = parse_board(rendered(BOARD))
    for key in ("captures", "captureReplies", "items", "details"):
        assert was[key] == now[key], key


def test_the_owners_own_column_name_is_kept():
    """`ideas.md` calls its second column `Idea`; the constant says `Item`."""
    layout = document_layout(BOARD)
    board_block = next(b for b in layout if b["kind"] == "board")
    assert board_block["columns"][1] == "Idea"
    assert "| # | Idea | Status |" in rendered(BOARD)


def test_a_short_header_is_padded_out_to_the_width_of_its_rows():
    """His header stops at `Updated`; `render_row` writes nine cells."""
    header = [
        line for line in rendered(BOARD).split("\n")
        if line.startswith("| # |")][0]
    assert header == "| " + " | ".join(("#", "Idea") + BOARD_COLUMNS[2:]) + " |"


def test_a_write_up_added_since_the_layout_was_taken_is_still_written():
    contents = parse_board(BOARD)
    contents["details"][3] = "A write-up with no seat in the layout."
    document = render_document(
        contents, "", layout=document_layout(BOARD))
    assert "A write-up with no seat in the layout." in document
    assert parse_board(document)["details"][3] == (
        "A write-up with no seat in the layout.")


def test_a_write_up_the_records_no_longer_hold_is_skipped():
    contents = parse_board(BOARD)
    del contents["details"][1]
    document = render_document(contents, "", layout=document_layout(BOARD))
    assert "The write-up for the first row." not in document
    assert "The write-up for the second row." in document


def test_an_empty_done_table_is_not_written_even_when_the_layout_names_it():
    layout = document_layout(BOARD) + [{"kind": "done"}]
    document = render_document(parse_board(BOARD), "", layout=layout)
    assert "## Done" not in document


def test_a_done_row_fills_the_done_block_the_layout_names():
    contents = parse_board(BOARD)
    contents["items"][0] = dict(contents["items"][0], done=True, where="#12")
    layout = document_layout(BOARD) + [{"kind": "done"}]
    document = render_document(contents, "", layout=layout)
    assert "## Done" in document
    assert parse_board(document)["items"][-1]["where"] == "#12"


def test_an_unknown_block_kind_raises_rather_than_being_dropped():
    with pytest.raises(ValueError, match="layout block's kind"):
        render_document(
            parse_board(BOARD), "", layout=[{"kind": "footnotes"}])


def test_a_document_with_no_headings_has_an_empty_layout():
    assert document_layout("- just a capture\n- \n") == []
    assert document_layout("") == []


def test_a_table_whose_header_is_missing_records_no_column_names():
    """The rule row is not a header, and reading it as one would render it.

    A `|---|---|` with nothing above it is malformed markdown, and taking
    its cells as column names would put `---` in the header of the
    generated view -- which parses, because `parse_board` drops the header
    by shape and never reads a word of it.
    """
    headless = BOARD.replace("| # | Idea | Status | Updated |\n", "")
    board_block = next(
        b for b in document_layout(headless) if b["kind"] == "board")
    assert "columns" not in board_block
    assert "| # | Item | Status |" in rendered(headless)
