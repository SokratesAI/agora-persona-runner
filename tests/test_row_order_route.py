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
from agora_runner.nova_boards import parse_board
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


def _orders(markdown):
    return {item["number"]: item["order"] for item in parse_board(markdown)["items"]}


# --- the capture layer: read, modify, write, and what it will not retry ---


def _wire(monkeypatch, doc=BOARD, results=("written",)):
    seen = {"doc": doc, "writes": [], "revs": []}
    pending = list(results)

    def read(path):
        seen["path"] = path
        return seen["doc"], "rev-1"

    def write(path, body, if_rev=None):
        seen["writes"].append(body)
        seen["revs"].append(if_rev)
        return pending.pop(0) if pending else "written"

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", read)
    monkeypatch.setattr(nova_capture, "vault_write_path", write)
    return seen


def test_a_placement_writes_the_group_back_against_the_revision_it_read(monkeypatch):
    seen = _wire(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 7, 1)
    assert ok, message
    # The revision goes back with the write -- that compare-and-swap is the
    # whole reason this is not a bare put, since a cycle boarding this file
    # is the concurrent writer and boarding is what step 6 does every cycle.
    assert seen["revs"] == ["rev-1"]
    # The first placement numbers the whole group, not just the row moved.
    assert _orders(seen["writes"][-1]) == {7: 1, 8: 2}


def test_a_missing_file_is_refused_rather_than_created(monkeypatch):
    # The message is the assertion, not the absent write. An empty document
    # is refused one layer down too -- `set_row_order` finds no board in it
    # -- so "nothing was written" passes whether or not this layer looked at
    # the file at all, and a check whose pass is guaranteed in advance is
    # not a check. `not found` can only come from here.
    seen = _wire(monkeypatch, doc=None)
    ok, message = nova_capture.set_row_order("ideas", 7, 1)
    assert not ok
    assert "not found" in message, message
    assert seen["writes"] == []


def test_a_refusal_from_the_markdown_layer_is_not_retried(monkeypatch):
    # #99 is on no board here. Re-reading gives the same answer, so a 409
    # loop around it would just spin -- the distinction `set_priority` draws.
    seen = _wire(monkeypatch)
    ok, message = nova_capture.set_row_order("ideas", 99, 1)
    assert not ok and "99" in message
    assert seen["writes"] == []


def test_a_conflict_is_retried_from_a_fresh_read(monkeypatch):
    seen = _wire(monkeypatch, results=("409 conflict", "written"))
    ok, message = nova_capture.set_row_order("ideas", 8, 1)
    assert ok, message
    assert len(seen["writes"]) == 2


def test_an_unknown_target_never_reaches_the_vault(monkeypatch):
    seen = _wire(monkeypatch)
    ok, message = nova_capture.set_row_order("notes-of-his", 7, 1)
    assert not ok and "unknown target" in message
    assert "path" not in seen


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
    # redraws from cache and the drag springs back.
    assert dropped == ["board:ideas"]


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
