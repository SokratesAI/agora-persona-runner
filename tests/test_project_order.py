"""His hand-ordered project list -- milestone M3 of idea #260.

The spec (`projects/sokrates/projects/nova/task-prioritization-redesign.md`)
quotes him: *"the list of projects... is an ordered list where the top one
has the highest priority... the ui for me also makes it easy with a drag and
drop list."* M1 made the picker read the project *ratings*; this is the
layer above them, where a position he set by hand outranks any label.

What these pin: the `Order` cell is read off a table that may not have one
yet, and a placed project outranks every rating in both rankers. The write
end (`POST /api/project/order`) came out with issue #229 -- projects stop
carrying a priority set from the app -- so the column is now hand-edited
only.
"""

from agora_runner.nova_boards import (
    parse_project_meta,
    project_positions,
    rank_projects,
)
from agora_runner.nova_next import project_ranks

UNORDERED = """---
type: board
---

# Projects

| Project | Priority | Updated |
|---|---|---|
| Marcus | 🔴 Immediately | 09-01 |
| Demos | ⚪ Low | 09-02 |
| Nova | 🟠 High | 09-01 |
"""

ORDERED = """---
type: board
---

# Projects

| Project | Priority | Updated | Order |
|---|---|---|---|
| Marcus | 🔴 Immediately | 09-01 | 2 |
| Demos | ⚪ Low | 09-02 | 1 |
| Nova | 🟠 High | 09-01 | 3 |
"""


def test_a_file_with_no_order_column_reads_as_unplaced_not_as_first():
    meta = parse_project_meta(UNORDERED)
    assert [m["order"] for m in meta.values()] == [None, None, None]
    # `None`, not `0`: the picker's fallback branches on "he has never
    # placed this", and `0` would read as a position ahead of everything.
    assert project_positions(UNORDERED) == {}


def test_the_order_cell_is_read_and_a_typo_in_it_is_unplaced():
    meta = parse_project_meta(ORDERED)
    assert meta["demos"]["order"] == 1
    assert meta["marcus"]["order"] == 2
    assert project_positions(ORDERED) == {"marcus": 2, "demos": 1, "nova": 3}
    # `0` is not a position. `is None` rather than a falsiness check: every
    # caller here branches on truthiness, so a `0` leaking through would
    # behave correctly today and become a row at the top of the list the
    # day one of them starts asking `is not None`.
    assert parse_project_meta(ORDERED.replace("| 2 |", "| 0 |"))["marcus"]["order"] is None
    typo = ORDERED.replace("| 2 |", "| soon |")
    assert parse_project_meta(typo)["marcus"]["order"] is None
    # The rest of the table still parses -- a cell he mistyped on a phone
    # must not take the whole list out.
    assert parse_project_meta(typo)["demos"]["order"] == 1


def test_a_placed_project_outranks_every_rating_in_the_picker():
    # Ratings alone put Marcus first -- he rates it Immediately.
    assert min(project_ranks(UNORDERED), key=project_ranks(UNORDERED).get) == "marcus"
    ranks = project_ranks(ORDERED)
    assert ranks["demos"] < ranks["marcus"] < ranks["nova"]
    # And a file he has never ordered behaves exactly as M1 left it.
    flat = project_ranks(UNORDERED)
    assert flat["marcus"] < flat["nova"] < flat["demos"]


def test_a_project_with_no_position_falls_in_behind_every_placed_one():
    mixed = ORDERED.replace("| Nova | 🟠 High | 09-01 | 3 |",
                            "| Nova | 🟠 High | 09-01 |  |")
    ranks = project_ranks(mixed)
    assert ranks["nova"] > ranks["marcus"]
    # Even though he rates Nova High and Demos Low: an explicit placement
    # is a decision and a rating is a description.
    assert ranks["nova"] > ranks["demos"]


def test_an_unplaced_project_lands_below_a_hand_edited_gap_not_inside_it():
    """He can edit these cells himself, and 1, 2, 9 is a list he can write.

    The unplaced project has to sort below *nine*, not below the count of
    placed rows -- otherwise editing the file by hand quietly moves an
    unrated project into the middle of the order he wrote.
    """
    gapped = ORDERED.replace("| 3 |", "| 9 |").replace(
        "| Marcus | 🔴 Immediately | 09-01 | 2 |",
        "| Marcus | 🔴 Immediately | 09-01 |  |")
    ranks = project_ranks(gapped)
    assert ranks["demos"] == 1
    assert ranks["nova"] == 9
    assert ranks["marcus"] > 9
    # And the same rule on the page's ranker, which sorts the index.
    assert rank_projects(["Marcus", "Demos", "Nova"], parse_project_meta(gapped)) == [
        "Demos", "Nova", "Marcus"]


def test_the_page_order_follows_the_placement_too():
    names = ["Marcus", "Demos", "Nova"]
    assert rank_projects(names, parse_project_meta(UNORDERED)) == ["Marcus", "Nova", "Demos"]
    assert rank_projects(names, parse_project_meta(ORDERED)) == ["Demos", "Marcus", "Nova"]


def test_the_payload_carries_the_position_so_the_page_can_move_a_row():
    """`order` on `projectPriority`, or the buttons cannot say where to go.

    A control that inferred the position from its index in the drawn list
    would send a number the file does not use the moment a cell is
    hand-edited to something non-contiguous.
    """
    source = open(__import__("agora_runner.nova_site", fromlist=["x"]).__file__).read()
    assert '"order": (meta.get(name.lower()) or {}).get("order") or 0,' in source
