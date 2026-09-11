"""Issue #202 part 1: every open row seated once, off the rating it carries.

The property that decides whether this is safe to run on his live boards is
the first test: `rank` must return exactly the order it returned before the
seats were written. `row_order_seats` seeds one board at a time and `rank`
merges both boards inside a milestone on the seat, so a per-board seed moves
an issue rated Low ahead of an idea rated Medium -- the fixture carries that
case on purpose.
"""

import json

from agora_runner import board_records, nova_next
from agora_runner.nova_boards import parse_board
from tools import seed_row_order

HEAD = """# Nova — {title}

## Board

| # | Item | Status | Updated | Priority | Project | Size | Milestone |
|---|---|---|---|---|---|---|---|
"""


def board(title, rows):
    return HEAD.format(title=title) + "".join(
        f"| [[#{n} — Row {n}\\|{n}]] | Row {n} | {status} | {updated} | "
        f"{priority} | {project} |  | {milestone} |\n"
        for n, status, updated, priority, project, milestone in rows)


ISSUES = board("Issues", [
    (1, "⚪ Backlog", "09-05", "🟠 High", "Marcus", "Push"),
    (2, "⚪ Backlog", "09-01", "⚪ Low", "Marcus", "Push"),
    (3, "✅ Done", "09-01", "🔴 Immediately", "Marcus", "Push"),
    (4, "⏸ Blocked on Edvard", "09-02", "🔵 Medium", "Nova", ""),
])
IDEAS = board("Ideas", [
    (7, "⚪ Backlog", "09-04", "🟠 High", "Marcus", "Push"),
    (8, "⚪ Backlog", "09-03", "🟠 High", "Marcus", "Push"),
    (9, "⚪ Backlog", "09-02", "🔵 Medium", "Marcus", "Push"),
    (10, "⚪ Backlog", "09-02", "", "Marcus", "Video"),
    (11, "⚪ Backlog", "09-09", "🔴 Immediately", "Nova", ""),
])


def rows_of(issues=ISSUES, ideas=IDEAS):
    return (nova_next.open_rows_from_contents(parse_board(issues), "issue")
            + nova_next.open_rows_from_contents(parse_board(ideas), "idea"))


def ranked(rows):
    return [(r["board"], r["number"]) for r in
            nova_next.rank(rows, None, nova_next.milestone_ranks(rows))]


def seated(rows, seats):
    return [dict(r, order=seats.get((r["board"], r["number"]), r["order"]))
            for r in rows]


def test_seating_every_row_leaves_the_ranking_exactly_as_it_was():
    rows = rows_of()
    seats, skipped = nova_next.seed_seats(rows)
    assert skipped == []
    assert ranked(seated(rows, seats)) == ranked(rows)
    # The case a per-board seed gets wrong: Marcus/Push is High 1, High 7,
    # High 8, Medium 9, Low 2 across both boards, so the Low issue sits
    # behind the Medium idea rather than at seat 2 of its own board.
    assert seats[("idea", 9)] == 4
    assert seats[("issue", 2)] == 5


def test_every_open_row_gets_a_seat_and_no_closed_one_does():
    seats, _ = nova_next.seed_seats(rows_of())
    assert ("issue", 3) not in seats
    # Blocked is open -- `row_order_seats` would place it, so it is seated.
    assert set(seats) == {("issue", 1), ("issue", 2), ("issue", 4),
                          ("idea", 7), ("idea", 8), ("idea", 9),
                          ("idea", 10), ("idea", 11)}
    # Immediately leads its group; a group of one is seat 1.
    assert seats[("idea", 11)] == 1 and seats[("issue", 4)] == 2
    assert seats[("idea", 10)] == 1


def test_a_group_he_ordered_by_hand_is_left_alone():
    rows = [dict(r, order=1) if (r["board"], r["number"]) == ("issue", 2)
            else r for r in rows_of()]
    seats, skipped = nova_next.seed_seats(rows)
    assert skipped == [("marcus", "push")]
    assert not any(key in seats for key in
                   [("issue", 1), ("issue", 2), ("idea", 7), ("idea", 8),
                    ("idea", 9)])
    # Other groups are still seated.
    assert seats[("idea", 10)] == 1


def test_a_seeding_that_stopped_part_way_finishes_and_then_does_nothing():
    rows = rows_of()
    full, _ = nova_next.seed_seats(rows)
    half = seated(rows, {k: v for k, v in full.items() if k[0] == "issue"})
    rest, skipped = nova_next.seed_seats(half)
    assert skipped == []
    assert rest == {k: v for k, v in full.items() if k[0] == "idea"}
    assert nova_next.seed_seats(seated(rows, full)) == ({}, [])


# --- the tool, against a fake record store ---


def _store():
    from tests.test_board_records import writable
    _, store = writable(board="issue", markdown=ISSUES)
    return store


def _orders(store):
    return {item["number"]: item["order"]
            for item in board_records.contents("issue", store=store)["items"]}


def test_without_write_nothing_is_written(capsys):
    store = _store()
    assert seed_row_order.main([], store=store, redraw=lambda b: []) == 0
    assert set(_orders(store).values()) == {None}
    assert "plan only" in capsys.readouterr().out


def test_write_seats_the_rows_saves_a_restore_point_and_unseed_undoes_it(tmp_path):
    store = _store()
    restore = tmp_path / "restore.json"
    redrawn = []
    assert seed_row_order.main(["--write", "--restore-file", str(restore)],
                               store=store,
                               redraw=lambda b: redrawn.append(b) or []) == 0
    assert _orders(store) == {1: 1, 2: 2, 3: None, 4: 1}
    assert redrawn == [["issue"]]
    assert json.loads(restore.read_text()) == [
        ["issue", 1, 1], ["issue", 2, 2], ["issue", 4, 1]]
    # A second run finds nothing left to seat.
    assert seed_row_order.plan(store=store) == ([], [])

    assert seed_row_order.main(["--unseed", str(restore)], store=store,
                               redraw=lambda b: []) == 0
    assert set(_orders(store).values()) == {None}


def test_a_seat_he_sets_after_the_plan_is_read_is_never_overwritten(tmp_path):
    from agora_runner import board_write
    store = _store()
    moves, _ = seed_row_order.plan(store=store)
    # He presses an arrow on #1 while the tool is working through the plan.
    board_write.change_row("issue", 1, {"order": 9}, store=store)
    written, left, problem = seed_row_order.write(
        [(b, n, None, s) for b, n, s in moves], store=store)
    assert (written, left, problem) == (2, [("issue", 1)], None)
    assert _orders(store)[1] == 9
    # And --unseed leaves his seat alone for the same reason.
    restore = tmp_path / "restore.json"
    restore.write_text(json.dumps([list(m) for m in moves]))
    assert seed_row_order.main(["--unseed", str(restore)], store=store,
                               redraw=lambda b: []) == 0
    assert _orders(store) == {1: 9, 2: None, 3: None, 4: None}
