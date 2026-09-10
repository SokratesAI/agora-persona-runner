"""`tools.board_milestone` -- grouping one row moves exactly that cell.

`tests/test_board_milestone.py` one file over covers `set_row_milestone`, the
markdown function this tool no longer calls. What is tested here is the CLI's
own vocabulary after the #203 conversion: which cell a regrouping writes, that
the name lands under the row's own project, and the refusals that have to
happen while nothing has been written yet.

**Every test here goes through the fake store and none of them holds a line of
board markdown**, except the one string the fixture is built from -- the rule
`tests/test_tools_board_project.py` set and for the same reason: a test that
asserts on `parse_board` of a file on disk agrees with a converted and an
unconverted tool alike, so it cannot tell the two apart.

The after-check is `board_write.change_row`'s now and is tested there, and the
cell-delimiter rule is `board_write.refuse_cell`'s. What is still this module's
own is the change set (one cell, no derived key), the `allow_blank` on
`--milestone` alone, and the one refusal with no twin below the seam: a
finished row.
"""

import pytest

from agora_runner import board_records
from tests.test_board_records import writable
from tools import board_milestone
from tools.board_milestone import main, milestone_changes, refuse_row

BOARD = """- A capture nothing here may touch.

## Board

| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |
|---|---|---|---|---|---|---|---|---|
| [[#100 — Weekly work\\|100]] | Weekly work | 🟡 In progress | 08-24 | 🟠 High | Nova | | Cost and quota | |
| [[#104 — Metered API\\|104]] | Metered API | ⚪ Backlog | 08-24 | 🟠 High | Marcus | | | |

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
    monkeypatch.setattr(board_milestone, "board_store", fake)
    return fake


def _contents(store):
    return board_records.contents("idea", store=store)


def _rows(store):
    return {item["number"]: item for item in _contents(store)["items"]}


def _run(*argv, number="100", milestone="Picking redesign"):
    return main(["--board", "idea", "--number", str(number),
                 "--milestone", milestone, *argv])


def test_the_named_row_is_regrouped_and_keeps_everything_else(store):
    before = _rows(store)[100]
    assert before["milestone"] == "Cost and quota", "the fixture's grouping"
    assert _run("--dated", "09-06") == 0

    row = _rows(store)[100]
    assert row["milestone"] == "Picking redesign"
    assert row["updated"] == "09-06"
    assert row["status"] == before["status"]
    assert row["priority"] == before["priority"]
    assert row["project"] == before["project"]


def test_an_empty_milestone_clears_the_cell_back_to_ungrouped(store):
    """The one blank this set accepts. A milestone that turns out to be two is
    regrouped by first emptying it, so this has to stay reachable."""
    assert _run("--dated", "09-06", milestone="") == 0
    assert _rows(store)[100]["milestone"] == ""


def test_the_name_lands_under_the_rows_own_project(store):
    """Against records the scoping is an id, not a coincidence of spelling:
    `store_item` mints the milestone under the row's *own* project, so the same
    name on two rows of two projects is two milestones."""
    assert _run(number=100, milestone="Backup") == 0
    assert _run(number=104, milestone="Backup") == 0

    registry = store.read_registry()
    by_project = {}
    for milestone_id, entry in registry["milestones"].items():
        if entry.get("name") == "Backup":
            by_project[entry["projectId"]] = milestone_id
    assert len(by_project) == 2, registry["milestones"]
    assert _rows(store)[100]["milestone"] == "Backup"
    assert _rows(store)[104]["milestone"] == "Backup"


def test_no_dated_leaves_the_updated_cell_alone(store):
    """`--dated` is optional and omitting it must not blank the cell."""
    assert _run() == 0
    assert _rows(store)[100]["updated"] == "08-24"


def test_every_other_row_and_the_captures_survive(store):
    before = _contents(store)
    assert _run("--dated", "09-06") == 0
    after = _contents(store)

    assert [row for row in after["items"] if row["number"] != 100] == \
        [row for row in before["items"] if row["number"] != 100]
    assert after["captures"] == before["captures"]
    assert after["details"][104] == before["details"][104]
    assert after["details"][100] == before["details"][100]


def test_a_note_is_appended_under_the_row_it_explains(store):
    assert _run("--dated", "09-06", "--note", "cost and quota split in two",
                "--cycle", "1333") == 0
    body = _contents(store)["details"][100]
    assert body.startswith("Three heartbeats, one prompt file each.")
    assert "cost and quota split in two" in body
    assert "(Cycle 1333)" in body


def test_a_note_and_the_regrouping_land_in_one_write(store):
    """Two `change_row` calls would be two writes and a window in between
    where the row has moved and nothing on it says why."""
    assert _run("--dated", "09-06", "--note", "why it moved") == 0

    writes = [call for call in store.calls if call[0] == "write_row"]
    assert len(writes) == 1
    assert _rows(store)[100]["milestone"] == "Picking redesign"
    assert "why it moved" in _contents(store)["details"][100]


def test_a_note_takes_the_updated_cell_from_its_own_date(store):
    """`append_note` refuses a change set that also names `updated`, so this
    asserts the tool leaves it out *and* that the cell still moves."""
    assert _run("--dated", "09-06", "--note", "why it moved") == 0
    assert _rows(store)[100]["updated"] == "09-06"


def test_a_note_without_a_date_is_refused_by_name(store, capsys):
    """The message is asserted, not just the exit code. `append_note` already
    refuses a dateless note, so deleting this guard leaves the exit code at 1
    and every other assertion here green -- what is lost is the caller being
    told *which* argument is missing."""
    before = _rows(store)
    assert _run("--note", "no date here") == 1
    assert "needs --dated" in capsys.readouterr().err
    assert _rows(store) == before


@pytest.mark.parametrize("flag,value", [
    ("--milestone", "Picking | redesign"),
    ("--milestone", "Picking\nredesign"),
    ("--milestone", "Picking\rredesign"),
    # The `--dated` path with no note goes through `change_row`, which has no
    # cell rules at all, so nothing below this seam would refuse it.
    ("--dated", "09-06 | extra"),
    ("--dated", " "),
    ("--dated", "09-06\rmore"),
    # Belt and braces: `_note_line` refuses these too, so these pin the exit
    # code rather than this module's guard.
    ("--note", "why | not"),
    ("--note", "why\rnot"),
])
def test_a_cell_delimiter_is_refused_before_anything_is_written(store, flag, value):
    before = _contents(store)
    argv = [] if flag == "--milestone" else [flag, value]
    if flag == "--note":
        argv += ["--dated", "09-06"]
    kwargs = {"milestone": value} if flag == "--milestone" else {}
    assert _run(*argv, **kwargs) == 1
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_a_blank_dated_is_refused_while_a_blank_milestone_is_not(store):
    """The `allow_blank` split, asserted from both sides in one test -- a flag
    that passes both values would satisfy either half alone."""
    assert _run("--dated", "   ") == 1
    assert _run("--dated", "09-06", milestone="   ") == 0
    assert _rows(store)[100]["milestone"] == ""


def test_a_finished_row_is_refused(store, capsys):
    """The one refusal with no twin inside `change_row`. In markdown it was
    `set_row_milestone` returning `None` for a row outside `## Board`; against
    records a closed row is an ordinary row with `done` set, so `change_row`
    would write it and the after-check would agree."""
    before = _contents(store)
    assert _rows(store)[51]["done"] is True, "the fixture's done row"

    assert _run(number=51) == 1
    assert "finished" in capsys.readouterr().err
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_a_row_that_does_not_exist_is_refused(store):
    before = _contents(store)
    assert _run(number=999) == 1
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_dry_run_reports_but_does_not_write(store):
    before = _contents(store)
    assert _run("--dated", "09-06", "--dry-run") == 0
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_dry_run_names_the_project_the_name_would_land_under(store, capsys):
    """The scoping is invisible in the record store -- two `Backup`s read the
    same on the page -- so the line printed has to say which project."""
    assert _run("--dry-run", number=104, milestone="Backup") == 0
    assert "Marcus" in capsys.readouterr().out


def test_the_change_set_is_one_cell_and_no_derived_key():
    """`from_document` reads the milestone *name* out of the registry by id, so
    unlike status and priority there is no second key to keep in step."""
    assert milestone_changes("Picking redesign") == {
        "milestone": "Picking redesign"}


def test_the_change_set_only_names_updated_when_a_date_was_given():
    assert "updated" not in milestone_changes("Backup")
    assert milestone_changes("Backup", dated="09-06")["updated"] == "09-06"


def test_refuse_row_passes_an_open_row_and_stops_a_finished_one():
    contents = {"items": [{"number": 1, "done": False},
                          {"number": 2, "done": True}]}
    assert refuse_row(contents, 1) is None
    assert refuse_row(contents, 2) is not None
    assert refuse_row(contents, 3) is not None
