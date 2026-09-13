"""A done row survives the round trip a publish refuses on.

Two things the store and the document disagree about the moment a row goes
done, and both of them refused his `issues.md` from 2026-09-12 until Cycle
1507:

**Order.** The store keeps one total order (`board_store.sort_key`, a rank
and then a number) and knows nothing about done-ness, while the document
draws `## Board` and then `## Done`. Rows 215 and 216 sit fourth and fifth
in rank order, so comparing the two lists positionally reported every one of
the two hundred rows below them as differing.

**Priority.** `board_view.DONE_COLUMNS` is four columns and none of them is
the rating, so a done row's priority is structurally unwritable and comes
back empty however it was stored. That is the same carve-out `placedBy`
already has, for the same reason: the view can never carry it.
"""

from agora_runner import board_view
from agora_runner.board_publish import differences


def item(number, **over):
    row = {
        "number": number, "title": f"Row {number}", "status": "⚪ Backlog",
        "updated": "09-09", "where": "", "priority": "", "priorityKey": "",
        "statusKey": "backlog", "project": "Nova", "size": "",
        "sizeKey": "", "milestone": "", "order": None, "done": False,
    }
    row.update(over)
    return row


def test_a_done_row_out_of_document_order_is_not_drift():
    ranked = [item(3), item(2, done=True, status="✅ Done",
                        statusKey="done"), item(1)]
    drawn = [ranked[0], ranked[2], ranked[1]]

    assert differences({"items": ranked}, {"items": drawn}) == []
    assert differences({"items": drawn}, {"items": ranked}) == []


def test_a_real_reorder_among_the_open_rows_is_still_drift():
    """The partition must not become a set comparison."""
    ranked = [item(3), item(2, done=True, status="✅ Done"), item(1)]
    swapped = [item(1), item(3), item(2, done=True, status="✅ Done")]

    moved = differences({"items": ranked}, {"items": swapped})
    assert moved and "number" in moved[0], moved


def test_a_done_rows_rating_is_not_drift_but_an_open_rows_is():
    rated = item(2, done=True, status="✅ Done", statusKey="done",
                 priority="🟠 High", priorityKey="high")
    blank = dict(rated, priority="", priorityKey="")
    assert differences({"items": [rated]}, {"items": [blank]}) == []
    assert differences({"items": [blank]}, {"items": [rated]}) == []

    open_rated = dict(rated, done=False, status="⚪ Backlog",
                      statusKey="backlog")
    open_blank = dict(open_rated, priority="", priorityKey="")
    dropped = differences({"items": [open_rated]}, {"items": [open_blank]})
    assert dropped and "priority" in dropped[0], dropped


def test_a_done_rows_other_fields_are_still_compared():
    """Only the rating is unwritable; a lost title is a real render bug."""
    row = item(2, done=True, status="✅ Done", statusKey="done")
    assert differences({"items": [row]},
                       {"items": [dict(row, title="something else")]})
    assert differences({"items": [row]},
                       {"items": [dict(row, updated="01-01")]})


def test_a_board_whose_layout_predates_its_first_done_row_round_trips():
    """End to end: draw from a layout with no `## Done` block, read it back.

    This is his `issues.md` in miniature -- and the render is the real one,
    so nothing here restates where `_laid_out` puts the section.
    """
    from agora_runner import board_document, board_publish
    contents = {
        "captures": [], "captureReplies": [],
        "items": [item(3), item(2, done=True, status="✅ Done",
                                statusKey="done", priority="🟠 High",
                                priorityKey="high"), item(1)],
        "details": {},
    }
    before = board_view.render_document(
        {**contents, "items": [item(3), item(2), item(1)]},
        frontmatter="---\ntype: board\n---")
    layout = board_document.layout_blocks_of(
        board_document.to_layout_document(
            board_view.document_layout(before), "issue"))
    assert "done" not in [b["kind"] for b in layout]

    text = board_view.render_document(
        contents, frontmatter="---\ntype: board\n---", layout=layout)
    reread = board_publish.reread(text)

    assert sorted(r["number"] for r in reread["items"]) == [1, 2, 3]
    assert differences(contents, reread) == []
    assert board_publish.layout_differences(text, "issue", layout) == []
