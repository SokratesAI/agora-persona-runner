"""`tools.board_project` -- tagging rows with a project, against the record store.

`tests/test_board_project.py` one file over still covers `set_row_project`,
the markdown writer, which this tool no longer calls: issue #203 moves the
boards out of markdown, so what this CLI now does is read `board_records`,
name a cell, and hand the row to `board_write.change_row`.

**Every test here goes through the fake store, and none of them holds a line
of board markdown.** That is the same rule `tests/test_board_write.py` sets
and it matters more here, because the old version of this file asserted on
`parse_board` output of a file on disk -- which agrees with a converted and
an unconverted tool alike, and so could not tell the two apart.

What is left worth testing is the vocabulary and the refusals, since the
after-check the old `check_from_contents` performed lives in `change_row`
now and is tested there against a damaged store rather than a bad argument:

- the named rows get the project and nothing else on the board moves;
- **a run naming a good row and an absent one writes nothing at all** -- the
  half of the old all-or-nothing promise that survives one document per row,
  and the one that catches a typo'd number halfway through a project;
- a name that would escape its own cell in the generated markdown view is
  refused here, by name, before the store is read.
"""

import pytest

from agora_runner import board_records, board_write
from agora_runner.nova_boards import DEFAULT_PROJECT, board_projects
from tests.test_board_records import writable
from tools import board_project
from tools.board_project import (
    main, missing_rows, refuse_project, unseated_by_the_move)

#: The fixture board's #41 carries `Records` under `Nova`; `NAS` seats the
#: same name so the ordinary runs below are a legal move, and `Agora` seats
#: something else so an illegal one is one argument away.
SEATS = (
    "| Project | Milestone | Position | Updated | Serves | Keeps |\n"
    "|---|---|---|---|---|---|\n"
    "| Nova | Records | 1 | 09-15 | nova-kr-in-the-app |  |\n"
    "| NAS | Records | 1 | 09-15 | nas-kr-off-box-watch |  |\n"
    "| Agora | Chat basics he asked for | 1 | 09-15 | agora-kr-chat-basics |  |\n"
)


@pytest.fixture
def store(monkeypatch):
    """A migrated, writable fake store, wired in where `main` looks for it."""
    _, fake = writable()
    monkeypatch.setattr(board_project, "board_store", fake)
    monkeypatch.setattr(board_project, "seats_markdown",
                        lambda *a, **k: (SEATS, True))
    return fake


def _seats_answer(monkeypatch, text, ok=True):
    monkeypatch.setattr(board_project, "seats_markdown",
                        lambda *a, **k: (text, ok))


def _run(numbers=(41,), project="NAS", board="issue", **overrides):
    argv = ["--board", board, "--project", project]
    for number in numbers:
        argv += ["--number", str(number)]
    for flag, value in overrides.items():
        flag = "--" + flag.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value is not None:
            argv += [flag, str(value)]
    return main(argv)


def _rows(store, board="issue"):
    return {item["number"]: item
            for item in board_records.contents(board, store=store)["items"]}


def test_one_row_gets_the_project_and_keeps_everything_else(store):
    before = _rows(store)[42]
    assert _run(numbers=(42,)) == 0
    row = _rows(store)[42]
    assert row["project"] == "NAS"
    assert row == dict(before, project="NAS")


def test_every_named_row_lands_in_one_run(store):
    assert _run(numbers=(41, 42)) == 0
    rows = _rows(store)
    assert rows[41]["project"] == "NAS"
    assert rows[42]["project"] == "NAS"
    # The row nobody named keeps what it had rather than inheriting the one
    # being written -- which is why "untagged" and "under NAS" are
    # distinguishable at all.
    assert rows[43]["project"] == "Marcus"
    assert rows[40]["project"] == DEFAULT_PROJECT


def test_the_rest_of_the_board_is_untouched(store):
    before = board_records.contents("issue", store=store)
    assert _run(numbers=(42,)) == 0
    after = board_records.contents("issue", store=store)
    assert after["captures"] == before["captures"]
    assert after["captureReplies"] == before["captureReplies"]
    assert after["details"] == before["details"]
    assert [item["number"] for item in after["items"]] \
        == [item["number"] for item in before["items"]]


def test_the_project_list_is_read_back_off_the_rows(store, capsys):
    assert _run(numbers=(41, 42)) == 0
    printed = capsys.readouterr().out
    assert "projects on this board: " in printed
    # Row 40 is never named and stays under the default, so the list is
    # read off the rows rather than being the argument echoed back.
    assert board_projects(
        board_records.contents("issue", store=store)["items"]) \
        == ["NAS", "Marcus", DEFAULT_PROJECT]


def test_an_absent_row_in_a_multi_row_run_writes_nothing(store, capsys):
    """The half of all-or-nothing that survives one document per row.

    The assertion is on the *first* named row, not only on the exit code: a
    tool that wrote #41 and then discovered #999 would exit 1 too, and the
    whole point of checking the board before the first write is that it does
    not. A mutation moving `missing_rows` below the write loop passes a test
    that reads the code alone.
    """
    before = _rows(store)[41]
    assert _run(numbers=(41, 999)) == 1
    assert _rows(store)[41] == before
    assert "#999 is not a row on the issue board" in capsys.readouterr().err


def test_a_pipe_in_the_project_name_is_refused(store):
    before = _rows(store)[42]
    assert _run(numbers=(42,), project="NAS | server1") == 1
    assert _rows(store)[42] == before


@pytest.mark.parametrize("name,phrase", [
    ("   ", "may not be blank"),
    ("N" * 41, "41 characters"),
    ("NAS*", "a '*'"),
    ("NAS\nserver1", "a newline"),
])
def test_a_name_that_escapes_its_cell_is_refused_by_name(name, phrase):
    """Refused before the store is read, and the message names the reason.

    Each of these exits 1, so a test reading only the exit code cannot tell
    the four apart -- and cannot tell any of them from a refusal further
    down. The phrase is what says which rule fired.
    """
    assert phrase in (refuse_project(name.strip()) or "")


def test_a_legal_name_is_not_refused():
    """The negative half. Without it, `refuse_project` could refuse always."""
    assert refuse_project("NAS") is None


def test_dry_run_prints_the_moves_and_writes_nothing(store, capsys):
    before = _rows(store)[42]
    assert _run(numbers=(42,), dry_run=True) == 0
    assert _rows(store)[42] == before
    # `contents` fills an unfiled row with `DEFAULT_PROJECT`, so the "from"
    # side is a name and never a blank -- there is no `(none)` on a record.
    assert "#42: Nova -> NAS" in capsys.readouterr().out


def test_missing_rows_keeps_the_order_it_was_given(store):
    contents = board_records.contents("issue", store=store)
    assert missing_rows(contents, [999, 41, 998]) == [999, 998]


def test_an_unmigrated_store_is_refused_rather_than_read_as_an_empty_board(
        monkeypatch, capsys):
    """`contents` raises here, and the tool may not turn that into a no-op."""

    class Unmigrated:
        def read_registry(self):
            return {}

    monkeypatch.setattr(board_project, "board_store", Unmigrated())
    assert _run(numbers=(41,)) == 1
    assert "never been written" in capsys.readouterr().err


def test_a_row_the_after_check_refuses_names_what_already_landed(
        store, monkeypatch, capsys):
    """Two rows, the second write refused: the first is named, not hidden.

    One document per row means there is no revision spanning the two writes
    to roll back, so the re-run has to be the rows that did not land -- and
    the caller can only know which those are if this says so.
    """
    real = board_write.change_row

    def refuse_the_second(board, number, changes, **kwargs):
        if number == 42:
            raise board_write.WriteRefused("the row moved underneath")
        return real(board, number, changes, **kwargs)

    monkeypatch.setattr(board_write, "change_row", refuse_the_second)
    assert _run(numbers=(41, 42)) == 1
    err = capsys.readouterr().err
    assert "already written: #41" in err, err
    assert _rows(store)[41]["project"] == "NAS"


# --- the seat the move leaves behind (issue #227) ------------------------
#
# A milestone is seated under a project. This tool changes the project and
# leaves the milestone where it is, so it is the third way a row reaches the
# state `project_goals.task_seat_problems` reports -- filed under a milestone
# that seats nothing under the row's own project, reading as placed while
# serving no key result. `board_capture` (#1139) and `board_milestone`
# (#1140) already refuse the other two.


def test_a_move_that_unseats_the_rows_milestone_is_refused(store, capsys):
    before = _rows(store)
    assert _run(numbers=(41,), project="Agora") == 1
    problem = capsys.readouterr().err
    assert "no seat in milestone-seats.md" in problem
    # It names what IS seated under the destination, so a typo is visible.
    assert "chat basics he asked for" in problem
    assert _rows(store) == before, "nothing was written"
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_a_seated_destination_is_moved(store):
    """`NAS` seats `Records` too, so the row keeps serving something."""
    assert _run(numbers=(41,), project="NAS") == 0
    assert _rows(store)[41]["project"] == "NAS"
    assert _rows(store)[41]["milestone"] == "Records"


def test_one_unseated_row_stops_the_whole_run(store, capsys):
    """Same promise as an absent row: every named row is judged before the
    first write, so a good row and a bad one leave the board untouched."""
    before = _rows(store)
    assert _run(numbers=(43, 41), project="Agora") == 1
    assert "#41" in capsys.readouterr().err
    assert _rows(store) == before


def test_a_row_under_no_milestone_is_never_judged(store, monkeypatch):
    """#43 carries no milestone, so there is no seat to lose and no reason to
    pay for the vault read at all."""
    def explode(*a, **k):
        raise AssertionError("the seats file was fetched for an ungrouped row")
    monkeypatch.setattr(board_project, "seats_markdown", explode)
    assert _run(numbers=(43,), project="Agora") == 0
    assert _rows(store)[43]["project"] == "Agora"


def test_a_dry_run_refuses_an_unseating_move_too(store):
    """A dry run that reports a move the real run would refuse is a lying
    instrument, so the seats are read before `--dry-run` returns."""
    assert _run(numbers=(41,), project="Agora", dry_run=True) == 1


def test_an_unreadable_seats_file_warns_and_tags_the_row(
        store, monkeypatch, capsys):
    """Not checked is not the same as no seats -- an unreadable vault must
    not stand between him and his own board."""
    _seats_answer(monkeypatch, "", ok=False)
    assert _run(numbers=(41,), project="Agora") == 0
    assert "NOT checked" in capsys.readouterr().err
    assert _rows(store)[41]["project"] == "Agora"


def test_unseated_by_the_move_tells_no_seats_from_unreadable(monkeypatch):
    """`[]` and `None` are different answers and the caller acts on each
    differently -- one writes silently, the other writes and warns -- so a
    falsy test on the return value cannot tell them apart."""
    ungrouped = {7: {"number": 7, "milestone": "", "project": "Nova"}}
    assert unseated_by_the_move(ungrouped, [7], "Agora") == []

    grouped = {7: {"number": 7, "milestone": "Records", "project": "Nova"}}
    _seats_answer(monkeypatch, "", ok=False)
    assert unseated_by_the_move(grouped, [7], "Agora") is None

    _seats_answer(monkeypatch, SEATS)
    assert unseated_by_the_move(grouped, [7], "NAS") == []
