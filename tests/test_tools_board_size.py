"""`tools.board_size` -- estimating one row moves exactly that cell.

`tests/test_board_size.py` one file over covers `set_row_size`, the markdown
function this tool no longer calls. What is tested here is the CLI's own
vocabulary after the #203 conversion: which cells a re-estimate writes, the
vocabulary check that stands in for a cell-delimiter rule on `--size`, and the
refusals that have to happen while nothing has been written yet.

**Every test here goes through the fake store and none of them holds a line of
board markdown**, except the one string the fixture is built from -- the rule
`tests/test_tools_board_project.py` set and for the same reason: a test that
asserts on `parse_board` of a file on disk agrees with a converted and an
unconverted tool alike, so it cannot tell the two apart.

The after-check is `board_write.change_row`'s now and is tested there, and the
cell-delimiter rule is `board_write.refuse_cell`'s. What is still this module's
own is the change set (the cell *and* its derived `sizeKey`), the blank that is
deliberately unreachable, and the one refusal with no twin below the seam: a
finished row.
"""

import pytest

from agora_runner import board_records
from agora_runner.nova_boards import size_key
from tests.test_board_records import writable
from tools import board_size
from tools.board_size import main, refuse_row, resolve_size, size_changes

BOARD = """- A capture nothing here may touch.

## Board

| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |
|---|---|---|---|---|---|---|---|---|
| [[#100 — Weekly work\\|100]] | Weekly work | 🟡 In progress | 08-24 | 🟠 High | Nova | M | Cost and quota | |
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
    monkeypatch.setattr(board_size, "board_store", fake)
    return fake


def _contents(store):
    return board_records.contents("idea", store=store)


def _rows(store):
    return {item["number"]: item for item in _contents(store)["items"]}


def _run(*argv, number="100", size="large"):
    return main(["--board", "idea", "--number", str(number),
                 "--size", size, *argv])


def test_the_named_row_is_re_estimated_and_keeps_everything_else(store):
    before = _rows(store)[100]
    assert before["size"] == "M", "the fixture's estimate"
    assert _run("--dated", "09-06") == 0

    row = _rows(store)[100]
    assert row["size"] == "L"
    assert row["updated"] == "09-06"
    assert row["status"] == before["status"]
    assert row["priority"] == before["priority"]
    assert row["milestone"] == before["milestone"]
    assert row["project"] == before["project"]


def test_the_derived_key_moves_with_the_cell(store):
    """`from_document` derives `sizeKey` off the stored `size`, so a change set
    naming only the cell reads back disagreeing with itself. Asserting the key
    here is what fails when `size_changes` stops naming it."""
    assert _rows(store)[100]["sizeKey"] == size_key("M"), "the precondition"
    assert _run() == 0
    row = _rows(store)[100]
    assert row["sizeKey"] == size_key("L")
    assert row["sizeKey"] != size_key("M")


def test_an_unsized_row_can_be_estimated(store):
    """The load-bearing direction: a row nobody has sized yet is the reason
    the tool exists, and it is a different branch from a re-estimate."""
    assert _rows(store)[104]["size"] == "", "the precondition"
    assert _run(number=104, size="xl") == 0
    assert _rows(store)[104]["size"] == "XL"


@pytest.mark.parametrize("typed,cell", [
    ("s", "S"), ("M", "M"), ("large", "L"), ("x-large", "XL"), ("XL", "XL"),
])
def test_every_spelling_the_rest_of_the_system_accepts_reaches_the_cell(
    store, typed, cell
):
    assert _run(size=typed) == 0
    assert _rows(store)[100]["size"] == cell


@pytest.mark.parametrize("typed", ["", "   ", "huge", "medium-ish", "0"])
def test_a_size_outside_the_vocabulary_is_refused_before_anything_is_written(
    store, capsys, typed
):
    """The blank rows are the point of this one. An empty size is a *legal*
    cell meaning nobody has estimated the row, so nothing below this seam
    refuses it -- `size_changes` would write it and `change_row` would agree.
    This module is the only thing keeping `--size ''` from blanking a row."""
    before = _contents(store)
    assert _run(size=typed) == 1
    assert "is not a size" in capsys.readouterr().err
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_resolve_size_refuses_the_blank_it_is_handed(store):
    """Driven directly as well, because the CLI test above passes against a
    `main` that refuses everything."""
    assert resolve_size("l") == "L"
    assert resolve_size("") is None
    assert resolve_size("   ") is None


def test_a_finished_row_is_refused_off_the_board_read_before_the_write(store, capsys):
    """The one refusal with no twin below this seam. Against records a done row
    is an ordinary row with `done` set, so `change_row` would write it and its
    after-check would agree with the write."""
    before = _contents(store)
    assert _run(number=51) == 1
    assert "not a fact about anything" in capsys.readouterr().err
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_refuse_row_answers_none_for_an_open_row_and_names_an_absent_one(store):
    """The negative half: without it this function could refuse everything and
    the test above would still pass."""
    contents = _contents(store)
    assert refuse_row(contents, 100) is None
    assert refuse_row(contents, 104) is None
    assert "is finished" in refuse_row(contents, 51)
    assert "not a row on this board" in refuse_row(contents, 999)


def test_size_changes_leaves_updated_alone_unless_it_is_given_one(store):
    assert size_changes("L") == {"size": "L", "sizeKey": size_key("L")}
    assert size_changes("L", dated="09-06")["updated"] == "09-06"


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


def test_a_dry_run_writes_nothing(store, capsys):
    before = _contents(store)
    assert _run("--dry-run") == 0
    assert "(unsized)" not in capsys.readouterr().out
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_a_note_is_appended_under_the_row_it_explains(store):
    assert _run("--dated", "09-06", "--note", "three cycles of migration left",
                "--cycle", "1334") == 0
    body = _contents(store)["details"][100]
    assert body.startswith("Three heartbeats, one prompt file each.")
    assert "three cycles of migration left" in body
    assert "(Cycle 1334)" in body


def test_a_note_and_the_re_estimate_land_in_one_write(store):
    """Two `change_row` calls would be two writes and a window in between
    where the row has been re-estimated and nothing on it says why."""
    assert _run("--dated", "09-06", "--note", "why it grew") == 0

    writes = [call for call in store.calls if call[0] == "write_row"]
    assert len(writes) == 1
    assert _rows(store)[100]["size"] == "L"
    assert "why it grew" in _contents(store)["details"][100]


def test_a_note_takes_the_updated_cell_from_its_own_date(store):
    """`append_note` refuses a change set that also names `updated`, so this
    asserts the tool leaves it out *and* that the cell still moves."""
    assert _rows(store)[100]["updated"] == "08-24", "the precondition"
    assert _run("--dated", "09-06", "--note", "why it grew") == 0
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
    # The `--dated` path with no note goes through `change_row`, which has no
    # cell rules at all, so nothing below this seam would refuse it.
    ("--dated", "09-06 | extra"),
    ("--dated", " "),
    ("--dated", "09-06\rmore"),
    # `_note_line` refuses a `\r` in a note body as well, so that row pins the
    # exit code rather than this module's guard. The `|` row does NOT have a
    # twin down there -- `_note_line` checks `|` against the date only, on the
    # reasoning that prose is not a cell -- so `refuse_cell` is the only thing
    # refusing it and deleting this module's call would let it through.
    ("--note", "why | not"),
    ("--note", "why\rnot"),
])
def test_a_cell_delimiter_is_refused_before_anything_is_written(store, flag, value):
    before = _contents(store)
    argv = [flag, value]
    if flag == "--note":
        argv += ["--dated", "09-06"]
    assert _run(*argv) == 1
    assert _contents(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_a_delimiter_cannot_reach_the_size_cell_through_the_vocabulary(store):
    """`--size` is the one flag here that does not call `refuse_cell`, and this
    is why: the value written is one of four constants, so a caller who spells
    a delimiter into it is refused as a bad *size*, not as a bad cell."""
    assert resolve_size("L | XL") is None
    assert resolve_size("L\nXL") is None
    assert _run(size="L | XL") == 1
    assert not [call for call in store.calls if call[0] == "write_row"]
