"""`tools.board_status` -- moving one row's status moves exactly that cell.

`tests/test_board_status.py` one file over covers `set_row_status`, the
markdown function this tool no longer calls. What is tested here is the CLI's
own vocabulary after the #203 conversion: which cells a status move writes,
and the refusals that have to happen while nothing has been written yet.

**Every test here goes through the fake store and none of them holds a line of
board markdown**, except the one string the fixture is built from -- the rule
`tests/test_tools_board_project.py` set and for the same reason: a test that
asserts on `parse_board` of a file on disk agrees with a converted and an
unconverted tool alike, so it cannot tell the two apart.

The after-check is `board_write.change_row`'s now and is tested there. The two
things that are still this module's own are the change set (a cell and the key
`from_document` derives off it, plus the rating a close blanks) and the one
refusal with no twin below the seam: a row that is already in `## Done`.
"""

import pytest

from agora_runner import board_records, nova_boards
from tests.test_board_records import writable
from tools import board_status
from tools.board_status import (
    CLOSED_STATUS_KEYS,
    main,
    refuse_cell,
    refuse_row,
    resolve_status,
    status_changes,
)

BOARD = """- A capture nothing here may touch.

## Board

| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |
|---|---|---|---|---|---|---|---|---|
| [[#100 — Weekly work\\|100]] | Weekly work | 🟡 In progress | 08-24 | 🟠 High | | | | |
| [[#104 — Metered API\\|104]] | Metered API | ⚪ Backlog | 08-24 | 🟠 High | | | | |

## Done

| # | Item | Landed | Where |
|---|---|---|---|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

# Details

## #100 — Weekly work

Three heartbeats, one prompt file each.

## #104 — Metered API

Body text nothing here may touch.
"""


@pytest.fixture
def store(monkeypatch):
    """A migrated, writable fake of that board, wired in where `main` looks."""
    _, fake = writable(board="idea", markdown=BOARD)
    monkeypatch.setattr(board_status, "board_store", fake)
    return fake


def _contents(store):
    return board_records.contents("idea", store=store)


def _rows(store):
    return {item["number"]: item for item in _contents(store)["items"]}


def _run(*argv, number="100", status="done"):
    return main(["--board", "idea", "--number", str(number),
                 "--status", status, *argv])


def test_the_named_row_moves_and_keeps_everything_else(store):
    before = _rows(store)[100]
    assert _run("--dated", "08-26") == 0

    row = _rows(store)[100]
    assert row["status"] == "✅ Done"
    assert row["statusKey"] == "done"
    assert row["updated"] == "08-26"
    assert row["title"] == before["title"]


def test_closing_a_row_clears_its_rating(store):
    """`set_row_priority` refuses a finished row, so a chip left behind by a
    close could never be cleared again -- the two have to agree."""
    assert _rows(store)[100]["priority"] == "🟠 High"
    assert _run("--dated", "08-26") == 0
    assert _rows(store)[100]["priority"] == ""
    assert _rows(store)[100]["priorityKey"] == ""


def test_an_open_status_keeps_the_rating(store):
    assert _run("--dated", "08-26", number=104, status="in-progress") == 0
    assert _rows(store)[104]["priority"] == "🟠 High"
    assert _rows(store)[104]["status"] == "🟡 In progress"


def test_no_dated_leaves_the_updated_cell_alone(store):
    """`--dated` is optional and omitting it must not blank the cell."""
    assert _run() == 0
    assert _rows(store)[100]["updated"] == "08-24"


def test_every_other_row_and_the_captures_survive(store):
    before = _contents(store)
    assert _run("--dated", "08-26") == 0
    after = _contents(store)

    assert [row for row in after["items"] if row["number"] != 100] == \
        [row for row in before["items"] if row["number"] != 100]
    assert after["captures"] == before["captures"]
    assert after["details"][104] == before["details"][104]


def test_a_note_is_appended_under_the_row_it_explains(store):
    assert _run("--dated", "08-26", "--note", "all three heartbeats fire",
                "--cycle", "498") == 0
    body = _contents(store)["details"][100]
    assert body.startswith("Three heartbeats, one prompt file each.")
    assert "all three heartbeats fire" in body
    assert "(Cycle 498)" in body


def test_a_note_and_its_status_move_land_in_one_write(store):
    """The reason a status move waited for `append_note` rather than doing two
    `change_row` calls: a window where his board says a row closed and nothing
    says why is issue #85 re-created by the tool that closes it."""
    assert _run("--dated", "08-26", "--note", "closed it") == 0

    writes = [call for call in store.calls if call[0] == "write_row"]
    assert len(writes) == 1
    row = _rows(store)[100]
    assert row["status"] == "✅ Done"
    assert "closed it" in _contents(store)["details"][100]


def test_a_note_takes_the_updated_cell_from_its_own_date(store):
    """`append_note` refuses a change set that also names `updated`, so this
    asserts the tool leaves it out *and* that the cell still moves."""
    assert _run("--dated", "08-26", "--note", "closed it") == 0
    assert _rows(store)[100]["updated"] == "08-26"


def test_a_note_without_a_date_is_refused_by_name(store, capsys):
    """The message is asserted, not just the exit code. `append_note` already
    refuses a dateless note, so deleting this guard leaves the exit code at 1
    and every other assertion here green -- what is lost is the caller being
    told *which* argument is missing, where the fallback names five causes."""
    before = _rows(store)
    assert _run("--note", "no date here") == 1
    assert "needs --dated" in capsys.readouterr().err
    assert _rows(store) == before


@pytest.mark.parametrize("flag,value", [
    ("--dated", "08-26 | extra"),
    ("--dated", " "),
    ("--dated", "08-26\nmore"),
    # A `\r` on the `--dated` path is the one this module has to catch by
    # itself: with no `--note` the write goes through `change_row`, which has
    # no cell rules at all, so nothing below this seam would refuse it. The
    # `--note` cases below are belt and braces -- `_note_line` refuses both
    # characters too -- and they are here to pin the exit code, not the guard.
    ("--dated", "08-26\rmore"),
    ("--note", "why | not"),
    ("--note", "why\rnot"),
])
def test_a_cell_delimiter_is_refused_before_anything_is_written(store, flag, value):
    before = _contents(store)
    argv = [flag, value] + (["--dated", "08-26"] if flag == "--note" else [])
    assert _run(*argv) == 1
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_an_unknown_status_is_refused(store):
    before = _rows(store)
    assert _run(status="nearly done") == 1
    assert _rows(store) == before


def test_a_row_already_in_done_is_refused(store, capsys):
    """The one refusal with no twin inside `change_row`. In markdown it was
    structural -- `_row_span` was told to look at `## Board` only. Against
    records a closed row is an ordinary row with `done` set, so `change_row`
    would write it and the after-check would agree."""
    before = _contents(store)
    assert _rows(store)[51]["done"] is True, "the fixture's done row"

    assert _run(number=51) == 1
    assert "already in '## Done'" in capsys.readouterr().err
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_a_row_that_does_not_exist_is_refused(store):
    before = _contents(store)
    assert _run(number=999) == 1
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_dry_run_reports_but_does_not_write(store):
    before = _contents(store)
    assert _run("--dated", "08-26", "--dry-run") == 0
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_every_written_status_spelling_is_accepted():
    for key, label in nova_boards.STATUS_LABELS.items():
        assert resolve_status(key) == label
        assert resolve_status(label) == label
    assert resolve_status("Done") == "✅ Done"
    assert resolve_status("") is None
    assert resolve_status("nearly") is None


def test_the_closed_status_copy_matches_the_module():
    """This constant is hand-copied because the module's is private.
    Two hand-copied constants drift; this is the check that they cannot."""
    assert CLOSED_STATUS_KEYS == nova_boards._CLOSED_STATUS_KEYS


def test_the_change_set_carries_the_key_the_cell_derives():
    """`from_document` derives `statusKey` off the stored cell, so a change set
    naming only the cell reads back disagreeing with itself."""
    assert status_changes("🟡 In progress") == {
        "status": "🟡 In progress", "statusKey": "in-progress"}


def test_the_change_set_blanks_both_halves_of_the_rating_on_a_close():
    for label in ("✅ Done", "⚫ Outdated"):
        changes = status_changes(label)
        assert changes["priority"] == ""
        assert changes["priorityKey"] == ""


def test_the_change_set_only_names_updated_when_a_date_was_given():
    assert "updated" not in status_changes("✅ Done")
    assert status_changes("✅ Done", dated="08-26")["updated"] == "08-26"


def test_refuse_cell_names_the_flag_it_was_asked_about():
    assert refuse_cell(None, "--dated") is None
    assert refuse_cell("08-26", "--dated") is None
    assert "--dated" in refuse_cell("", "--dated")
    assert "--note" in refuse_cell("a | b", "--note")
    # CommonMark makes a bare `\r` a line ending, so Obsidian renders the
    # break on his phone even though a `"\n" in value` check sees nothing.
    assert refuse_cell("08-26\rmore", "--dated") is not None


def test_refuse_row_passes_an_open_row_and_stops_a_closed_one():
    contents = {"items": [{"number": 1, "done": False},
                          {"number": 2, "done": True}]}
    assert refuse_row(contents, 1) is None
    assert refuse_row(contents, 2) is not None
    assert refuse_row(contents, 3) is not None
