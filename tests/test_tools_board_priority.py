"""`tools.board_priority` -- re-rating one row moves exactly that cell.

`tests/test_board_priority.py` one file over covers `set_row_priority`, the
markdown function this tool no longer calls. What is tested here is the CLI's
own vocabulary after the #203 conversion (Cycle 1377): the change set a
re-rating writes, and the refusals that have to happen while nothing has been
written yet.

**Every test here goes through the fake store and none of them holds a line of
board markdown**, except the one string the fixture is built from -- the rule
`tests/test_tools_board_status.py` follows, for the reason it gives: a test
that asserts on `parse_board` of a file on disk agrees with a converted and an
unconverted tool alike, so it cannot tell the two apart.

The after-check is `board_write.change_row`'s now and is tested there.
"""

import pytest

from agora_runner import board_records, nova_boards
from tests.test_board_records import writable
from tools import board_priority
from tools.board_priority import (
    CLOSED_STATUS_KEYS,
    main,
    priority_changes,
    refuse_row,
    resolve_priority,
)

# #258 is the row the finished-row refusal is about: `✅ Done` and still in
# `## Board`, so `done` is false and only the status cell says it is finished.
BOARD = """- A capture nothing here may touch.

## Board

| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |
|---|---|---|---|---|---|---|---|---|
| [[#260 — Redesign the picker\\|260]] | Redesign the picker | ⚪ Backlog | 09-06 | 🟠 High | | | | |
| [[#259 — Demos live two weeks\\|259]] | Demos live two weeks | 🟡 In progress | 09-06 | 🔵 Medium | | | | |
| [[#258 — Spread the load\\|258]] | Spread the load | ✅ Done | 09-05 | | | | | |

## Done

| # | Item | Landed | Where |
|---|---|---|---|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

# Details

## #260 — Redesign the picker

The full spec lives in its own note.

## #259 — Demos live two weeks

Body text nothing here may touch.
"""


@pytest.fixture
def store(monkeypatch):
    """A migrated, writable fake of that board, wired in where `main` looks."""
    _, fake = writable(board="idea", markdown=BOARD)
    monkeypatch.setattr(board_priority, "board_store", fake)
    return fake


def _contents(store):
    return board_records.contents("idea", store=store)


def _rows(store):
    return {item["number"]: item for item in _contents(store)["items"]}


def _writes(store):
    return [call for call in store.calls if call[0] == "write_row"]


def _run(*argv, number="260", priority="immediate"):
    return main(["--board", "idea", "--number", str(number),
                 "--priority", priority, *argv])


def test_the_named_row_is_re_rated_and_keeps_everything_else(store):
    before = _rows(store)[260]
    assert _run() == 0

    row = _rows(store)[260]
    assert row["priority"] == "🔴 Immediately"
    assert row["priorityKey"] == "immediate"
    assert {k: v for k, v in row.items() if k not in ("priority", "priorityKey")} == \
        {k: v for k, v in before.items() if k not in ("priority", "priorityKey")}


def test_every_other_row_the_captures_and_the_write_ups_survive(store):
    before = _contents(store)
    assert _run() == 0
    after = _contents(store)

    assert [row for row in after["items"] if row["number"] != 260] == \
        [row for row in before["items"] if row["number"] != 260]
    assert after["captures"] == before["captures"]
    assert after["details"] == before["details"]


def test_dated_without_a_note_leaves_the_updated_cell_alone(store):
    """A re-rating is not a touch of the row; `updated` moves only with the
    note that explains why."""
    assert _run("--dated", "09-11") == 0
    assert _rows(store)[260]["updated"] == "09-06"


def test_a_note_is_appended_and_moves_updated_in_the_same_write(store):
    assert _run("--dated", "09-11", "--note", "it gates every other project",
                "--cycle", "1377") == 0

    assert len(_writes(store)) == 1
    row = _rows(store)[260]
    assert row["priority"] == "🔴 Immediately"
    assert row["updated"] == "09-11"
    body = _contents(store)["details"][260]
    assert body.startswith("The full spec lives in its own note.")
    assert "it gates every other project" in body
    assert "(Cycle 1377)" in body


def test_a_note_without_a_date_is_refused_by_name(store, capsys):
    before = _contents(store)
    assert _run("--note", "no date here") == 1
    assert "needs --dated" in capsys.readouterr().err
    assert _contents(store) == before


@pytest.mark.parametrize("flag,value", [
    ("--dated", "09-11 | extra"),
    ("--dated", " "),
    ("--dated", "09-11\nmore"),
    ("--dated", "09-11\rmore"),
    ("--note", "why | not"),
    ("--note", "why\nnot"),
    ("--note", "why\rnot"),
])
def test_a_cell_delimiter_is_refused_before_anything_is_written(store, flag, value):
    before = _contents(store)
    argv = [flag, value] + (["--dated", "09-11"] if flag == "--note" else [])
    assert _run(*argv) == 1
    assert _contents(store) == before
    assert not _writes(store)


@pytest.mark.parametrize("spelling", ["", "  ", "nearly", "🟣 Purple"])
def test_a_rating_the_system_does_not_have_is_refused(store, spelling):
    before = _contents(store)
    assert _run(priority=spelling) == 1
    assert _contents(store) == before
    assert not _writes(store)


def test_a_done_row_still_on_the_board_is_refused(store, capsys):
    """The case `done` alone cannot see. A `✅ Done` row that never moved to
    `## Done` has `done` false, and `change_row` would rate it happily --
    putting a chip on a finished item, the state Cycle 188 left empty."""
    before = _contents(store)
    assert _rows(store)[258]["done"] is False, "the fixture's finished open-table row"
    assert _rows(store)[258]["status"] == "✅ Done"

    assert _run(number=258) == 1
    assert "finished" in capsys.readouterr().err
    assert _contents(store) == before
    assert not _writes(store)


def test_a_row_in_the_done_table_is_refused(store):
    before = _contents(store)
    assert _rows(store)[51]["done"] is True, "the fixture's done row"
    assert _run(number=51) == 1
    assert _contents(store) == before
    assert not _writes(store)


def test_a_row_that_does_not_exist_is_refused(store, capsys):
    before = _contents(store)
    assert _run(number=999) == 1
    assert "not a row" in capsys.readouterr().err
    assert _contents(store) == before


def test_dry_run_prints_the_move_and_writes_nothing(store, capsys):
    before = _contents(store)
    assert _run("--dry-run") == 0
    assert "🟠 High -> 🔴 Immediately" in capsys.readouterr().out
    assert _contents(store) == before
    assert not _writes(store)


def test_it_never_opens_the_markdown(store, monkeypatch):
    """The point of the conversion: after the switchover the markdown is a view
    generated from the records, so a tool that edited it would have its edit
    overwritten by the next render. Both markdown doors raise here."""
    def no_markdown(*a, **k):
        raise AssertionError("board_priority reached for board markdown")

    monkeypatch.setattr(nova_boards, "parse_board", no_markdown)
    monkeypatch.setattr(nova_boards, "set_row_priority", no_markdown)
    assert _run("--dated", "09-11", "--note", "why") == 0
    assert _rows(store)[260]["priority"] == "🔴 Immediately"


def test_it_takes_a_board_not_a_file():
    with pytest.raises(SystemExit):
        main(["--file", "ideas.md", "--number", "260", "--priority", "high"])


@pytest.mark.parametrize("spelling", ["immediate", "🔴 Immediately", "Immediately"])
def test_every_spelling_the_rest_of_the_system_treats_as_equal(spelling):
    assert resolve_priority(spelling) == "🔴 Immediately"


def test_the_closed_status_copy_matches_the_module():
    """Hand-copied because the module's is private; this is what stops drift."""
    assert CLOSED_STATUS_KEYS == nova_boards._CLOSED_STATUS_KEYS


def test_the_change_set_carries_the_key_the_cell_derives_and_never_updated():
    assert priority_changes("🔵 Medium") == {
        "priority": "🔵 Medium", "priorityKey": "medium"}


def test_refuse_row_passes_an_open_row_and_stops_both_kinds_of_finished():
    contents = {"items": [
        {"number": 1, "done": False, "status": "⚪ Backlog"},
        {"number": 2, "done": True, "status": ""},
        {"number": 3, "done": False, "status": "⚫ Outdated"},
    ]}
    assert refuse_row(contents, 1) is None
    assert refuse_row(contents, 2) is not None
    assert refuse_row(contents, 3) is not None
    assert refuse_row(contents, 4) is not None
