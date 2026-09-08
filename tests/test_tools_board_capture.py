"""`tools.board_capture` -- a capture becomes a row and leaves the box.

The failure this guards is the one that made the tool necessary: boarding
a capture by hand is an add and a delete on one document, and a cycle
that does the add and not the delete leaves the item in his "not boarded
yet" box *and* on the board at the same time. So every test below asserts
on both halves -- `parse_board`'s rows and `capture_entries`'s bullets --
because either one alone passes on a half-done edit.

The other half is the span. A capture's replies are indented bullets that
`capture_entries` folds into the capture above them, so an off-by-one in
the lines being cut silently takes a neighbour's answer with it and both
documents still render.
"""

import pytest

from agora_runner.nova_boards import DEFAULT_PROJECT, capture_entries, parse_board
from tools.board_capture import check, first_sentence, main, promote

BOARD = """---
type: board
---

- The first thing he typed. It goes on for a second sentence.
  - Cycle 500 answered this one.
- 🟠 High: A rated capture.
- DONE (Cycle 501): A capture a cycle already closed.
- 

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#2 — The second thing\\|2]] | The second thing | 🟡 In progress | 08-25 | 🟠 High |
| [[#1 — The first thing\\|1]] | The first thing | ✅ Done | 08-25 |  |

# Details

### #2 — The second thing

Why the second thing matters.

### #1 — The first thing

Why the first thing mattered.
"""


def _run(tmp_path, board=BOARD, **overrides):
    path = tmp_path / "issues.md"
    path.write_text(board, encoding="utf-8")
    argv = ["--file", str(path), "--index", "0", "--dated", "08-27"]
    for flag, value in overrides.items():
        argv += ["--" + flag.replace("_", "-")] + ([value] if value is not None else [])
    return main(argv), path


def test_the_capture_leaves_the_box_and_arrives_as_a_row(tmp_path):
    code, path = _run(tmp_path, priority="medium")
    assert code == 0
    after = path.read_text(encoding="utf-8")
    board = parse_board(after)
    assert [item["number"] for item in board["items"]] == [3, 2, 1]
    new = board["items"][0]
    # The title is his first sentence; the write-up is everything he wrote.
    assert new["title"] == "The first thing he typed."
    assert new["status"] == "⚪ Backlog"
    assert new["priority"] == "🔵 Medium"
    assert board["details"][3].startswith(
        "The first thing he typed. It goes on for a second sentence."
    )
    # And it is gone from the box, without taking its neighbours.
    texts = [text for _, _, text, _ in capture_entries(after)]
    assert texts == ["🟠 High: A rated capture.",
                     "DONE (Cycle 501): A capture a cycle already closed."]


def test_the_reply_written_under_it_rides_across(tmp_path):
    code, path = _run(tmp_path, priority="medium")
    assert code == 0
    assert "Cycle 500 answered this one." in parse_board(
        path.read_text(encoding="utf-8")
    )["details"][3]


def test_the_empty_cursor_bullet_stays(tmp_path):
    """He types into it. `capture_entries` ignores it and so must the cut."""
    code, path = _run(tmp_path, priority="medium")
    assert code == 0
    head = path.read_text(encoding="utf-8").split("## Board")[0]
    assert "\n- \n" in head


def test_a_rated_capture_keeps_its_own_rating(tmp_path):
    code, path = _run(tmp_path, index="1")
    assert code == 0
    board = parse_board(path.read_text(encoding="utf-8"))
    assert board["items"][0]["priority"] == "🟠 High"
    # The prefix is a cell now, so it is off the title and off the write-up.
    assert board["items"][0]["title"] == "A rated capture."
    assert not board["details"][3].startswith("🟠")


def test_an_explicit_priority_beats_the_bullets_own(tmp_path):
    code, path = _run(tmp_path, index="1", priority="low")
    assert code == 0
    assert parse_board(path.read_text(encoding="utf-8"))["items"][0]["priority"] == "⚪ Low"


def test_a_done_marker_lands_done_not_backlog(tmp_path):
    """His complaint in as many words: finished items in the unstaged box."""
    code, path = _run(tmp_path, index="2")
    assert code == 0
    board = parse_board(path.read_text(encoding="utf-8"))
    assert board["items"][0]["status"] == "✅ Done"
    assert board["items"][0]["title"] == "A capture a cycle already closed."
    # A closed row takes no rating -- `set_row_status` clears it.
    assert board["items"][0]["priority"] == ""


@pytest.mark.parametrize(
    "status,cell",
    [("in-progress", "🟡 In progress"),
     ("blocked-on-edvard", "⏸ Blocked on Edvard"),
     ("done", "✅ Done")],
)
def test_every_status_reaches_the_cell(tmp_path, status, cell):
    code, path = _run(tmp_path, priority="medium", status=status)
    assert code == 0
    assert parse_board(path.read_text(encoding="utf-8"))["items"][0]["status"] == cell


def test_outdated_is_not_a_status_a_cycle_may_set(tmp_path):
    """He deletes those himself; nothing he typed this week arrives written off."""
    with pytest.raises(SystemExit):
        _run(tmp_path, priority="medium", status="outdated")


def test_it_refuses_an_index_that_is_not_there(tmp_path, capsys):
    code, path = _run(tmp_path, index="9", priority="medium")
    assert code == 1
    assert "no capture at index 9" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == BOARD


def test_it_refuses_a_pipe_in_the_date(tmp_path, capsys):
    """A stray `|` shifts every column right of it -- `parse_board` then
    reads the tail of the date as the rating."""
    code, path = _run(tmp_path, priority="medium", dated="08|27")
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_it_refuses_a_rating_that_is_not_one(tmp_path, capsys):
    code, path = _run(tmp_path, priority="urgentish")
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_dry_run_writes_nothing(tmp_path):
    code, path = _run(tmp_path, priority="medium", dry_run=None)
    assert code == 0
    assert path.read_text(encoding="utf-8") == BOARD


def test_first_sentence_keeps_a_long_one_whole(tmp_path):
    """No character count: a truncated title reads as a different item."""
    long = "A" * 300 + ". And then a second sentence."
    assert first_sentence(long) == "A" * 300 + "."
    assert first_sentence("No full stop here") == "No full stop here"
    assert first_sentence("Is it? Yes.") == "Is it?"


def test_check_catches_a_capture_lost_beside_the_one_boarded():
    """The off-by-one this guard exists for, forced by hand."""
    after, number, title, _ = promote(BOARD, 0, "medium", "backlog", "08-27")
    assert not check(BOARD, after, number, title,
                     "The first thing he typed. It goes on for a second sentence.")
    damaged = after.replace("- 🟠 High: A rated capture.\n", "")
    problems = check(BOARD, damaged, number, title,
                     "The first thing he typed. It goes on for a second sentence.")
    assert any("capture count went" in p for p in problems)


def test_check_catches_a_reply_taken_with_the_cut():
    after, number, title, _ = promote(BOARD, 1, "medium", "backlog", "08-27")
    damaged = after.replace("  - Cycle 500 answered this one.\n", "")
    problems = check(BOARD, damaged, number, title, "🟠 High: A rated capture.")
    assert any("a capture changed underneath" in p for p in problems)


def test_check_catches_a_row_that_changed_underneath():
    after, number, title, _ = promote(BOARD, 0, "medium", "backlog", "08-27")
    damaged = after.replace("| The second thing | 🟡 In progress |",
                            "| The second thing | ✅ Done |")
    problems = check(BOARD, damaged, number, title,
                     "The first thing he typed. It goes on for a second sentence.")
    assert any("#2 changed underneath" in p for p in problems)


def test_a_project_tag_is_lifted_out_of_the_title_into_the_cell(tmp_path):
    """His third capture prefix, and the one that went in as prose for 38 rows.

    A rating prefix and a `DONE (Cycle N)` prefix were already stripped
    before a bullet became a title; `(Project: X)` was not, so it was
    written into the `Item` cell and the `Project` cell stayed at the
    `Nova` default -- which is what he filed on 2026-09-01.
    """
    board = BOARD.replace(
        "- The first thing he typed. It goes on for a second sentence.",
        "- (Project: Marcus) The first thing he typed. It goes on for a second sentence.",
    )
    code, path = _run(tmp_path, board=board, priority="medium")
    assert code == 0
    new = parse_board(path.read_text(encoding="utf-8"))["items"][0]
    assert new["title"] == "The first thing he typed."
    assert new["project"] == "Marcus"
    # The write-up is still everything he wrote from the tag onwards --
    # the prefix is a cell now, so it is not repeated in the prose either.
    assert "(Project: Marcus)" not in path.read_text(encoding="utf-8")


def test_a_project_tag_rides_beside_a_rating_and_a_done_marker(tmp_path):
    board = BOARD.replace(
        "- The first thing he typed. It goes on for a second sentence.",
        "- DONE (Cycle 501): 🟠 High: (Project: Marcus) Shipped already.",
    )
    code, path = _run(tmp_path, board=board)
    assert code == 0
    new = parse_board(path.read_text(encoding="utf-8"))["items"][0]
    assert new["title"] == "Shipped already."
    assert new["project"] == "Marcus"
    assert new["status"] == "✅ Done"
    # The rating is gone on purpose -- `set_row_status`'s own rule, a
    # closed row loses its rating -- so all three prefixes were read and
    # only the two that survive a closure are written.
    assert new["priority"] == ""


#: The same board with a `Project` column that already names Marcus. A slug
#: only resolves against a project that exists, so a fixture with no
#: `Project` cell anywhere cannot exercise the tag at all -- and a test
#: written on `BOARD` would pass for the wrong reason, by finding nothing.
BOARD_WITH_PROJECTS = BOARD.replace(
    "| # | Item | Status | Updated | Priority |\n|---|------|--------|---------|---|\n"
    "| [[#2 — The second thing\\|2]] | The second thing | 🟡 In progress | 08-25 | 🟠 High |\n"
    "| [[#1 — The first thing\\|1]] | The first thing | ✅ Done | 08-25 |  |",
    "| # | Item | Status | Updated | Priority | Project |\n|---|------|--------|---------|---|---|\n"
    "| [[#2 — The second thing\\|2]] | The second thing | 🟡 In progress | 08-25 | 🟠 High | Marcus |\n"
    "| [[#1 — The first thing\\|1]] | The first thing | ✅ Done | 08-25 |  | Sokrates Post |",
)


def test_the_projects_fixture_really_carries_the_column():
    """The precondition, asserted rather than assumed.

    Every test below is a negative-shaped claim about a slug resolving, and
    a fixture whose replacement silently missed would make all of them pass
    by finding no projects to resolve against.
    """
    assert BOARD_WITH_PROJECTS != BOARD
    items = parse_board(BOARD_WITH_PROJECTS)["items"]
    assert [item["project"] for item in items] == ["Marcus", "Sokrates Post"]


def test_the_apps_project_tag_reaches_the_cell_too(tmp_path):
    """The picker's shape, not the one he types by hand.

    PR #887 gave the capture box a project picker that writes the choice as
    a trailing `#slug` (`nova_capture.project_slug`). Nothing read it:
    measured before this was written, `split_capture_project` returned
    `("", "the reminder never fires #marcus")` for a bullet the picker had
    produced, so the row landed at the `Nova` default with the tag stuck
    inside its title -- the same defect he filed on 2026-09-01, in a new
    syntax.
    """
    board = BOARD_WITH_PROJECTS.replace(
        "- The first thing he typed. It goes on for a second sentence.",
        "- The first thing he typed. It goes on for a second sentence. #marcus",
    )
    code, path = _run(tmp_path, board=board, priority="medium")
    assert code == 0
    after = path.read_text(encoding="utf-8")
    new = parse_board(after)["items"][0]
    assert new["project"] == "Marcus"
    assert new["title"] == "The first thing he typed."
    # And the slug is gone from the file, not merely absent from the title:
    # the write-up carries the rest of his sentence and must not keep it.
    assert "#marcus" not in after


def test_a_slug_with_a_hyphen_resolves_to_the_name_that_made_it(tmp_path):
    """`Sokrates Post` -> `sokrates-post` and back. The multi-word case is
    the one a de-hyphenating guess would get wrong."""
    board = BOARD_WITH_PROJECTS.replace(
        "- The first thing he typed. It goes on for a second sentence.",
        "- The first thing he typed. It goes on. #sokrates-post",
    )
    code, path = _run(tmp_path, board=board, priority="medium")
    assert code == 0
    new = parse_board(path.read_text(encoding="utf-8"))["items"][0]
    assert new["project"] == "Sokrates Post"


def test_an_unknown_slug_is_left_alone_rather_than_invented(tmp_path):
    """The one judgement in this change, pinned.

    `board_projects` derives the project list from the cells, so writing a
    de-slugged `Recipe App` into one would create a project he never named
    -- and a lowercase `recipe-app` would sit beside a `Recipe App` he
    later types as a second project on the same page. So an unresolved slug
    stays in the title, project unset: no worse than before this change,
    and never a name I made up.
    """
    board = BOARD_WITH_PROJECTS.replace(
        "- The first thing he typed. It goes on for a second sentence.",
        "- The first thing he typed. It goes on. #recipe-app",
    )
    code, path = _run(tmp_path, board=board, priority="medium")
    assert code == 0
    after = path.read_text(encoding="utf-8")
    assert parse_board(after)["items"][0]["project"] == DEFAULT_PROJECT
    assert "#recipe-app" in after


def test_a_project_name_mid_sentence_is_prose_not_a_tag(tmp_path):
    """The anchor, and it needs a *resolvable* slug to test anything.

    I wrote this first with `#4` mid-sentence and it was worthless: drop
    the `$` from the pattern and it still passed, because `4` resolves to
    no project and the function returns the bullet untouched either way.
    A negative result that was guaranteed in advance. `#marcus` resolves,
    so an unanchored pattern eats it out of the middle of his sentence and
    truncates the title to the two words in front of it -- which is what
    this actually pins.
    """
    board = BOARD_WITH_PROJECTS.replace(
        "- The first thing he typed. It goes on for a second sentence.",
        "- The #marcus reminder never fires in the evening.",
    )
    code, path = _run(tmp_path, board=board, priority="medium")
    assert code == 0
    after = path.read_text(encoding="utf-8")
    new = parse_board(after)["items"][0]
    assert new["project"] == DEFAULT_PROJECT
    assert new["title"] == "The #marcus reminder never fires in the evening."


def test_a_hash_in_his_prose_is_not_a_project_tag(tmp_path):
    """He writes `#4` and `#267` in sentences constantly. This one cannot
    catch a dropped anchor on its own -- see the test above for why -- but
    it does pin that a trailing number is not read as a project."""
    board = BOARD_WITH_PROJECTS.replace(
        "- The first thing he typed. It goes on for a second sentence.",
        "- The first thing he typed. It is the same bug as #4",
    )
    code, path = _run(tmp_path, board=board, priority="medium")
    assert code == 0
    after = path.read_text(encoding="utf-8")
    assert parse_board(after)["items"][0]["project"] == DEFAULT_PROJECT
    assert "#4" in after


def test_the_hand_typed_prefix_still_wins_over_a_trailing_tag(tmp_path):
    """Both shapes on one bullet. The prefix is what he typed deliberately;
    the tag can be left over from a picker that was on last-used."""
    board = BOARD_WITH_PROJECTS.replace(
        "- The first thing he typed. It goes on for a second sentence.",
        "- (Project: Sokrates Post) The first thing he typed. It goes on. #marcus",
    )
    code, path = _run(tmp_path, board=board, priority="medium")
    assert code == 0
    assert parse_board(path.read_text(encoding="utf-8"))["items"][0]["project"] == "Sokrates Post"


def test_an_explicit_project_flag_beats_the_bullets_own_tag(tmp_path):
    board = BOARD.replace(
        "- The first thing he typed. It goes on for a second sentence.",
        "- (Project: Marcus) The first thing he typed. It goes on.",
    )
    code, path = _run(tmp_path, board=board, priority="medium", project="Agora")
    assert code == 0
    assert parse_board(path.read_text(encoding="utf-8"))["items"][0]["project"] == "Agora"


def test_an_untagged_capture_gets_no_project_cell(tmp_path):
    """The control: nothing here may start stamping a project on every row."""
    code, path = _run(tmp_path, priority="medium")
    assert code == 0
    after = path.read_text(encoding="utf-8")
    assert parse_board(after)["items"][0]["project"] == DEFAULT_PROJECT
    # And the table is still five columns wide -- no `Project` header was
    # appended for a row that never asked for one.
    header = [line for line in after.split("\n") if line.startswith("| # |")][0]
    assert header.count("|") == 6
