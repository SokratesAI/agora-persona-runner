"""Lifting `(Project: X)` out of a title, on rows boarded before the fix.

The owner found this himself and named the count: 38 rows across his two
boards carried the tag as prose inside their title with the `Project` cell
left at the `Nova` default, so `board_projects` could not see one of them.

The two failures worth testing are both quiet. **A title is written three
times** -- the table cell, the wiki-link target, and the `### #N — ...`
heading over the write-up -- so a retag that moves the cell alone leaves
him a link that goes nowhere in Obsidian. And **the check has to compare
against the original title, not against the regex's own output**: a regex
that ate a word too many would produce a `new_title` that a check built on
the same regex agrees with perfectly.
"""

from agora_runner.nova_boards import board_projects, parse_board
from tools.board_untag_project import check, main, tagged_rows, untag

BOARD = """---
type: board
---

- A capture nobody has boarded.

## Board

| # | Idea | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#57 — (Project: Marcus) More pages\\|57]] | (Project: Marcus) More pages | 🟡 In progress | 08-11 | 🔵 Medium |
| [[#59 — Small pickings\\|59]] | Small pickings | ⚪ Backlog | 08-11 | ⚪ Low |
| [[#60 — (project:  Sokrates Post ) Lower case and padded\\|60]] | (project:  Sokrates Post ) Lower case and padded | ⚪ Backlog | 08-11 | ⚪ Low |

# Details

### #57 — (Project: Marcus) More pages

Body text I must not touch.

### #59 — Small pickings

Another body.
"""


def rows(markdown):
    return {item["number"]: item for item in parse_board(markdown)["items"]}


def test_the_tag_moves_out_of_the_title_and_into_the_cell():
    after, moves, skipped = untag(BOARD)
    assert skipped == []
    assert [number for number, _, _, _ in moves] == [57, 60]
    parsed = rows(after)
    assert parsed[57]["title"] == "More pages"
    assert parsed[57]["project"] == "Marcus"
    # Case and padding are his typing, not a second project.
    assert parsed[60]["title"] == "Lower case and padded"
    assert parsed[60]["project"] == "Sokrates Post"
    # The untagged row is the control: it must come back byte-identical.
    assert parsed[59] == rows(BOARD)[59]
    # #59 is untagged and so reads as the `Nova` default, in first
    # position because it is the row order that decides.
    assert board_projects(parse_board(after)["items"]) == [
        "Marcus",
        "Nova",
        "Sokrates Post",
    ]


def test_all_three_copies_of_the_title_move_together():
    after, _, _ = untag(BOARD, [57])
    assert "(Project: Marcus)" not in after
    assert "[[#57 — More pages\\|57]]" in after
    assert "### #57 — More pages" in after
    assert "Body text I must not touch." in after


def test_an_untagged_board_is_left_alone():
    after, moves, skipped = untag("---\n---\n\n## Board\n\n| # | Idea |\n|---|---|\n")
    assert after is None and moves == [] and skipped == []


def test_a_title_that_is_only_a_tag_is_not_touched():
    """Stripping it would leave an empty title, which `set_row_title` reads
    as a delete -- so the row keeps its odd title rather than vanishing."""
    board = BOARD.replace(
        "(Project: Marcus) More pages", "(Project: Marcus)"
    )
    assert tagged_rows(board) == [
        (60, "Sokrates Post", "(project:  Sokrates Post ) Lower case and padded",
         "Lower case and padded"),
    ]


def test_check_catches_a_title_that_lost_more_than_its_tag():
    """The regression the `endswith` guard exists for: a `new_title` that
    the mover and the checker agree on can still be wrong about his text."""
    after, moves, _ = untag(BOARD, [57])
    # Same rows, but claiming the title should have lost a word too.
    lying = [(57, "Marcus", "(Project: Marcus) More pages", "pages")]
    problems = check(BOARD, after, lying)
    assert any("came back titled" in problem for problem in problems)
    assert check(BOARD, after, moves) == []


def test_the_cli_writes_and_dry_run_does_not(tmp_path):
    path = tmp_path / "ideas.md"
    path.write_text(BOARD, encoding="utf-8")
    assert main(["--file", str(path), "--dry-run"]) == 0
    assert path.read_text(encoding="utf-8") == BOARD
    assert main(["--file", str(path), "--number", "57"]) == 0
    parsed = rows(path.read_text(encoding="utf-8"))
    assert parsed[57]["project"] == "Marcus"
    assert parsed[60]["title"].startswith("(project:")


def test_check_catches_a_retag_that_ate_a_word_of_his_title():
    """The guard the `endswith` test above does not reach.

    That one is caught by the plain title comparison before the shape
    guard ever runs -- so it proves nothing about the shape guard. This
    one hands `check` an `after` and a `moves` that agree with each
    other, both wrong in the same direction, which is exactly what a
    regex that ate one word too many produces.
    """
    from agora_runner.nova_boards import set_row_project, set_row_title

    after = set_row_project(set_row_title(BOARD, 57, "pages"), 57, "Marcus")
    lying = [(57, "Marcus", "(Project: Marcus) More pages", "pages")]
    problems = check(BOARD, after, lying)
    assert any("lost more than a tag" in problem for problem in problems)
