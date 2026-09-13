"""`tools.project_goals_check` -- issue #227's orphan list as an exit code."""

from agora_runner.project_goals import parse_project_goals
from tools.project_goals_check import main, report

GOALS = ("# Project goals\n\n## Nova\n\n"
         "```objective\nstatement: s\nstatus: approved\n```\n\n"
         "```key-result\nid: nova-kr1\nname: n\nmeasure: m\n```\n\n"
         "```kpi\nid: nova-cost\nname: c\nmeasure: m\nhigh: 2\n```\n")
SEATS = ("| Project | Milestone | Position | Updated | Serves |\n"
         "|---|---|---|---|---|\n"
         "| Nova | Picking | 1 | 09-13 | nova-kr1 |\n")


def test_a_linked_milestone_is_clean():
    lines, code = report(GOALS, SEATS)
    assert code == 0
    assert lines == ["1 project section(s), 1 seated milestone(s)"]


def test_an_orphan_milestone_is_listed_and_exits_one():
    seats = SEATS + "| Nova | Runner engineering | 2 | 09-13 |  |\n"
    lines, code = report(GOALS, seats)
    assert code == 1
    assert any("serves nothing" in line for line in lines)


def test_a_milestone_pointed_at_a_kpi_exits_one():
    seats = SEATS.replace("nova-kr1", "nova-cost")
    lines, code = report(GOALS, seats)
    assert code == 1
    assert any("is a KPI" in line for line in lines)


def test_a_broken_model_exits_one_even_with_no_seats():
    goals = GOALS.replace("id: nova-kr1\nname: n\nmeasure: m", "id: nova-kr1\nname: n")
    lines, code = report(goals, "")
    assert code == 1
    assert any("has no measure" in line for line in lines)


def test_an_empty_goals_document_is_the_state_before_step_two_not_a_failure():
    lines, code = report("", SEATS)
    assert code == 0
    assert "step 2" in lines[0]


def test_an_unreadable_file_exits_two_rather_than_clean(tmp_path, capsys):
    good = tmp_path / "s.md"
    good.write_text(SEATS)
    assert main(["--goals", str(tmp_path / "nope.md"), "--seats", str(good)]) == 2
    assert "UNREADABLE" in capsys.readouterr().out


def test_main_prints_the_report_and_returns_its_code(tmp_path, capsys):
    goals, seats = tmp_path / "g.md", tmp_path / "s.md"
    goals.write_text(GOALS)
    seats.write_text(SEATS + "| Nova | Orphan | 2 | 09-13 |  |\n")
    assert main(["--goals", str(goals), "--seats", str(seats)]) == 1
    assert "serves nothing" in capsys.readouterr().out


def test_scaffold_prints_an_empty_document_that_parses_as_empty(capsys):
    assert main(["--scaffold"]) == 0
    out = capsys.readouterr().out
    assert "# Project goals" in out and "contract:" in out
    assert parse_project_goals(out) == {}
