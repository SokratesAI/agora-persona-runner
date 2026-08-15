"""Writing the reason a row closed into the file the row lives in.

`set_row_status` moves a row to `✅ Done` and says nothing about why; the
why lands in a journal entry, in another file, in another database. Issue
#85 is that a board row and reality drift apart, and a row that closed
without an account of itself is that drift pointing the other way.

The tests that matter here are the ones about the *span*: a write-up ends
at the next heading, so a note that lands outside it, or one that
introduces a heading of its own, silently truncates Edvard's own text on
the page rather than looking wrong.
"""

from agora_runner.nova_boards import append_detail_note, parse_board

BOARD = """---
type: board
---

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#57 — More pages\\|57]] | More pages | 🟡 In progress | 08-11 | 🔵 Medium |
| [[#59 — Small pickings\\|59]] | Small pickings | ⚪ Backlog | 08-11 |
| [[#63 — No write-up\\|63]] | No write-up | ⚪ Backlog | 08-12 |

# Details

## #57 — More pages

The problem, in his words.

### Where it lives

A subheading inside the write-up, which stays inside it.

### #59 — Small pickings

One line.

## #60 — Empty on purpose

## #62 — Padded below

Two blank lines follow this one.


## #61 — Last block in the file

Nothing follows this one.
"""


def _detail(markdown, number):
    return parse_board(markdown)["details"][number]


def test_note_lands_at_the_end_of_the_right_write_up():
    out = append_detail_note(BOARD, 57, "Closed by #192.", "08-15", cycle=204)
    assert out is not None
    assert _detail(out, 57).endswith("**Nova, 08-15 (Cycle 204):** Closed by #192.")
    # The neighbours are untouched, which is the thing a line-offset bug
    # breaks first.
    assert _detail(out, 59) == _detail(BOARD, 59)
    assert _detail(out, 61) == _detail(BOARD, 61)


def test_the_write_up_keeps_everything_it_had():
    out = append_detail_note(BOARD, 57, "Closed by #192.", "08-15")
    body = _detail(out, 57)
    assert "The problem, in his words." in body
    assert "### Where it lives" in body
    assert "A subheading inside the write-up, which stays inside it." in body


def test_the_note_stays_inside_the_span_and_does_not_leak_into_the_next_block():
    out = append_detail_note(BOARD, 57, "Closed by #192.", "08-15")
    assert "Closed by #192." not in _detail(out, 59)
    # And the boarded rows are the same bytes -- this writes prose, not
    # table cells.
    assert parse_board(out)["items"] == parse_board(BOARD)["items"]


def test_two_notes_stack_in_the_order_they_were_written():
    out = append_detail_note(BOARD, 57, "First.", "08-15", cycle=204)
    out = append_detail_note(out, 57, "Second.", "08-16", cycle=205)
    body = _detail(out, 57)
    assert body.index("First.") < body.index("Second.")
    # Directly under one another, not drifting a blank line further from
    # the write-up on each call.
    assert "**Nova, 08-15 (Cycle 204):** First.\n\n**Nova, 08-16 (Cycle 205):** Second." in body


def test_cycle_is_optional():
    out = append_detail_note(BOARD, 59, "Closed by #192.", "08-15")
    assert _detail(out, 59).endswith("**Nova, 08-15:** Closed by #192.")
    assert "Cycle" not in _detail(out, 59)


def test_an_empty_write_up_gets_the_note_with_no_leading_blank_line():
    out = append_detail_note(BOARD, 60, "Closed by #192.", "08-15")
    assert _detail(out, 60) == "**Nova, 08-15:** Closed by #192."
    # `parse_board` strips the body it returns, so the assertion above is
    # blind to a blank line opening the block. Read the raw file for that
    # -- an empty write-up has nothing to separate the note from.
    assert (
        "## #60 — Empty on purpose\n**Nova, 08-15:** Closed by #192." in out
    )


def test_the_note_is_separated_from_the_body_by_exactly_one_blank_line():
    # #62's block ends in two blank lines before the next heading. They
    # are the separator, not the body, so the note goes above them and
    # lands one line under the prose however many there are. The stripped
    # body cannot see this either.
    out = append_detail_note(BOARD, 62, "Closed by #192.", "08-15")
    assert (
        "Two blank lines follow this one.\n\n**Nova, 08-15:** Closed by #192.\n\n\n"
        "## #61" in out
    )


def test_the_last_block_in_the_file_is_reachable():
    out = append_detail_note(BOARD, 61, "Closed by #192.", "08-15")
    assert _detail(out, 61).endswith("**Nova, 08-15:** Closed by #192.")
    assert "Nothing follows this one." in _detail(out, 61)


def test_a_row_with_no_write_up_is_refused():
    # #63 is on the board and has no `# Details` block. That is a normal
    # state for these files, not an error, so the caller has to be told
    # rather than have a block invented under it.
    assert append_detail_note(BOARD, 63, "Closed by #192.", "08-15") is None


def test_an_unknown_number_is_refused():
    assert append_detail_note(BOARD, 999, "Closed by #192.", "08-15") is None


def test_a_newline_in_the_note_is_refused():
    # The one that would actually destroy something: `_detail_spans` ends
    # a block at the next `#`/`##` heading, so this note would truncate
    # #57's write-up and drop everything under it off the page.
    assert append_detail_note(BOARD, 57, "Closed.\n## #58 — Injected", "08-15") is None


def test_the_heading_it_would_have_injected_really_does_split_a_span():
    # The refusal above is only worth having if the thing it refuses is
    # dangerous. Write that heading into the body directly and watch #57's
    # write-up lose the text below it.
    poisoned = BOARD.replace(
        "The problem, in his words.",
        "The problem, in his words.\n\n## #58 — Injected",
    )
    assert "A subheading inside the write-up" in _detail(BOARD, 57)
    assert "A subheading inside the write-up" not in _detail(poisoned, 57)


def test_empty_and_whitespace_notes_are_refused():
    assert append_detail_note(BOARD, 57, "", "08-15") is None
    assert append_detail_note(BOARD, 57, "   ", "08-15") is None
    assert append_detail_note(BOARD, 57, None, "08-15") is None


def test_a_missing_or_multiline_date_is_refused():
    assert append_detail_note(BOARD, 57, "Closed.", "") is None
    assert append_detail_note(BOARD, 57, "Closed.", None) is None
    assert append_detail_note(BOARD, 57, "Closed.", "08-15\n## #58 — Injected") is None
