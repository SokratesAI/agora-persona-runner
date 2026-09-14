"""`tools.project_goals_check` -- issue #227's model as an exit code.

The split these tests are built around: a **defect** raises and an
**orphan** does not. Both used to raise together, which is what kept the
check out of `tools.preflight` -- 43 of today's seated milestones serve
nothing and 32 sit in projects the owner scoped out, so a merged list is red
forever on work nobody is allowed to do.
"""

import json

from agora_runner.project_goals import parse_project_goals
from tools.project_goals_check import main, report

GOALS = ("# Project goals\n\n## Nova\n\n"
         "```objective\nstatement: s\nstatus: agreed\nconversation: 18bdb05e-2ad0-479d-9a7d-d9b8bab3fd5e\n```\n\n"
         "```key-result\nid: nova-kr1\nname: n\nmeasure: m\n```\n\n"
         "```kpi\nid: nova-cost\nname: c\nmeasure: m\nhigh: 2\n```\n")
SEATS = ("| Project | Milestone | Position | Updated | Serves |\n"
         "|---|---|---|---|---|\n"
         "| Nova | Picking | 1 | 09-13 | nova-kr1 |\n")


def row(number, board="issues", project="Nova", milestone="Picking",
        status_key="backlog", done=False):
    return {"number": number, "board": board, "project": project,
            "milestone": milestone, "statusKey": status_key, "done": done}


def test_a_linked_milestone_is_clean():
    lines, code = report(GOALS, SEATS, rows=[row(1)])
    assert code == 0
    assert lines == ["MODEL HOLDS",
                     "1 project section(s), 1 seated milestone(s), "
                     "0 model problem(s), 0 orphan(s), 0 unplaced task(s)"]


def test_boards_that_were_not_read_say_so_instead_of_reading_clean():
    """`rows=None` and `rows=[]` are the same value to every function under
    this and opposite findings. An unread board reporting zero unplaced
    tasks is the best possible answer arriving from a check that never
    ran."""
    lines, code = report(GOALS, SEATS)
    assert code == 0
    assert any(line.startswith("TASKS NOT EVALUATED") for line in lines)
    assert lines[-1].endswith("tasks not read")
    assert not any("unplaced task(s)" in line for line in lines)

    empty, code = report(GOALS, SEATS, rows=[])
    assert code == 0
    assert not any(line.startswith("TASKS NOT EVALUATED") for line in empty)
    assert empty[-1].endswith("0 unplaced task(s)")


def test_a_task_under_an_unseated_milestone_exits_two():
    lines, code = report(GOALS, SEATS, rows=[row(7, milestone="Galaxy")])
    assert code == 2
    assert lines[0] == "BROKEN"
    assert any("issues #7: under Nova / 'Galaxy', which has no seat"
               in line for line in lines)


def test_the_seat_is_matched_on_project_and_milestone_together():
    """The same milestone title sits under more than one project, so a row
    matched on the title alone would pass under any project at all."""
    lines, code = report(GOALS, SEATS, rows=[row(7, project="Marcus")])
    assert code == 2
    assert any("under Marcus / 'Picking'" in line for line in lines)


def test_a_task_under_no_milestone_is_listed_but_does_not_raise():
    lines, code = report(GOALS, SEATS, rows=[row(9, milestone="")])
    assert code == 0
    assert any(line.startswith("UNPLACED TASKS (1)") for line in lines)
    assert any("issues #9: Nova, under no milestone" in line for line in lines)
    assert lines[0] == "MODEL HOLDS"


def test_a_task_with_no_project_is_reported_as_unplaced_not_as_a_defect():
    """It cannot be seated either way, and "this row hangs off nothing" is
    one verdict -- splitting it across two lists would ask the reader to
    join them back up."""
    lines, code = report(GOALS, SEATS,
                         rows=[row(9, project="", milestone="")])
    assert code == 0
    assert any("issues #9: no project, under no milestone" in line
               for line in lines)


def test_a_task_naming_a_milestone_but_no_project_is_unplaced_not_broken():
    """A seat is keyed on the pair, so a row with no project cannot be
    matched against one whatever milestone it names -- and calling that a
    broken pointer would report a defect no diff can close, because there
    is no seat it could ever match. It is the same verdict as naming no
    milestone at all: the row hangs off nothing."""
    lines, code = report(GOALS, SEATS, rows=[row(9, project="")])
    assert code == 0
    assert lines[0] == "MODEL HOLDS"
    assert any("issues #9: no project, so its milestone 'Picking' cannot be "
               "seated" in line for line in lines)
    assert not any("no seat" in line for line in lines)


def test_a_closed_task_is_not_asked_which_milestone_it_serves():
    """The separating case for the open-row filter: a `done` or `outdated`
    row is finished work, and counting it would make this list grow with
    every board roll and never shrink."""
    rows = [row(2, status_key="done", done=True, milestone=""),
            row(3, status_key="outdated", milestone=""),
            row(4, status_key="done", done=True, milestone="Galaxy")]
    lines, code = report(GOALS, SEATS, rows=rows)
    assert code == 0
    assert lines[-1].endswith("0 unplaced task(s)")
    assert not any("#2" in line or "#3" in line or "#4" in line
                   for line in lines)


def test_an_unplaced_task_beside_a_real_defect_still_raises_on_the_defect():
    """Mirror of the orphan test below: if the unplaced list had merely been
    dropped rather than split off, this would pass anyway."""
    rows = [row(9, milestone=""), row(7, milestone="Galaxy")]
    lines, code = report(GOALS, SEATS, rows=rows)
    assert code == 2
    assert lines[0] == "BROKEN"
    assert any("no seat" in line for line in lines)
    assert any(line.startswith("UNPLACED TASKS (1)") for line in lines)


def test_an_orphan_milestone_is_listed_but_does_not_raise():
    """The whole reason this check can sit in `preflight`. An orphan is
    either keep-the-lights-on work or a pruning signal for the owner, and
    neither has a pull request that closes it."""
    seats = SEATS + "| Nova | Runner engineering | 2 | 09-13 |  |\n"
    lines, code = report(GOALS, seats)
    assert code == 0
    assert any("serves no key result and keeps no KPI" in line
               for line in lines)
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
    assert any("serves no key result and keeps no KPI" in line
               for line in lines)
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
    goals, seats, rows = tmp_path / "g.md", tmp_path / "s.md", tmp_path / "r.json"
    goals.write_text(GOALS)
    seats.write_text(SEATS.replace("nova-kr1", "nova-cost"))
    rows.write_text(json.dumps([row(1)]))
    assert main(["--goals", str(goals), "--seats", str(seats),
                 "--rows", str(rows)]) == 2
    assert "is a KPI" in capsys.readouterr().out


def test_boards_it_cannot_read_exit_one_rather_than_reporting_no_unplaced_tasks(
        tmp_path, capsys):
    """The same call `--goals` makes on a missing file. Reporting a clean
    task list off a board nobody answered for is the guaranteed-positive
    result -- it looks identical to the best possible outcome."""
    goals, seats = tmp_path / "g.md", tmp_path / "s.md"
    goals.write_text(GOALS)
    seats.write_text(SEATS)
    assert main(["--goals", str(goals), "--seats", str(seats),
                 "--rows", str(tmp_path / "nope.json")]) == 1
    assert "UNREADABLE: the boards" in capsys.readouterr().out


def test_orphans_prints_the_inventory_alone_and_exits_zero(tmp_path, capsys):
    goals, seats = tmp_path / "g.md", tmp_path / "s.md"
    goals.write_text(GOALS)
    seats.write_text(SEATS + "| Nova | Runner engineering | 2 | 09-13 |  |\n")
    assert main(["--goals", str(goals), "--seats", str(seats), "--orphans"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["nova / runner engineering: serves no key result and "
                   "keeps no KPI -- either keep-the-lights-on work whose "
                   "guardrail has not been written yet, or work nobody can "
                   "justify"]


def test_scaffold_prints_an_empty_document_that_parses_as_empty(capsys):
    assert main(["--scaffold"]) == 0
    out = capsys.readouterr().out
    assert "# Project goals" in out and "contract:" in out
    assert parse_project_goals(out) == {}


def test_a_seat_naming_the_kpi_it_keeps_leaves_the_orphan_list_empty():
    """The `Keeps` column read end to end: the same row that was an orphan
    above stops being one once it says which guardrail it holds, and the
    document still holds -- issue #227's fourth rule, both verdicts."""
    seats = (SEATS
             + "| Nova | Runner engineering | 2 | 09-13 |  | nova-cost |\n")
    lines, code = report(GOALS, seats)
    assert code == 0
    assert any("0 orphan(s)" in line for line in lines)
    assert not any("keeps no KPI" in line for line in lines)


def test_a_seat_keeping_a_key_result_raises():
    """The other half of the rule that a guardrail is not a goal."""
    seats = (SEATS
             + "| Nova | Runner engineering | 2 | 09-13 |  | nova-kr1 |\n")
    lines, code = report(GOALS, seats)
    assert code == 2
    assert any("is a key result" in line for line in lines)
