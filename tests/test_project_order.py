"""His hand-ordered project list -- milestone M3 of idea #260.

The spec (`projects/sokrates/projects/nova/task-prioritization-redesign.md`)
quotes him: *"the list of projects... is an ordered list where the top one
has the highest priority... the ui for me also makes it easy with a drag and
drop list."* M1 made the picker read the project *ratings*; this is the
layer above them, where a position he set by hand outranks any label.

Four things these pin, in the order the write travels: the `Order` cell is
read off a table that may not have one yet, the first placement numbers the
whole list rather than one row, a placed project outranks every rating in
both rankers, and the endpoint refuses a position that is not an integer.
"""

import json

import agora_runner.nova_capture as nova_capture
from agora_runner.nova_boards import (
    parse_project_meta,
    project_positions,
    rank_projects,
    set_project_order,
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


def test_the_first_placement_numbers_every_row_seeded_from_the_ratings():
    written = set_project_order(UNORDERED, "Demos", 1)
    assert written is not None
    positions = project_positions(written)
    # Demos went where he put it; the other two kept the order M1's rating
    # ranking already gave them, so the list he sees after one move is the
    # list the picker was already using with one thing moved.
    assert positions == {"demos": 1, "marcus": 2, "nova": 3}
    # The heading and the rule grew a column rather than the rows growing a
    # cell nothing names.
    assert "| Project | Priority | Updated | Order |" in written
    assert "|---|---|---|---|" in written


def test_a_second_move_renumbers_contiguously_from_the_written_order():
    written = set_project_order(ORDERED, "Nova", 1)
    assert project_positions(written) == {"nova": 1, "demos": 2, "marcus": 3}


def test_moving_a_project_to_where_it_already_is_changes_nothing():
    written = set_project_order(ORDERED, "Demos", 1)
    assert project_positions(written) == {"demos": 1, "marcus": 2, "nova": 3}


def test_an_unknown_project_and_an_out_of_range_position_are_refused():
    # A position is a statement about a row that exists. Inventing the row
    # would file a project he never filed.
    assert set_project_order(ORDERED, "Marcuss", 1) is None
    assert set_project_order(ORDERED, "Marcus", 0) is None
    assert set_project_order(ORDERED, "Marcus", 4) is None
    assert set_project_order(ORDERED, "Marcus", "top") is None
    # A file with no table at all has no list to order.
    assert set_project_order("# Projects\n\nnothing here.\n", "Marcus", 1) is None
    assert set_project_order("", "Marcus", 1) is None


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


def test_the_capture_layer_writes_it_and_refuses_a_missing_file(monkeypatch):
    seen = {}

    def read(path):
        return seen.get("doc", ORDERED), "rev-1"

    def write(path, body, if_rev=None):
        seen["written"] = body
        seen["rev"] = if_rev
        return "written"

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", read)
    monkeypatch.setattr(nova_capture, "vault_write_path", write)
    ok, message = nova_capture.set_project_order("Nova", 1)
    assert ok, message
    assert seen["rev"] == "rev-1"
    assert project_positions(seen["written"])["nova"] == 1

    # A document that is not there is a refusal, not a fresh table: a
    # rating creates the file because the first rating has to land
    # somewhere, but a position describes a list nobody has written.
    seen["doc"] = ""
    seen.pop("written", None)
    ok, message = nova_capture.set_project_order("Nova", 1)
    assert not ok
    assert "written" not in seen


# --- the HTTP layer: what a client is allowed to send ---


class _Handler:
    """`_post_project_order` unbound, with the two things it touches."""

    def __init__(self):
        self.sent = []
        self.headers = {}

    def _send_json(self, status, body):
        self.sent.append((status, body))


def _call(payload, result=(True, "Nova is now #1"), monkeypatch=None):
    import agora_runner.nova_site as nova_site
    from agora_runner.nova_site import NovaSiteHandler

    handler = _Handler()
    calls = []

    def fake_set(project, position):
        calls.append((project, position))
        return result

    monkeypatch.setattr(nova_site, "set_project_order", fake_set)
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: None)
    NovaSiteHandler._post_project_order(handler, payload)
    return handler.sent[-1], calls


def test_a_good_request_trims_the_name_and_passes_the_position(monkeypatch):
    (status, body), calls = _call({"project": "  Nova  ", "position": 1},
                                  monkeypatch=monkeypatch)
    assert status == 200 and body["ok"] is True
    assert calls == [("Nova", 1)]


def test_a_position_that_is_not_a_positive_int_is_refused_before_any_write(monkeypatch):
    for bad in ("2", 0, -1, 1.5, None, True):
        (status, body), calls = _call({"project": "Nova", "position": bad},
                                      monkeypatch=monkeypatch)
        assert status == 400, bad
        assert "position must be" in body["error"]
        assert calls == []


def test_a_missing_or_blank_project_is_refused(monkeypatch):
    for bad in ({"position": 1}, {"project": "   ", "position": 1},
                {"project": 7, "position": 1}):
        (status, body), calls = _call(bad, monkeypatch=monkeypatch)
        assert status == 400 and "project must be" in body["error"]
        assert calls == []


def test_a_refused_write_answers_502_rather_than_claiming_success(monkeypatch):
    (status, body), _calls = _call(
        {"project": "Ghost", "position": 1},
        result=(False, "cannot place 'Ghost' at 1"),
        monkeypatch=monkeypatch)
    assert status == 502 and body["ok"] is False and "Ghost" in body["message"]


def test_the_route_is_in_the_post_allowlist():
    import re

    from agora_runner import nova_site

    source = open(nova_site.__file__).read()
    # Both halves: a handler with no route is dead code, and a route the
    # allowlist does not carry is a 404.
    assert '"/api/project/order"' in source
    assert re.search(
        r'if path == "/api/project/order":\n\s+self\._post_project_order\(payload\)',
        source)


def test_the_payload_carries_the_position_so_the_page_can_move_a_row():
    """`order` on `projectPriority`, or the buttons cannot say where to go.

    A control that inferred the position from its index in the drawn list
    would send a number the file does not use the moment a cell is
    hand-edited to something non-contiguous.
    """
    source = open(__import__("agora_runner.nova_site", fromlist=["x"]).__file__).read()
    assert '"order": (meta.get(name.lower()) or {}).get("order") or 0,' in source
