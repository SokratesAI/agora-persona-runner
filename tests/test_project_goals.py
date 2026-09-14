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
    keeps_problems, serves_orphans, serves_problems, split_serves,
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
    low = _doc("## Nova\n\n```kpi\nid: c\nname: Cost\nmeasure: m\nlow: 1\n```\n")
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
