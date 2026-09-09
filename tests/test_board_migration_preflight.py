"""The three merged #203 primitives, run over one board instead of three suites.

Each of `rank_key`, `entity_id` and `board_view` is covered on its own.
What these cover is the seam between them -- the thing the migration
commit does and no test did until now.
"""
import pytest

from agora_runner import entity_id, nova_boards
from tools import board_migration_preflight as preflight

HEADER = (
    "## Board\n\n"
    "| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |\n"
    "|---|------|--------|---------|----------|---------|------|-----------|-------|\n"
)


def board(rows):
    """Board markdown for `(number, project, milestone)` triples."""
    lines = [
        f"| [[#{n} — Item {n}\\|{n}]] | Item {n} | ⚪ Backlog | 09-09 "
        f"| 🟡 Medium | {project} | | {milestone} | |"
        for n, project, milestone in rows
    ]
    return HEADER + "\n".join(lines) + "\n"


def compose(rows):
    items = preflight.board_items(board(rows))
    assert len(items) == len(rows), "the fixture board did not parse as rows"
    return preflight.compose(items)


def test_two_spellings_of_one_project_are_one_project():
    """The whole reason `entity_id` exists: `Nova` and `nova` are one thing,
    so eleven spellings of eleven projects must not become twelve ids."""
    report, problems = compose([(1, "Nova", ""), (2, "nova", ""), (3, "Agora", "")])
    assert report["rows"] == 3
    assert report["projects"] == 2
    assert problems == []


def test_projects_that_differ_by_more_than_spelling_stay_apart():
    report, problems = compose([(1, "Nova", ""), (2, "Marcus", ""), (3, "Infra", "")])
    assert report["projects"] == 3
    assert problems == []


def test_one_milestone_name_under_two_projects_is_two_milestones():
    """Milestones are minted inside a project, so `v1` on Nova and `v1` on
    Marcus are different rows to move and must not collapse to one id."""
    report, problems = compose([(1, "Nova", "v1"), (2, "Marcus", "v1")])
    assert report["milestones"] == 2
    assert problems == []


def test_a_blank_project_cell_is_minted_as_nova_rather_than_as_nothing():
    """The finding this module exists to carry, pinned against the real
    parser rather than restated: `parse_board` reads an empty `Project`
    cell as `DEFAULT_PROJECT`, so the migration hands that row a permanent
    `Nova` id. A preflight cannot count project-less rows because none
    ever reaches it."""
    items = preflight.board_items(board([(1, "Marcus", ""), (2, "", "")]))
    assert [item["project"] for item in items] == ["Marcus", nova_boards.DEFAULT_PROJECT]
    report, problems = preflight.compose(items)
    assert report["projects"] == 2
    assert problems == []


def test_every_gap_in_a_board_sized_run_still_takes_an_insert():
    """`between` is tested on pairs; this is `between` on every adjacent
    pair of a sequence the size of the real boards, which is where a
    midpoint runs out of room."""
    rows = [(n, "Nova", "") for n in range(1, 521)]
    report, problems = compose(rows)
    assert report["rank_keys"] == 520
    assert problems == []


def test_two_names_sharing_one_id_is_a_problem_the_report_names(monkeypatch):
    """The check has to be able to fire. Force `ensure_project` to hand two
    genuinely different names the same id and the pair is named."""
    monkeypatch.setattr(
        entity_id, "ensure_project", lambda registry, name: "prj-collapsed"
    )
    _, problems = compose([(1, "Nova", ""), (2, "Marcus", "")])
    assert any("prj-collapsed" in p and "Marcus" in p for p in problems)


def test_assert_clean_exits_two_only_when_something_would_not_compose(tmp_path, monkeypatch):
    clean = tmp_path / "clean.md"
    clean.write_text(board([(1, "Nova", "v1"), (2, "Marcus", "v1")]), encoding="utf-8")
    assert preflight.main(["--board", str(clean), "--assert-clean"]) == 0

    dirty = tmp_path / "dirty.md"
    dirty.write_text(board([(1, "Nova", ""), (2, "Marcus", "")]), encoding="utf-8")
    monkeypatch.setattr(
        entity_id, "ensure_project", lambda registry, name: "prj-collapsed"
    )
    assert preflight.main(["--board", str(dirty), "--assert-clean"]) == 2
    assert preflight.main(["--board", str(dirty)]) == 0


def test_a_board_with_no_rows_composes_to_nothing_rather_than_raising():
    report, problems = preflight.compose([])
    assert report["rows"] == 0 and report["rank_keys"] == 0 and report["projects"] == 0
    assert problems == []


def test_it_needs_a_board_to_look_at():
    with pytest.raises(SystemExit):
        preflight.main([])
