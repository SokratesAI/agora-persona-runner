"""Lifting `(Project: X)` out of a title, against the record store.

The owner found this himself and named the count: 38 rows across his two
boards carried the tag as prose inside their title with the `Project` cell
left at the `Nova` default, so `board_projects` could not see one of them.

**Every test here goes through the fake store and none of them holds a line
of board markdown**, except the one string the fixture is built from -- the
same rule `tests/test_tools_board_project.py` sets, and for the same reason:
a test that asserts on `parse_board` of a file on disk agrees with a
converted and an unconverted tool alike, so it cannot tell the two apart.

The after-check is `board_write.change_row`'s now and is tested there. What
is left here is this tool's own vocabulary: which rows carry a tag, and the
one refusal `change_row` structurally cannot make -- **the check has to
compare against the original title, not against the regex's own output**,
because a regex that ate a word too many produces a `new_title` that a check
built on the same regex agrees with perfectly.
"""

import pytest

from agora_runner import board_records
from tests.test_board_records import writable
from tools import board_untag_project
from tools.board_untag_project import (
    main,
    refuse_move,
    refusals,
    tagged_rows_from_contents,
)

BOARD = """- A capture nobody has boarded.

## Board

| # | Idea | Status | Updated | Priority | Project | Size | Milestone | Order |
|---|---|---|---|---|---|---|---|---|
| [[#57]] | (Project: Marcus) More pages | 🟡 In progress | 2026-08-11 | 🔵 Medium | | | | |
| [[#59]] | Small pickings | ⚪ Backlog | 2026-08-11 | ⚪ Low | | | | |
| [[#60]] | (project:  Sokrates Post ) Lower case and padded | ⚪ Backlog | 2026-08-11 | ⚪ Low | | | | |

# Details

## #57 — (Project: Marcus) More pages

The prose body of fifty-seven.
"""


@pytest.fixture
def store(monkeypatch):
    """A migrated, writable fake of the tagged board, wired in where `main` looks."""
    _, fake = writable(board="idea", markdown=BOARD)
    monkeypatch.setattr(board_untag_project, "board_store", fake)
    return fake


def _rows(store):
    return {item["number"]: item
            for item in board_records.contents("idea", store=store)["items"]}


def _contents(store):
    return board_records.contents("idea", store=store)


def _run(*argv):
    return main(["--board", "idea", *argv])


def test_a_tagged_row_moves_its_tag_into_the_cell(store):
    before = _rows(store)[57]
    assert _run() == 0

    row = _rows(store)[57]
    assert row["title"] == "More pages"
    assert row["project"] == "Marcus"
    assert row == dict(before, title="More pages", project="Marcus")


def test_an_untagged_row_is_left_alone(store):
    before = _rows(store)[59]
    assert _run() == 0
    assert _rows(store)[59] == before


def test_the_write_up_under_a_retagged_row_is_not_touched(store):
    """The body is not the heading: a title change must not rewrite his prose."""
    before = _contents(store)["details"]
    assert before[57], "the fixture's tagged row carries a write-up"
    assert _run() == 0
    assert _contents(store)["details"] == before


def test_number_narrows_the_sweep_to_the_named_rows(store):
    was = _rows(store)[57]
    assert _run("--number", "60") == 0

    assert _rows(store)[57] == was
    assert _rows(store)[60]["project"] == "Sokrates Post"
    assert _rows(store)[60]["title"] == "Lower case and padded"


def test_dry_run_prints_the_moves_and_writes_nothing(store):
    before = _rows(store)
    assert _run("--dry-run") == 0
    assert _rows(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_a_board_with_no_tag_anywhere_is_a_clean_no_op(monkeypatch):
    _, fake = writable(board="idea", markdown=BOARD.replace(
        "(Project: Marcus) ", "").replace("(project:  Sokrates Post ) ", ""))
    monkeypatch.setattr(board_untag_project, "board_store", fake)

    assert _run() == 0
    assert not [call for call in fake.calls if call[0] == "write_row"]


def test_a_number_that_is_not_on_the_board_is_simply_not_a_move(store):
    """`--number 999` names nothing, so there is nothing to refuse."""
    before = _rows(store)
    assert _run("--number", "999") == 0
    assert _rows(store) == before


def test_a_title_that_is_only_a_tag_is_not_touched():
    """Retagging it would leave the row with no title at all."""
    contents = {"items": [{"number": 1, "title": "(Project: Marcus)"}]}
    assert tagged_rows_from_contents(contents) == []


def test_refuse_move_rejects_a_regex_that_ate_a_word_too_many():
    """The one refusal `change_row` cannot make: it only checks what landed."""
    assert refuse_move("(Project: Marcus) More pages", "pages") is not None
    assert refuse_move("(Project: Marcus) More pages", "More pages") is None


def test_refuse_move_rejects_a_head_that_is_not_parenthesised():
    assert refuse_move("Project: Marcus -- More pages", "More pages") is not None


def test_refuse_move_rejects_an_empty_title():
    """A title that is nothing but a tag would leave the row nameless -- and
    the suffix and parenthesis checks below both wave it through, because
    every string ends with `""` and the whole of `(Project: Marcus)` is a
    legal head."""
    assert refuse_move("(Project: Marcus)", "") is not None


def test_refuse_move_rejects_a_new_title_that_is_not_a_suffix():
    assert refuse_move("(Project: Marcus) More pages", "Other pages") is not None


def test_an_over_long_tag_is_left_in_the_title_rather_than_lifted(monkeypatch):
    """The two copies of the cell rule still agree, which is what makes the
    pre-write refusals unreachable from real input.

    `split_capture_project` bounds the name it returns to `set_row_project`'s
    characters and its 40-character limit, and `refuse_project` is the same
    rule written down in `tools.board_project`. So a tag too long for a cell
    is never a move at all -- it stays in the title where it does no harm.
    This test is here because those are two copies of one rule and nothing
    else would notice them drifting apart.
    """
    _, fake = writable(board="idea", markdown=BOARD.replace(
        "(project:  Sokrates Post )", "(Project: " + "S" * 41 + ")"))
    monkeypatch.setattr(board_untag_project, "board_store", fake)

    moves = tagged_rows_from_contents(board_records.contents("idea", store=fake))
    assert [number for number, *_ in moves] == [57], (
        "the over-long tag is not a move, so no refusal has to catch it")
    assert _run() == 0
    assert _rows(fake)[60]["title"].startswith("(Project: ")


def test_a_refusal_stops_the_run_before_the_first_write(monkeypatch, store):
    """The half of the old all-or-nothing promise that survives one document
    per row: a run naming a bad row writes nothing at all, not the good rows
    that came before it.

    Driven through the `refusals` seam because -- see the test above -- no
    real board can currently produce one, and a test that cannot fail is
    worth less than one that says why it had to be staged.
    """
    monkeypatch.setattr(board_untag_project, "refusals",
                        lambda contents, moves: ["#60: staged"])
    before = _rows(store)

    assert _run() == 1
    assert _rows(store) == before
    assert not [call for call in store.calls if call[0] == "write_row"]


def test_refusals_names_every_bad_row_rather_than_the_first(store):
    problems = refusals(
        _contents(store),
        [(57, "Marcus", "(Project: Marcus) More pages", "pages"),
         (999, "Marcus", "(Project: Marcus) Gone", "Gone")],
    )
    assert len(problems) == 2
    assert any("#57" in problem for problem in problems)
    assert any("#999" in problem for problem in problems)
