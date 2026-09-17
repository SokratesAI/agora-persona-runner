"""`tools.project_goals_check` -- issue #227's model as an exit code.

The split these tests are built around: a **defect** raises and an
**orphan** does not. Both used to raise together, which is what kept the
check out of `tools.preflight` -- 43 of today's seated milestones serve
nothing and 32 sit in projects the owner scoped out, so a merged list is red
forever on work nobody is allowed to do.
"""

import datetime
import json

from agora_runner import board_store
from agora_runner.project_goals import parse_project_goals
from tools import project_goals_check
from tools.project_goals_check import main, report

GOALS = ("# Project goals\n\n## Nova\n\n"
         "```objective\nstatement: s\nstatus: agreed\nconversation: 18bdb05e-2ad0-479d-9a7d-d9b8bab3fd5e\n```\n\n"
         "```key-result\nid: nova-kr1\nname: n\nmeasure: m\ntarget: 1\nstatus: agreed\n```\n\n"
         "```kpi\nid: nova-cost\nname: c\nmeasure: m\nhigh: 2\n```\n")
SEATS = ("| Project | Milestone | Position | Updated | Serves |\n"
         "|---|---|---|---|---|\n"
         "| Nova | Picking | 1 | 09-13 | nova-kr1 |\n")


def row(number, board="issues", project="Nova", milestone="Picking",
        status_key="backlog", done=False):
    return {"number": number, "board": board, "project": project,
            "milestone": milestone, "statusKey": status_key, "done": done}


def test_a_linked_milestone_is_clean():
    """`SEATS` has no `Keeps` column, so `nova-cost` is a KPI nobody holds in
    bounds. That is the unpointed-goal inventory and it is pinned here rather
    than hidden, because the subject of this test is that a model with an
    inventory line in it still exits 0 -- an inventory is not a defect.

    `GOALS` carries no `period:` deliberately, and for a second reason: a
    fixture pinned to a real month goes red the morning that month ends,
    which is a suite that was green at merge and fails later with no change
    to the code. Undated is the one state rule 7 reads the same way forever,
    so the shared fixture holds it and the tests that care about the clock
    pass their own `today`."""
    lines, code = report(GOALS, SEATS, rows=[row(1)])
    assert code == 0
    assert lines == ["MODEL HOLDS",
                     "NOTHING POINTS AT (1) -- rule 4 read from the goals "
                     "side, an inventory rather than a defect, so it does "
                     "not raise:",
                     "  Nova / nova-cost: a KPI no milestone keeps -- no "
                     "milestone is accountable for holding it in bounds",
                     "NO BASELINE (1 of 1) -- a key result records where its "
                     "number stands, and nothing records where it started. An "
                     "inventory rather than a defect, so it does not raise: "
                     "the baseline is a reading someone has to take:",
                     "  Nova / nova-kr1",
                     "NO MONTH AT ALL (1) -- also rule 7, and also not a "
                     "defect: an objective carrying no period is one nothing "
                     "can ever report as stale:",
                     "  Nova: objective names no period -- nothing can tell "
                     "whether this month's goal is this month's",
                     "1 project section(s), 1 seated milestone(s), "
                     "0 model problem(s), 0 orphan(s), 1 unpointed goal(s), "
                     "0 KPI(s) out of bounds, "
                     "0 of them with nobody on it, "
                     "0 key result(s) short of target with nobody on it, "
                     "1 of 1 key result(s) with no baseline, "
                     "0 write-up(s) contradicting their own number, "
                     "0 quoting an earlier reading, "
                     "0 objective(s) past their month, 1 undated, "
                     "0 of 1 project(s) still being argued, "
                     "0 unplaced task(s), "
                     "1 of 1 project(s) have a goal"]


def test_boards_that_were_not_read_say_so_instead_of_reading_clean():
    """`rows=None` and `rows=[]` are the same value to every function under
    this and opposite findings. An unread board reporting zero unplaced
    tasks is the best possible answer arriving from a check that never
    ran."""
    lines, code = report(GOALS, SEATS)
    assert code == 0
    assert any(line.startswith("TASKS NOT EVALUATED") for line in lines)
    assert lines[-1].endswith("tasks not read, project coverage not read")
    assert not any("unplaced task(s)" in line for line in lines)
    # The same claim about the other half of what the boards are read for:
    # which projects still have no goal is unknowable without them, and a
    # clean "0 of 0" would be the guaranteed-positive answer again.
    assert any(line.startswith("PROJECT COVERAGE NOT EVALUATED")
               for line in lines)
    assert not any(line.startswith("PROJECTS WITH NO GOAL") for line in lines)

    empty, code = report(GOALS, SEATS, rows=[])
    assert code == 0
    assert not any(line.startswith("TASKS NOT EVALUATED") for line in empty)
    assert not any(line.startswith("PROJECT COVERAGE NOT EVALUATED")
                   for line in empty)
    assert empty[-1].endswith("0 unplaced task(s), "
                              "0 of 0 project(s) have a goal")


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
    assert lines[-1].endswith("0 unplaced task(s), "
                              "0 of 0 project(s) have a goal")
    assert not any("issues #2" in line or "issues #3" in line
                   or "issues #4" in line for line in lines)


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

    # The whole board payload rather than its `items` list: `list()` on it
    # hands back its keys, and every key would then be reported as a task
    # with no milestone.
    whole = tmp_path / "whole.json"
    whole.write_text(json.dumps({"name": "issues", "items": [row(1)]}))
    assert main(["--goals", str(goals), "--seats", str(seats),
                 "--rows", str(whole)]) == 1
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


def test_the_report_prints_the_two_kinds_of_orphan_under_their_own_headings():
    """The readability failure this split exists for: one empty seat under
    a project with goals is rule 4's question, one under a project with
    none is not, and printed together under one heading the first is
    unfindable. Both are still reported and the total is still both."""
    seats = (SEATS
             + "| Nova | Runner engineering | 2 | 09-13 |  |\n"
               "| Agora | Ask me a question | 3 | 09-13 |  |\n")
    lines, code = report(GOALS, seats,
                         rows=[row(1),
                               row(2, milestone="Runner engineering")])
    assert code == 0
    body = "\n".join(lines)
    assert "ORPHANS (1)" in body
    assert "NO GOALS TO SERVE YET (1)" in body
    assert "NOTHING LEFT TO KEEP" not in body
    assert "  nova / runner engineering: serves no key result and keeps no " \
           "KPI -- either keep-the-lights-on work whose guardrail has not " \
           "been written yet, or work nobody can justify" in lines
    assert "  agora / ask me a question: serves no key result and keeps no " \
           "KPI -- and there is none to serve, because no key result or " \
           "KPI is written for this project yet" in lines
    assert "2 orphan(s) (1 pruning signal, 0 with nothing left to keep, " \
           "1 awaiting project goals, 0 not bet this period)" in lines[-1]
    # Each orphan is printed once, under exactly one of the two headings.
    # Reporting the whole list under `ORPHANS` as well is the failure this
    # split exists to end, and it leaves both assertions above true.
    assert sum("ask me a question" in l for l in lines) == 1
    assert sum("runner engineering" in l for l in lines) == 1
    heading = next(i for i, l in enumerate(lines)
                   if l.startswith("NO GOALS TO SERVE"))
    assert next(i for i, l in enumerate(lines)
                if "ask me a question" in l) == heading + 1
    assert next(i for i, l in enumerate(lines)
                if "runner engineering" in l) < heading


def test_a_report_with_no_orphans_does_not_carry_the_split():
    """A parenthetical that reads `(0 pruning signal, 0 awaiting project
    goals)` on every clean run is noise on the line a cycle actually reads,
    so the split only prints when there is something to split."""
    lines, _ = report(GOALS, SEATS, rows=[row(1)])
    assert "0 orphan(s)," in lines[-1]
    assert "pruning signal" not in lines[-1]


def test_the_orphans_flag_groups_the_two_kinds_too(tmp_path, capsys):
    """`--orphans` is for when the list is the thing you came for, so it
    gets the same grouping the report does -- four pruning signals
    interleaved alphabetically with thirty-two that have nothing to serve
    is the state this split exists to end. `Alfa` sorts before `Nova`, so
    a flat alphabetical list puts it first and this test reds."""
    goals = tmp_path / "g.md"
    goals.write_text(GOALS)
    seats = tmp_path / "s.md"
    seats.write_text(SEATS
                     + "| Alfa | Nothing to serve | 2 | 09-13 |  |\n"
                       "| Nova | Runner engineering | 3 | 09-13 |  |\n")
    assert main(["--orphans", "--goals", str(goals),
                 "--seats", str(seats)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2
    assert out[0].startswith("nova / runner engineering")
    assert out[1].startswith("alfa / nothing to serve")


def _goals_with(period, status="agreed"):
    """`GOALS` with a period and a status on its objective."""
    conversation = "conversation: 18bdb05e-2ad0-479d-9a7d-d9b8bab3fd5e\n"
    return GOALS.replace(
        f"statement: s\nstatus: agreed\n{conversation}",
        f"statement: s\nstatus: {status}\n{conversation}period: {period}\n")


def test_an_objective_whose_month_has_ended_is_listed_but_does_not_raise():
    """Issue #227's seventh rule -- *"Monthly objectives, weekly check"* --
    and the reason it is an inventory rather than a defect: re-cutting a
    goal is a conversation with the owner, not something a pull request
    closes. The same call `serves_orphans` makes on the pruning list."""
    lines, code = report(_goals_with("2026-08"), SEATS, rows=[row(1)],
                         today=datetime.date(2026, 9, 14))
    assert code == 0
    assert sum(1 for l in lines if l.startswith("PAST THEIR MONTH (1)")) == 1
    assert any("objective covers August 2026, which ended before "
               "September 2026" in l for l in lines)
    assert "1 objective(s) past their month, 0 undated," in lines[-1]


def test_the_current_month_is_not_past_it():
    """The boundary, and the half that makes the test above evidence: an
    objective covering *this* month reads clean, so the finding comes from
    the month having ended rather than from the field merely existing."""
    lines, code = report(_goals_with("2026-09"), SEATS, rows=[row(1)],
                         today=datetime.date(2026, 9, 1))
    assert code == 0
    assert not [l for l in lines if l.startswith("PAST THEIR MONTH")]
    assert "0 objective(s) past their month, 0 undated," in lines[-1]


def test_a_struck_objective_is_never_asked_to_be_re_cut():
    """A struck objective is a decision kept so it can be read back, the
    same way `/plan` keeps a declined goal's block. Asking him to re-cut a
    goal he has already killed is the check inventing work."""
    lines, code = report(_goals_with("2026-01", status="struck"), SEATS,
                         rows=[row(1)], today=datetime.date(2026, 9, 14))
    assert code == 0
    assert not [l for l in lines if l.startswith("PAST THEIR MONTH")]
    assert not [l for l in lines if l.startswith("NO MONTH AT ALL")]


def test_a_period_that_is_not_a_month_is_a_defect_and_raises():
    """Unlike the two inventories, a period this cannot parse *is* a model
    problem: it is an objective nothing can ever age, which is the state
    rule 7 exists to end, and it is closed by editing one line."""
    lines, code = report(_goals_with("September"), SEATS, rows=[row(1)],
                         today=datetime.date(2026, 9, 14))
    assert code == 2
    assert any("objective period 'September' is not a month" in l
               for l in lines)


def test_the_month_check_reads_the_caller_s_clock_not_the_box_s():
    """`report` defaults `today` at the edge and `objective_periods` refuses
    to guess, so the same document answers differently only when the caller
    says so. A month test against an implicit clock is green forever on a
    box whose date happens to agree with the fixture."""
    goals = _goals_with("2026-09")
    assert not [l for l in report(goals, SEATS, rows=[row(1)],
                                  today=datetime.date(2026, 9, 30))[0]
                if l.startswith("PAST THEIR MONTH")]
    assert [l for l in report(goals, SEATS, rows=[row(1)],
                              today=datetime.date(2026, 10, 1))[0]
            if l.startswith("PAST THEIR MONTH")]


def _kpi(**fields):
    body = "".join(f"{k.replace('_', '-')}: {v}\n" for k, v in fields.items())
    return GOALS.replace(
        "```kpi\nid: nova-cost\nname: c\nmeasure: m\nhigh: 2\n```\n",
        f"```kpi\nid: nova-cost\nname: c\nmeasure: m\n{body}```\n")


def test_a_kpi_outside_its_range_is_listed_but_does_not_raise():
    """Issue #227 defines a KPI as a number that has to stay in bounds, and
    nothing in the model compared the two until now. It is an inventory
    rather than a defect: the document is well formed and the system is out
    of bounds, which is a reading to act on rather than a file to fix."""
    lines, code = report(_kpi(now=6, high=1), SEATS, rows=[])
    assert code == 0
    assert lines[0] == "MODEL HOLDS"
    heading = next(t for t in lines if t.startswith("KPIS OUT OF BOUNDS"))
    assert heading.startswith("KPIS OUT OF BOUNDS (1)")
    assert "  Nova / nova-cost: 6 is above the ceiling of 1" in lines
    assert lines[-1].count("1 KPI(s) out of bounds") == 1


def test_a_kpi_below_its_floor_is_a_breach_too():
    lines, _ = report(_kpi(now=0, low=1), SEATS, rows=[])
    assert "  Nova / nova-cost: 0 is below the floor of 1" in lines


def test_a_kpi_inside_its_range_says_nothing_and_the_summary_counts_zero():
    lines, code = report(_kpi(now=1, low=0, high=2), SEATS, rows=[])
    assert code == 0
    assert not [t for t in lines if t.startswith("KPIS OUT OF BOUNDS")]
    assert "0 KPI(s) out of bounds" in lines[-1]


def test_a_value_on_the_bound_is_in_bounds():
    """A ratchet KPI is written with `high` equal to the reading taken the day
    it was declared -- `nova-kpi-markdown-board-readers` is 11 against a
    ceiling of 11 -- so a `>=` here would report every ratchet as breached on
    the day it was written."""
    lines, _ = report(_kpi(now=11, low=0, high=11), SEATS, rows=[])
    assert not [t for t in lines if t.startswith("KPIS OUT OF BOUNDS")]


def test_a_kpi_with_no_reading_is_not_reported_as_in_bounds_or_out():
    """Four goals in the live document carry no number because no instrument
    exists yet. Unmeasured and in-bounds are not the same state, and reading a
    blank as 0 would put every unmeasured KPI with a floor on the list."""
    for now in ("", "not measured"):
        lines, _ = report(_kpi(now=now, low=1, high=2), SEATS, rows=[])
        assert not [t for t in lines if t.startswith("KPIS OUT OF BOUNDS")]


def test_the_breach_line_carries_the_unit_the_owner_reads():
    lines, _ = report(_kpi(now=3.4, high=2.0, unit="M"), SEATS, rows=[])
    assert "  Nova / nova-cost: 3.4 M is above the ceiling of 2.0 M" in lines


def test_a_project_on_the_boards_with_no_objective_is_listed_but_does_not_raise():
    """Issue #227's own title, counted. `Demos` has rows and no section, so
    nothing in the document knows it exists -- which is why the list is read
    off the boards rather than off `project-goals.md`, where every project
    would have a goal by construction."""
    rows = [row(1), row(2, project="Demos", milestone="")]
    lines, code = report(GOALS, SEATS, rows=rows)
    assert code == 0
    assert ("PROJECTS WITH NO GOAL (1 of 2) -- issue #227's own title, an "
            "inventory rather than a defect, so it does not raise. These "
            "projects have rows on the boards and no objective anywhere:"
            ) in lines
    assert ("  Demos: no objective is written for this project -- 1 open "
            "row(s) on the boards and nothing saying what any of them is for"
            ) in lines
    assert lines[-1].endswith("1 of 2 project(s) have a goal")


def test_a_project_whose_rows_are_all_closed_is_not_a_project_with_no_goal():
    """Cycle 1630. `Research` has closed rows and nothing open, so there is no
    work left to say what it is for -- the same call `split_orphans` makes on a
    pruning orphan whose rows are all closed. It is not a pruning signal
    either: pruning is stopping open work, and this has already stopped."""
    rows = [row(1),
            row(9, project="Research", milestone="", status_key="done",
                done=True)]
    lines, code = report(GOALS, SEATS, rows=rows)
    assert code == 0
    assert not [line for line in lines if "Research: no objective" in line]
    assert lines[-1].endswith("1 of 1 project(s) have a goal")


def test_a_project_that_has_a_goal_is_not_in_the_list():
    """The whole-document case: one project, one objective, nothing to say."""
    lines, code = report(GOALS, SEATS, rows=[row(1)])
    assert not any(line.startswith("PROJECTS WITH NO GOAL") for line in lines)
    assert lines[-1].endswith("1 of 1 project(s) have a goal")
    assert code == 0


def test_an_orphan_with_no_open_row_gets_its_own_heading():
    """The third heading, and the reason it is not a fourth verdict: the
    total is unchanged and the line moves out of the question list, because
    a milestone whose every row is closed is not a prioritisation call."""
    seats = SEATS + "| Nova | Runner engineering | 2 | 09-13 |  |\n"
    lines, code = report(
        GOALS, seats,
        rows=[row(1),
              row(2, milestone="Runner engineering", status_key="outdated")])
    assert code == 0
    body = "\n".join(lines)
    assert "ORPHANS" not in body
    assert "NOTHING LEFT TO KEEP (1)" in body
    assert "  nova / runner engineering: serves no key result and keeps no " \
           "KPI -- and no row under it is still open, so there is nothing " \
           "here to keep: retire the milestone" in lines
    assert "1 orphan(s) (0 pruning signal, 1 with nothing left to keep, " \
           "0 awaiting project goals, 0 not bet this period)" in lines[-1]


def test_a_written_goal_nobody_settled_prints_and_still_exits_zero():
    """The state the live document was in for two days: twelve projects with
    an objective each, `12 of 12 project(s) have a goal` on the summary line,
    and not one of them agreed with him. Written and settled are different
    states and the summary now carries both."""
    goals = GOALS.replace("status: agreed", "status: discussing")
    lines, code = report(goals, SEATS, rows=[row(1)])
    assert code == 0
    assert any(line.startswith("STILL BEING ARGUED (1 of 1)")
               for line in lines)
    assert ("  Nova: still discussing the objective; "
            "1 key result(s): nova-kr1") in lines
    assert "1 of 1 project(s) still being argued" in lines[-1]
    assert lines[-1].endswith("1 of 1 project(s) have a goal")


def test_an_undated_write_up_is_listed_counted_and_does_not_raise():
    """The sweep is where this has to arrive -- `preflight` shows the summary
    line and nothing else, so a finding that is only in the body is one the
    morning read never sees. And it must not raise: with no date on the
    sentence, which of the two numbers is current is a judgement, the same
    call `kpi_breaches` makes."""
    goals = (GOALS.replace("measure: m\nhigh: 2", "measure: m\nnow: 1\nhigh: 2")
             + "\nMeasured 7 at 14:47 Oslo -- the paragraph nobody rewrote.\n")
    lines, code = report(goals, SEATS, rows=[row(1)],
                         today=datetime.date(2026, 9, 15))
    assert code == 0
    assert any(line.startswith("WRITE-UP CONTRADICTS ITS OWN NUMBER (1)")
               for line in lines)
    assert any("nova-cost: now: " in line for line in lines)
    assert "1 write-up(s) contradicting their own number" in lines[-1]
    assert "0 quoting an earlier reading" in lines[-1]


def test_a_dated_write_up_is_an_earlier_reading_rather_than_a_contradiction():
    """The live document's four disagreements were all dated, and calling
    them contradictions asked a cycle to settle a question the dates had
    already settled. `now:` is rewritten on every sweep, so a dated sentence
    beneath it is the reading before this one."""
    goals = (GOALS.replace("measure: m\nhigh: 2", "measure: m\nnow: 1\nhigh: 2")
             + "\nMeasured 7 at 14:47 Oslo on 2026-09-14 -- the paragraph "
               "nobody rewrote.\n")
    lines, code = report(goals, SEATS, rows=[row(1)],
                         today=datetime.date(2026, 9, 15))
    assert code == 0
    assert not any(line.startswith("WRITE-UP CONTRADICTS") for line in lines)
    assert any(line.startswith("WRITE-UP QUOTES AN EARLIER READING (1)")
               for line in lines)
    assert any("taken on 2026-09-14" in line for line in lines)
    assert "0 write-up(s) contradicting their own number" in lines[-1]
    assert "1 quoting an earlier reading" in lines[-1]


def test_a_document_whose_numbers_and_sentences_agree_says_zero():
    """A count that only ever appears when it is non-zero cannot be read as
    "checked and clean" -- the summary carries the 0 as well."""
    lines, code = report(GOALS, SEATS, rows=[row(1)],
                         today=datetime.date(2026, 9, 15))
    assert code == 0
    assert not any(line.startswith("WRITE-UP CONTRADICTS") for line in lines)
    assert not any(line.startswith("WRITE-UP QUOTES") for line in lines)
    assert "0 write-up(s) contradicting their own number" in lines[-1]
    assert "0 quoting an earlier reading" in lines[-1]


# --- where the rows come from -------------------------------------------
#
# The site's `/api/board` is a stale-while-revalidate cache over the same
# records, so a cycle that seats a row and then runs this check used to be
# told its own write had not happened. These pin the store as the source and
# the API as the fallback, which is the only thing that separates them.


class _Store:
    """A `board_store` stand-in: two boards, one registry, optional raise."""

    StoreError = board_store.StoreError

    def __init__(self, rows, raises=None):
        self._rows = rows
        self._raises = raises
        self.asked = []

    def read_registry(self):
        if self._raises:
            raise self._raises
        return {
            "projects": {"prj_nova": {"name": "Nova"}},
            "milestones": {"ms_picking": {"name": "Picking"}},
        }

    def read_rows(self, board):
        self.asked.append(board)
        return self._rows.get(board, [])


def _doc(number, board="idea", **fields):
    doc = {"_id": f"board:{board}:{number}", "type": "row", "board": board,
           "number": number, "title": "t", "status": "⚪ Backlog",
           "updated": "09-15", "where": "", "priority": "🔵 Medium",
           "size": "", "order": None, "done": False}
    doc.update(fields)
    return doc


def test_rows_come_out_of_the_record_store_with_their_names_resolved(monkeypatch):
    store = _Store({"idea": [_doc(308, projectId="prj_nova",
                                  milestoneId="ms_picking")],
                    "issue": []})
    monkeypatch.setattr(project_goals_check, "board_store", store)
    rows, ok = project_goals_check._rows_from_store()
    assert ok
    assert sorted(store.asked) == ["idea", "issue"]
    assert [r["board"] for r in rows] == ["ideas"]
    assert rows[0]["number"] == 308
    assert rows[0]["project"] == "Nova"
    assert rows[0]["milestone"] == "Picking"
    # Derived by `from_document`, not copied into a second definition here:
    # `open_rows` drops a row on `statusKey` and the record carries none.
    assert rows[0]["statusKey"] == "backlog"


def test_a_done_record_keeps_the_key_that_closes_it(monkeypatch):
    store = _Store({"idea": [_doc(1, done=True, status="✅ Done",
                                  projectId="prj_nova")],
                    "issue": []})
    monkeypatch.setattr(project_goals_check, "board_store", store)
    rows, ok = project_goals_check._rows_from_store()
    assert ok and rows[0]["statusKey"] == "done"


def test_the_board_name_a_finding_prints_is_the_plural_one(monkeypatch):
    store = _Store({"idea": [_doc(1, board="idea")],
                    "issue": [_doc(2, board="issue")]})
    monkeypatch.setattr(project_goals_check, "board_store", store)
    rows, _ = project_goals_check._rows_from_store()
    assert sorted(r["board"] for r in rows) == ["ideas", "issues"]


def test_the_check_reads_the_store_and_never_asks_the_site(monkeypatch):
    store = _Store({"idea": [_doc(1, projectId="prj_nova",
                                  milestoneId="ms_picking")], "issue": []})
    monkeypatch.setattr(project_goals_check, "board_store", store)

    def refuse(*args, **kwargs):  # pragma: no cover - a call is the failure
        raise AssertionError("the site was asked while the store answered")

    monkeypatch.setattr(project_goals_check.urllib.request, "urlopen", refuse)
    rows, ok = project_goals_check._fetch_rows()
    assert ok and [r["number"] for r in rows] == [1]


def test_an_unreadable_store_falls_back_to_the_site(monkeypatch):
    store = _Store({}, raises=board_store.StoreError("no credentials"))
    monkeypatch.setattr(project_goals_check, "board_store", store)
    payload = json.dumps({"items": [{"number": 7, "project": "Nova",
                                     "milestone": "Picking",
                                     "statusKey": "backlog"}]}).encode()

    class _Response:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(project_goals_check.urllib.request, "urlopen",
                        lambda *a, **k: _Response())
    rows, ok = project_goals_check._fetch_rows()
    assert ok
    assert sorted(r["board"] for r in rows) == ["ideas", "issues"]


def test_both_stores_unreadable_is_unreadable_rather_than_no_rows(monkeypatch):
    store = _Store({}, raises=board_store.StoreError("no credentials"))
    monkeypatch.setattr(project_goals_check, "board_store", store)

    def boom(*args, **kwargs):
        raise OSError("no route")

    monkeypatch.setattr(project_goals_check.urllib.request, "urlopen", boom)
    assert project_goals_check._fetch_rows() == ([], False)


#: `SEATS` with a `Keeps` column, so `nova-cost` has a keeper and leaves the
#: unpointed inventory. Without this the breach can never reach the
#: nobody-on-it list at all, and every test below would pass on a document
#: the list is not about.
KEPT_SEATS = ("| Project | Milestone | Position | Updated | Serves | Keeps |\n"
              "|---|---|---|---|---|---|\n"
              "| Nova | Picking | 1 | 09-13 | nova-kr1 | nova-cost |\n")


def test_a_breached_kpi_whose_keeper_holds_no_open_row_is_listed():
    """The state between the two lists that already existed: `kpi_breaches`
    says the number is out of range and `unpointed_goals` says nobody is
    named, so a breach with a keeper that is empty of work read as owned.
    Live when this shipped: `marcus-kpi-browser-monolith` at 303 KB against a
    ceiling of 292 KB, kept by *Marcus / Codebase health*, no open row."""
    lines, code = report(_kpi(now=6, high=1), KEPT_SEATS, rows=[])
    assert code == 0
    assert lines[0] == "MODEL HOLDS"
    heading = next(t for t in lines if t.startswith("BREACHED WITH NOBODY"))
    assert heading.startswith("BREACHED WITH NOBODY ON IT (1)")
    assert ["  Nova / nova-cost: 6 is above the ceiling of 1 -- and nova / "
            "picking keeps it with no open row under it, so nothing on "
            "either board would bring the number back"] == [
        t for t in lines if t.startswith("  Nova / nova-cost: 6 is above")
        and "keeps it" in t]
    assert "1 of them with nobody on it" in lines[-1]


def test_a_breached_kpi_whose_keeper_holds_an_open_row_is_not_listed():
    """`post-kpi-volume` is the other half of the live split: 112 articles a
    day against a ceiling of 60, and `ideas #96` is open under the milestone
    that keeps it. Somebody is on it, so it is not this list's finding."""
    lines, _ = report(_kpi(now=6, high=1), KEPT_SEATS, rows=[row(1)])
    assert not [t for t in lines if t.startswith("BREACHED WITH NOBODY")]
    assert "0 of them with nobody on it" in lines[-1]


def test_a_keeper_holding_only_closed_rows_counts_as_nobody():
    """A milestone whose work is all done is not work in progress. Counting a
    closed row here would make the list empty out as the boards roll, which is
    the opposite of what a breached guardrail means."""
    lines, _ = report(_kpi(now=6, high=1), KEPT_SEATS,
                      rows=[row(1, status_key="done", done=True)])
    assert [t for t in lines if t.startswith("BREACHED WITH NOBODY")]


def test_a_breached_kpi_nobody_keeps_is_left_to_the_unpointed_list():
    """One fact under two verdicts is how a list silently changes size. A KPI
    with no keeper is already `NOTHING POINTS AT`, and its action is different
    -- write the pointer, not open the work."""
    lines, _ = report(_kpi(now=6, high=1), SEATS, rows=[])
    assert not [t for t in lines if t.startswith("BREACHED WITH NOBODY")]
    assert [t for t in lines if t.startswith("NOTHING POINTS AT")]
    assert "0 of them with nobody on it" in lines[-1]


def test_an_unread_board_claims_nothing_rather_than_listing_every_breach():
    """With no boards, "no open row" is true of every keeper by construction
    -- the guaranteed positive `report` already refuses for unplaced tasks. So
    the list is empty and the summary drops the phrase rather than printing a
    zero nobody measured."""
    lines, _ = report(_kpi(now=6, high=1), KEPT_SEATS, rows=None)
    assert not [t for t in lines if t.startswith("BREACHED WITH NOBODY")]
    assert "with nobody on it" not in lines[-1]
    assert "1 KPI(s) out of bounds" in lines[-1]


def test_the_seat_and_the_row_match_on_case():
    """The seats file is parsed lowercased and a board row carries the case the
    owner typed. Comparing them raw makes every keeper look empty, which reads
    as the strongest possible finding and is wrong on every line -- measured
    against the live boards before this rule existed."""
    lines, _ = report(_kpi(now=6, high=1), KEPT_SEATS,
                      rows=[row(1, project="NOVA", milestone="PICKING")])
    assert not [t for t in lines if t.startswith("BREACHED WITH NOBODY")]


def test_one_keeper_with_open_work_answers_for_all_of_them():
    """Two milestones keep `nova-kpi-silent-cycles` on the live seats file. The
    question is whether anything is being done about the number, so one open
    row anywhere under it is enough."""
    seats = KEPT_SEATS + "| Nova | Planning | 2 | 09-13 | nova-kr1 | nova-cost |\n"
    both_empty, _ = report(_kpi(now=6, high=1), seats, rows=[])
    listed = [t for t in both_empty if "keeps it with no open row" in t]
    assert len(listed) == 1
    assert "nova / picking, nova / planning keeps it" in listed[0]
    one_busy, _ = report(_kpi(now=6, high=1), seats,
                         rows=[row(1, milestone="Planning")])
    assert not [t for t in one_busy if t.startswith("BREACHED WITH NOBODY")]


def _kr(**fields):
    """`GOALS` with the key result's fields replaced wholesale.

    `id`, `name` and `measure` are kept because `problems()` refuses a key
    result missing any of them, and a fixture that fails the model check
    would raise before the list under test is ever reached.
    """
    body = "".join(f"{k.replace('_', '-')}: {v}\n" for k, v in fields.items())
    return GOALS.replace(
        "```key-result\nid: nova-kr1\nname: n\nmeasure: m\ntarget: 1\n"
        "status: agreed\n```\n",
        f"```key-result\nid: nova-kr1\nname: n\nmeasure: m\n{body}```\n")


def _no_baseline(lines):
    return [t for t in lines if t.startswith("NO BASELINE")]


def test_a_key_result_carrying_a_baseline_is_not_listed():
    """Flaw 3 of the goals-model review: a key result with no baseline is a
    wish. Live when this shipped, 25 of 25 carried none. The separating input
    is the one field, so this runs the same fixture both ways."""
    without, code = report(_kr(target=1, status="agreed"), SEATS, rows=[])
    assert code == 0
    assert _no_baseline(without) == [
        "NO BASELINE (1 of 1) -- a key result records where its number "
        "stands, and nothing records where it started. An inventory rather "
        "than a defect, so it does not raise: the baseline is a reading "
        "someone has to take:"]
    assert "  Nova / nova-kr1" in without
    assert "1 of 1 key result(s) with no baseline" in without[-1]
    carrying, code = report(_kr(target=1, baseline=3, status="agreed"),
                            SEATS, rows=[])
    assert code == 0
    assert not _no_baseline(carrying)
    assert "0 of 1 key result(s) with no baseline" in carrying[-1]


def test_a_baseline_survives_the_parse():
    """`_fields` drops a key it does not know as a typo, so before `baseline`
    was in `KEY_RESULT_FIELDS` a baseline written into the document vanished
    on parse and the list above would have asked for one that was there."""
    sections = parse_project_goals(_kr(target=1, baseline=3, status="agreed"))
    (section,) = sections.values()
    assert section["keyResults"][0]["baseline"] == "3"


def test_a_baseline_that_is_not_one_number_still_counts_as_present():
    """A dated baseline like `0 (09-15)` is a baseline. Reading it through
    `_number` would drop it and ask for a reading already taken."""
    lines, _ = report(_kr(target=1, baseline="0 (09-15)", status="agreed"),
                      SEATS, rows=[])
    assert not _no_baseline(lines)


def test_a_struck_key_result_is_not_asked_for_a_baseline():
    """He killed it, so asking where it started invents work -- and it drops
    out of the count too, or the summary would read 0 of 1 over nothing."""
    lines, _ = report(_kr(target=1, status="struck"), SEATS, rows=[])
    assert not _no_baseline(lines)
    assert "0 of 0 key result(s) with no baseline" in lines[-1]


def test_a_short_key_result_whose_server_holds_no_open_row_is_listed():
    """The mirror of the breach list, and the half that was missing: nothing
    in the model ever compared a key result's `now` to its `target` at all.
    Live when this shipped: `research-kr-reused` at 7.3% against a target of
    50%, served only by *Research / read what other agent loops do*, which
    holds no open row."""
    lines, code = report(_kr(now=7.3, target=50, direction="up",
                             unit="%", status="agreed"), SEATS, rows=[])
    assert code == 0
    assert lines[0] == "MODEL HOLDS"
    heading = next(t for t in lines if t.startswith("SHORT OF TARGET WITH NOBODY"))
    assert heading.startswith("SHORT OF TARGET WITH NOBODY ON IT (1)")
    # The count in the heading is read off the document rather than typed:
    # a frozen "24 of the 28" is a live reading baked into a printed string,
    # and it goes wrong the first time a key result moves.
    assert "1 of them are short right now" in heading
    assert ("  Nova / nova-kr1: 7.3 % is below the target of 50 % -- and "
            "nova / picking serves it with no open row under it, so nothing "
            "on either board would move the number") in lines
    assert "1 key result(s) short of target with nobody on it" in lines[-1]


def test_a_key_result_over_its_target_downward_is_short_too():
    """`direction: down` inverts which side of the target is the shortfall --
    `nova-kr-your-rows` reads 6.9 against a target of 2.0 and is behind, not
    ahead. A single comparison here would call half the live document done."""
    lines, _ = report(_kr(now=6.9, target=2.0, direction="down",
                          status="agreed"), SEATS, rows=[])
    assert ["  Nova / nova-kr1: 6.9 is above the target of 2.0 -- and nova / "
            "picking serves it with no open row under it, so nothing on "
            "either board would move the number"] == [
        t for t in lines if t.startswith("  Nova / nova-kr1:")]


def test_a_key_result_on_its_target_is_not_short():
    """Reaching the target is the point, so the boundary belongs on the good
    side of the line in both directions."""
    for direction, now in (("up", 50), ("down", 2.0)):
        lines, _ = report(_kr(now=now, target=(50 if direction == "up" else 2.0),
                              direction=direction, status="agreed"),
                          SEATS, rows=[])
        assert not [t for t in lines
                    if t.startswith("SHORT OF TARGET WITH NOBODY")], direction
        assert "0 key result(s) short of target with nobody on it" in lines[-1]


def test_a_short_key_result_whose_server_holds_an_open_row_is_not_listed():
    """Somebody is on it. That is the whole difference between this list and
    the 24 short key results it deliberately does not print."""
    lines, _ = report(_kr(now=7.3, target=50, direction="up",
                          status="agreed"), SEATS, rows=[row(1)])
    assert not [t for t in lines if t.startswith("SHORT OF TARGET WITH NOBODY")]
    assert "0 key result(s) short of target with nobody on it" in lines[-1]


def test_a_server_holding_only_closed_rows_counts_as_nobody():
    """A milestone whose work is all done is not work in progress. Counting a
    closed row would empty this list out as the boards roll, which is the
    opposite of what an unmet target means."""
    lines, _ = report(_kr(now=7.3, target=50, direction="up", status="agreed"),
                      SEATS, rows=[row(1, status_key="done", done=True)])
    assert [t for t in lines if t.startswith("SHORT OF TARGET WITH NOBODY")]


def test_a_short_key_result_nobody_serves_is_left_to_the_unpointed_list():
    """One fact under two verdicts is how a list silently changes size. A key
    result with no server is already `NOTHING POINTS AT`, and its action is
    different -- write the pointer, not open the work."""
    unserved = ("| Project | Milestone | Position | Updated | Serves |\n"
                "|---|---|---|---|---|\n"
                "| Nova | Picking | 1 | 09-13 |  |\n")
    lines, _ = report(_kr(now=7.3, target=50, direction="up", status="agreed"),
                      unserved, rows=[])
    assert not [t for t in lines if t.startswith("SHORT OF TARGET WITH NOBODY")]
    assert [t for t in lines if t.startswith("NOTHING POINTS AT")]
    assert "0 key result(s) short of target with nobody on it" in lines[-1]


def test_an_unread_board_claims_nothing_rather_than_listing_every_shortfall():
    """With no boards, "no open row" is true of every server by construction.
    Twenty-four lines would appear and every one of them would be a negative
    result nothing could have contradicted."""
    lines, _ = report(_kr(now=7.3, target=50, direction="up", status="agreed"),
                      SEATS, rows=None)
    assert not [t for t in lines if t.startswith("SHORT OF TARGET WITH NOBODY")]
    assert "short of target with nobody on it" not in lines[-1]


def test_the_serving_seat_and_the_row_match_on_case():
    """The seats file is parsed lowercased and a board row carries the case the
    owner typed. Comparing them raw makes every server look empty, which reads
    as the strongest possible finding and is wrong on every line."""
    lines, _ = report(_kr(now=7.3, target=50, direction="up", status="agreed"),
                      SEATS, rows=[row(1, project="NOVA", milestone="PICKING")])
    assert not [t for t in lines if t.startswith("SHORT OF TARGET WITH NOBODY")]


def test_a_struck_key_result_is_not_measured():
    """He killed it. Asking whether anybody is working towards a target he
    struck is the check inventing work, the same call `undecided_goals`
    makes."""
    lines, _ = report(_kr(now=7.3, target=50, direction="up", status="struck"),
                      SEATS, rows=[])
    assert not [t for t in lines if t.startswith("SHORT OF TARGET WITH NOBODY")]
    assert "0 key result(s) short of target with nobody on it" in lines[-1]


def test_a_key_result_with_no_direction_is_not_judged():
    """`now: 3` against `target: 0` is finished work if down is good and
    untouched work if up is. Guessing would put a verdict on his page that
    nothing in the document supports, so the row is skipped rather than
    assumed."""
    lines, _ = report(_kr(now=7.3, target=50, status="agreed"), SEATS, rows=[])
    assert not [t for t in lines if t.startswith("SHORT OF TARGET WITH NOBODY")]
    assert "0 key result(s) short of target with nobody on it" in lines[-1]


def test_a_key_result_whose_now_is_not_a_number_is_not_judged():
    """`now: not measured` is the honest state of a key result whose
    instrument has not run, and it is neither short nor met."""
    lines, _ = report(_kr(now="not measured", target=50, direction="up",
                          status="agreed"), SEATS, rows=[])
    assert not [t for t in lines if t.startswith("SHORT OF TARGET WITH NOBODY")]


def test_one_server_with_open_work_answers_for_all_of_them():
    """The question is whether anything is being done about the number, so one
    open row anywhere under any serving milestone is enough."""
    seats = SEATS + "| Nova | Planning | 2 | 09-13 | nova-kr1 |\n"
    goals = _kr(now=7.3, target=50, direction="up", status="agreed")
    both_empty, _ = report(goals, seats, rows=[])
    listed = [t for t in both_empty if "serves it with no open row" in t]
    assert len(listed) == 1
    assert "nova / picking, nova / planning serves it" in listed[0]
    one_busy, _ = report(goals, seats, rows=[row(1, milestone="Planning")])
    assert not [t for t in one_busy if t.startswith("SHORT OF TARGET WITH NOBODY")]


def test_a_not_bet_seat_prints_under_its_own_heading_and_still_exits_zero():
    """The fourth heading. An orphan carrying a `Not bet` period covering
    today is a decision already taken, so it leaves the question list without
    leaving the orphan count -- and a defect in the cell is a defect, so a
    period on a seat that also serves something raises."""
    import datetime
    seats = SEATS + "| Nova | Runner engineering | 2 | 09-16 |  |  | 2026-09 |\n"
    lines, code = report(GOALS, seats, rows=[row(1), row(2, milestone="Runner engineering")],
                         today=datetime.date(2026, 9, 16))
    assert code == 0
    body = "\n".join(lines)
    assert "ORPHANS" not in body
    assert "NOT BET THIS PERIOD (1)" in body
    assert "1 orphan(s) (0 pruning signal, 0 with nothing left to keep, " \
           "0 awaiting project goals, 1 not bet this period)" in lines[-1]

    confused = SEATS + ("| Nova | Runner engineering | 2 | 09-16 | "
                        "nova-kr-true-first-time |  | 2026-09 |\n")
    lines, code = report(
        GOALS, confused, rows=[row(1)], today=datetime.date(2026, 9, 16))
    assert code == 2
    assert any("serves or keeps something" in line for line in lines)


def _landed_row(updated, status_key="done"):
    return {**row(1, status_key=status_key, done=status_key == "done"),
            "updated": updated}


def _short_lists(rows, today=datetime.date(2026, 9, 17)):
    lines, code = report(_kr(now=30.8, target=100, direction="up",
                             unit="%", status="agreed"), SEATS, rows=rows,
                         today=today)
    assert code == 0
    nobody = [t for t in lines if t.startswith("SHORT OF TARGET WITH NOBODY")]
    landed = [t for t in lines if t.startswith("SHORT OF TARGET, WORK JUST")]
    return nobody, landed, lines


def test_a_short_key_result_whose_task_closed_this_week_is_work_just_landed():
    """Cycle 1742: `nova-kr-scale-blocks-recorded` at 30.8% was listed as
    nobody on it with "nothing on either board would move the number" the
    morning issue #241 closed under its milestone -- and every entry after it
    already counted as recorded. The measure reads a trailing week, so the
    number was moving with no new row. The separating input is the date on
    the closed row: the same row eight days old is back on the nobody list."""
    nobody, landed, lines = _short_lists([_landed_row("09-17")])
    assert not nobody
    assert landed and landed[0].startswith("SHORT OF TARGET, WORK JUST LANDED (1)")
    assert ("  Nova / nova-kr1: 30.8 % is below the target of 100 % -- "
            "nova / picking holds no open row, and a row under it closed as "
            "done in the last 7 days") in lines
    assert ("0 key result(s) short of target with nobody on it, 1 more with "
            "work just landed, ") in lines[-1]
    nobody, landed, lines = _short_lists([_landed_row("09-10")])
    assert nobody and not landed
    assert "1 key result(s) short of target with nobody on it, 1 of" in lines[-1]


def test_a_full_date_and_a_year_boundary_both_read_as_the_right_day():
    """`updated` is `MM-DD` on the boards and sometimes a full date. A `12-30`
    read on 01-02 is three days old, not a date next December."""
    _, landed, _ = _short_lists([_landed_row("2026-09-16")])
    assert landed
    _, landed, _ = _short_lists([_landed_row("12-30")],
                                today=datetime.date(2027, 1, 2))
    assert landed
    nobody, landed, _ = _short_lists([_landed_row("not a date")])
    assert nobody and not landed


def test_a_row_closed_as_outdated_this_week_is_not_work_that_landed():
    """Outdated is a row dropped, not a fix shipped -- nothing it did can be
    moving the number."""
    nobody, landed, _ = _short_lists([_landed_row("09-17", "outdated")])
    assert nobody and not landed
