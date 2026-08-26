"""`tools.close_done_captures` -- the ledger closes his captures, nothing else does.

Two failures are guarded here and they point opposite ways. Marking too
little leaves the box he types into full of finished work, which is the
complaint this tool answers. Marking too much hides a capture he is
still waiting on, and a capture is a bare bullet with no status cell --
once it reads `DONE`, `top_board_rows` skips it and `roll_done_captures`
files it away, so there is no second reader left to notice.

Assertions are on `nova_boards.parse_board` wherever the point is that
his board still renders, and on the raw text wherever the point is that
his sentence came back byte-identical.
"""

import json

from agora_runner.nova_boards import parse_board
from agora_runner.nova_claims import slug_for_capture
from tools.close_done_captures import check, done_cycles, plan, rewrite

SHIPPED = "the search bar closes my keyboard"
RATED = "make the chat modal full height"
OPEN = "connect Nova to my home NAS"
FRESH = "the sidebar scrolls off the bottom"

BOARD = f"""---
type: board
---

- {SHIPPED}
- 🔵 Medium: {RATED}
- {OPEN}
- {FRESH}
-

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| #2 | The search bar closes my keyboard | ⚪ Backlog | 08-20 | 🟠 High |

# Details

### #2 — The search bar closes my keyboard
Every letter dismisses it.
"""

LEDGER = json.dumps({"claims": [
    {"item": slug_for_capture(SHIPPED), "cycle": 434, "state": "done",
     "at": "2026-08-26T09:00:00+02:00", "outcome": "runner#376 merged"},
    {"item": slug_for_capture(RATED), "cycle": 435, "state": "done",
     "at": "2026-08-26T09:20:00+02:00", "outcome": "runner#378 merged"},
    {"item": slug_for_capture(OPEN), "cycle": 453, "state": "progressed",
     "at": "2026-08-26T10:00:00+02:00", "outcome": "step 4 still open"},
    {"item": "idea-88", "cycle": 486, "state": "done",
     "at": "2026-08-26T16:57:00+02:00", "outcome": "runner#423 merged"},
]})

FINISHED = done_cycles(LEDGER)


def test_only_capture_slugs_come_out_of_the_ledger():
    """`idea-88` is done too and is a board row, not one of his bullets."""
    assert set(FINISHED) == {slug_for_capture(SHIPPED), slug_for_capture(RATED)}
    assert FINISHED[slug_for_capture(SHIPPED)] == 434


def test_a_progressed_claim_does_not_close_his_capture():
    """The failure that would cost most: `progressed` means work is left.

    Three of the 21 live captures on 2026-08-26 were `progressed` -- the
    IDP one and the Groq key among them -- and each carries a question
    still waiting on him. Reading `progressed` as finished would file all
    three away where no later cycle looks.
    """
    marked = [old.strip() for _i, old, _n, _s, _c in plan(BOARD, FINISHED)]
    assert f"- {OPEN}" not in marked


def test_an_unclaimed_capture_is_left_alone():
    """His newest bullet has no claim at all and must survive untouched."""
    after, _count = rewrite(BOARD, FINISHED)
    assert f"- {FRESH}" in after.split("\n")


def test_the_finished_ones_are_marked_with_the_cycle_that_closed_them():
    after, count = rewrite(BOARD, FINISHED)
    assert count == 2
    captures = parse_board(after)["captures"]
    assert captures[0] == f"DONE (Cycle 434): {SHIPPED}"
    # The rating stays where he put it: `split_capture_done` runs before
    # `split_capture_priority` in every reader, so the marker goes in
    # front of the glyph rather than behind it.
    assert captures[1] == f"DONE (Cycle 435): 🔵 Medium: {RATED}"
    assert captures[2] == OPEN


def test_marking_does_not_move_the_slug():
    """The invariant the whole tool rests on.

    `slug_for_capture` is hashed off his sentence with the marker and the
    rating stripped. If a marked bullet hashed differently the ledger
    would stop matching it, the next run would mark it again, and the
    prefix would stack.
    """
    after, _count = rewrite(BOARD, FINISHED)
    marked = parse_board(after)["captures"][1]
    from agora_runner.nova_boards import split_capture_done, split_capture_priority
    _done, rest = split_capture_done(marked)
    _priority, text = split_capture_priority(rest)
    assert slug_for_capture(text) == slug_for_capture(RATED)


def test_a_second_run_changes_nothing():
    once, first = rewrite(BOARD, FINISHED)
    twice, second = rewrite(once, FINISHED)
    assert (first, second) == (2, 0)
    assert twice == once


def test_the_board_rows_and_write_ups_survive():
    before = parse_board(BOARD)
    after, _count = rewrite(BOARD, FINISHED)
    assert parse_board(after)["items"] == before["items"]
    assert parse_board(after)["details"] == before["details"]


def test_nothing_to_mark_returns_the_file_unchanged():
    """So the caller can skip the `put` rather than burn a revision."""
    after, count = rewrite(BOARD, {})
    assert (after, count) == (BOARD, 0)


def test_a_reply_written_under_his_capture_is_not_marked():
    """An indented bullet is my own sentence, not his.

    `roll_done_captures.plan` folds one into the block above for the same
    reason. Marking one would put a DONE prefix on a cycle's own reply and
    -- because the fold means the page reads them as one -- change what he
    sees his own capture say.
    """
    reply = "Read Cycle 434. Shipped it."
    with_reply = BOARD.replace(
        f"- {SHIPPED}\n", f"- {SHIPPED}\n  - {reply}\n")
    # The ledger is given a done claim on the reply's own text, so the
    # indent is the *only* thing standing between it and a DONE prefix.
    # Without this the reply is skipped for having no claim, the guard is
    # never reached, and the test passes whether or not it exists.
    ledger = dict(FINISHED)
    ledger[slug_for_capture(reply)] = 434
    after, count = rewrite(with_reply, ledger)
    assert count == 2
    assert f"  - {reply}" in after.split("\n")


def test_check_catches_a_rewrite_that_ate_his_text():
    """`check` asks the reader, so it has to fail on damage `rewrite` cannot do.

    Hand-built rather than provoked: the guard exists for a future edit to
    this file, and a guard only ever exercised by code that cannot trip it
    is one that passes because nothing reaches it.
    """
    good, count = rewrite(BOARD, FINISHED)
    assert check(BOARD, good, count) == ""
    truncated = good.replace(f"DONE (Cycle 434): {SHIPPED}", "DONE (Cycle 434): the search bar")
    assert "beyond its DONE prefix" in check(BOARD, truncated, count)
    dropped = good.replace(f"- {FRESH}\n", "")
    assert "capture count moved" in check(BOARD, dropped, count)
    assert check(BOARD, good, 1) == "marked 1 bullet(s) but 2 capture(s) changed"


def test_check_catches_a_broken_board():
    good, count = rewrite(BOARD, FINISHED)
    broken = good.replace("| #2 | The search bar closes my keyboard", "| #2 | something else")
    assert check(BOARD, broken, count) == "board rows changed"


def test_the_slug_guard_refuses_a_mark_that_ate_a_word():
    """Both directions, because a guard that only ever says yes says nothing."""
    from tools.close_done_captures import mark_kept_its_slug
    slug = slug_for_capture(SHIPPED)
    assert mark_kept_its_slug(f"- DONE (Cycle 434): {SHIPPED}", slug)
    assert mark_kept_its_slug(f"- DONE (Cycle 434): 🔵 Medium: {SHIPPED}", slug)
    assert not mark_kept_its_slug("- DONE (Cycle 434): the search bar", slug)
    assert not mark_kept_its_slug(f"- {SHIPPED}", slug)
