"""Issue #227 step 1: the model and the storage for objectives, key results
and KPIs, plus the `Serves` pointer from a milestone to a key result.

Every rule the issue calls "the whole point" that can be checked by reading
gets a test with a document that violates exactly it.
"""
from agora_runner import project_goals

from agora_runner.nova_boards import (
    parse_milestone_pins, parse_milestone_serves, render_milestone_seats,
)
from agora_runner.project_goals import (
    MAX_KEY_RESULTS, key_result_ids, kpi_ids, parse_project_goals, problems,
    keeps_problems, project_has_goals_to_serve, serves_orphans,
    serves_problems, split_orphans, split_serves, unpointed_goals,
)


def _doc(body):
    return "# Project goals\n\n" + body


NOVA = _doc(
    "## Nova\n"
    "\n"
    "```objective\n"
    "statement: Nova ships the work he would have picked himself\n"
    "status: agreed\n"
    "conversation: 18bdb05e-2ad0-479d-9a7d-d9b8bab3fd5e\n"
    "```\n"
    "\n"
    "Prose about the objective that nothing parses.\n"
    "\n"
    "```key-result\n"
    "id: nova-kr1\n"
    "name: He does not have to re-pick\n"
    "measure: share of cycles whose pick he did not override, 14-day\n"
    "now: 0.6\n"
    "target: 0.9\n"
    "direction: up\n"
    "```\n"
    "\n"
    "```kpi\n"
    "id: nova-cost\n"
    "name: Cost per cycle\n"
    "measure: weighted tokens, 7-day median\n"
    "now: 1.74\n"
    "high: 2.0\n"
    "unit: M\n"
    "```\n"
)


def test_a_section_carries_its_objective_key_results_and_kpis():
    sections = parse_project_goals(NOVA)
    assert list(sections) == ["nova"]
    nova = sections["nova"]
    assert nova["project"] == "Nova"
    assert nova["objective"]["status"] == "agreed"
    assert nova["objective"]["statement"].startswith("Nova ships")
    assert [row["id"] for row in nova["keyResults"]] == ["nova-kr1"]
    assert [row["id"] for row in nova["kpis"]] == ["nova-cost"]
    assert nova["keyResults"][0]["direction"] == "up"
    assert problems(NOVA) == []


def test_the_prose_between_fences_is_not_read_as_fields():
    # The only line outside a fence that looks like a field is the heading
    # itself; a sentence with a colon in it must not become a key.
    doc = NOVA.replace("Prose about the objective that nothing parses.",
                       "measure: this sentence is prose, not a field")
    assert problems(doc) == []
    assert parse_project_goals(doc)["nova"]["keyResults"][0]["measure"].startswith(
        "share of cycles")


def test_a_fence_this_module_does_not_own_is_skipped_whole():
    doc = NOVA + "\n```python\nid: nova-kr1\n```\n"
    # The sample re-uses a live id; if its body were read, the duplicate-id
    # rule would fire on a code block.
    assert problems(doc) == []


def test_a_fourth_key_result_is_refused():
    extra = "".join(
        f"```key-result\nid: nova-kr{n}\nname: n\nmeasure: m\n```\n"
        for n in range(2, 5))
    found = problems(NOVA + extra)
    assert any(f"the limit is {MAX_KEY_RESULTS}" in line for line in found)


def test_a_key_result_with_no_measure_is_refused():
    doc = _doc("## Nova\n\n```key-result\nid: k\nname: Ship the landing page\n```\n")
    assert any("has no measure" in line for line in problems(doc))


def test_a_kpi_carrying_a_target_is_refused():
    doc = _doc("## Nova\n\n```kpi\nid: c\nname: Cost\nmeasure: m\n"
               "high: 2\ntarget: 1\n```\n")
    found = problems(doc)
    assert any("carries a target" in line for line in found)


def test_a_kpi_with_no_range_is_refused():
    doc = _doc("## Nova\n\n```kpi\nid: c\nname: Cost\nmeasure: m\nnow: 3\n```\n")
    assert any("has no range" in line for line in problems(doc))
    low = _doc("## Nova\n\n```objective\nstatement: s\n```\n\n```kpi\nid: c\nname: Cost\nmeasure: m\nlow: 1\n```\n")
    assert problems(low) == []


def test_an_id_used_twice_across_two_projects_is_refused():
    doc = _doc(
        "## Nova\n\n```key-result\nid: shared\nname: a\nmeasure: m\n```\n"
        "\n## Marcus\n\n```kpi\nid: shared\nname: b\nmeasure: m\nhigh: 1\n```\n")
    assert any("is used twice" in line for line in problems(doc))


def test_a_block_with_no_id_is_named_rather_than_silently_kept():
    doc = _doc("## Nova\n\n```key-result\nname: a\nmeasure: m\n```\n")
    assert any("has no id" in line for line in problems(doc))


def test_an_objective_with_no_statement_or_a_bad_status_is_refused():
    doc = _doc("## Nova\n\n```objective\nstatus: struck\n```\n")
    assert any("no statement" in line for line in problems(doc))
    bad = _doc("## Nova\n\n```objective\nstatement: s\nstatus: maybe\n```\n")
    assert any("is not one of" in line for line in problems(bad))


def test_a_fence_before_any_heading_is_dropped_rather_than_given_a_blank_owner():
    doc = _doc("```key-result\nid: orphan\nname: a\nmeasure: m\n```\n\n## Nova\n")
    sections = parse_project_goals(doc)
    assert list(sections) == ["nova"]
    assert sections["nova"]["keyResults"] == []


def test_the_same_project_spelled_two_ways_is_one_project():
    doc = _doc("## Nova\n\n```key-result\nid: a\nname: a\nmeasure: m\n```\n"
               "\n## nova\n\n```key-result\nid: b\nname: b\nmeasure: m\n```\n")
    sections = parse_project_goals(doc)
    assert list(sections) == ["nova"]
    assert [row["id"] for row in sections["nova"]["keyResults"]] == ["a", "b"]


def test_ids_resolve_to_their_project_and_the_two_kinds_stay_apart():
    sections = parse_project_goals(NOVA)
    assert key_result_ids(sections) == {"nova-kr1": "Nova"}
    assert kpi_ids(sections) == {"nova-cost": "Nova"}


def test_split_serves_takes_a_list():
    assert split_serves(" nova-kr1 , NOVA-KR2 ") == ["nova-kr1", "nova-kr2"]
    assert split_serves("") == []


def test_a_milestone_serving_a_kpi_is_refused_by_name():
    sections = parse_project_goals(NOVA)
    found = serves_problems({("Nova", "Cost and quota"): "nova-cost"}, sections)
    assert len(found) == 1
    assert "is a KPI" in found[0]


def test_a_milestone_serving_an_unknown_id_is_refused():
    sections = parse_project_goals(NOVA)
    found = serves_problems({("Nova", "M"): "nova-kr9"}, sections)
    assert found and "not a key result id" in found[0]


def test_a_milestone_serving_nothing_is_the_orphan_list_and_not_a_problem():
    """Issue #227's fourth rule is an inventory, so it comes out of
    `serves_orphans` and never out of `serves_problems` -- the two are
    separate because only one of them is a thing a pull request can fix."""
    sections = parse_project_goals(NOVA)
    seats = {("Nova", "Runner engineering"): ""}
    assert serves_problems(seats, sections) == []
    orphans = serves_orphans(seats, sections)
    assert orphans and "serves no key result and keeps no KPI" in orphans[0]


def test_a_well_linked_milestone_is_not_an_orphan():
    sections = parse_project_goals(NOVA)
    assert serves_orphans({("Nova", "Picking"): "nova-kr1"}, sections) == []


def test_a_milestone_pointing_at_a_kpi_is_a_defect_and_not_an_orphan():
    """The near miss: it has a `Serves` cell, so it is not an orphan, and
    the cell names a guardrail, so it is broken. Reading a broken pointer as
    an orphan would quietly stop it raising."""
    sections = parse_project_goals(NOVA)
    seats = {("Nova", "M"): "nova-cost"}
    assert serves_orphans(seats, sections) == []
    assert any("is a KPI" in line for line in serves_problems(seats, sections))


def test_a_milestone_serving_a_real_key_result_is_clean():
    sections = parse_project_goals(NOVA)
    assert serves_problems({("Nova", "Picking"): "nova-kr1"}, sections) == []


TWO_PROJECTS = NOVA + (
    "\n"
    "## Marcus\n"
    "\n"
    "```objective\n"
    "statement: Marcus is the app he trains from\n"
    "status: discussing\n"
    "```\n"
    "\n"
    "```key-result\n"
    "id: marcus-kr1\n"
    "name: He logs the session he did\n"
    "measure: sessions logged per week\n"
    "now: 0\n"
    "target: 3\n"
    "direction: up\n"
    "```\n"
    "\n"
    "```kpi\n"
    "id: marcus-page-weight\n"
    "name: Biggest hand-written file served\n"
    "measure: KB\n"
    "now: 292\n"
    "high: 292\n"
    "```\n"
)


def test_serving_another_projects_key_result_is_refused():
    """Issue #227's model is a tree: a milestone serves a key result of the
    objective it sits under. The id resolves, so nothing downstream notices
    -- the seat reads as filled, the milestone leaves rule 4's orphan list,
    and `/plan` counts it under the other project's coverage line."""
    sections = parse_project_goals(TWO_PROJECTS)
    found = serves_problems({("marcus", "Body and progress"): "nova-kr1"},
                            sections)
    assert len(found) == 1
    assert "belongs to 'Nova'" in found[0]
    assert "its own project" in found[0]


def test_serving_your_own_projects_key_result_is_clean_with_two_projects():
    """The other half of the same call: the rule must not fire on the seat
    it is meant to allow, and both projects carry a key result here so a
    test that passed by there being only one id cannot pass."""
    sections = parse_project_goals(TWO_PROJECTS)
    assert serves_problems({("marcus", "Body and progress"): "marcus-kr1",
                            ("nova", "Picking"): "nova-kr1"}, sections) == []


def test_a_cross_project_pointer_is_a_defect_rather_than_an_orphan():
    """The near miss `serves_problems` has made twice already: the cell was
    filled in, so it is not an orphan, and it is wrong, so it raises. If it
    read as an orphan instead it would never exit non-zero."""
    sections = parse_project_goals(TWO_PROJECTS)
    seats = {("marcus", "Body and progress"): "nova-kr1"}
    assert serves_orphans(seats, sections) == []
    assert serves_problems(seats, sections)


def test_seat_and_goal_project_names_are_compared_case_folded():
    """The seats table writes `product management` and the goals document
    writes `Product management`, so a raw comparison would report every
    correct seat in the file as cross-project."""
    sections = parse_project_goals(TWO_PROJECTS)
    assert serves_problems({("MARCUS", "M"): "marcus-kr1"}, sections) == []


def test_keeping_another_projects_kpi_is_refused():
    """`Keeps` sits in the same tree as `Serves`: a guardrail held by
    another project is not this milestone's guardrail."""
    sections = parse_project_goals(TWO_PROJECTS)
    found = keeps_problems({("nova", "Runner engineering"):
                            "marcus-page-weight"}, sections)
    assert len(found) == 1
    assert "belongs to 'Marcus'" in found[0]
    assert "its own project" in found[0]


def test_keeping_your_own_projects_kpi_is_clean_with_two_projects():
    sections = parse_project_goals(TWO_PROJECTS)
    assert keeps_problems({("nova", "Cost and quota"): "nova-cost",
                           ("marcus", "Codebase health"):
                           "marcus-page-weight"}, sections) == []


def test_seats_carry_the_serves_cell_and_still_parse_as_seats():
    markdown = render_milestone_seats(
        [("Nova", "Picking"), ("Nova", "Cost and quota")],
        updated="09-13",
        serves={("nova", "picking"): "nova-kr1"})
    assert "| Nova | Picking | 1 | 09-13 | nova-kr1 |" in markdown
    assert "| Nova | Cost and quota | 2 | 09-13 |  |" in markdown
    # The seat reader is untouched by the new column.
    assert parse_milestone_pins(markdown) == {
        ("nova", "picking"): 1, ("nova", "cost and quota"): 2}
    assert parse_milestone_serves(markdown) == {
        ("nova", "picking"): "nova-kr1", ("nova", "cost and quota"): ""}


def test_a_seats_file_written_before_the_column_existed_reads_as_orphans():
    old = ("| Project | Milestone | Position | Updated |\n"
           "|---|---|---|---|\n"
           "| Nova | Picking | 1 | 09-12 |\n")
    assert parse_milestone_serves(old) == {("nova", "picking"): ""}


def test_a_serves_cell_carrying_a_pipe_is_refused_like_a_name():
    assert render_milestone_seats(
        [("Nova", "Picking")], serves={("nova", "picking"): "a | b"}) is None


def test_the_old_approval_vocabulary_is_refused_rather_than_aliased():
    """His 2026-09-13 21:02 correction: goals are agreed in a conversation,
    never approved on a page. A document still saying `approved` is a
    document written under the old rule, and reading it as `agreed` would
    erase the only difference the correction is about."""
    for word in ("proposed", "approved"):
        doc = _doc(f"## Nova\n\n```objective\nstatement: s\nstatus: {word}\n```\n")
        assert any("is not one of" in line for line in problems(doc)), word


def test_an_agreed_objective_that_links_no_conversation_is_refused():
    """`agreed` names a second party. Without a thread to point at, the word
    is set by whoever wrote the file -- which is Nova, which is the gate he
    just deleted."""
    doc = _doc("## Nova\n\n```objective\nstatement: s\nstatus: agreed\n```\n")
    assert any("links no conversation" in line for line in problems(doc))

    blank = _doc(
        "## Nova\n\n```objective\nstatement: s\nstatus: agreed\n"
        "conversation:   \n```\n")
    assert any("links no conversation" in line for line in problems(blank))

    linked = _doc(
        "## Nova\n\n```objective\nstatement: s\nstatus: agreed\n"
        "conversation: 18bdb05e-2ad0-479d-9a7d-d9b8bab3fd5e\n```\n")
    assert not any("links no conversation" in line for line in problems(linked))


def test_discussing_and_struck_need_no_conversation():
    """Only an agreement names where it was reached. An objective still being
    argued about may well have a thread, and a struck one may predate the
    rule; neither is a defect."""
    for word in ("discussing", "struck"):
        doc = _doc(f"## Nova\n\n```objective\nstatement: s\nstatus: {word}\n```\n")
        assert not any("links no conversation" in line for line in problems(doc)), word


DOC_FOR_SETTER = """# Project goals

## Nova

```key-result
id: nova-kr-your-rows
name: The work closes your rows
measure: Merged pull requests per board row closed
now: 6.8
target: 2.0
direction: down
```
Prose under the block that must not move.

```key-result
id: nova-kr-in-the-app
name: Everything reaches your phone
measure: Things you still have to leave the Nova app to do
target: 0
direction: down
```
"""


def test_set_field_in_key_result_replaces_in_place_and_touches_nothing_else():
    out = project_goals.set_field_in_key_result(
        DOC_FOR_SETTER, "nova-kr-your-rows", "now", "3.9")
    assert "now: 3.9" in out
    assert "now: 6.8" not in out
    # The block keeps its field order and the prose around it is untouched.
    assert out.index("now: 3.9") < out.index("target: 2.0")
    assert "Prose under the block that must not move." in out
    # The other key result is not given a `now` it never had.
    assert out.count("now:") == 1


def test_set_field_in_key_result_appends_when_the_field_is_absent():
    out = project_goals.set_field_in_key_result(
        DOC_FOR_SETTER, "nova-kr-in-the-app", "now", "3")
    parsed = project_goals.parse_project_goals(out)
    by_id = {kr["id"]: kr for kr in parsed["nova"]["keyResults"]}
    assert by_id["nova-kr-in-the-app"]["now"] == "3"
    assert by_id["nova-kr-your-rows"]["now"] == "6.8"


def test_set_field_in_key_result_refuses_an_id_that_is_not_there():
    assert project_goals.set_field_in_key_result(
        DOC_FOR_SETTER, "nova-kr-moved", "now", "1") is None
    assert project_goals.set_field_in_key_result(DOC_FOR_SETTER, "", "now", "1") is None


def test_set_field_in_key_result_refuses_two_blocks_claiming_one_id():
    # No way to tell them apart, so editing whichever comes first would
    # report success on the one the caller did not mean.
    doubled = DOC_FOR_SETTER + DOC_FOR_SETTER.split("## Nova", 1)[1]
    assert project_goals.set_field_in_key_result(
        doubled, "nova-kr-your-rows", "now", "3.9") is None


def test_set_field_in_key_result_refuses_an_unterminated_fence():
    half = "```key-result\nid: nova-kr-your-rows\nnow: 6.8\n"
    assert project_goals.set_field_in_key_result(
        half, "nova-kr-your-rows", "now", "3.9") is None


def test_set_field_in_key_result_refuses_a_value_it_could_not_parse_back():
    assert project_goals.set_field_in_key_result(
        DOC_FOR_SETTER, "nova-kr-your-rows", "now", "3.9\nname: hijacked") is None
    assert project_goals.set_field_in_key_result(
        DOC_FOR_SETTER, "nova-kr-your-rows", "no thanks", "3.9") is None


def test_set_field_in_key_result_does_not_edit_an_objective_or_kpi_fence():
    doc = DOC_FOR_SETTER + """
```kpi
id: nova-kr-your-rows
name: a kpi wearing the same id
measure: something else
now: 99
low: 0
high: 1
```
"""
    out = project_goals.set_field_in_key_result(doc, "nova-kr-your-rows", "now", "3.9")
    assert out is not None
    assert "now: 99" in out


def test_a_lights_on_milestone_naming_its_kpi_is_not_an_orphan():
    """Rule 4's first verdict, which had nowhere to be written down before.

    *"keep-the-lights-on work, which is legitimate and sits under a KPI
    rather than a goal"* -- `Cost and quota` is that, and until the `Keeps`
    column it appeared in the same undifferentiated list as work nobody can
    justify.
    """
    sections = parse_project_goals(NOVA)
    seats = {("Nova", "Cost and quota"): ""}
    keeps = {("Nova", "Cost and quota"): "nova-cost"}
    assert serves_orphans(seats, sections, keeps) == []
    assert keeps_problems(keeps, sections) == []


def test_keeping_a_key_result_is_refused():
    """The mirror of `Serves` naming a KPI, and the reason `Keeps` is its own
    column: without this, writing the key result into `Keeps` is the cheapest
    way to make an orphan disappear."""
    sections = parse_project_goals(NOVA)
    found = keeps_problems({("Nova", "M"): "nova-kr1"}, sections)
    assert found and "is a key result" in found[0]


def test_keeping_an_unknown_id_is_refused():
    sections = parse_project_goals(NOVA)
    found = keeps_problems({("Nova", "M"): "nova-kpi9"}, sections)
    assert found and "not a KPI id" in found[0]


def test_a_broken_keeps_pointer_is_a_defect_rather_than_an_orphan():
    """The same call `serves_problems` already makes one column to the left:
    a cell that was filled in is an answer, and a wrong answer raises. It
    cannot hide, because the defect exits 2 while the orphan list never
    does."""
    sections = parse_project_goals(NOVA)
    seats = {("Nova", "M"): ""}
    keeps = {("Nova", "M"): "nova-kpi9"}
    assert keeps_problems(keeps, sections)
    assert serves_orphans(seats, sections, keeps) == []


def test_orphans_without_a_keeps_map_answers_as_before():
    """`keeps` defaults to empty so a caller holding only the old column is
    not silently told every milestone is answered."""
    sections = parse_project_goals(NOVA)
    assert len(serves_orphans({("Nova", "M"): ""}, sections)) == 1


def test_a_key_result_no_seat_serves_is_reported():
    """Rule 4 read from the goals side. Every pointer here resolves, so
    `serves_problems` and `serves_orphans` are both silent -- and the
    objective still has nothing being built toward it."""
    sections = parse_project_goals(NOVA)
    serves, keeps = {}, {("Nova", "Cost and quota"): "nova-cost"}
    assert serves_problems(serves, sections) == []
    assert serves_orphans(serves, sections, keeps) == []
    found = unpointed_goals(serves, keeps, sections)
    assert [line for line in found if "nova-kr1" in line]
    assert "a key result no milestone serves" in found[0]


def test_a_kpi_no_seat_keeps_is_reported():
    sections = parse_project_goals(NOVA)
    serves, keeps = {("Nova", "Picking"): "nova-kr1"}, {}
    found = unpointed_goals(serves, keeps, sections)
    assert [line for line in found if "nova-cost" in line]
    assert all("nova-kr1" not in line for line in found)


def test_a_fully_pointed_at_document_reports_nothing():
    sections = parse_project_goals(NOVA)
    assert unpointed_goals({("Nova", "Picking"): "nova-kr1"},
                           {("Nova", "Cost"): "nova-cost"}, sections) == []


def test_a_key_result_named_in_keeps_does_not_count_as_served():
    """The near miss, and the reason each id is looked for in its own column
    only: writing a key result into `Keeps` is already a defect, and letting
    it answer this inventory would make a broken pointer hide two findings
    at once."""
    sections = parse_project_goals(NOVA)
    keeps = {("Nova", "M"): "nova-kr1"}
    assert keeps_problems(keeps, sections)
    found = unpointed_goals({}, keeps, sections)
    assert [line for line in found if "nova-kr1" in line]


def test_a_kpi_named_in_serves_does_not_count_as_kept():
    sections = parse_project_goals(NOVA)
    serves = {("Nova", "M"): "nova-cost"}
    assert serves_problems(serves, sections)
    found = unpointed_goals(serves, {}, sections)
    assert [line for line in found if "nova-cost" in line]


def test_one_seat_of_many_is_enough_to_point_at_a_goal():
    sections = parse_project_goals(NOVA)
    serves = {("Nova", "A"): "", ("Nova", "B"): "nova-kr1"}
    found = unpointed_goals(serves, {("Nova", "C"): "nova-cost"}, sections)
    assert found == []


def test_the_check_prints_the_unpointed_goals_and_still_exits_zero():
    from tools.project_goals_check import report
    seats = ("| Project | Milestone | Position | Updated | Serves | Keeps |\n"
             "| --- | --- | --- | --- | --- | --- |\n"
             "| Nova | Picking | 1 | 09-14 | nova-kr1 |  |\n")
    lines, code = report(NOVA, seats, rows=[])
    assert code == 0
    assert any("NOTHING POINTS AT (1)" in line for line in lines)
    assert any("nova-cost" in line for line in lines)
    assert any("1 unpointed goal(s)" in line for line in lines)


def test_an_orphan_under_a_project_with_goals_is_the_pruning_signal():
    """Rule 4's short list: Nova has key results and this seat names none
    of them, so the question really is whether the work is justified."""
    sections = parse_project_goals(NOVA)
    prunable, awaiting = split_orphans({("Nova", "Runner engineering"): ""},
                                       sections)
    assert awaiting == []
    assert prunable and "work nobody can justify" in prunable[0]


def test_an_orphan_under_a_project_with_no_goals_is_not_a_pruning_signal():
    """The 32 of 36 that made the list unreadable. `Agora` has no section
    in the goals document at all, so nothing exists for this seat to point
    at and pruning is not the question -- writing Agora's goals is."""
    sections = parse_project_goals(NOVA)
    prunable, awaiting = split_orphans({("Agora", "Ask me a question"): ""},
                                       sections)
    assert prunable == []
    assert awaiting and "no key result or KPI is written for this project" \
        in awaiting[0]
    assert "work nobody can justify" not in awaiting[0]


def test_a_project_section_with_no_goals_at_all_offers_nothing_to_serve():
    """A section can exist and still be empty -- an objective alone gives a
    milestone nothing to name, so its seats are awaiting, not prunable."""
    empty = _doc("## Husk\n"
                 "\n"
                 "```objective\n"
                 "statement: Something he has not decided yet\n"
                 "status: discussing\n"
                 "```\n")
    sections = parse_project_goals(empty)
    assert sections and not project_has_goals_to_serve("Husk", sections)
    prunable, awaiting = split_orphans({("Husk", "M"): ""}, sections)
    assert prunable == [] and len(awaiting) == 1


def test_the_split_drops_nothing_and_serves_orphans_is_unchanged():
    """The whole worry about reading the goals document here: an orphan
    must not stop being an orphan. The two lists together are exactly what
    the old single list returned, for both kinds at once."""
    sections = parse_project_goals(NOVA)
    seats = {("Nova", "Runner engineering"): "",
             ("Agora", "Ask me a question"): "",
             ("Nova", "Picking"): "nova-kr1"}
    prunable, awaiting = split_orphans(seats, sections)
    assert len(prunable) == 1 and len(awaiting) == 1
    assert sorted(prunable + awaiting) == serves_orphans(seats, sections)
    assert len(serves_orphans(seats, sections)) == 2


def test_another_projects_goals_cannot_move_a_seats_verdict():
    """Reading only the seat's own project is the thing that makes this
    safe: `Agora` stays awaiting however much Nova gains."""
    sections = parse_project_goals(NOVA)
    assert not project_has_goals_to_serve("Agora", sections)
    assert project_has_goals_to_serve("Nova", sections)
    _, awaiting = split_orphans({("Agora", "M"): ""}, sections)
    assert len(awaiting) == 1


def test_a_seat_naming_its_guardrail_is_in_neither_list():
    """`Keeps` already answered this seat; the new split must not
    resurrect it under the second heading."""
    sections = parse_project_goals(NOVA)
    prunable, awaiting = split_orphans({("Agora", "M"): ""}, sections,
                                       {("Agora", "M"): "nova-cost"})
    assert prunable == [] and awaiting == []


def test_month_name_does_not_read_the_process_locale():
    """A month name is something the owner reads on his phone, and `strftime`
    would render it differently on two boxes with different locales. A
    value this cannot parse comes back verbatim rather than as an
    invented month -- that case is `problems`' to report, not this one's."""
    from agora_runner.project_goals import month_name
    assert month_name("2026-09") == "September 2026"
    assert month_name("2026-01") == "January 2026"
    assert month_name("2026-12") == "December 2026"
    assert month_name("September") == "September"
    assert month_name("") == ""


def test_objective_periods_requires_a_clock_rather_than_defaulting_to_today():
    """The whole point of not defaulting it: a month comparison against an
    implicit clock passes against broken code on any box whose date agrees
    with the fixture, and CI runs in UTC."""
    import datetime
    import pytest as _pytest
    from agora_runner.project_goals import objective_periods, parse_project_goals
    sections = parse_project_goals(
        "## Nova\n\n```objective\nstatement: s\nperiod: 2026-08\n```\n")
    with _pytest.raises(TypeError):
        objective_periods(sections)
    past, undated = objective_periods(sections, datetime.date(2026, 9, 1))
    assert len(past) == 1 and not undated


def test_a_thirteenth_month_is_refused_rather_than_read_as_a_year():
    """`\\d{4}-\\d{2}` would accept `2026-13` and then sort it after every
    real month, so an objective could be permanently not-yet-due. The
    month group is `0[1-9]|1[0-2]` for that reason."""
    from agora_runner.project_goals import problems
    assert problems("## Nova\n\n```objective\nstatement: s\nperiod: 2026-13\n```\n")
    assert not problems(
        "## Nova\n\n```objective\nstatement: s\nperiod: 2026-12\n```\n")


def _key_result(measure, unit="", name="A real outcome"):
    return _doc(
        "## Nova\n"
        "\n"
        "```key-result\n"
        "id: nova-kr-attr\n"
        f"name: {name}\n"
        f"measure: {measure}\n"
        f"unit: {unit}\n"
        "now: 6\n"
        "target: 8\n"
        "```\n")


def _attribute_complaints(markdown):
    return [p for p in problems(markdown) if "rule 8" in p]


def test_a_key_result_measuring_trl_is_refused():
    """Rule 8: TRL is an attribute, and a hardened project can be pointless.

    It passes every other rule in `problems()` -- it has a measure, a `now`
    and a `target` -- which is exactly why this one has to exist.
    """
    found = _attribute_complaints(_key_result("TRL of the runner"))
    assert len(found) == 1, found
    assert "measures trl" in found[0]
    assert "nova-kr-attr" in found[0]


def test_the_spelled_out_attribute_is_refused_too():
    found = _attribute_complaints(
        _key_result("technology readiness of the bridge image"))
    assert len(found) == 1, found
    assert "measures trl" in found[0]


def test_a_key_result_measuring_satisfaction_is_refused():
    found = _attribute_complaints(
        _key_result("his satisfaction score for the project"))
    assert len(found) == 1, found
    assert "measures satisfaction" in found[0]


def test_a_key_result_measuring_lifecycle_is_refused():
    found = _attribute_complaints(_key_result("lifecycle stage reached"))
    assert len(found) == 1, found
    assert "measures lifecycle" in found[0]


def test_the_unit_is_read_as_well_as_the_measure():
    """`measure: maturity of the artefact` / `unit: TRL` is the same score
    wearing a different field, and only the unit names it."""
    found = _attribute_complaints(
        _key_result("maturity of the artefact", unit="TRL"))
    assert len(found) == 1, found
    assert "measures trl" in found[0]


def test_a_key_result_naming_two_attributes_complains_once():
    """One key result, one defect -- or the count of problems stops being a
    count of key results and the same row is reported twice."""
    found = _attribute_complaints(
        _key_result("TRL reached, weighted by his satisfaction"))
    assert len(found) == 1, found


def test_the_word_inside_another_word_is_not_an_attribute():
    """`\\b` rather than `in`: the substring version refuses a measure that
    says nothing about maturity at all."""
    assert _attribute_complaints(_key_result("controls he still misses")) == []
    assert _attribute_complaints(_key_result("dissatisfactions logged")) == []


def test_a_kpi_may_still_watch_an_attribute():
    """Rule 8's sentence is about what says a goal was *reached*, which is a
    key result. A guardrail watching an attribute stay in bounds is a
    different claim and the issue does not forbid it."""
    markdown = _doc(
        "## Nova\n"
        "\n"
        "```kpi\n"
        "id: nova-kpi-attr\n"
        "name: Projects stuck below TRL 4\n"
        "measure: projects whose TRL has not moved in 90 days\n"
        "now: 2\n"
        "low: 0\n"
        "high: 3\n"
        "```\n")
    assert _attribute_complaints(markdown) == []


def test_an_ordinary_key_result_document_is_untouched():
    """The rule has to start green on documents that were already fine, or
    it is a permanently red check nobody reads."""
    assert _attribute_complaints(NOVA) == []


def _kr(status):
    return _doc(
        "## Nova\n\n```key-result\nid: k\nname: a\nmeasure: m\n"
        f"status: {status}\n```\n")


def test_a_key_result_carries_the_same_three_words_as_its_objective():
    """His 09-13 21:02 correction deletes the approval gate from goals, and a
    key result is a goal. Cycle 1531 changed the objective and stopped there.
    """
    for word in project_goals.KEY_RESULT_STATUSES:
        assert not [line for line in problems(_kr(word))
                    if "status" in line], word


def test_a_key_result_still_saying_proposed_is_refused_not_aliased():
    """The word that survived on all fifteen live key results for a day.

    Refused rather than quietly re-read as `discussing`: the whole point of
    the correction is that an approval is not an agreement.
    """
    for word in ("proposed", "approved", "declined", "maybe"):
        found = [line for line in problems(_kr(word)) if "status" in line]
        assert len(found) == 1, (word, found)
        assert word in found[0] and "'k'" in found[0]
        assert "discussing/agreed/struck" in found[0]


def test_a_key_result_with_no_status_at_all_is_not_a_defect():
    """Absent is not wrong -- `problems()` reports a *bad* word, and the
    fifteen blocks that predate the field must not all read as broken."""
    doc = _doc("## Nova\n\n```key-result\nid: k\nname: a\nmeasure: m\n```\n")
    assert not [line for line in problems(doc) if "status" in line]
    blank = _doc(
        "## Nova\n\n```key-result\nid: k\nname: a\nmeasure: m\nstatus:  \n```\n")
    assert not [line for line in problems(blank) if "status" in line]


def test_a_bad_key_result_status_names_the_key_result_not_the_objective():
    """Both blocks may be wrong in one section and the two lines have to be
    tellable apart -- an objective's message says `objective`, this one says
    `key result`."""
    doc = _doc(
        "## Nova\n\n```objective\nstatement: s\nstatus: approved\n```\n"
        "\n```key-result\nid: k\nname: a\nmeasure: m\nstatus: approved\n```\n")
    lines = [line for line in problems(doc) if "status" in line]
    assert len(lines) == 2
    assert any("objective status" in line for line in lines)
    assert any("key result 'k' has status" in line for line in lines)


def test_a_key_result_with_no_target_is_refused():
    # Rule 7 reads a goal as target against current number. A measure with
    # no target is a number going up towards nothing.
    doc = _doc("## Nova\n\n```key-result\nid: k\nname: a\n"
               "measure: share of cycles he did not override\nnow: 0.6\n```\n")
    assert any("has no target" in line for line in problems(doc))


def test_a_blank_target_line_is_the_same_as_no_target():
    doc = _doc("## Nova\n\n```key-result\nid: k\nname: a\nmeasure: m\n"
               "target:   \n```\n")
    assert any("has no target" in line for line in problems(doc))


def test_a_key_result_with_no_now_is_not_a_defect():
    # The asymmetry with the target rule above, pinned: four live key results
    # have no `now` because no instrument exists to read one yet, and that is
    # honest rather than wrong.
    doc = _doc("## Nova\n\n```objective\nstatement: s\n```\n\n```key-result\nid: k\nname: a\nmeasure: m\n"
               "target: 3\n```\n")
    assert problems(doc) == []


def test_a_target_of_zero_is_a_target():
    # `0` is falsy as a string only if it is empty; a goal of "zero silent
    # cycles" is the most common shape in the live document.
    doc = _doc("## Nova\n\n```objective\nstatement: s\n```\n\n```key-result\nid: k\nname: a\nmeasure: m\n"
               "target: 0\n```\n")
    assert problems(doc) == []


def test_a_kpi_is_not_asked_for_a_target():
    # The KPI rule is the opposite one: a target on a KPI is itself the
    # defect, so the key-result rule must not reach across to it.
    doc = _doc("## Nova\n\n```objective\nstatement: s\n```\n\n```kpi\nid: c\nname: Cost\nmeasure: m\nhigh: 2\n```\n")
    assert problems(doc) == []


def test_key_results_with_no_objective_are_refused():
    """Issue #227's own title: *"give every project a goal"*. A section can
    carry three key results and no ```objective fence and every other rule
    in `problems()` passes it, because every other rule reads a fence that
    is there. It is the inverse of rule 4's orphan milestone -- outcomes
    with nothing to be outcomes of."""
    found = problems(_doc(
        "## Nova\n"
        "\n"
        "```key-result\n"
        "id: nova-kr-lonely\n"
        "name: A real outcome\n"
        "measure: rows closed per week\n"
        "target: 8\n"
        "```\n"))
    assert len(found) == 1, found
    assert "no objective" in found[0]
    assert "Nova" in found[0]


def test_kpis_with_no_objective_are_refused_too():
    """A KPI sits beside the objective rather than under it, so a section
    holding only guardrails is still a project claiming goals live here."""
    found = problems(_doc(
        "## Nova\n"
        "\n"
        "```kpi\n"
        "id: nova-kpi-lonely\n"
        "name: Cost per cycle\n"
        "measure: weighted tokens\n"
        "low: 0.8\n"
        "high: 2.0\n"
        "```\n"))
    assert len(found) == 1, found
    assert "no objective" in found[0]


def test_a_heading_with_nothing_under_it_is_not_a_defect():
    """`parse_project_goals` opens a section for every `##` heading in the
    document, including a prose heading he types between projects. A
    heading with no goal blocks under it claims nothing, so raising on it
    would make the check red over his own writing -- which is the same call
    `serves_orphans` makes about a project with no goals written yet."""
    assert not problems(_doc("## Notes to self\n\nSome prose, no fences.\n"))


def test_the_live_document_carries_an_objective_in_every_section():
    """The rule starts green, which is why it is worth adding today: seven
    more sections are still to be written by hand for the projects with no
    goals yet, and a rule added after the writing argues with work already
    on his board."""
    assert not [p for p in problems(NOVA) if "no objective" in p]
