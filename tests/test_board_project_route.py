"""The owner moving a boarded row to a project from the app.

His capture, 2026-09-01, rated 🔴 Immediately: *"I/you should easily be
able to assign issues and ideas to projects, and change project if
assigned wrongly ... I/you should easily be able to create new
projects."* `set_row_project` and `tools.board_project` already existed;
what did not was any route the app could reach, so every `Project` cell
on both boards had been written by a cycle at a shell.

`tests/test_board_project.py` covers `set_row_project` itself. This file
covers the two layers above it: the write into the #203 record store and
the HTTP validation, which is where a route can put arbitrary text into a
cell of his board.
"""

import json

import pytest

import agora_runner.nova_capture as nova_capture

BOARD = """---
type: board
---

## Board

| # | Item | Status | Updated | Priority | Project |
|---|------|--------|---------|---|---|
| [[#57 — More pages\\|57]] | More pages | 🟡 In progress | 08-11 | 🔵 Medium | Nova |
| [[#59 — Small pickings\\|59]] | Small pickings | ⚪ Backlog | 08-11 | | Nova |

## Done

| # | Item | Landed | Where |
|---|------|--------|-------|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

# Details

## #57 — More pages

Body text I must not touch.
"""


def _records(monkeypatch):
    """A fake record store holding `BOARD`, and a vault that must not be
    touched -- the project button writes the records, never his file."""
    from tests.test_board_records import writable

    _, fake = writable(board="issue", markdown=BOARD)
    monkeypatch.setattr(nova_capture, "board_store", fake)

    def landmine(*a, **k):
        raise AssertionError("the project button touched the markdown")

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", landmine)
    monkeypatch.setattr(nova_capture, "vault_write_path", landmine)
    return fake


def _row(store, number):
    from agora_runner import board_records
    return next(item for item in board_records.contents("issue", store=store)["items"]
                if item["number"] == number)


def test_set_project_writes_the_record_and_not_his_file(monkeypatch):
    from agora_runner import board_records

    store = _records(monkeypatch)
    ok, message = nova_capture.set_project("issues", 57, "Marcus")
    assert (ok, message) == (True, "#57 moved on issues")
    row = _row(store, 57)
    assert row["project"] == "Marcus"
    # The rest of the row is the row, not a re-render of it.
    assert row["title"] == "More pages" and row["priority"] == "🔵 Medium"
    details = board_records.contents("issue", store=store)["details"]
    assert "Body text I must not touch." in details[57]


def test_a_name_no_row_carries_is_how_a_project_is_created(monkeypatch):
    """The create half of his ask, and it needs no second document.

    `store_item` mints the id the first time a name appears, so writing a
    name nothing else uses *is* creating the project. This test is the one
    that would fail if a later cycle added an allowed-projects list.
    """
    from agora_runner import board_records
    from agora_runner.nova_boards import board_projects

    store = _records(monkeypatch)
    ok, _message = nova_capture.set_project("issues", 59, "Infra")
    assert ok
    items = board_records.contents("issue", store=store)["items"]
    assert "Infra" in board_projects(items)


@pytest.mark.parametrize("bad", ["a|b", "**bold", "two\nlines", "a\rb", "   ", "x" * 41])
def test_set_project_refuses_a_name_that_would_break_the_cell(monkeypatch, bad):
    store = _records(monkeypatch)
    ok, message = nova_capture.set_project("issues", 57, bad)
    assert not ok
    # A bad name is not a missing row: the page must not give up on a row
    # that is sitting right there.
    assert "is not a row" not in message
    assert _row(store, 57)["project"] == "Nova"


def test_set_project_strips_the_name_it_was_handed(monkeypatch):
    store = _records(monkeypatch)
    assert nova_capture.set_project("issues", 57, "  Marcus  ")[0]
    assert _row(store, 57)["project"] == "Marcus"


def test_forty_characters_is_still_a_name(monkeypatch):
    store = _records(monkeypatch)
    assert nova_capture.set_project("issues", 57, "x" * 40)[0]
    assert _row(store, 57)["project"] == "x" * 40


def test_a_row_in_the_finished_table_is_refused_and_writes_nothing(monkeypatch):
    """`set_row_project` could not reach the `## Done` table, and the view's
    Done table has no Project column, so a write there would show nowhere."""
    store = _records(monkeypatch)
    before = _row(store, 51)
    assert before["done"] is True, "the fixture must exercise the finished table"
    ok, message = nova_capture.set_project("issues", 51, "Marcus")
    assert not ok and "finished table" in message
    assert "is not a row" not in message
    assert _row(store, 51) == before


def test_a_missing_row_says_the_phrase_the_site_answers_409_on(monkeypatch):
    _records(monkeypatch)
    assert nova_capture.set_project("issues", 999, "Marcus") == (
        False, "#999 is not a row on issues")


def test_a_row_that_moved_under_the_write_is_not_reported_as_missing(monkeypatch):
    from agora_runner import board_write

    _records(monkeypatch)

    def moved(board, number, changes, detail=None, store=None):
        raise board_write.WriteRefused(
            f"row #{number} of board {board!r} changed between reading the "
            "board and writing it")

    monkeypatch.setattr(board_write, "change_row", moved)
    ok, message = nova_capture.set_project("issues", 57, "Marcus")
    assert not ok
    assert "is not a row" not in message and "changed between" in message


def test_set_project_writes_to_the_store_it_is_handed(monkeypatch):
    from tests.test_board_records import writable

    _records(monkeypatch)
    _, handed = writable(board="issue", markdown=BOARD)
    assert nova_capture.set_project("issues", 57, "Marcus", store=handed)[0]
    assert _row(handed, 57)["project"] == "Marcus"
    assert _row(nova_capture.board_store, 57)["project"] == "Nova"


def test_set_project_refuses_an_unknown_target(monkeypatch):
    _records(monkeypatch)
    ok, message = nova_capture.set_project("notes", 57, "Nova")
    assert not ok and "unknown target" in message


# --- the HTTP layer: what a client is allowed to send ---


class _Handler:
    """`_post_project` unbound from the server, with the two things it uses.

    Subclassing the real handler would drag in a socket; the method only
    touches `self.headers` and `self._send_json`, so this is the whole
    surface it has. Verified by the tests below actually calling it.
    """

    def __init__(self):
        self.sent = []
        self.headers = {}

    def _send_json(self, status, body):
        self.sent.append((status, body))


def _call(payload, result=(True, "#57 moved on issues"), monkeypatch=None):
    from agora_runner.nova_site import NovaSiteHandler

    handler = _Handler()
    calls = []

    def fake_set_project(target, number, project):
        calls.append((target, number, project))
        return result

    import agora_runner.nova_site as nova_site

    monkeypatch.setattr(nova_site, "set_project", fake_set_project)
    monkeypatch.setattr(nova_site, "invalidate", lambda key: calls.append(("invalidate", key)))
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: None)
    NovaSiteHandler._post_project(handler, payload)
    return handler.sent[-1], calls


def test_a_good_request_writes_and_invalidates_the_board(monkeypatch):
    (status, body), calls = _call(
        {"target": "issues", "number": 57, "project": "  Marcus  "}, monkeypatch=monkeypatch)
    assert status == 200 and body["ok"] is True
    assert ("issues", 57, "Marcus") in calls
    # The project index is built from the two `board:<name>` cache entries,
    # so invalidating the board is what makes a brand-new project name show
    # up at /projects. Nothing else clears it.
    assert ("invalidate", "board:issues") in calls


def test_a_failed_write_does_not_invalidate(monkeypatch):
    (status, body), calls = _call(
        {"target": "issues", "number": 57, "project": "Marcus"},
        result=(False, "could not write"), monkeypatch=monkeypatch)
    assert status == 502 and body["ok"] is False
    assert not any(c[0] == "invalidate" for c in calls if isinstance(c, tuple) and len(c) == 2)


def test_the_route_refuses_before_it_writes(monkeypatch):
    """Each of these must be a 400 and must not reach the vault.

    `True` is the one worth naming: it is an `int` in Python, so a bare
    `isinstance(n, int)` accepts it and it addresses row 1 -- the trap the
    priority and amend routes already guard.
    """
    bad = [
        {"target": "../etc", "number": 57, "project": "Marcus"},
        {"target": "notes", "number": 57, "project": "Marcus"},
        {"target": "issues", "number": True, "project": "Marcus"},
        {"target": "issues", "number": 0, "project": "Marcus"},
        {"target": "issues", "number": "57", "project": "Marcus"},
        {"target": "issues", "number": 57, "project": ""},
        {"target": "issues", "number": 57, "project": "   "},
        {"target": "issues", "number": 57, "project": None},
        {"target": "issues", "number": 57, "project": 3},
    ]
    for payload in bad:
        (status, body), calls = _call(payload, monkeypatch=monkeypatch)
        assert status == 400, payload
        assert "error" in body, payload
        assert calls == [], payload
