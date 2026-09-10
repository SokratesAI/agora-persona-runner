"""An id here has one job -- to survive a rename -- so most tests rename.

The failure this guards against does not raise. A design that derives the id
from the name passes every "two spellings are one project" test in this file
and then silently orphans every row the moment somebody renames a project,
which is precisely the bug issue #203 is about. So the assertions compare an
id taken *before* a rename with one resolved after it.
"""

import json

import pytest

from agora_runner import board_document
from agora_runner.entity_id import (
    EntityError,
    capture_high_water,
    ensure_milestone,
    ensure_project,
    mint_capture,
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


def test_a_renamed_away_name_is_not_free_for_a_new_project():
    registry = new_registry()
    old = ensure_project(registry, "Nova")
    rename_project(registry, old, "Aurora")
    # "Nova" is still an alias of `old`, so it resolves to `old` rather than
    # minting a second project. That is deliberate: a row written before the
    # rename means the old project, and there is no way to tell it apart from
    # a row that means a hypothetical new one. Rows win.
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


# A capture id is the one id in this module that is *not* seeded from a name,
# so none of the rename tests above reach it. What replaces "survives a
# rename" as the failure worth guarding is "is never handed out twice": the
# owner's replies address a capture by id, so a reused id puts an old reply
# under new words and nothing in the store can tell that happened.


def test_a_capture_id_is_never_reissued_after_the_capture_is_gone():
    # The failure this exists for. A counter derived from how many captures
    # a board currently holds passes every test that only ever mints, and
    # then reissues `cap_1` the first time the owner closes his only
    # capture and writes another.
    registry = new_registry()
    first = mint_capture(registry, "issue")
    # He closes it: the record is gone, and nothing in the registry knows.
    second = mint_capture(registry, "issue")
    assert first == "cap_1"
    assert second == "cap_2"


def test_two_boards_number_captures_independently():
    # Both files number from the top, and `capture_document_id` already puts
    # the board in the id, so `cap_1` on issues and `cap_1` on ideas are two
    # different captures rather than a collision.
    registry = new_registry()
    assert mint_capture(registry, "issue") == "cap_1"
    assert mint_capture(registry, "idea") == "cap_1"
    assert mint_capture(registry, "issue") == "cap_2"
    assert registry["captures"] == {"issue": 2, "idea": 1}


def test_a_minted_capture_id_is_accepted_by_the_document_id():
    # The two halves were written a cycle apart and this is the only place
    # they meet: `capture_document_id` refuses an id containing ':', and a
    # mint that ever produced one would fail at the store rather than here.
    registry = new_registry()
    capture_id = mint_capture(registry, "issue")
    assert (board_document.capture_document_id("issue", capture_id)
            == "capture:issue:cap_1")


def test_a_registry_written_before_captures_existed_still_mints():
    # Every registry stored before this function existed has `projects` and
    # `milestones` and no `captures`. Reading it must be a 0, not a KeyError
    # in whichever caller happens to touch the boards first.
    old = {"projects": {}, "milestones": {}}
    assert capture_high_water(old, "issue") == 0
    assert mint_capture(old, "issue") == "cap_1"
    assert old["captures"] == {"issue": 1}


def test_the_high_water_mark_does_not_mint():
    # A migration asks "has this board ever minted a capture" to decide
    # whether it is safe to write; asking must not consume a number.
    registry = new_registry()
    mint_capture(registry, "issue")
    assert capture_high_water(registry, "issue") == 1
    assert capture_high_water(registry, "issue") == 1
    assert mint_capture(registry, "issue") == "cap_2"


def test_a_board_name_that_is_not_a_board_is_refused():
    # `issues` is the *filename*; the store's board is `issue`. A typo would
    # otherwise open a third counter starting at 1 and hand out ids the real
    # board has already issued.
    registry = new_registry()
    with pytest.raises(EntityError):
        mint_capture(registry, "issues")
    with pytest.raises(EntityError):
        capture_high_water(registry, "captures")
    assert registry["captures"] == {}


def test_the_boards_this_module_mints_for_are_the_documents_boards():
    # Held against the constant rather than re-spelled, the way
    # `roadmap_drift`'s test holds `_REF_RE` against `board_document.BOARDS`.
    registry = new_registry()
    for board in board_document.BOARDS:
        assert mint_capture(registry, board) == "cap_1"


def test_a_corrupt_capture_counter_raises_rather_than_restarting():
    # The dangerous version reads a bad counter as "nothing minted yet" and
    # reissues from 1. Every one of these is a value CouchDB will happily
    # store and hand back.
    for bad in ("3", 2.0, -1, True, None, [3]):
        registry = {"projects": {}, "milestones": {}, "captures": {"issue": bad}}
        with pytest.raises(EntityError):
            capture_high_water(registry, "issue")


def test_the_capture_registry_round_trips_through_json():
    registry = new_registry()
    mint_capture(registry, "issue")
    mint_capture(registry, "idea")
    assert json.loads(json.dumps(registry)) == registry


def test_a_captures_map_that_is_not_a_map_raises_rather_than_reading_empty():
    # `registry.get("captures") or {}` passes every other test in this file
    # and turns a corrupt map into a fresh counter -- an absent key and a
    # `None` are the same value to `or`, and only one of them is safe.
    for bad in (None, [], "", 0):
        with pytest.raises(EntityError):
            capture_high_water({"captures": bad}, "issue")
