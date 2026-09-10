"""`tools.close_done_captures` -- the ledger closes his captures, nothing else does.

Two failures are guarded here and they point opposite ways. Marking too
little leaves the box he types into full of finished work, which is the
complaint this tool answers. Marking too much hides a capture he is
still waiting on, and a capture is a bare bullet with no status cell --
once it reads `DONE`, `top_board_rows` skips it and `roll_done_captures`
files it away, so there is no second reader left to notice.

**The #203 conversion moved where each of those is asserted.** Everything
here goes through the fake record store, the rule
`tests/test_tools_board_capture.py` set and for its reason: a test that
asserts on `parse_board` of a file on disk agrees with a converted and an
unconverted tool alike, so it cannot tell the two apart. The whole-board
after-check that used to live in this module is
`board_write.change_capture_text`'s now and is tested there; what is still
this module's own is which bullets it picks, that the pick is idempotent,
and that a refusal arriving mid-walk says how far the run got.
"""

import json

import pytest

from agora_runner import board_records, board_write
from agora_runner.nova_claims import slug_for_capture
from tests.test_board_records import writable
from tools import close_done_captures
from tools.close_done_captures import done_cycles, mark_kept_its_slug, plan

SHIPPED = "the search bar closes my keyboard"
RATED = "make the chat modal full height"
OPEN = "connect Nova to my home NAS"
FRESH = "the sidebar scrolls off the bottom"

BOARD = f"""- {SHIPPED}
  - Nova, cycle 434: shipped it.
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


@pytest.fixture
def store(monkeypatch):
    """A migrated, writable fake of that board, wired in where `main` looks."""
    _, fake = writable(board="issue", markdown=BOARD)
    monkeypatch.setattr(close_done_captures, "board_store", fake)
    return fake


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "claims.json"
    path.write_text(LEDGER, encoding="utf-8")
    return str(path)


def _run(ledger, *extra):
    return close_done_captures.main(
        ["--board", "issue", "--claims", ledger, *extra])


def _captures(store):
    return board_records.contents("issue", store=store)["captures"]


def _docs(store):
    return board_records.capture_documents("issue", store=store)


# --- which bullets the ledger closes ------------------------------------


def test_only_capture_slugs_come_out_of_the_ledger():
    """`idea-88` is done too and is a board row, not one of his bullets."""
    assert set(FINISHED) == {slug_for_capture(SHIPPED), slug_for_capture(RATED)}
    assert FINISHED[slug_for_capture(SHIPPED)] == 434


def test_a_progressed_claim_does_not_close_his_capture(store):
    """The failure that would cost most: `progressed` means work is left.

    Three of the 21 live captures on 2026-08-26 were `progressed` -- the
    IDP one and the Groq key among them -- and each carries a question
    still waiting on him. Reading `progressed` as finished would file all
    three away where no later cycle looks.
    """
    assert OPEN not in [old for _d, old, _n, _s, _c in plan(_docs(store), FINISHED)]


def test_an_unclaimed_capture_is_left_alone(store, ledger):
    """His newest bullet has no claim at all and must survive untouched."""
    assert _run(ledger) == 0
    assert FRESH in _captures(store)


def test_the_finished_ones_are_marked_with_the_cycle_that_closed_them(store, ledger):
    assert _run(ledger) == 0
    captures = _captures(store)
    assert captures[0] == f"DONE (Cycle 434): {SHIPPED}"
    # The rating stays where he put it: `split_capture_done` runs before
    # `split_capture_priority` in every reader, so the marker goes in
    # front of the glyph rather than behind it.
    assert captures[1] == f"DONE (Cycle 435): 🔵 Medium: {RATED}"
    assert captures[2] == OPEN


def test_marking_does_not_move_the_slug(store, ledger):
    """The invariant the whole tool rests on.

    `slug_for_capture` is hashed off his sentence with the marker and the
    rating stripped. If a marked bullet hashed differently the ledger
    would stop matching it, the next run would mark it again, and the
    prefix would stack.
    """
    assert _run(ledger) == 0
    assert mark_kept_its_slug(_captures(store)[1], slug_for_capture(RATED))


def test_a_second_run_changes_nothing(store, ledger, capsys):
    assert _run(ledger) == 0
    once = _captures(store)
    capsys.readouterr()
    assert _run(ledger) == 0
    assert "nothing to mark" in capsys.readouterr().out
    assert _captures(store) == once


def test_the_reply_under_his_capture_survives_the_mark(store, ledger):
    """A reply is a field on the document now, not a bullet in the walk.

    In markdown it was an indented line the walk had to skip, and marking
    one would have put a DONE prefix on a cycle's own sentence. The record
    store carries it as `replies`, and `change_capture_text` copies it
    across rather than taking it from the caller -- so the thing to assert
    is that a mark leaves it where it was, on the bullet it belongs to.
    """
    before = board_records.contents("issue", store=store)["captureReplies"]
    assert before[0] and before[0][0].endswith("shipped it.")
    assert _run(ledger) == 0
    assert board_records.contents("issue", store=store)["captureReplies"] == before


def test_the_board_rows_and_write_ups_survive(store, ledger):
    before = board_records.contents("issue", store=store)
    assert _run(ledger) == 0
    after = board_records.contents("issue", store=store)
    assert after["items"] == before["items"]
    assert after["details"] == before["details"]


def test_nothing_to_mark_writes_nothing(store, tmp_path, capsys):
    """So a run on a quiet ledger burns no revision on any document."""
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"claims": []}), encoding="utf-8")
    before = [dict(doc) for doc in _docs(store)]
    assert _run(str(empty)) == 0
    assert "nothing to mark" in capsys.readouterr().out
    assert [dict(doc) for doc in _docs(store)] == before


def test_the_slug_guard_refuses_a_mark_that_ate_a_word():
    """Both directions, because a guard that only ever says yes says nothing."""
    slug = slug_for_capture(SHIPPED)
    assert mark_kept_its_slug(f"DONE (Cycle 434): {SHIPPED}", slug)
    assert mark_kept_its_slug(f"DONE (Cycle 434): 🔵 Medium: {SHIPPED}", slug)
    assert not mark_kept_its_slug("DONE (Cycle 434): the search bar", slug)
    assert not mark_kept_its_slug(SHIPPED, slug)


# --- what the walk hands to the writer ----------------------------------


def test_the_walk_hands_over_the_document_it_read(store):
    """Not a fresh one built from the text.

    `change_capture_text` refuses a document with no `_rev`, because that
    revision is the only thing between this rewrite and clobbering a reply
    another cycle wrote under his bullet while I was deciding to mark it.
    A `plan` that returned the text alone would make the caller mint one.
    """
    read = {doc["_id"]: doc for doc in _docs(store)}
    for doc, _old, _new, _slug, _cycle in plan(list(read.values()), FINISHED):
        assert doc is read[doc["_id"]]
        assert doc["_rev"]


def test_a_refusal_mid_walk_says_how_many_landed(store, ledger, capsys):
    """The one thing a per-bullet write owes that a single write does not.

    The second mark is refused after the first has landed. A run that
    printed only the refusal would read as a run that wrote nothing, and
    the next cycle would go looking for two unmarked bullets when one of
    them is already marked.
    """
    real = board_write.change_capture_text
    calls = {"n": 0}

    def refuse_the_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise board_write.CaptureRefused("no")
        return real(*args, **kwargs)

    close_done_captures.board_write.change_capture_text = refuse_the_second
    try:
        assert _run(ledger) == 1
    finally:
        close_done_captures.board_write.change_capture_text = real
    assert "marked 1 capture(s), then stopped" in capsys.readouterr().err
    # And the half that landed really landed, which is what makes the
    # count a fact rather than a guess.
    assert _captures(store)[0] == f"DONE (Cycle 434): {SHIPPED}"


def test_a_dry_run_writes_nothing(store, ledger, capsys):
    before = [dict(doc) for doc in _docs(store)]
    assert _run(ledger, "--dry-run") == 0
    assert "would mark 2 capture(s)" in capsys.readouterr().out
    assert [dict(doc) for doc in _docs(store)] == before


def test_an_unmigrated_board_is_refused_rather_than_read_empty(monkeypatch, ledger, capsys):
    """`capture_documents` raises `UnmigratedStore`; an empty walk would not.

    A store with no records answers "no captures", which reads exactly like
    a board whose bullets are all marked already -- and the run would exit 0
    having looked at nothing.
    """
    def unmigrated(*args, **kwargs):
        raise board_records.UnmigratedStore("no records here")

    monkeypatch.setattr(board_records, "capture_documents", unmigrated)
    assert _run(ledger) == 1
    assert "no records here" in capsys.readouterr().err


def test_a_mark_that_moved_a_slug_stops_the_run_before_any_write(store, ledger, monkeypatch, capsys):
    """The guard `main` cannot trip from the inside, driven from the outside.

    `mark_kept_its_slug` re-hashes the bullet this run is about to write, and
    no correct mark can fail it -- so nothing in the walk reaches the refusal,
    and a mutation deleting it survived the first round of this file. It is
    worth keeping rather than deleting because it is the one failure this tool
    cannot make quietly: a mark that ate a word stops matching the ledger, the
    next run marks the shortened bullet again under a new slug, and the prefix
    stacks.

    The assertion that matters is the second one. The check runs over every
    mark before the first write, so a refusal on the last of them still costs
    nothing -- and a version that checked each bullet as it wrote it would
    pass the exit code and fail this.
    """
    before = [dict(doc) for doc in _docs(store)]
    monkeypatch.setattr(close_done_captures, "mark_kept_its_slug",
                        lambda text, slug: False)
    assert _run(ledger) == 1
    assert "marking moved the slug" in capsys.readouterr().err
    assert [dict(doc) for doc in _docs(store)] == before
