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
    ok, message = nova_capture.set_row_order("ideas", 7, 1)
    assert ok, message
    assert _stored_orders(store) == {7: 1, 8: 2}
    assert _stored_orders(store, "issue") == {5: None, 7: 3}


def test_an_issue_and_an_idea_with_one_number_are_different_rows(monkeypatch):
    store = _records(monkeypatch, issues=SHARED)
    ok, message = nova_capture.set_row_order("issues", 7, 1)
    assert ok, message
    assert _stored_orders(store, "issue") == {5: None, 7: 1}
    assert _stored_orders(store) == {8: 2, 7: 3}


def test_the_last_seat_is_the_size_of_the_merged_group(monkeypatch):
    # Three open rows across two boards: seat 3 exists, seat 4 does not.
    store = _records(monkeypatch, issues=SHARED)
    assert nova_capture.set_row_order("ideas", 8, 3)[0]
    assert _stored_orders(store) == {7: 2, 8: 3}
    assert _stored_orders(store, "issue")[7] == 1
    ok, message = nova_capture.set_row_order("ideas", 8, 4)
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
    ok, message = nova_capture.set_row_order("ideas", 7, 1)
    assert ok, message
    # The first placement numbers the whole group, not just the row moved.
    assert _stored_orders(store) == {7: 1, 8: 2}


def test_the_seed_is_the_rating_when_nobody_has_placed_anything(monkeypatch):
    # #8 is High and #7 Low, so before any placement #8 sits first; placing
    # #8 at 2 has to put #7 above it rather than leave the seed standing.
    store = _records(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 8, 2)
    assert ok, message
    assert _stored_orders(store) == {7: 1, 8: 2}


def test_a_row_already_in_its_seat_is_not_rewritten(monkeypatch):
    store = _records(monkeypatch)
    assert nova_capture.set_row_order("ideas", 7, 1)[0]
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 7, 1)
    assert ok, message
    assert rows == []
    assert _stored_orders(store) == {7: 1, 8: 2}


def test_a_missing_row_says_the_phrase_the_site_answers_409_on(monkeypatch):
    _records(monkeypatch)
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 99, 1)
    assert not ok
    assert "is not a row" in message, message
    assert rows == []


def test_a_closed_row_is_refused_and_writes_nothing(monkeypatch):
    store = _records(monkeypatch, CLOSED)
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 9, 1)
    assert not ok and "cannot place #9" in message, message
    # Refused, not missing: the site must not tell the page to re-read.
    assert "is not a row" not in message
    assert rows == []
    assert _stored_orders(store)[9] is None


def test_a_position_past_the_end_of_the_group_writes_nothing(monkeypatch):
    # Two open rows in the group; the archived #9 does not make it three.
    store = _records(monkeypatch, CLOSED)
    rows = _count_writes(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 7, 3)
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
    ok, message = nova_capture.set_row_order("ideas", 7, 1)
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
    ok, message = nova_capture.set_row_order("ideas", 7, 1)
    assert not ok
    assert "1 of 2 seat(s) were written" in message, message
    assert "is not a row" not in message, message


def test_set_row_order_writes_to_the_store_it_is_handed(monkeypatch):
    from tests.test_nova_capture import _dest_records
    _records(monkeypatch)
    mine = _dest_records(BOARD, source=ISSUES)
    ok, message = nova_capture.set_row_order("ideas", 7, 1, store=mine)
    assert ok, message
    assert _stored_orders(mine) == {7: 1, 8: 2}
    assert set(_stored_orders(nova_capture.board_store).values()) == {None}


def test_an_unknown_target_never_reaches_the_store(monkeypatch):
    class Refuses:
        def __getattr__(self, name):
            raise AssertionError(f"the store was asked for {name}")

    monkeypatch.setattr(nova_capture, "board_store", Refuses())
    ok, message = nova_capture.set_row_order("notes-of-his", 7, 1)
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

    def fake_set(target, number, position):
        calls.append((target, number, position))
        return result

    monkeypatch.setattr(nova_site, "set_row_order", fake_set)
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: None)
    monkeypatch.setattr(nova_site, "invalidate", dropped.append)
    NovaSiteHandler._post_row_order(handler, payload)
    return handler.sent[-1], calls, dropped


def test_a_good_request_passes_all_three_and_drops_the_cached_board(monkeypatch):
    (status, body), calls, dropped = _call(
        {"target": "ideas", "number": 7, "position": 1}, monkeypatch)
    assert status == 200 and body["ok"] is True
    assert calls == [("ideas", 7, 1)]
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
        {"target": "ideas", "number": 99, "position": 1}, monkeypatch,
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
        {"target": "ideas", "number": 99, "position": 1}, monkeypatch,
        result=(False, "#99 is not a row on ideas"))
    assert status == 409, body
    assert dropped == []


def test_a_refused_placement_is_still_a_502(monkeypatch):
    (status, body), _calls, _dropped = _call(
        {"target": "ideas", "number": 7, "position": 9}, monkeypatch,
        result=(False, "cannot place #7 on ideas at 9"))
    assert status == 502, body
