"""`tools.project_goals_check` -- issue #227's model as an exit code.

The split these tests are built around: a **defect** raises and an
**orphan** does not. Both used to raise together, which is what kept the
check out of `tools.preflight` -- 43 of today's seated milestones serve
nothing and 32 sit in projects the owner scoped out, so a merged list is red
forever on work nobody is allowed to do.
"""

import datetime
import json

from agora_runner.project_goals import parse_project_goals
from tools.project_goals_check import main, report

GOALS = ("# Project goals\n\n## Nova\n\n"
         "```objective\nstatement: s\nstatus: agreed\nconversation: 18bdb05e-2ad0-479d-9a7d-d9b8bab3fd5e\n```\n\n"
         "```key-result\nid: nova-kr1\nname: n\nmeasure: m\ntarget: 1\n```\n\n"
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
                     "NO MONTH AT ALL (1) -- also rule 7, and also not a "
                     "defect: an objective carrying no period is one nothing "
                     "can ever report as stale:",
                     "  Nova: objective names no period -- nothing can tell "
                     "whether this month's goal is this month's",
                     "1 project section(s), 1 seated milestone(s), "
                     "0 model problem(s), 0 orphan(s), 1 unpointed goal(s), "
                     "0 KPI(s) out of bounds, "
                     "0 objective(s) past their month, 1 undated, "
                     "0 unplaced task(s)"]


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
    lines, code = report(GOALS, seats, rows=[row(1)])
    assert code == 0
    body = "\n".join(lines)
    assert "ORPHANS (1)" in body
    assert "NO GOALS TO SERVE YET (1)" in body
    assert "  nova / runner engineering: serves no key result and keeps no " \
           "KPI -- either keep-the-lights-on work whose guardrail has not " \
           "been written yet, or work nobody can justify" in lines
    assert "  agora / ask me a question: serves no key result and keeps no " \
           "KPI -- and there is none to serve, because no key result or " \
           "KPI is written for this project yet" in lines
    assert "2 orphan(s) (1 pruning signal, 1 awaiting project goals)" \
        in lines[-1]
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
