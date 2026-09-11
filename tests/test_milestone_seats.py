"""Milestone seats: the order I set, between the formula and his pins.

Issue #202 retires the row rating that `milestone_ranks` divides by size.
Taking it out on its own reorders six of eleven live projects (Cycle 1408),
so the order first has to be held by something that is not the rating and
is not his pin. These pin the three things that has to mean: a seat beats
the formula, his pin beats a seat, and today's order written as seats reads
back as today's order.
"""

from agora_runner.nova_boards import (
    parse_milestone_pins, render_milestone_seats,
)
from agora_runner.nova_next import milestone_ranks, project_milestones

from tests.test_nova_next_milestones import HIGH, IMMEDIATE, LOW, row


def _order(ranks, project):
    return [key[1] for key, _ in sorted(ranks.items(), key=lambda kv: kv[1])
            if key[0] == project]


ROWS = [
    row(1, IMMEDIATE, "Marcus", "S", "Urgent"),
    row(2, HIGH, "Marcus", "M", "Middle"),
    row(3, LOW, "Marcus", "XL", "Trivial"),
    row(4, HIGH, "Demos", "S", "Other"),
]


def test_without_seats_the_formula_orders():
    # The precondition the next test depends on: the formula puts Urgent
    # first, so a seat moving it down is visible.
    assert _order(milestone_ranks(ROWS), "marcus") == [
        "urgent", "middle", "trivial"]


def test_a_seat_beats_the_formula():
    seats = {("marcus", "trivial"): 1, ("marcus", "urgent"): 2,
             ("marcus", "middle"): 3}
    assert _order(milestone_ranks(ROWS, None, seats), "marcus") == [
        "trivial", "urgent", "middle"]


def test_an_unseated_milestone_goes_after_the_seated_ones():
    seats = {("marcus", "trivial"): 1}
    assert _order(milestone_ranks(ROWS, None, seats), "marcus") == [
        "trivial", "urgent", "middle"]


def test_his_pin_beats_my_seat():
    seats = {("marcus", "trivial"): 1, ("marcus", "urgent"): 2,
             ("marcus", "middle"): 3}
    pins = {("marcus", "middle"): 1}
    assert _order(milestone_ranks(ROWS, pins, seats), "marcus") == [
        "middle", "trivial", "urgent"]


def test_a_seat_in_one_project_does_not_move_another():
    before = milestone_ranks(ROWS)
    after = milestone_ranks(ROWS, None, {("marcus", "trivial"): 1})
    assert after[("demos", "other")] == before[("demos", "other")]


def test_todays_order_written_as_seats_reads_back_identical():
    # The seed: write the computed order out, read it back as seats, and
    # nothing may move. This is what makes dropping the rating safe.
    ranks = milestone_ranks(ROWS)
    written = render_milestone_seats(
        [key for key, _ in sorted(ranks.items(), key=lambda kv: kv[1])],
        updated="09-11")
    seats = parse_milestone_pins(written)
    assert seats[("marcus", "trivial")] == 3
    assert seats[("demos", "other")] == 1
    assert milestone_ranks(ROWS, None, seats) == ranks


def test_a_seat_is_never_reported_as_his_pin():
    # The whole reason seats are not rows in milestones.md: the drawer draws
    # "pinned" off `pin`, and a seat must leave it at 0.
    seats = {("marcus", "trivial"): 1}
    drawn = project_milestones(ROWS, "Marcus", pins=None, seats=seats)
    assert [m["name"] for m in drawn][0] == "Trivial"
    assert all(m["pin"] == 0 for m in drawn)


def test_render_refuses_a_pipe():
    assert render_milestone_seats([("Mar|cus", "x")]) is None


def test_top_board_rows_reads_the_seats(monkeypatch):
    """The wiring, for `test_top_board_rows_reads_the_pins`'s reason: a
    seat file nothing passes on ranks exactly like no seat file, silently."""
    from tools import top_board_rows as tbr
    fetched = []
    monkeypatch.setattr(tbr, "_fetch", lambda path: fetched.append(path))
    assert tbr.fetch_milestone_seats() == ""
    assert fetched == ["projects/sokrates/projects/nova/milestone-seats.md"]

    seen = {}
    real = tbr.milestone_ranks
    monkeypatch.setattr(tbr, "milestone_ranks",
                        lambda rows, pins=None, seats=None:
                        seen.setdefault("seats", seats) or real(rows, pins))
    tbr.render([], milestone_seats_markdown=render_milestone_seats(
        [("Nova", "Picking")]))
    assert seen["seats"] == {("nova", "picking"): 1}


def test_the_site_hands_the_seats_to_both_milestone_orders():
    """Source-level, the way `test_milestone_pin_route` pins the pins: the
    project drawer and the Next page each rank milestones, and a seat that
    reaches one and not the other is two answers to one question."""
    import inspect
    from agora_runner import nova_site
    source = inspect.getsource(nova_site)
    assert "parse_milestone_pins(milestone_seats_markdown()))" in source
    assert "seats_markdown=milestone_seats_markdown()," in source


def test_next_payload_orders_by_the_seats():
    from agora_runner.nova_next import next_payload_from_contents
    from agora_runner.nova_boards import parse_board, PRIORITY_LABELS
    from tests.test_nova_next import NOW
    board = parse_board(
        "## Board\n\n| # | Idea | Status | Updated | Priority | Project "
        "| Size | Milestone |\n|---|---|---|---|---|---|---|---|\n"
        f"| [[#1 — a\\|1]] | a | ⚪ Backlog | 09-01 | "
        # High, not Immediately: `rank` still lifts an Immediately row over
        # every milestone, which would hide the seat this test is about.
        f"{PRIORITY_LABELS['high']} | Nova | S | Urgent |\n"
        f"| [[#2 — b\\|2]] | b | ⚪ Backlog | 09-01 | "
        f"{PRIORITY_LABELS['low']} | Nova | XL | Trivial |\n")
    empty = parse_board("")

    def top(**kw):
        payload = next_payload_from_contents(empty, board, "", NOW, **kw)
        return [r["number"] for r in payload["next"]]

    assert top()[0] == 1
    seats = render_milestone_seats([("Nova", "Trivial"), ("Nova", "Urgent")])
    assert top(seats_markdown=seats)[0] == 2
