"""`tools.project_goals_check` -- issue #227's model as an exit code.

The split these tests are built around: a **defect** raises and an
**orphan** does not. Both used to raise together, which is what kept the
check out of `tools.preflight` -- 43 of today's seated milestones serve
nothing and 32 sit in projects the owner scoped out, so a merged list is red
forever on work nobody is allowed to do.
"""

from agora_runner.project_goals import parse_project_goals
from tools.project_goals_check import main, report

GOALS = ("# Project goals\n\n## Nova\n\n"
         "```objective\nstatement: s\nstatus: agreed\nconversation: 18bdb05e-2ad0-479d-9a7d-d9b8bab3fd5e\n```\n\n"
         "```key-result\nid: nova-kr1\nname: n\nmeasure: m\n```\n\n"
         "```kpi\nid: nova-cost\nname: c\nmeasure: m\nhigh: 2\n```\n")
SEATS = ("| Project | Milestone | Position | Updated | Serves |\n"
         "|---|---|---|---|---|\n"
         "| Nova | Picking | 1 | 09-13 | nova-kr1 |\n")


def test_a_linked_milestone_is_clean():
    lines, code = report(GOALS, SEATS)
    assert code == 0
    assert lines == ["MODEL HOLDS",
                     "1 project section(s), 1 seated milestone(s), "
                     "0 model problem(s), 0 orphan(s)"]


def test_an_orphan_milestone_is_listed_but_does_not_raise():
    """The whole reason this check can sit in `preflight`. An orphan is
    either keep-the-lights-on work or a pruning signal for the owner, and
    neither has a pull request that closes it."""
    seats = SEATS + "| Nova | Runner engineering | 2 | 09-13 |  |\n"
    lines, code = report(GOALS, seats)
    assert code == 0
    assert any("serves nothing" in line for line in lines)
    assert any("1 orphan(s)" in line for line in lines)
    assert not any(line.startswith("BROKEN") for line in lines)


def test_an_orphan_beside_a_real_defect_still_raises_on_the_defect():
    """The separating case: if the orphan had merely been dropped from the
    list instead of split off, this would pass anyway. The defect has to
    reach the exit code across a document that also holds an orphan."""
    seats = (SEATS.replace("nova-kr1", "nova-cost")
             + "| Nova | Runner engineering | 2 | 09-13 |  |\n")
    lines, code = report(GOALS, seats)
    assert code == 2
    assert any("is a KPI" in line for line in lines)
    assert any("serves nothing" in line for line in lines)
    assert lines[0] == "BROKEN"


def test_a_milestone_pointed_at_a_kpi_exits_two():
    seats = SEATS.replace("nova-kr1", "nova-cost")
    lines, code = report(GOALS, seats)
    assert code == 2
    assert any("is a KPI" in line for line in lines)


def test_a_broken_model_exits_two_even_with_no_seats():
    goals = GOALS.replace("id: nova-kr1\nname: n\nmeasure: m", "id: nova-kr1\nname: n")
    lines, code = report(goals, "")
    assert code == 2
    assert any("has no measure" in line for line in lines)


def test_an_empty_goals_document_is_the_state_before_step_two_not_a_failure():
    lines, code = report("", SEATS)
    assert code == 0
    assert "step 2" in lines[0]


def test_an_unreadable_file_exits_one_rather_than_clean(tmp_path, capsys):
    """`preflight` reads 1 as UNREADABLE and 2 as ACT. This tool had the two
    the other way round until cycle 1536, so a real defect would have been
    tabled as "could not read" and a vault it could not reach as "act"."""
    good = tmp_path / "s.md"
    good.write_text(SEATS)
    assert main(["--goals", str(tmp_path / "nope.md"), "--seats", str(good)]) == 1
    assert "UNREADABLE" in capsys.readouterr().out


def test_main_prints_the_report_and_returns_its_code(tmp_path, capsys):
    goals, seats = tmp_path / "g.md", tmp_path / "s.md"
    goals.write_text(GOALS)
    seats.write_text(SEATS.replace("nova-kr1", "nova-cost"))
    assert main(["--goals", str(goals), "--seats", str(seats)]) == 2
    assert "is a KPI" in capsys.readouterr().out


def test_orphans_prints_the_inventory_alone_and_exits_zero(tmp_path, capsys):
    goals, seats = tmp_path / "g.md", tmp_path / "s.md"
    goals.write_text(GOALS)
    seats.write_text(SEATS + "| Nova | Runner engineering | 2 | 09-13 |  |\n")
    assert main(["--goals", str(goals), "--seats", str(seats), "--orphans"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["nova / runner engineering: serves nothing -- either "
                   "keep-the-lights-on work that belongs under a KPI, or "
                   "work nobody can justify"]


def test_scaffold_prints_an_empty_document_that_parses_as_empty(capsys):
    assert main(["--scaffold"]) == 0
    out = capsys.readouterr().out
    assert "# Project goals" in out and "contract:" in out
    assert parse_project_goals(out) == {}
