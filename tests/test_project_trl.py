"""A project's TRL -- milestone M5 of idea #260, the field Nova owns.

The spec (`projects/sokrates/projects/nova/task-prioritization-redesign.md`)
names four levels -- *"Prototype -> Functional -> Hardened -> Proven"* --
and asks for five so the meter matches satisfaction's width, leaving the
fifth open. `Concept` is the chosen fifth and it sits at the *bottom*; the
first test here fails by name if somebody puts it on top, because a level
above `Proven` would change what the ceiling he already named means.

What these pin, in the order the write travels: the five levels and their
order, a cell that is read off a table with no TRL column yet, a numeric
spelling meaning the same as the word, the difference between unassessed
and `Concept`, the setter refusing a project that has no row, and the CLI
refusing a write that moved anything else.
"""

import agora_runner.nova_boards as nova_boards
from agora_runner.nova_boards import (
    PROJECT_TRL_LEVELS,
    canonical_trl,
    parse_project_meta,
    parse_project_trl_cell,
    project_trl_key,
    set_project_trl,
)
import tools.project_trl as project_trl

PLAIN = """---
type: board
---

# Projects

| Project | Priority | Updated |
|---|---|---|
| Marcus | 🔴 Immediately | 09-01 |
| Demos | ⚪ Low | 09-02 |
| Nova | 🟠 High | 09-01 |
"""

WIDE = """---
type: board
---

# Projects

| Project | Priority | Updated | Order | TRL |
|---|---|---|---|---|
| Marcus | 🔴 Immediately | 09-01 | 1 | Proven |
| Demos | ⚪ Low | 09-02 | 2 |  |
| Nova | 🟠 High | 09-01 | 3 | Concept |
"""


def test_the_five_levels_run_worst_first_with_concept_at_the_bottom():
    assert PROJECT_TRL_LEVELS == (
        "Concept", "Prototype", "Functional", "Hardened", "Proven",
    )
    # The four the spec named keep the order it gave them, and the added
    # fifth is below all of them rather than above `Proven`.
    assert project_trl_key("Concept") == 1
    assert project_trl_key("Proven") == len(PROJECT_TRL_LEVELS)
    for lower, higher in zip(PROJECT_TRL_LEVELS, PROJECT_TRL_LEVELS[1:]):
        assert project_trl_key(lower) < project_trl_key(higher)


def test_a_number_and_a_word_are_the_same_level():
    assert canonical_trl("3") == "Functional"
    assert canonical_trl("functional") == "Functional"
    assert canonical_trl("FUNCTIONAL") == "Functional"
    # Off the ends, and a word that is not a level at all.
    assert canonical_trl("0") is None
    assert canonical_trl("6") is None
    assert canonical_trl("Amazing") is None


def test_unassessed_is_not_the_bottom_level():
    # `""` means nobody has looked; `Concept` is a judgement that the
    # project is at the bottom of the scale. The meter draws zero dots for
    # one and one dot for the other, so they must not collapse.
    assert parse_project_trl_cell("") == ""
    assert project_trl_key("") == 0
    assert parse_project_trl_cell("Concept") == "Concept"
    assert project_trl_key("Concept") == 1
    # A typo in a file he can hand-edit reads as unassessed, never as an
    # error that takes the whole table out.
    assert parse_project_trl_cell("hardned") == ""


def test_a_table_with_no_trl_column_grows_one_on_the_first_write():
    written = set_project_trl(PLAIN, "marcus", "Hardened")
    assert "| Project | Priority | Updated | Order | TRL |" in written
    meta = parse_project_meta(written)
    assert meta["marcus"]["trl"] == "Hardened"
    # Nobody else was assessed by the widening, and no rating moved.
    assert meta["demos"]["trl"] == ""
    assert meta["nova"]["trl"] == ""
    assert meta["demos"]["priority"] == "⚪ Low"
    assert meta["marcus"]["updated"] == "09-01"


def test_the_write_touches_one_cell_and_leaves_the_order_alone():
    written = set_project_trl(WIDE, "Demos", "2")
    meta = parse_project_meta(written)
    assert meta["demos"]["trl"] == "Prototype"
    assert meta["demos"]["order"] == 2
    assert meta["marcus"]["trl"] == "Proven"
    assert meta["nova"]["trl"] == "Concept"


def test_clearing_is_legal_and_a_bad_level_is_not():
    cleared = set_project_trl(WIDE, "Nova", "")
    assert parse_project_meta(cleared)["nova"]["trl"] == ""
    assert set_project_trl(WIDE, "Nova", "Amazing") is None
    assert set_project_trl(WIDE, "Nova", "9") is None


def test_a_project_with_no_row_is_refused():
    # A readiness score is a statement about a project that exists;
    # inventing the row would rate a project he never rated.
    assert set_project_trl(WIDE, "Ghost", "Proven") is None
    assert set_project_trl("", "Marcus", "Proven") is None


def test_check_catches_a_write_that_moved_something_else():
    good = set_project_trl(WIDE, "Demos", "Functional")
    assert project_trl.check(WIDE, good, "Demos", "Functional") == []

    # A setter that also re-rated the project, which is the failure this
    # guard exists for -- the cell is one column away from his priority.
    bad = good.replace("| Demos | ⚪ Low |", "| Demos | 🟠 High |")
    problems = project_trl.check(WIDE, bad, "Demos", "Functional")
    assert problems and "other than its TRL" in problems[0]

    # And a write that landed the wrong level.
    assert project_trl.check(WIDE, good, "Demos", "Proven")


def test_cli_writes_the_file_and_refuses_an_unknown_project(tmp_path):
    path = tmp_path / "projects.md"
    path.write_text(PLAIN, encoding="utf-8")

    assert project_trl.main(
        ["--file", str(path), "--project", "nova", "--trl", "prototype"]
    ) == 0
    assert parse_project_meta(path.read_text(encoding="utf-8"))["nova"]["trl"] == "Prototype"

    before = path.read_text(encoding="utf-8")
    assert project_trl.main(
        ["--file", str(path), "--project", "Ghost", "--trl", "proven"]
    ) == 1
    assert project_trl.main(
        ["--file", str(path), "--project", "Nova", "--trl", "Amazing"]
    ) == 1
    # A refusal writes nothing at all.
    assert path.read_text(encoding="utf-8") == before
