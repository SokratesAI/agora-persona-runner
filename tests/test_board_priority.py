"""Edvard changing a rating a cycle wrote (`issues.md` capture, 2026-08-14)."""

import agora_runner.nova_capture as nova_capture
from agora_runner.nova_boards import PRIORITY_LABELS, parse_board, set_row_priority

BOARD = """---
type: board
---

- an unboarded capture

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#57 — More pages\\|57]] | More pages | 🟡 In progress | 08-11 | 🔵 Medium |
| [[#59 — Small pickings\\|59]] | Small pickings | ⚪ Backlog | 08-11 |

## Done

| # | Item | Landed | Where |
|---|------|--------|-------|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

## #57 — More pages

Body text I must not touch.
"""


def test_changes_the_cell_and_parse_board_reads_it_back():
    updated = set_row_priority(BOARD, 57, "🔴 Immediately")
    row = [i for i in parse_board(updated)["items"] if i["number"] == 57][0]
    assert row["priority"] == "🔴 Immediately"
    assert row["priorityKey"] == "immediate"
    assert row["title"] == "More pages" and row["status"] == "🟡 In progress"


def test_grows_the_fifth_cell_on_a_row_that_never_had_one():
    updated = set_row_priority(BOARD, 59, "🟠 High")
    row = [i for i in parse_board(updated)["items"] if i["number"] == 59][0]
    assert row["priority"] == "🟠 High"
    assert row["updated"] == "08-11"


def test_clearing_back_to_unrated_is_reachable():
    updated = set_row_priority(BOARD, 57, "")
    row = [i for i in parse_board(updated)["items"] if i["number"] == 57][0]
    assert row["priority"] == "" and row["priorityKey"] == ""


def test_touches_nothing_but_the_one_row():
    updated = set_row_priority(BOARD, 57, "🟠 High")
    before, after = BOARD.split("\n"), updated.split("\n")
    assert len(before) == len(after)
    differing = [i for i in range(len(before)) if before[i] != after[i]]
    assert len(differing) == 1 and "#57" in before[differing[0]]


def test_refuses_a_done_row_a_missing_row_and_a_rating_i_did_not_offer():
    assert set_row_priority(BOARD, 51, "🟠 High") is None
    assert set_row_priority(BOARD, 999, "🟠 High") is None
    assert set_row_priority(BOARD, 57, "🟣 Whenever") is None
    assert set_row_priority(BOARD, 57, "high") is None


def test_labels_round_trip_through_priority_key():
    from agora_runner.nova_boards import priority_key
    for key, label in PRIORITY_LABELS.items():
        assert priority_key(label) == key


def test_set_priority_writes_once_and_sends_the_revision_it_read(monkeypatch):
    seen = {}
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (BOARD, "7-abc"))

    def fake_write(path, body, if_rev=None):
        seen.update(path=path, body=body, if_rev=if_rev)
        return "written"

    monkeypatch.setattr(nova_capture, "vault_write_path", fake_write)
    ok, message = nova_capture.set_priority("issues", 57, "🟠 High")
    assert ok and "#57" in message
    assert seen["if_rev"] == "7-abc"
    assert "🟠 High" in seen["body"]


def test_set_priority_does_not_write_when_the_row_is_not_open(monkeypatch):
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (BOARD, "7-abc"))

    def refuse(*a, **k):
        raise AssertionError("must not write")

    monkeypatch.setattr(nova_capture, "vault_write_path", refuse)
    ok, message = nova_capture.set_priority("issues", 51, "🟠 High")
    assert not ok and "not an open row" in message


# --- the other half of the same capture: a rating typed with the capture ---

from agora_runner.nova_boards import split_capture_priority


def test_a_rated_bullet_splits_into_a_rating_and_his_words():
    assert split_capture_priority("🟠 fix the sort order") == ("🟠 High", "fix the sort order")
    assert split_capture_priority("🔴 the app is down") == ("🔴 Immediately", "the app is down")


def test_an_unrated_bullet_comes_back_whole():
    assert split_capture_priority("just a thought") == ("", "just a thought")
    # Only a leading glyph is a rating; the same emoji mid-sentence is prose.
    assert split_capture_priority("the 🔴 dot is wrong") == ("", "the 🔴 dot is wrong")


def test_capture_prefixes_only_the_first_bullet_of_a_paste(monkeypatch):
    written = {}
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: ("---\n---\n\n- \n\n## Board\n", "1-a"))
    monkeypatch.setattr(
        nova_capture, "vault_write_path",
        lambda path, body, if_rev=None: written.update(body=body) or "written")
    ok, _ = nova_capture.capture("issues", "first line\nsecond line", "🟠 High")
    assert ok
    assert "- 🟠 first line" in written["body"]
    assert "- second line" in written["body"]
    assert "🟠 second line" not in written["body"]


def test_capture_refuses_a_rating_that_is_not_one_of_the_four(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("must not read or write")

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", refuse)
    ok, message = nova_capture.capture("issues", "text", "🟣 Whenever")
    assert not ok and "unknown priority" in message


def test_an_unrated_capture_is_written_exactly_as_typed(monkeypatch):
    written = {}
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: ("---\n---\n\n- \n\n## Board\n", "1-a"))
    monkeypatch.setattr(
        nova_capture, "vault_write_path",
        lambda path, body, if_rev=None: written.update(body=body) or "written")
    ok, _ = nova_capture.capture("issues", "plain thought")
    assert ok and "- plain thought" in written["body"]
