"""`nova_boards.unresolved_capture_project_tag` -- a picker miss, named.

`split_capture_project_tag` answers `("", text)` for two different events:
his bullet carried no project tag at all, and it carried one that matched
no project this call had heard of. Collapsing them is right for the *cell*
-- an invented project name is worse than a tag left in a title -- and
wrong for the *report*, because only one of them is somebody's choice
being dropped on the floor.

The case that made this necessary, measured against the live site on
2026-09-08: `/api/project` builds the picker's list from both boards and
returned eleven names; `issues.md` carried eight. So Maintenance, Research
and Demos were pickable in the app and unresolvable when the capture was
boarded onto issues -- and the row landed with no project, silently.
"""

from agora_runner.nova_boards import (
    split_capture_project_tag,
    unresolved_capture_project_tag,
)

KNOWN = ("Marcus", "Sokrates Post")


def test_a_tag_that_matches_nothing_comes_back_as_its_slug():
    assert unresolved_capture_project_tag(
        "the reminder never fires #maintenance", KNOWN
    ) == "maintenance"


def test_a_tag_that_resolves_is_not_a_miss():
    assert unresolved_capture_project_tag("the reminder never fires #marcus", KNOWN) == ""


def test_a_multi_word_name_resolves_through_its_own_slug():
    """`Sokrates Post` -> `sokrates-post`, the case a de-hyphenating guess
    would get wrong in the other direction."""
    assert unresolved_capture_project_tag("no editor #sokrates-post", KNOWN) == ""


def test_a_bullet_with_no_tag_at_all_is_not_a_miss():
    assert unresolved_capture_project_tag("just a sentence he typed", KNOWN) == ""


def test_a_hash_in_the_middle_of_his_prose_is_not_a_tag():
    """Shape-only, and the shape is anchored at the end -- `nova_capture`
    appends the picker's choice there and nowhere else."""
    assert unresolved_capture_project_tag("the #maintenance job fails nightly", KNOWN) == ""


def test_an_empty_known_list_makes_every_tag_a_miss():
    """The degenerate case the caller hits when the board has no Project
    column yet: every pick is unplaceable and the report should say so."""
    assert unresolved_capture_project_tag("fires late #marcus", ()) == "marcus"


def test_it_agrees_with_the_splitter_on_every_case():
    """The two functions read one regex and must not drift apart.

    The expectations are written out rather than recomputed from the
    inputs: a predicate that re-spells the rule passes against a broken
    implementation that made the same mistake.
    """
    cases = [
        # bullet, resolved name, reported miss
        ("the reminder never fires #maintenance", "", "maintenance"),
        ("the reminder never fires #marcus", "Marcus", ""),
        ("just a sentence he typed", "", ""),
        ("the #maintenance job fails nightly", "", ""),
        ("no editor #sokrates-post", "Sokrates Post", ""),
    ]
    for bullet, expected_name, expected_miss in cases:
        name, _text = split_capture_project_tag(bullet, KNOWN)
        assert name == expected_name, bullet
        assert unresolved_capture_project_tag(bullet, KNOWN) == expected_miss, bullet
        # And they can never both answer: a name that resolved is not a miss.
        assert not (name and expected_miss)
