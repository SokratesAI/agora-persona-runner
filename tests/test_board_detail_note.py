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

## 57 — More pages

The problem, in his words.

### Where it lives

A subheading inside the write-up, which stays inside it.

### #59 — Small pickings

One line.

## 60 — Empty on purpose

## 62 — Padded below

Two blank lines follow this one.


## 61 — Last block in the file

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
    # The boarded rows are untouched apart from the one `Updated` cell the
    # note is meant to stamp. This assertion used to demand byte-identical
    # rows, which was the old behaviour and the bug: a note is the only
    # signal that a row was worked, so leaving the sort key stale is what
    # let `top_board_rows` rank issue #7 as the oldest `High` row four
    # hours after Cycle 270 wrote a note into it.
    before = {item["number"]: dict(item) for item in parse_board(BOARD)["items"]}
    after = {item["number"]: dict(item) for item in parse_board(out)["items"]}
    assert after[57].pop("updated") == "08-15"
    assert before[57].pop("updated") == "08-11"
    assert after == before


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
        "## 60 — Empty on purpose\n**Nova, 08-15:** Closed by #192." in out
    )


def test_the_note_is_separated_from_the_body_by_exactly_one_blank_line():
    # #62's block ends in two blank lines before the next heading. They
    # are the separator, not the body, so the note goes above them and
    # lands one line under the prose however many there are. The stripped
    # body cannot see this either.
    out = append_detail_note(BOARD, 62, "Closed by #192.", "08-15")
    assert (
        "Two blank lines follow this one.\n\n**Nova, 08-15:** Closed by #192.\n\n\n"
        "## 61" in out
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
    assert append_detail_note(BOARD, 57, "Closed.\n## 58 — Injected", "08-15") is None


def test_a_carriage_return_in_the_note_is_refused():
    # The one a `"\n" in note` check lets through. `re.MULTILINE` anchors
    # on `\n` alone, so a bare `\r` splits no span here and every
    # server-side assertion agrees it is harmless -- but CommonMark calls
    # it a line ending, so Obsidian on his phone breaks the line and puts
    # the heading under his prose. Same corruption, different reader.
    assert append_detail_note(BOARD, 57, "Closed.\r## 58 — Injected", "08-15") is None
    assert append_detail_note(BOARD, 57, "Closed.\r\n## 58 — Injected", "08-15") is None
    assert append_detail_note(BOARD, 57, "Closed.", "08-15\r## 58 — Injected") is None


def test_the_heading_it_would_have_injected_really_does_split_a_span():
    # The refusal above is only worth having if the thing it refuses is
    # dangerous. Write that heading into the body directly and watch #57's
    # write-up lose the text below it.
    poisoned = BOARD.replace(
        "The problem, in his words.",
        "The problem, in his words.\n\n## 58 — Injected",
    )
    assert "A subheading inside the write-up" in _detail(BOARD, 57)
    assert "A subheading inside the write-up" not in _detail(poisoned, 57)
    # And tie that back to the function, so this pins the refusal rather
    # than only demonstrating the danger: the same text handed to
    # `append_detail_note` leaves #57 whole.
    out = append_detail_note(BOARD, 57, "Closed.\n## 58 — Injected", "08-15")
    assert out is None
    assert "A subheading inside the write-up" in _detail(BOARD, 57)


def test_empty_and_whitespace_notes_are_refused():
    assert append_detail_note(BOARD, 57, "", "08-15") is None
    assert append_detail_note(BOARD, 57, "   ", "08-15") is None
    assert append_detail_note(BOARD, 57, None, "08-15") is None


def test_a_missing_or_multiline_date_is_refused():
    assert append_detail_note(BOARD, 57, "Closed.", "") is None
    assert append_detail_note(BOARD, 57, "Closed.", None) is None
    assert append_detail_note(BOARD, 57, "Closed.", "08-15\n## 58 — Injected") is None


# --- Idea #64: the same append, attributed to Edvard ---
# *"Lets me have the same comment conversation on ideas, notes and issues
# like the Journal."* The thread lives inside the write-up, so a comment
# is `append_detail_note` with a different name in front of the colon --
# and the name is the one new thing that can go wrong, because it is
# interpolated inside `**...**` in his own file.

def test_a_comment_from_edvard_is_attributed_to_him_not_to_me():
    out = append_detail_note(BOARD, 57, "This is still wrong on my phone.", "08-15",
                             author="Edvard")
    assert "**Edvard, 08-15:** This is still wrong on my phone." in _detail(out, 57)
    assert "**Nova, 08-15:**" not in _detail(out, 57)


def test_the_author_defaults_to_me_so_every_existing_caller_is_unchanged():
    assert _detail(append_detail_note(BOARD, 57, "x", "08-15"), 57).endswith("**Nova, 08-15:** x")


def test_an_unknown_author_is_refused_rather_than_falling_back_to_me():
    """Attributing his sentence to me is the corruption worth refusing --
    a cycle reading the row would answer its own comment."""
    for author in ["Sokrates", "Nova**, 08-15:** injected", "anon"]:
        assert append_detail_note(BOARD, 57, "hello", "08-15", author=author) is None


def test_a_blank_author_is_refused_rather_than_signed_with_my_name():
    """`None` means "not specified" and defaults to me. An empty string
    means a caller that meant to name someone and sent nothing -- an unset
    payload field -- and defaulting that to me signs his sentence with my
    name, which is what the closed set is for. Caught by this test against
    a first version that wrote `author or "Nova"`."""
    for author in ["", "   "]:
        assert append_detail_note(BOARD, 57, "hello", "08-15", author=author) is None


def test_a_comment_and_my_reply_stack_under_the_write_up_in_order():
    """The whole reason inline beat a second comments file: the
    conversation sits under the idea it is about, oldest first."""
    out = append_detail_note(BOARD, 57, "Why is this still open?", "08-15", author="Edvard")
    out = append_detail_note(out, 57, "Built it this cycle.", "08-15", cycle=219, author="Nova")
    body = _detail(out, 57)
    assert body.index("**Edvard, 08-15:**") < body.index("**Nova, 08-15 (Cycle 219):**")


def test_a_comment_still_cannot_carry_a_line_break_into_his_write_up():
    """`author` is a new argument on a call whose span safety is the whole
    point of this file; it must not have opened a second door."""
    for text in ["two\nlines", "sneaky\rreturn"]:
        assert append_detail_note(BOARD, 57, text, "08-15", author="Edvard") is None


def test_a_row_with_no_write_up_takes_no_comment():
    """#63 is a board row with no `## 63 —` block, which is most rows.
    The route turns this into a 409 rather than a retry."""
    assert append_detail_note(BOARD, 63, "hello", "08-15", author="Edvard") is None


# --- who a comment is attributed to (Cycle 253) -----------------------------

_ROW = """---
type: board
---

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#94 — A dormant app\\|94]] | A dormant app | 🟡 In progress | 08-16 | 🟠 High |

## #94 — A dormant app

> His statement of the problem.
"""


def _write_comment(monkeypatch, **kwargs):
    """`comment_on_row` for real, with only the vault boundary faked."""
    import agora_runner.nova_capture as nova_capture
    seen = {}
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (_ROW, "7-abc"))
    monkeypatch.setattr(
        nova_capture, "vault_write_path",
        lambda path, body, if_rev=None: seen.update(body=body) or "written")
    ok, message = nova_capture.comment_on_row(
        "issues", 94, "Not taken this cycle.", "08-17", **kwargs)
    assert ok, message
    return seen["body"]


def test_a_cycles_reply_is_written_under_novas_name(monkeypatch):
    """The bug this pins is not cosmetic and it is not hypothetical.

    `comment_on_row` hardcoded `author="Edvard"` while its own docstring
    told a cycle to reply with `author="Nova"`, so two replies this loop
    made are in his `issues.md` right now as words he said. And
    `unanswered_comments` calls a row waiting when the **last** note under
    it is his -- a flag that outranks a 🔴 in `tools.top_board_rows` -- so
    a cycle commenting on a row raised a permanent "he is waiting" marker
    that its own reply could never clear. Found by doing exactly that to
    #94 and watching the flag appear between two runs of the tool.
    """
    body = _write_comment(monkeypatch, author="Nova")
    assert "**Nova, 08-17:**" in body
    assert "**Edvard, 08-17:**" not in body


def test_the_reply_does_not_leave_the_row_reading_as_waiting_on_him(monkeypatch):
    """The consequence, asserted against the thing that actually reads it.

    Attribution is the mechanism; this is the damage. Asserting only on the
    rendered name would pass if `unanswered_comments` keyed on something
    else entirely, so this asks the ranking's own predicate.
    """
    from agora_runner.nova_boards import unanswered_comments

    assert 94 in unanswered_comments(_write_comment(monkeypatch, author="Edvard"))
    assert 94 not in unanswered_comments(_write_comment(monkeypatch, author="Nova"))


def test_an_unstated_author_is_still_him(monkeypatch):
    """The page is his; the default must not move under the site."""
    assert "**Edvard, 08-17:**" in _write_comment(monkeypatch)


# --- The `Updated` cell ------------------------------------------------
#
# `tools.top_board_rows` ranks Edvard's boards by rating and then by
# `age_key(updated)`, oldest first, so this cell decides which row a cycle
# is told to take. A note is the only call that always means the row was
# genuinely worked, and it used to leave the cell alone -- measured live on
# 2026-08-20, issue #7 topped the ranking as the oldest `High` row while
# its write-up already carried a note from four hours earlier.


def test_a_note_stamps_the_rows_updated_cell():
    out = append_detail_note(BOARD, 57, "Closed by #192.", "08-15", cycle=204)
    row = next(i for i in parse_board(out)["items"] if i["number"] == 57)
    assert row["updated"] == "08-15"


def test_edvards_comment_stamps_it_too():
    """The cell means "when did this row last change", not "when did I work it"."""
    out = append_detail_note(BOARD, 57, "Why is this still open?", "08-16",
                             author="Edvard")
    row = next(i for i in parse_board(out)["items"] if i["number"] == 57)
    assert row["updated"] == "08-16"


def test_the_stamp_touches_nothing_but_the_date():
    """The row is rebuilt from its own cells, so the rest must come back whole.

    The first cell is a wiki-link holding an escaped `\\|`; splitting a row
    on a bare pipe yields five cells where the table has four and shifts
    every column right. `_row_span` masks it, and this is the assertion
    that would catch the day something stops.
    """
    out = append_detail_note(BOARD, 57, "Closed.", "08-15")
    assert "| [[#57 — More pages\\|57]] | More pages | 🟡 In progress | 08-15 | 🔵 Medium |" in out


def test_a_row_with_no_priority_cell_does_not_grow_one():
    """#59's row is four cells wide. Stamping it must not append a fifth.

    `set_row_priority` grows the row on purpose; this call has no rating to
    write, and a blank fifth cell here would put an empty chip on the page.
    """
    out = append_detail_note(BOARD, 59, "Closed.", "08-15")
    assert "| [[#59 — Small pickings\\|59]] | Small pickings | ⚪ Backlog | 08-15 |" in out
    row = next(i for i in parse_board(out)["items"] if i["number"] == 59)
    assert row["priority"] == ""


def test_a_write_up_with_no_board_row_still_gets_its_note():
    """Only some write-ups have a row. Losing his sentence to fix a sort order
    would be the worse trade, so the stamp is best-effort and the note is not.
    """
    out = append_detail_note(BOARD, 60, "Closed by #192.", "08-15")
    assert out is not None
    assert _detail(out, 60) == "**Nova, 08-15:** Closed by #192."
    assert parse_board(out)["items"] == parse_board(BOARD)["items"]


def test_a_pipe_in_the_date_is_refused():
    """`dated` reaches a table cell now, so it has to survive being one."""
    assert append_detail_note(BOARD, 57, "Closed.", "08-15 | evil") is None
    # The note body is not a table cell and keeps taking any pipe it likes.
    assert append_detail_note(BOARD, 57, "Closed by a | b.", "08-15") is not None
