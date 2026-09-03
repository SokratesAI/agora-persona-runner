"""Board comments drawn as bubbles instead of as more write-up prose.

The owner, `issues.md` capture 2026-08-26: *"i see that boarded issues does
not have those nice colored comments like there are now in the 'not
boarded yet' box, so take the best from both worlds here."*

A board comment is appended into the row's own write-up (`append_detail_
note`), which was a good decision about the *file* and left the page
drawing three voices -- his statement of the problem, his later question,
my reply -- as one column of identical paragraphs.

What is actually worth pinning here is the split, not the colours: the
write-up must keep everything the author wrote above the first note, and a
row whose whole body is a conversation must not come back with an empty
write-up and a lost thread.
"""

import json

from agora_runner.nova_boards import (
    append_detail_note,
    split_detail_conversation,
    unanswered_comments,
)
from agora_runner import nova_site


BOARD = """---
type: board
---

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#57 — Talked about\\|57]] | Talked about | 🟡 In progress | 08-26 | 🔵 Medium |
| [[#59 — Never talked about\\|59]] | Never talked about | ⚪ Backlog | 08-26 |

# Details

## 57 — Talked about

The problem, in his words.

A second paragraph of it.

**Edvard, 08-26:** why is this still open?

**Nova, 08-26 (Cycle 462):** because the fix needs a decision.

## 59 — Never talked about

Just a write-up.
"""


def test_prose_above_the_first_note_stays_in_the_write_up():
    prose, messages = split_detail_conversation(
        "The problem.\n\nMore of it.\n\n**Edvard, 08-26:** and a question?"
    )
    assert prose == "The problem.\n\nMore of it."
    assert [m["author"] for m in messages] == ["Edvard"]
    assert messages[0]["text"] == "and a question?"


def test_a_write_up_with_no_notes_is_all_prose():
    prose, messages = split_detail_conversation("Just a write-up.\n")
    assert prose == "Just a write-up."
    assert messages == []


def test_a_stamp_with_the_year_in_it_is_still_a_note():
    """7 of the 83 notes in the live `issues.md` are `2026-08-15`, not `08-15`.

    `dated` is whatever the caller passed and both shapes are in the file.
    A bare `\\d{2}-\\d{2}` fails at a fixed offset on the long one -- it eats
    `20`, then wants `-` and finds `2` -- so four whole board rows (#81,
    #87, #90, #91) were read as prose end to end. Found by review, and the
    fixtures could not have caught it because every stamp in them was the
    short shape.
    """
    prose, messages = split_detail_conversation(
        "The problem.\n\n"
        "**Edvard, 2026-08-20:** long-form stamp.\n\n"
        "**Nova, 08-20 (Cycle 269):** short-form stamp."
    )
    assert prose == "The problem."
    assert [(m["author"], m["stamp"]) for m in messages] == [
        ("Edvard", "2026-08-20"),
        ("Nova", "08-20 (Cycle 269)"),
    ]


def test_the_waiting_flag_reads_the_same_notes_the_page_draws():
    """One definition of a note, checked rather than asserted in a comment.

    `unanswered_comment_bodies` and `split_detail_conversation` share
    `_NOTE_STAMP` now; before the review that found the missing year they
    shared a copy of the date pattern, and the copy is where it went wrong.
    """
    body = "The problem.\n\n**Nova, 2026-08-20:** answered.\n\n**Edvard, 2026-08-21:** not yet."
    board = BOARD.replace("Just a write-up.", body)
    assert unanswered_comments(board) == [59]
    _, messages = split_detail_conversation(body)
    assert [m["author"] for m in messages] == ["Nova", "Edvard"]


def test_a_multi_line_note_keeps_its_own_lines_and_stops_at_the_next_author():
    _, messages = split_detail_conversation(
        "**Edvard, 08-26:** one\ntwo\n\n**Nova, 08-26 (Cycle 462):** three"
    )
    assert [m["text"] for m in messages] == ["one\ntwo", "three"]
    # The stamp is whatever the author wrote, cycle marker and all -- it is
    # not re-derived on either side of the wire.
    assert [m["stamp"] for m in messages] == ["08-26", "08-26 (Cycle 462)"]


def test_bold_that_is_not_a_dated_note_is_not_a_speaker():
    """The same bar `_COMMENT_NOTE_RE` sets, so the two readings agree.

    The case is the one that module's own comment names -- a write-up
    opening `**<author>, in his own words:**`, which is prose. Without the
    date requirement the whole body goes into a bubble attributed to him,
    and the *first* draft of this test could not see that: it used a form
    with no comma at all, which no plausible loosening of the pattern would
    have matched either. It survived the mutation it was written for.
    """
    for prose_line in (
        "**Edvard, in his own words:** the write-up starts here.",
        "**Nova:** not a note at all.",
    ):
        prose, messages = split_detail_conversation(prose_line)
        assert messages == [], prose_line
        assert prose == prose_line


def test_a_note_the_page_can_read_back_is_the_one_the_writer_wrote():
    """Round trip: what `append_detail_note` writes, this reads as one message.

    The two live on opposite sides of the file and nothing else checks that
    they agree on the shape.
    """
    written = append_detail_note(BOARD, 59, "a fresh question", "08-26", author="Edvard")
    body = written.split("## 59 — Never talked about", 1)[1]
    prose, messages = split_detail_conversation(body)
    assert prose == "Just a write-up."
    assert [(m["author"], m["text"]) for m in messages] == [
        ("Edvard", "a fresh question"),
    ]


def test_the_detail_request_carries_the_thread_and_the_list_does_not(monkeypatch):
    monkeypatch.setattr(nova_site, "edvard_board_markdown", lambda name: BOARD)
    monkeypatch.setattr(nova_site, "nova_board_markdown", lambda name: ("", ""))
    payload = nova_site.board_payload("issues")

    # The write-up the page draws no longer contains the exchange...
    drawn = json.dumps(payload["details"]["57"])
    assert "why is this still open?" not in drawn
    assert "The problem, in his words." in drawn

    # ...and the exchange comes back beside it, in order, with its authors.
    thread = payload["detailComments"]["57"]
    assert [m["author"] for m in thread] == ["Edvard", "Nova"]
    assert "why is this still open?" in json.dumps(thread[0]["blocks"])
    # A row nobody commented on carries no key at all.
    assert "59" not in payload["detailComments"]

    one = nova_site.board_page(payload, item=57)
    assert [m["author"] for m in one["item"]["comments"]] == ["Edvard", "Nova"]
    assert nova_site.board_page(payload, item=59)["item"]["comments"] == []

    # The list request is the one that must not carry write-up bodies, and a
    # thread is part of a body.
    listed = nova_site.board_page(payload, limit=5)
    assert listed["details"] == {}
    assert listed["detailComments"] == {}
    assert listed["novaDetailComments"] == {}


def test_search_still_reaches_a_comment(monkeypatch):
    """Splitting the thread out of `details` must not narrow the search.

    `searchText` is built from the raw markdown rather than from the
    rendered blocks, and this is what says so -- ideas.md #71 asked for
    search over the write-up precisely because the body is where the
    substance is, and half the substance of an old row is the argument
    underneath it.
    """
    monkeypatch.setattr(nova_site, "edvard_board_markdown", lambda name: BOARD)
    monkeypatch.setattr(nova_site, "nova_board_markdown", lambda name: ("", ""))
    payload = nova_site.board_payload("issues")
    assert nova_site.board_page(payload, search="still open") == {
        "name": "issues",
        "query": "still open",
        "matches": [57],
    }
