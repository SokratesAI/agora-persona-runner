"""Issue #227 step 1: the model and the storage for objectives, key results
and KPIs, plus the `Serves` pointer from a milestone to a key result.

Every rule the issue calls "the whole point" that can be checked by reading
gets a test with a document that violates exactly it.
"""

from agora_runner.nova_boards import (
    parse_milestone_pins, parse_milestone_serves, render_milestone_seats,
)
from agora_runner.project_goals import (
    MAX_KEY_RESULTS, key_result_ids, kpi_ids, parse_project_goals, problems,
    serves_problems, split_serves,
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


def test_a_milestone_serving_nothing_is_the_orphan_list():
    sections = parse_project_goals(NOVA)
    found = serves_problems({("Nova", "Runner engineering"): ""}, sections)
    assert found and "serves nothing" in found[0]


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
