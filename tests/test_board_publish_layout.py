"""`layout_differences` accepts what `board_view._laid_out` is meant to draw.

The stored layout is captured once, at migration. Every document here is
drawn by the real `render_document` FROM that stored layout, so these tests
never restate where the renderer puts a new write-up or when it widens a
table -- they ask whether the check accepts the renderer's own output, which
is the question a publish asks. Until Cycle 1401 it did not: the first row
boarded after the flip (issue #210) and the first seat #202 wrote (the
`Order` column on `ideas.md`) each made every publish of that board refuse.
"""

from agora_runner import board_document, board_view
from agora_runner.board_publish import layout_differences

FRONTMATTER = "---\ntype: board\n---"
ARCHIVE = "## Processed captures\n\n- something he archived"


def item(number, order=None):
    return {
        "number": number, "title": f"Row {number}", "status": "⚪ Backlog",
        "updated": "09-09", "where": "", "priority": "", "project": "Nova",
        "size": "", "milestone": "", "order": order, "done": False,
    }


def draw(items, details, layout=None, tail=""):
    return board_view.render_document(
        {"captures": [], "captureReplies": [], "items": items,
         "details": details},
        frontmatter=FRONTMATTER, layout=layout, tail=tail)


def layout_of(markdown):
    return board_document.layout_blocks_of(board_document.to_layout_document(
        board_view.document_layout(markdown), "issue"))


def stored():
    """The layout a migration would have stored: two rows, two write-ups,
    his archive after them, and a board with no `Order` column."""
    return layout_of(draw([item(1), item(2)], {1: "one", 2: "two"},
                          tail=ARCHIVE))


def test_the_stored_layout_is_the_shape_these_tests_assume():
    blocks = stored()
    assert [b["kind"] for b in blocks] == [
        "board", "verbatim", "detail", "detail", "verbatim"]
    assert "Order" not in blocks[0]["columns"]


def test_a_row_boarded_since_the_layout_was_stored_is_not_drift():
    old = stored()
    markdown = draw([item(1), item(2), item(3)],
                    {1: "one", 2: "two", 3: "three"}, layout=old)
    assert layout_of(markdown) != old  # the old equality check refused this

    assert layout_differences(markdown, "issue", old) == []


def test_a_seat_that_widens_the_table_is_not_drift():
    old = stored()
    markdown = draw([item(1, order=1), item(2)], {1: "one", 2: "two"},
                    layout=old)
    assert "Order" in layout_of(markdown)[0]["columns"]

    assert layout_differences(markdown, "issue", old) == []


def test_a_deleted_write_up_is_not_drift():
    old = stored()
    markdown = draw([item(1), item(2)], {1: "one"}, layout=old)
    assert layout_of(markdown) != old

    assert layout_differences(markdown, "issue", old) == []


def test_his_archive_going_missing_is_still_drift():
    old = stored()
    markdown = draw([item(1), item(2)], {1: "one", 2: "two"}, layout=old)
    assert ARCHIVE in markdown and layout_differences(
        markdown, "issue", old) == []

    problems = layout_differences(
        markdown.replace(ARCHIVE, ""), "issue", old)

    assert problems and "Processed captures" in problems[0]


def test_a_write_up_the_layout_names_drawn_in_another_place_is_drift():
    """Only a write-up the store never named may appear anywhere; one it did
    name has a seat, and leaving it is a reorder of his document."""
    old = stored()
    moved = [old[0], old[1], old[3], old[2], old[4]]
    markdown = draw([item(1), item(2)], {1: "one", 2: "two"}, layout=old)

    assert layout_differences(markdown, "issue", moved)


def test_a_table_that_narrows_or_renames_a_column_is_still_drift():
    old = stored()
    markdown = draw([item(1), item(2)], {1: "one", 2: "two"}, layout=old)
    wider = [dict(old[0], columns=old[0]["columns"] + ["Order"])] + old[1:]
    renamed = [dict(old[0], columns=["#", "Idea"] + old[0]["columns"][2:])] \
        + old[1:]

    assert layout_differences(markdown, "issue", wider)
    assert layout_differences(markdown, "issue", renamed)
