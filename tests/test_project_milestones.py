"""`nova_next.project_milestones` -- the list his milestone pin acts on.

Milestone M4 of idea #260. The pin has been settable since
`tools.milestone_pin` and `POST /api/milestone/pin`, and nothing drew the
order it overrides, so these tests are about the three things the page
needs and cannot get anywhere else: the order is the picker's order and
not a second one computed here, a pin is reported as he wrote it rather
than as it landed, and a milestone is scoped to its project.
"""

from agora_runner.nova_next import milestone_ranks, project_milestones

from tests.test_nova_next_milestones import HIGH, LOW, MEDIUM, row


def names(items):
    return [item["name"] for item in items]


def test_order_is_the_pickers_order_not_a_second_one():
    """The one rule that must not be re-derived here.

    `small` is 1 point of High work and `big` is 5 points of the same, so
    the divide puts `small` first -- and the assertion is against
    `milestone_ranks` itself rather than against a hand-written list, so
    a change to the formula moves both or neither.
    """
    rows = [
        row(1, HIGH, "Marcus", "XL", "big"),
        row(2, HIGH, "Marcus", "S", "small"),
    ]
    ranks = milestone_ranks(rows)
    expected = sorted(
        (key for key in ranks if key[0] == "marcus"), key=lambda k: ranks[k])
    assert names(project_milestones(rows, "Marcus")) == [
        key[1] for key in expected]
    assert names(project_milestones(rows, "Marcus")) == ["small", "big"]


def test_a_pin_moves_the_list_it_is_a_pin_inside():
    """His override, applied to the drawn order and not only to the picker."""
    rows = [
        row(1, HIGH, "Marcus", "XL", "big"),
        row(2, HIGH, "Marcus", "S", "small"),
    ]
    pins = {("marcus", "big"): 1}
    assert names(project_milestones(rows, "Marcus", pins)) == ["big", "small"]


def test_the_pin_reported_is_the_one_he_wrote_not_where_it_landed():
    """A pin past the end clamps in the order and is still reported as 4.

    Echoing the clamped position back would rewrite his file the next time
    he pressed a button built from what the page showed.
    """
    rows = [
        row(1, HIGH, "Marcus", "S", "one"),
        row(2, HIGH, "Marcus", "S", "two"),
    ]
    pins = {("marcus", "one"): 4}
    items = project_milestones(rows, "Marcus", pins)
    assert names(items) == ["two", "one"]
    assert [item["pin"] for item in items] == [0, 4]


def test_a_milestone_is_scoped_to_its_project():
    """`Backup` in Marcus and `Backup` in Demos are two milestones."""
    rows = [
        row(1, HIGH, "Marcus", "S", "Backup"),
        row(2, LOW, "Demos", "S", "Backup"),
        row(3, MEDIUM, "Demos", "S", "Other"),
    ]
    assert names(project_milestones(rows, "Marcus")) == ["Backup"]
    assert sorted(names(project_milestones(rows, "Demos"))) == [
        "Backup", "Other"]
    assert project_milestones(rows, "Marcus")[0]["open"] == 1


def test_the_name_is_the_spelling_on_the_rows():
    """He types the cell, so the page shows what he typed, not the key."""
    rows = [row(1, HIGH, "Marcus", "S", "Codebase Health")]
    assert names(project_milestones(rows, "marcus")) == ["Codebase Health"]


def test_open_counts_the_rows_in_the_milestone():
    rows = [
        row(1, HIGH, "Marcus", "S", "one"),
        row(2, LOW, "Marcus", "S", "one"),
        row(3, HIGH, "Marcus", "S", "two"),
    ]
    counts = {item["name"]: item["open"] for item in
              project_milestones(rows, "Marcus")}
    assert counts == {"one": 2, "two": 1}


def test_an_ungrouped_row_is_not_a_milestone():
    """A blank `Milestone` cell is a real state, not a group called ''."""
    rows = [
        row(1, HIGH, "Marcus", "S", ""),
        row(2, HIGH, "Marcus", "S", "one"),
    ]
    assert names(project_milestones(rows, "Marcus")) == ["one"]


def test_no_project_asked_is_no_list():
    """`/projects` asks for no name; a list of every milestone in the org
    is not what that page draws, and answering with one would put 57 rows
    behind a payload that has nowhere to show them."""
    rows = [row(1, HIGH, "Marcus", "S", "one")]
    assert project_milestones(rows, "") == []
    assert project_milestones(rows, None) == []


def test_a_project_with_no_grouped_rows_is_an_empty_list():
    rows = [row(1, HIGH, "Marcus", "S", "one")]
    assert project_milestones(rows, "Demos") == []
