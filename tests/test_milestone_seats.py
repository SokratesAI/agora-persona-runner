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
    row(2, HIGH, "Marcus", "S", "Middle"),
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
