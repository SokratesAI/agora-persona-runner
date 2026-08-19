"""Editing and deleting a boarded row -- Edvard's issue #84.

*"I need to be able to edit and especially delete boarded ideas and issues
from the agora app. If i hold the card for more than 1 second i get into
edit mode and also have the option of deleting, save or cancel the edit."*

The first half of this file is the parser fix the delete path needed: a
write-up headed `### #84 — ...` was invisible to `parse_board`, which is
21 of Edvard's 85 issue rows and 4 of his 72 idea rows.
"""

import agora_runner.nova_capture as nova_capture
from agora_runner.nova_boards import delete_row, parse_board, set_row_title

# Both live heading shapes in one fixture, because the bug was that only
# one of them parsed: `## 57 —` is what the older write-ups use and
# `### #84 —` is what every issue from #70 up uses.
BOARD = """---
type: board
---

- an unboarded capture

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#57 — More pages\\|57]] | More pages | 🟡 In progress | 08-11 | 🔵 Medium |
| [[#84 — Hold a card\\|84]] | Hold a card | 🟡 In progress | 08-14 | 🔴 Immediately |
| #59 | No link on this one | ⚪ Backlog | 08-11 |

## Done

| # | Item | Landed | Where |
|---|------|--------|-------|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

# Details

## 57 — More pages

Body text I must not touch.

### #84 — Hold a card

His words about holding a card.

### A subheading inside the write-up

Still part of #84, and the whole reason `_sections` was not widened.

## 51 — One way

The finished one.

## Notes

Not a write-up, and not part of #51.
"""


# --- the parser fix ---

def test_the_hash_hash_hash_shape_is_a_write_up_at_all():
    details = parse_board(BOARD)["details"]
    assert 84 in details, "### #84 — ... was invisible to the page"
    assert "His words about holding a card." in details[84]


def test_a_subheading_inside_a_write_up_stays_inside_it():
    # Widening `_SECTION_RE` to `#{1,3}` would have been the small fix and
    # would have truncated #84 here. Three live write-ups in `ideas.md`
    # carry subheadings exactly like this one.
    body = parse_board(BOARD)["details"][84]
    assert "### A subheading inside the write-up" in body
    assert "the whole reason `_sections` was not widened" in body


def test_the_older_two_hash_shape_still_parses():
    details = parse_board(BOARD)["details"]
    assert 57 in details and "Body text I must not touch." in details[57]


def test_a_write_up_stops_at_the_next_item_and_not_before():
    assert "One way" not in parse_board(BOARD)["details"][84]
    assert "The finished one." in parse_board(BOARD)["details"][51]


def test_a_write_up_also_stops_at_a_heading_that_is_not_an_item():
    # The last block in the file has no next item to end it, so a section
    # like `## Notes` is the only thing that closes it. Without that, the
    # last write-up swallows the rest of the document -- and `delete_row`
    # would then take the rest of the document with it.
    assert "Not a write-up" not in parse_board(BOARD)["details"][51]
    updated = delete_row(BOARD, 51)
    assert "## Notes" in updated and "Not a write-up, and not part of #51." in updated


# --- edit ---

def test_edit_moves_the_cell_the_link_and_the_heading_together():
    updated = set_row_title(BOARD, 84, "Hold a card to edit or delete it")
    assert "| [[#84 — Hold a card to edit or delete it\\|84]] |" in updated
    assert "| Hold a card to edit or delete it |" in updated
    assert "### #84 — Hold a card to edit or delete it" in updated
    # The old title survives nowhere: an Obsidian link resolves by heading
    # text, so a half-done rename leaves him a row that links to nothing.
    assert "Hold a card\\|84" not in updated
    row = [i for i in parse_board(updated)["items"] if i["number"] == 84][0]
    assert row["title"] == "Hold a card to edit or delete it"
    assert row["status"] == "🟡 In progress" and row["priority"] == "Immediately"


def test_edit_keeps_the_heading_depth_it_found():
    two = set_row_title(BOARD, 57, "Renamed")
    assert "## 57 — Renamed" in two and "### 57 — Renamed" not in two
    three = set_row_title(BOARD, 84, "Renamed")
    assert "### #84 — Renamed" in three


def test_edit_touches_exactly_the_two_lines_it_must():
    updated = set_row_title(BOARD, 84, "Renamed")
    before, after = BOARD.split("\n"), updated.split("\n")
    assert len(before) == len(after)
    differing = [i for i in range(len(before)) if before[i] != after[i]]
    assert len(differing) == 2
    assert differing[0] < differing[1]
    assert before[differing[0]].startswith("| [[#84")
    assert before[differing[1]].startswith("### #84")


def test_edit_works_on_a_row_with_no_link_and_no_write_up():
    # Both repetitions of the title are optional; only the cell is not.
    updated = set_row_title(BOARD, 59, "Renamed")
    row = [i for i in parse_board(updated)["items"] if i["number"] == 59][0]
    assert row["title"] == "Renamed" and row["status"] == "⚪ Backlog"
    assert "| #59 | Renamed |" in updated


def test_edit_refuses_a_missing_row_an_empty_title_and_a_pipe():
    assert set_row_title(BOARD, 999, "Anything") is None
    assert set_row_title(BOARD, 84, "   ") is None
    # A title is one cell of a markdown table; either of these ends the
    # row somewhere he did not mean it to.
    assert set_row_title(BOARD, 84, "a | b") is None
    assert set_row_title(BOARD, 84, "a\nb") is None


def test_edit_reaches_a_finished_row_where_rating_one_does_not():
    # `set_row_priority` refuses a `## Done` row on purpose. Retitling one
    # is a different question and he asked for it on "boarded ideas and
    # issues", not on open ones.
    assert set_row_title(BOARD, 51, "Renamed") is not None


# --- delete ---

def test_delete_takes_the_row_and_its_write_up():
    updated = delete_row(BOARD, 84)
    board = parse_board(updated)
    assert not [i for i in board["items"] if i["number"] == 84]
    assert 84 not in board["details"]
    assert "His words about holding a card." not in updated
    # Including the subheading, which belongs to #84's body.
    assert "A subheading inside the write-up" not in updated


def test_delete_leaves_every_other_row_and_write_up_alone():
    updated = delete_row(BOARD, 84)
    board = parse_board(updated)
    assert sorted(i["number"] for i in board["items"]) == [51, 57, 59]
    assert board["details"][57] == parse_board(BOARD)["details"][57]
    assert board["details"][51] == parse_board(BOARD)["details"][51]
    assert board["captures"] == ["an unboarded capture"]


def test_delete_reaches_a_finished_row():
    # `## Done` is where the rows he has stopped caring about pile up, so
    # refusing one would refuse the most likely delete.
    updated = delete_row(BOARD, 51)
    assert not [i for i in parse_board(updated)["items"] if i["number"] == 51]
    assert "The finished one." not in updated


def test_delete_of_a_row_that_is_not_there_writes_nothing():
    assert delete_row(BOARD, 999) is None


# --- the vault write around both ---

def test_edit_writes_once_and_sends_the_revision_it_read(monkeypatch):
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (BOARD, "7-abc"))
    calls = []

    def fake_write(path, body, if_rev=None):
        # Counted rather than recorded: a retry loop that failed to break
        # on success would leave the other assertions passing.
        calls.append((path, body, if_rev))
        return "written"

    monkeypatch.setattr(nova_capture, "vault_write_path", fake_write)
    ok, message = nova_capture.edit_row("issues", 84, "Renamed")
    assert ok and "#84" in message
    assert len(calls) == 1
    path, body, if_rev = calls[0]
    assert path == "projects/sokrates/projects/nova/issues.md"
    assert if_rev == "7-abc"
    assert "### #84 — Renamed" in body


def test_delete_writes_once_and_sends_the_revision_it_read(monkeypatch):
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (BOARD, "7-abc"))
    calls = []
    monkeypatch.setattr(
        nova_capture, "vault_write_path",
        lambda path, body, if_rev=None: calls.append((path, body, if_rev)) or "written")
    ok, message = nova_capture.remove_row("ideas", 84)
    assert ok and "#84" in message
    assert len(calls) == 1
    assert calls[0][0] == "projects/sokrates/projects/nova/ideas.md"
    assert calls[0][2] == "7-abc"
    assert "Hold a card" not in calls[0][1]


def test_neither_writes_when_the_row_is_gone(monkeypatch):
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (BOARD, "7-abc"))

    def refuse(*a, **k):
        raise AssertionError("must not write")

    monkeypatch.setattr(nova_capture, "vault_write_path", refuse)
    ok, message = nova_capture.edit_row("issues", 999, "Renamed")
    assert not ok and "not a row" in message
    ok, message = nova_capture.remove_row("issues", 999)
    assert not ok and "not a row" in message


def test_a_conflict_is_retried_against_a_fresh_read(monkeypatch):
    reads = []

    def read(path):
        reads.append(path)
        return BOARD, f"{len(reads)}-abc"

    results = ["409 conflict", "written"]
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", read)
    monkeypatch.setattr(
        nova_capture, "vault_write_path",
        lambda path, body, if_rev=None: results.pop(0))
    ok, _ = nova_capture.edit_row("issues", 84, "Renamed")
    assert ok
    # The second attempt re-read rather than resending the body it built
    # against a revision that no longer exists.
    assert len(reads) == 2


def test_a_non_conflict_failure_is_not_retried(monkeypatch):
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (BOARD, "7-abc"))
    calls = []
    monkeypatch.setattr(
        nova_capture, "vault_write_path",
        lambda path, body, if_rev=None: calls.append(1) or "500 boom")
    ok, message = nova_capture.remove_row("issues", 84)
    assert not ok and "500 boom" in message
    assert len(calls) == 1


def test_notes_is_not_a_board_and_the_row_writers_refuse_it(monkeypatch):
    """The branch where `BOARD_PATHS` and `CAPTURE_TARGETS` actually differ.

    They hold the same string for `issues` and `ideas`, so a test asking
    which file was written passes under either lookup -- it compares the
    code to a coincidence. `notes` is the difference: it is a capture
    target and not a board, it has no `## Board` table, and under the
    capture dict these two would resolve it and write to his notes file.
    """
    def refuse(*a, **k):
        raise AssertionError("must not touch notes.md")

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", refuse)
    monkeypatch.setattr(nova_capture, "vault_write_path", refuse)
    for call in (lambda: nova_capture.edit_row("notes", 1, "Renamed"),
                 lambda: nova_capture.remove_row("notes", 1)):
        ok, message = call()
        assert not ok and "unknown target" in message
