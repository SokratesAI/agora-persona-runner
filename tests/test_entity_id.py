"""An id here has one job -- to survive a rename -- so most tests rename.

The failure this guards against does not raise. A design that derives the id
from the name passes every "two spellings are one project" test in this file
and then silently orphans every row the moment somebody renames a project,
which is precisely the bug issue #203 is about. So the assertions compare an
id taken *before* a rename with one resolved after it.
"""

import json

import pytest

from agora_runner.entity_id import (
    EntityError,
    ensure_milestone,
    ensure_project,
    new_registry,
    normalise,
    rename_milestone,
    rename_project,
    resolve_milestone,
    resolve_project,
)


def test_normalise_folds_case_and_collapses_whitespace():
    assert normalise("Nova") == normalise(" nova ") == normalise("NOVA")
    assert normalise("Board  records\tnow") == "board records now"


def test_normalise_refuses_a_name_that_is_not_one():
    with pytest.raises(EntityError):
        normalise("   ")
    with pytest.raises(EntityError):
        normalise(None)


def test_two_spellings_of_one_project_mint_one_id():
    registry = new_registry()
    first = ensure_project(registry, "Nova")
    assert ensure_project(registry, " nova ") == first
    assert len(registry["projects"]) == 1


def test_the_display_name_kept_is_the_one_first_seen_not_the_fold():
    registry = new_registry()
    pid = ensure_project(registry, "Nova")
    assert registry["projects"][pid]["name"] == "Nova"


def test_a_rename_keeps_the_id():
    registry = new_registry()
    pid = ensure_project(registry, "Nova")
    rename_project(registry, pid, "Aurora")
    assert resolve_project(registry, "Aurora") == pid
    assert registry["projects"][pid]["name"] == "Aurora"


def test_a_row_still_carrying_the_old_name_resolves_after_a_rename():
    # The migration reads markdown rows that carry names, so a board written
    # before a rename has to land on the same project after it.
    registry = new_registry()
    pid = ensure_project(registry, "Nova")
    rename_project(registry, pid, "Aurora")
    assert resolve_project(registry, "nova") == pid
    assert ensure_project(registry, "Nova") == pid
    assert len(registry["projects"]) == 1


def test_renaming_onto_another_projects_name_is_refused():
    registry = new_registry()
    ensure_project(registry, "Nova")
    other = ensure_project(registry, "Marcus")
    with pytest.raises(EntityError):
        rename_project(registry, other, "nova")
    # and the refusal left the entity alone rather than half-renaming it
    assert registry["projects"][other]["name"] == "Marcus"


def test_renaming_a_project_back_to_a_name_it_already_answers_to():
    registry = new_registry()
    pid = ensure_project(registry, "Nova")
    rename_project(registry, pid, "Aurora")
    rename_project(registry, pid, "Nova")
    assert resolve_project(registry, "Nova") == pid
    assert resolve_project(registry, "Aurora") == pid
    assert registry["projects"][pid]["aliases"].count("nova") == 0
    assert registry["projects"][pid]["aliases"] == ["aurora"]


def test_a_new_project_may_take_a_freed_name_without_taking_the_id():
    registry = new_registry()
    old = ensure_project(registry, "Nova")
    rename_project(registry, old, "Aurora")
    # "Nova" is still an alias of `old`, so the freed name is not free --
    # resolving it must not mint, and must not hand back a shared id.
    assert ensure_project(registry, "Nova") == old


def test_a_slug_collision_across_two_live_projects_gets_distinct_ids():
    registry = new_registry()
    first = ensure_project(registry, "Nova!")
    second = ensure_project(registry, "Nova?")
    assert first != second
    assert len(registry["projects"]) == 2


def test_a_name_with_no_sluggable_characters_still_gets_a_unique_id():
    registry = new_registry()
    first = ensure_project(registry, "★")
    second = ensure_project(registry, "☆")
    assert first != second
    assert resolve_project(registry, "★") == first


def test_the_same_milestone_name_under_two_projects_is_two_milestones():
    registry = new_registry()
    nova = ensure_project(registry, "Nova")
    marcus = ensure_project(registry, "Marcus")
    one = ensure_milestone(registry, nova, "Backup")
    two = ensure_milestone(registry, marcus, "Backup")
    assert one != two
    assert resolve_milestone(registry, nova, "backup") == one
    assert resolve_milestone(registry, marcus, "backup") == two


def test_a_milestone_is_not_resolvable_from_the_wrong_project():
    registry = new_registry()
    nova = ensure_project(registry, "Nova")
    marcus = ensure_project(registry, "Marcus")
    ensure_milestone(registry, nova, "Backup")
    assert resolve_milestone(registry, marcus, "Backup") is None


def test_a_milestone_under_an_unknown_project_is_refused():
    registry = new_registry()
    with pytest.raises(EntityError):
        ensure_milestone(registry, "prj_nope", "Backup")
    assert registry["milestones"] == {}


def test_renaming_a_milestone_keeps_its_id_and_its_project():
    registry = new_registry()
    nova = ensure_project(registry, "Nova")
    mid = ensure_milestone(registry, nova, "Backup")
    rename_milestone(registry, mid, "Restore")
    assert resolve_milestone(registry, nova, "Restore") == mid
    assert resolve_milestone(registry, nova, "Backup") == mid
    assert registry["milestones"][mid]["projectId"] == nova


def test_a_milestone_may_be_renamed_onto_a_name_another_project_holds():
    registry = new_registry()
    nova = ensure_project(registry, "Nova")
    marcus = ensure_project(registry, "Marcus")
    ensure_milestone(registry, nova, "Backup")
    mid = ensure_milestone(registry, marcus, "Restore")
    rename_milestone(registry, mid, "Backup")
    assert resolve_milestone(registry, marcus, "Backup") == mid
    assert resolve_milestone(registry, nova, "Backup") != mid


def test_renaming_a_milestone_onto_a_sibling_is_refused():
    registry = new_registry()
    nova = ensure_project(registry, "Nova")
    ensure_milestone(registry, nova, "Backup")
    other = ensure_milestone(registry, nova, "Restore")
    with pytest.raises(EntityError):
        rename_milestone(registry, other, "backup")
    assert registry["milestones"][other]["name"] == "Restore"


def test_an_unknown_id_is_refused_rather_than_ignored():
    registry = new_registry()
    with pytest.raises(EntityError):
        rename_project(registry, "prj_nope", "Nova")
    with pytest.raises(EntityError):
        rename_milestone(registry, "ms_nope", "Backup")


def test_two_renames_keep_both_earlier_names_resolvable():
    # A row written two renames ago is still a row. Keeping only the most
    # recent former name would orphan it, which is the whole failure.
    registry = new_registry()
    pid = ensure_project(registry, "Nova")
    rename_project(registry, pid, "Aurora")
    rename_project(registry, pid, "Borealis")
    assert resolve_project(registry, "Nova") == pid
    assert resolve_project(registry, "Aurora") == pid
    assert resolve_project(registry, "Borealis") == pid


def test_normalise_folds_the_pairs_that_lower_misses():
    # `casefold`, not `lower` -- the docstring claims the difference and this
    # is the pair that shows it. A project spelled either way is one project.
    assert normalise("Straße") == normalise("STRASSE")


def test_the_registry_round_trips_through_json():
    # It is destined for CouchDB, so anything that is not plain JSON here --
    # a tuple, a set, a dataclass -- is a bug that only shows up on write.
    registry = new_registry()
    nova = ensure_project(registry, "Nova")
    ensure_milestone(registry, nova, "Backup")
    rename_project(registry, nova, "Aurora")
    assert json.loads(json.dumps(registry)) == registry
