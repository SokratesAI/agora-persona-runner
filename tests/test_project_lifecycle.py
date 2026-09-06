"""Milestone M5 of idea #260: the lifecycle field and its approval gate.

The other two fields of that milestone are owned outright -- the TRL is
Nova's, the satisfaction is Edvard's -- and each has exactly one write
path. This one is shared, so it has two, and the whole value of the field
is that they cannot be swapped: Nova writes a proposal and can never write
a live stage, and the approve/decline route never carries a stage at all.
Most of what is below is that one invariant asked from several sides.
"""

import pytest

from agora_runner.nova_boards import (
    PROJECT_LIFECYCLE_STAGES,
    canonical_lifecycle,
    parse_project_lifecycle_cell,
    parse_project_meta,
    propose_project_lifecycle,
    resolve_project_lifecycle,
)
from tools import project_lifecycle


TABLE = """# Projects

| Project | Priority | Updated | Order | TRL | Satisfaction | Lifecycle | Proposed |
|---|---|---|---|---|---|---|---|
| Nova | 🟠 High | 09-06 | 1 | Proven | 4 | Active |  |
| Marcus | 🔴 Immediately | 09-06 | 2 | Functional | 2 |  |  |
"""


def test_the_four_stages_are_these_four_in_this_order():
    # Pinned by name: a fifth stage invented to make a meter line up is the
    # exact call `PROJECT_TRL_LEVELS` had to make and this field does not.
    assert PROJECT_LIFECYCLE_STAGES == ("Idea", "Active", "Paused", "Retired")


@pytest.mark.parametrize("text,expected", [
    ("active", "Active"),
    ("  RETIRED ", "Retired"),
    ("Idea", "Idea"),
])
def test_a_stage_is_matched_case_insensitively(text, expected):
    assert canonical_lifecycle(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "Archived", "3", None, "2"])
def test_anything_that_is_not_a_stage_is_refused(text):
    # `3` in particular: a TRL takes a 1-5 number because the rungs of that
    # ladder mean something. "Stage 3" means nothing, so there is no
    # numeric form and a number must not silently select `Paused`.
    assert canonical_lifecycle(text) is None


def test_no_stage_and_idea_are_different_answers():
    assert parse_project_lifecycle_cell("") == ""
    assert parse_project_lifecycle_cell("Idea") == "Idea"


def test_the_payload_carries_both_cells():
    meta = parse_project_meta(TABLE)
    assert meta["nova"]["lifecycle"] == "Active"
    assert meta["nova"]["lifecycleProposed"] == ""


def test_a_proposal_lands_in_the_proposed_cell_and_not_the_live_one():
    after = propose_project_lifecycle(TABLE, "Marcus", "Active")
    meta = parse_project_meta(after)
    assert meta["marcus"]["lifecycleProposed"] == "Active"
    assert meta["marcus"]["lifecycle"] == ""


def test_a_proposal_cannot_move_a_live_stage_that_is_already_set():
    # The gate, asked from the side that actually matters: Nova proposing
    # `Retired` for a project he has already marked `Active` must leave
    # `Active` standing until he answers.
    after = propose_project_lifecycle(TABLE, "Nova", "Retired")
    meta = parse_project_meta(after)
    assert meta["nova"]["lifecycle"] == "Active"
    assert meta["nova"]["lifecycleProposed"] == "Retired"


def test_a_proposal_can_be_withdrawn():
    proposed = propose_project_lifecycle(TABLE, "Marcus", "Paused")
    after = propose_project_lifecycle(proposed, "Marcus", "")
    assert parse_project_meta(after)["marcus"]["lifecycleProposed"] == ""


@pytest.mark.parametrize("project,stage", [
    ("Nowhere", "Active"),
    ("Marcus", "Archived"),
    ("", "Active"),
])
def test_a_proposal_is_refused(project, stage):
    assert propose_project_lifecycle(TABLE, project, stage) is None


def test_approving_moves_the_proposal_into_the_live_cell_and_clears_it():
    proposed = propose_project_lifecycle(TABLE, "Marcus", "Active")
    after = resolve_project_lifecycle(proposed, "Marcus", "approve")
    meta = parse_project_meta(after)
    assert meta["marcus"]["lifecycle"] == "Active"
    assert meta["marcus"]["lifecycleProposed"] == ""


def test_declining_clears_the_proposal_and_leaves_the_live_stage_alone():
    proposed = propose_project_lifecycle(TABLE, "Nova", "Retired")
    after = resolve_project_lifecycle(proposed, "Nova", "decline")
    meta = parse_project_meta(after)
    assert meta["nova"]["lifecycle"] == "Active"
    assert meta["nova"]["lifecycleProposed"] == ""


def test_a_decision_with_nothing_proposed_is_refused_not_a_no_op():
    # Different facts, and the caller has to be able to tell him which
    # happened -- approving a proposal a cycle withdrew a minute ago must
    # not report success against a stage he never saw.
    assert resolve_project_lifecycle(TABLE, "Marcus", "approve") is None


@pytest.mark.parametrize("decision", ["", "yes", "APPROVE!", None, "approved"])
def test_only_approve_and_decline_are_decisions(decision):
    proposed = propose_project_lifecycle(TABLE, "Marcus", "Active")
    assert resolve_project_lifecycle(proposed, "Marcus", decision) is None


def test_a_row_that_has_never_had_the_columns_grows_them():
    narrow = """# Projects

| Project | Priority | Updated |
|---|---|---|
| Nova | 🟠 High | 09-06 |
"""
    after = propose_project_lifecycle(narrow, "Nova", "Active")
    assert "| Lifecycle | Proposed |" in after
    assert parse_project_meta(after)["nova"]["lifecycleProposed"] == "Active"
    # Every column this module appends is named, not just the two it wrote.
    assert "|  |  |" not in after.split("\n")[2]


def test_the_cli_refuses_a_write_that_moved_a_live_stage(tmp_path):
    # `check` compares `lifecycle` rather than exempting it, so a bug that
    # let the tool write a live stage fails here as well as being
    # impossible in `propose_project_lifecycle`.
    before = TABLE
    forged = TABLE.replace("| Nova | 🟠 High | 09-06 | 1 | Proven | 4 | Active |  |",
                           "| Nova | 🟠 High | 09-06 | 1 | Proven | 4 | Retired | Retired |")
    problems = project_lifecycle.check(before, forged, "Nova", "Retired")
    assert any("other than its proposal" in p for p in problems)


def test_the_cli_writes_a_proposal_end_to_end(tmp_path):
    path = tmp_path / "projects.md"
    path.write_text(TABLE, encoding="utf-8")
    assert project_lifecycle.main(
        ["--file", str(path), "--project", "Marcus", "--propose", "paused"]) == 0
    meta = parse_project_meta(path.read_text(encoding="utf-8"))
    assert meta["marcus"]["lifecycleProposed"] == "Paused"
    assert meta["marcus"]["lifecycle"] == ""


def test_the_cli_has_no_flag_that_sets_a_live_stage():
    # The gate stated as a fact about the interface rather than about one
    # code path: there is no argument here he does not have to approve.
    source = open(project_lifecycle.__file__, encoding="utf-8").read()
    assert "set_project_lifecycle" not in source
    assert "--lifecycle" not in source
