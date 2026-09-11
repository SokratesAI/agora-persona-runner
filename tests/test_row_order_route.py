"""The write path for a task's position: `nova_capture` and `POST /api/row/order`.

`set_row_order` in `nova_boards` shipped in #918 and nothing ever called it,
so the `Order` cell existed on both boards and he had no way to write one.
Part 3 of `row-order-and-priority-migration.md` is his: *"adding
functionality for me to change it."*

Three things these pin, in the order the write travels. The capture layer
sends the revision it read back with the write, so a cycle boarding the same
file mid-drag loses the race rather than the row. A refusal from the markdown
layer is not retried, because re-reading returns the same answer. And the
endpoint decides types and nothing else -- which milestone a number lives in
is a fact about the document, and a second parse here would be a second
opinion about the same file.
"""

import re

import agora_runner.nova_capture as nova_capture
import agora_runner.nova_site as nova_site
from agora_runner.nova_boards import ROW_ORDER_STRIDE as S
from agora_runner.nova_site import NovaSiteHandler

BOARD = """---
type: board
---

# Nova — Ideas

## Board

| # | Idea | Status | Updated | Priority | Project | Size | Milestone |
|---|---|---|---|---|---|---|---|
| [[#7 — Low one\\|7]] | Low one | ⚪ Backlog | 09-05 | ⚪ Low | Marcus | M | Push |
| [[#8 — High one\\|8]] | High one | ⚪ Backlog | 09-04 | 🟠 High | Marcus | S | Push |
"""


# --- the capture layer: the #203 record store, and never his file ---


#: The issues board. Its one row is in another group, so it stays out of
#: every placement on BOARD's (Marcus, Push) -- `set_row_order` reads both
#: boards now, and an absent one would be a refusal rather than an empty.
ISSUES = """---
type: board
---

# Nova — Issues

## Board

| # | Issue | Status | Updated | Priority | Project | Size | Milestone |
|---|---|---|---|---|---|---|---|
| [[#5 — Elsewhere\\|5]] | Elsewhere | ⚪ Backlog | 09-05 | 🟠 High | Nova | S | Other |
"""

#: An issue in BOARD's (Marcus, Push) group, carrying the SAME number as the
#: idea rated Low -- so a seat keyed on the number alone lands on the wrong
#: row. Medium, so the rating seed puts it between the two ideas.
SHARED = ISSUES + (
    "| [[#7 — Same number\\|7]] | Same number | ⚪ Backlog | 09-05 | 🔵 Medium "
    "| Marcus | S | Push |\n")


def _records(monkeypatch, markdown=BOARD, issues=ISSUES):
    """A fake record store holding `markdown` as the ideas board and `issues`
    as the issues board, and a vault that must not be touched -- the order
    button writes the records."""
    from tests.test_nova_capture import _dest_records

    fake = _dest_records(markdown, source=issues)
    monkeypatch.setattr(nova_capture, "board_store", fake)

    def landmine(*a, **k):
        raise AssertionError("the order button touched the markdown")

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", landmine)
    monkeypatch.setattr(nova_capture, "vault_write_path", landmine)
    return fake


def _stored_orders(store, board="idea"):
    from agora_runner import board_records
    return {item["number"]: item["order"]
            for item in board_records.contents(board, store=store)["items"]}


def test_the_position_counts_the_milestone_across_both_boards(monkeypatch):
    # Seed: idea #8 High, issue #7 Medium, idea #7 Low. Placing idea #7 first
    # reseats the issue too -- one queue, so no two rows share a seat.
    store = _records(monkeypatch, issues=SHARED)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, "Nova")
    assert ok, message
    assert _stored_orders(store) == {7: 1 * S, 8: 2 * S}
    assert _stored_orders(store, "issue") == {5: None, 7: 3 * S}


def test_an_issue_and_an_idea_with_one_number_are_different_rows(monkeypatch):
    store = _records(monkeypatch, issues=SHARED)
    ok, message = nova_capture.set_row_order("issues", 7, 1, "Nova")
    assert ok, message
    assert _stored_orders(store, "issue") == {5: None, 7: 1 * S}
    assert _stored_orders(store) == {8: 2 * S, 7: 3 * S}


def test_a_repeated_placement_rewrites_nothing_on_either_board(monkeypatch):
    # Issue #7 already holds seat 3; its idea namesake holds seat 1. Judging
    # "already in its seat" by the number alone reads the idea's seat for
    # the issue and rewrites a row that did not move.
    _records(monkeypatch, issues=SHARED)
    assert nova_capture.set_row_order("ideas", 7, 1, "Nova")[0]
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, "Nova")
    assert ok, message
    assert rows == []


def test_the_other_board_unreadable_writes_nothing(monkeypatch):
    # A seat computed from the ideas board alone would collide with the
    # issues board's seats, so an unreadable issues board is a refusal.
    from agora_runner import board_records
    _records(monkeypatch, issues=SHARED)
    rows = _count_writes(monkeypatch)
    real = board_records.contents

    def issues_down(board, *a, **k):
        if board == "issue":
            raise board_records.RecordError("issues board unreadable")
        return real(board, *a, **k)

    monkeypatch.setattr(board_records, "contents", issues_down)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, "Nova")
    assert not ok and "could not read issues" in message, message
    assert rows == []


def test_the_last_seat_is_the_size_of_the_merged_group(monkeypatch):
    # Three open rows across two boards: seat 3 exists, seat 4 does not.
    store = _records(monkeypatch, issues=SHARED)
    assert nova_capture.set_row_order("ideas", 8, 3, "Nova")[0]
    assert _stored_orders(store) == {7: 2 * S, 8: 3 * S}
    assert _stored_orders(store, "issue")[7] == 1 * S
    ok, message = nova_capture.set_row_order("ideas", 8, 4, "Nova")
    assert not ok and "cannot place #8" in message, message


def _count_writes(monkeypatch):
    from agora_runner import board_write
    real = board_write.change_row
    rows = []

    def counting(board, number, changes, *a, **k):
        rows.append((number, dict(changes)))
        return real(board, number, changes, *a, **k)

    monkeypatch.setattr(board_write, "change_row", counting)
    return rows


# #9 sits in the same (Marcus, Push) group and is archived, so a position is
# not a statement about it: it is neither placeable nor counted in the group.
CLOSED = BOARD + (
    "| [[#9 — Old one\\|9]] | Old one | ⚫ Outdated | 09-01 | | Marcus | S | Push |\n")


def test_a_placement_writes_the_records_and_numbers_the_whole_group(monkeypatch):
    store = _records(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, "Nova")
    assert ok, message
    # The first placement numbers the whole group, not just the row moved.
    assert _stored_orders(store) == {7: 1 * S, 8: 2 * S}


def test_the_seed_is_the_rating_when_nobody_has_placed_anything(monkeypatch):
    # #8 is High and #7 Low, so before any placement #8 sits first; placing
    # #8 at 2 has to put #7 above it rather than leave the seed standing.
    store = _records(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 8, 2, "Nova")
    assert ok, message
    assert _stored_orders(store) == {7: 1 * S, 8: 2 * S}


def test_a_row_already_in_its_seat_is_not_rewritten(monkeypatch):
    store = _records(monkeypatch)
    assert nova_capture.set_row_order("ideas", 7, 1, "Nova")[0]
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, "Nova")
    assert ok, message
    assert rows == []
    assert _stored_orders(store) == {7: 1 * S, 8: 2 * S}


def test_a_missing_row_says_the_phrase_the_site_answers_409_on(monkeypatch):
    _records(monkeypatch)
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 99, 1, "Nova")
    assert not ok
    assert "is not a row" in message, message
    assert rows == []


def test_a_closed_row_is_refused_and_writes_nothing(monkeypatch):
    store = _records(monkeypatch, CLOSED)
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 9, 1, "Nova")
    assert not ok and "cannot place #9" in message, message
    # Refused, not missing: the site must not tell the page to re-read.
    assert "is not a row" not in message
    assert rows == []
    assert _stored_orders(store)[9] is None


def test_a_position_past_the_end_of_the_group_writes_nothing(monkeypatch):
    # Two open rows in the group; the archived #9 does not make it three.
    store = _records(monkeypatch, CLOSED)
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 7, 3, "Nova")
    assert not ok and "cannot place #7" in message, message
    assert rows == []
    assert set(_stored_orders(store).values()) == {None}


def test_a_write_that_fails_part_way_says_how_many_landed(monkeypatch):
    from agora_runner import board_write
    store = _records(monkeypatch)
    real = board_write.change_row
    calls = []

    def second_fails(board, number, changes, *a, **k):
        calls.append(number)
        if len(calls) == 2:
            raise board_write.WriteRefused("row moved")
        return real(board, number, changes, *a, **k)

    monkeypatch.setattr(board_write, "change_row", second_fails)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, "Nova")
    assert not ok
    assert "1 of 2 seat(s) were written" in message, message
    assert "is not a row" not in message


def test_a_row_vanishing_after_a_seat_landed_is_not_the_409_phrase(monkeypatch):
    # `change_row`'s own words for a row deleted mid-group carry the site's
    # 409 phrase; after a seat has landed that would tell the page nothing
    # was written. The raised text is `change_row`'s real one, not a stand-in.
    from agora_runner import board_write
    store = _records(monkeypatch)
    real = board_write.change_row
    calls = []

    def second_vanishes(board, number, changes, *a, **k):
        calls.append(number)
        if len(calls) == 2:
            store.docs = [doc for doc in store.docs
                          if doc.get("_id") != f"board:idea:{number}"]
        return real(board, number, changes, *a, **k)

    monkeypatch.setattr(board_write, "change_row", second_vanishes)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, "Nova")
    assert not ok
    assert "1 of 2 seat(s) were written" in message, message
    assert "is not a row" not in message, message


def test_set_row_order_writes_to_the_store_it_is_handed(monkeypatch):
    from tests.test_nova_capture import _dest_records
    _records(monkeypatch)
    mine = _dest_records(BOARD, source=ISSUES)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, "Nova", store=mine)
    assert ok, message
    assert _stored_orders(mine) == {7: 1 * S, 8: 2 * S}
    assert set(_stored_orders(nova_capture.board_store).values()) == {None}


def test_an_unknown_target_never_reaches_the_store(monkeypatch):
    class Refuses:
        def __getattr__(self, name):
            raise AssertionError(f"the store was asked for {name}")

    monkeypatch.setattr(nova_capture, "board_store", Refuses())
    ok, message = nova_capture.set_row_order("notes-of-his", 7, 1, "Nova")
    assert not ok and "unknown target" in message


# --- the HTTP layer: what a client is allowed to send ---


class _Handler:
    def __init__(self):
        self.sent = []
        self.headers = {}

    def _send_json(self, status, body):
        self.sent.append((status, body))


def _call(payload, monkeypatch, result=(True, "#7 is now #1 in its milestone")):
    handler = _Handler()
    calls = []
    dropped = []

    def fake_set(target, number, position, author):
        calls.append((target, number, position, author))
        return result

    monkeypatch.setattr(nova_site, "set_row_order", fake_set)
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: None)
    monkeypatch.setattr(nova_site, "invalidate", dropped.append)
    NovaSiteHandler._post_row_order(handler, payload)
    return handler.sent[-1], calls, dropped


def test_a_good_request_passes_all_three_and_drops_the_cached_board(monkeypatch):
    (status, body), calls, dropped = _call(
        {"target": "ideas", "number": 7, "position": 1, "author": "Edvard"},
        monkeypatch)
    assert status == 200 and body["ok"] is True
    assert calls == [("ideas", 7, 1, "Edvard")]
    # He is looking at the old order right now; without this the page
    # redraws from cache and the drag springs back. Both boards: a seat on
    # the one `target` does not name may have moved as well.
    assert dropped == ["board:issues", "board:ideas"]


def test_a_target_that_is_not_one_of_his_two_boards_is_refused(monkeypatch):
    for bad in ("notes", "projects", "../ideas", None, 7):
        (status, body), calls, _dropped = _call(
            {"target": bad, "number": 7, "position": 1}, monkeypatch)
        assert status == 400, bad
        assert "target must be" in body["error"]
        assert calls == []


def test_a_number_that_is_not_a_positive_int_is_refused_before_any_write(monkeypatch):
    # `True` is an `int` in Python and would address row 1 -- the trap
    # `_post_amend` names and the reason this is not a truthiness check.
    for bad in ("7", 0, -1, 1.5, None, True):
        (status, body), calls, _dropped = _call(
            {"target": "ideas", "number": bad, "position": 1}, monkeypatch)
        assert status == 400, bad
        assert "number must be" in body["error"]
        assert calls == []


def test_a_position_that_is_not_a_positive_int_is_refused_before_any_write(monkeypatch):
    for bad in ("1", 0, -1, 1.5, None, True):
        (status, body), calls, _dropped = _call(
            {"target": "ideas", "number": 7, "position": bad}, monkeypatch)
        assert status == 400, bad
        assert "position must be" in body["error"]
        assert calls == []


def test_a_refused_write_answers_502_and_leaves_the_cache_alone(monkeypatch):
    (status, body), _calls, dropped = _call(
        {"target": "ideas", "number": 99, "position": 1, "author": "Nova"},
        monkeypatch,
        result=(False, "cannot place #99 on ideas at 1"))
    assert status == 502 and body["ok"] is False and "99" in body["message"]
    # Nothing was written, so nothing is stale -- dropping the cache here
    # would make a refusal cost a re-read of both boards.
    assert dropped == []


def test_the_route_is_dispatched_and_in_the_post_allowlist():
    source = open(nova_site.__file__).read()
    # Both halves: a handler with no route is dead code, and a route the
    # allowlist does not carry is a 405 before it ever dispatches.
    assert '"/api/row/order",' in source
    assert re.search(
        r'if path == "/api/row/order":\n\s+self\._post_row_order\(payload\)',
        source)


def test_a_missing_row_is_a_409_and_not_a_502(monkeypatch):
    # The page's cue to re-read, as on every sibling row route.
    (status, body), _calls, dropped = _call(
        {"target": "ideas", "number": 99, "position": 1, "author": "Nova"},
        monkeypatch,
        result=(False, "#99 is not a row on ideas"))
    assert status == 409, body
    assert dropped == []


def test_a_refused_placement_is_still_a_502(monkeypatch):
    (status, body), _calls, _dropped = _call(
        {"target": "ideas", "number": 7, "position": 9, "author": "Nova"},
        monkeypatch,
        result=(False, "cannot place #7 on ideas at 9"))
    assert status == 502, body


# --- issue #202: a position he set is his, and a cycle may not overwrite it ---


def _stored(store, key, board="idea"):
    from agora_runner import board_records
    return {item["number"]: item.get(key)
            for item in board_records.contents(board, store=store)["items"]}


def test_his_placement_is_recorded_as_his_and_only_on_the_row_he_moved(monkeypatch):
    store = _records(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, "Edvard")
    assert ok, message
    # #8 was reseated by his move, not placed by him.
    assert _stored(store, "placedBy") == {7: "Edvard", 8: None}


def test_a_cycle_may_not_move_a_row_he_placed(monkeypatch):
    store = _records(monkeypatch)
    assert nova_capture.set_row_order("ideas", 7, 1, "Edvard")[0]
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 7, 2, "Nova")
    assert not ok and "placed by Edvard" in message, message
    # A refusal, not a missing row: the site must answer 502, not 409.
    assert "is not a row" not in message
    assert rows == []
    assert _stored_orders(store) == {7: 1 * S, 8: 2 * S}


def test_a_cycle_may_still_move_a_row_he_did_not_place(monkeypatch):
    store = _records(monkeypatch)
    assert nova_capture.set_row_order("ideas", 7, 1, "Edvard")[0]
    ok, message = nova_capture.set_row_order("ideas", 8, 1, "Nova")
    assert ok, message
    assert _queue(store) == [8, 7]
    assert _stored(store, "placedBy") == {7: "Edvard", 8: None}


def test_he_may_move_a_row_he_already_placed(monkeypatch):
    store = _records(monkeypatch)
    assert nova_capture.set_row_order("ideas", 7, 1, "Edvard")[0]
    ok, message = nova_capture.set_row_order("ideas", 7, 2, "Edvard")
    assert ok, message
    assert _queue(store) == [8, 7]


def test_his_placement_on_the_seat_a_cycle_gave_it_is_still_recorded(monkeypatch):
    # The seat does not move, so an order-only diff would write nothing and
    # his choice would go unrecorded -- the stamp is a change of its own.
    store = _records(monkeypatch)
    assert nova_capture.set_row_order("ideas", 7, 1, "Nova")[0]
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, "Edvard")
    assert ok, message
    assert rows == [(7, {"placedBy": "Edvard"})]
    assert _stored(store, "placedBy")[7] == "Edvard"


def test_his_stamp_survives_another_writer_changing_the_row(monkeypatch):
    # `store_item` re-mints the document from the row, so a field it does
    # not know would be dropped by the next status or date change.
    from agora_runner import board_write
    store = _records(monkeypatch)
    assert nova_capture.set_row_order("ideas", 7, 1, "Edvard")[0]
    board_write.change_row("idea", 7, {"updated": "09-12"}, store=store)
    assert _stored(store, "placedBy")[7] == "Edvard"
    assert _stored(store, "updated")[7] == "09-12"


def test_an_author_that_is_neither_of_the_two_never_reaches_the_store(monkeypatch):
    class Refuses:
        def __getattr__(self, name):
            raise AssertionError(f"the store was asked for {name}")

    monkeypatch.setattr(nova_capture, "board_store", Refuses())
    for bad in ("edvard", "", None, "Sokrates"):
        ok, message = nova_capture.set_row_order("ideas", 7, 1, bad)
        assert not ok and "author must be" in message, bad


def test_the_route_refuses_a_request_that_does_not_say_who_moved_it(monkeypatch):
    for payload in ({"target": "ideas", "number": 7, "position": 1},
                    {"target": "ideas", "number": 7, "position": 1, "author": "edvard"}):
        (status, body), calls, _dropped = _call(payload, monkeypatch)
        assert status == 400, payload
        assert "author must be" in body["error"]
        assert calls == []


def test_the_app_sends_its_moves_as_his():
    import pathlib
    source = (pathlib.Path(nova_site.__file__).parent / "nova_public" / "app.js").read_text()
    start = source.index("function sendRowOrder(")
    body = source[start:source.index("\n  }\n", start)]
    assert 'author: "Edvard"' in body


def test_a_row_he_placed_is_not_drift_against_his_markdown():
    # The markdown view has no cell for the stamp, so it can never carry it.
    from agora_runner.board_publish import differences
    row = {"number": 7, "order": 1}
    assert differences({"items": [row]}, {"items": [dict(row, placedBy="Edvard")]}) == []
    moved = differences({"items": [row]}, {"items": [dict(row, order=2, placedBy="Edvard")]})
    assert moved and "order" in moved[0], moved


def test_the_stamp_round_trips_and_an_unstamped_row_gains_no_key():
    from agora_runner import board_document
    item = {"number": 7, "title": "t", "order": 1, "placedBy": "Edvard"}
    doc = board_document.to_document(item, "idea")
    assert doc["placedBy"] == "Edvard"
    assert board_document.from_document(doc)["placedBy"] == "Edvard"
    bare = board_document.to_document({"number": 8, "title": "t"}, "idea")
    assert "placedBy" not in bare
    assert "placedBy" not in board_document.from_document(bare)


def test_a_row_he_placed_is_not_drift_with_the_store_on_either_side():
    # `render` passes the records FIRST (`differences(contents, reread(text))`)
    # and `status` passes them second; a stamp on only one side is not drift
    # in either order, or his board stops redrawing after his first drag.
    from agora_runner.board_publish import differences
    row = {"number": 7, "order": 1}
    stamped = dict(row, placedBy="Edvard")
    assert differences({"items": [stamped]}, {"items": [row]}) == []
    assert differences({"items": [row]}, {"items": [stamped]}) == []


def test_his_board_still_renders_clean_after_he_places_a_row(monkeypatch):
    from agora_runner import board_publish
    store = _records(monkeypatch)
    assert nova_capture.set_row_order("ideas", 7, 1, "Edvard")[0]
    _text, problems = board_publish.render("idea", BOARD, store=store)
    # The fake store keeps no layout document, which `render` reports on
    # every board with or without a stamp; the rows are what this is about.
    rows = [p for p in problems if not p.startswith("layout:")]
    assert rows == [], rows


def test_his_drag_landing_mid_move_is_not_overwritten_by_a_cycle(monkeypatch):
    # The cycle read the boards before his drag landed; the stamp check has
    # to run on `change_row`'s own read, not on that stale one.
    from agora_runner import board_write
    store = _records(monkeypatch)
    real = nova_capture._row_order_seats

    def his_drag_lands_now(*a, **k):
        board_write.change_row(
            "idea", 7, {"order": 1, "placedBy": "Edvard"}, store=store)
        return real(*a, **k)

    monkeypatch.setattr(nova_capture, "_row_order_seats", his_drag_lands_now)
    ok, message = nova_capture.set_row_order("ideas", 7, 2, "Nova")
    assert not ok and "placedBy" in message, message
    assert "0 of 2 seat(s) were written" in message, message
    assert _stored_orders(store) == {7: 1, 8: None}
    assert _stored(store, "placedBy")[7] == "Edvard"


# --- issue #202, definition of done item 4: moving one task writes one document ---


#: Four placed ideas in one (Marcus, Push) group, on dense seats 1..4 --
#: the shape every open row on the live boards was seeded into.
DENSE = """---
type: board
---

# Nova — Ideas

## Board

| # | Idea | Status | Updated | Priority | Project | Size | Milestone | Order |
|---|---|---|---|---|---|---|---|---|
| [[#1 — One\\|1]] | One | ⚪ Backlog | 09-05 | 🟠 High | Marcus | S | Push | 1 |
| [[#2 — Two\\|2]] | Two | ⚪ Backlog | 09-05 | 🟠 High | Marcus | S | Push | 2 |
| [[#3 — Three\\|3]] | Three | ⚪ Backlog | 09-05 | 🟠 High | Marcus | S | Push | 3 |
| [[#4 — Four\\|4]] | Four | ⚪ Backlog | 09-05 | 🟠 High | Marcus | S | Push | 4 |
"""


def _queue(store):
    seats = _stored_orders(store)
    return sorted(seats, key=seats.get)


def test_a_dense_group_is_spaced_out_once_and_then_one_move_is_one_write(monkeypatch):
    store = _records(monkeypatch, markdown=DENSE)
    rows = _count_writes(monkeypatch)
    # Seats 1..4 leave no room above seat 1, so the first move numbers them.
    assert nova_capture.set_row_order("ideas", 4, 1, "Nova")[0]
    assert len(rows) == 4
    assert _stored_orders(store) == {4: 1 * S, 1: 2 * S, 2: 3 * S, 3: 4 * S}
    del rows[:]
    # The bottom task of the group to the top: one document.
    ok, message = nova_capture.set_row_order("ideas", 3, 1, "Nova")
    assert ok, message
    assert [number for number, _ in rows] == [3]
    assert _queue(store) == [3, 4, 1, 2]
    del rows[:]
    # And into the middle, between two neighbours.
    assert nova_capture.set_row_order("ideas", 2, 3, "Nova")[0]
    assert [number for number, _ in rows] == [2]
    assert _queue(store) == [3, 4, 2, 1]


def test_a_used_up_gap_numbers_the_group_again(monkeypatch):
    store = _records(monkeypatch, markdown=DENSE)
    rows = _count_writes(monkeypatch)
    # Seats 1 and 2 have no whole number between them.
    assert nova_capture.set_row_order("ideas", 4, 2, "Nova")[0]
    assert len(rows) == 4
    assert _queue(store) == [1, 4, 2, 3]
    assert all(seat % S == 0 for seat in _stored_orders(store).values())


TIED = DENSE.replace("| Push | 2 |", "| Push | 1 |")


def test_two_rows_on_one_seat_are_numbered_rather_than_squeezed(monkeypatch):
    # #1 and #2 share seat 1, which a half-finished write can leave behind;
    # a seat "between" them does not exist, so the group is numbered again.
    store = _records(monkeypatch, markdown=TIED.replace("| Push | 4 |", "| Push | 40 |"))
    rows = _count_writes(monkeypatch)
    assert nova_capture.set_row_order("ideas", 4, 4, "Nova")[0]
    assert len(rows) == 4
    seats = _stored_orders(store)
    assert len(set(seats.values())) == 4


def test_a_move_to_the_bottom_takes_a_seat_below_the_last_one(monkeypatch):
    store = _records(monkeypatch, markdown=DENSE)
    assert nova_capture.set_row_order("ideas", 4, 1, "Nova")[0]  # spaced out
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 4, 4, "Nova")
    assert ok, message
    assert rows == [(4, {"order": 5 * S})]
    assert _queue(store) == [1, 2, 3, 4]
