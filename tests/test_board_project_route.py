"""The owner moving a boarded row to a project from the app.

His capture, 2026-09-01, rated 🔴 Immediately: *"I/you should easily be
able to assign issues and ideas to projects, and change project if
assigned wrongly ... I/you should easily be able to create new
projects."* `set_row_project` and `tools.board_project` already existed;
what did not was any route the app could reach, so every `Project` cell
on both boards had been written by a cycle at a shell.

`tests/test_board_project.py` covers `set_row_project` itself. This file
covers the two layers above it: the vault write path and the HTTP
validation, which is where a route can put arbitrary text into a cell of
his file.
"""

import json

import agora_runner.nova_capture as nova_capture
from agora_runner.nova_boards import parse_board

BOARD = """---
type: board
---

## Board

| # | Item | Status | Updated | Priority | Project |
|---|------|--------|---------|---|---|
| [[#57 — More pages\\|57]] | More pages | 🟡 In progress | 08-11 | 🔵 Medium | Nova |
| [[#59 — Small pickings\\|59]] | Small pickings | ⚪ Backlog | 08-11 |
| [[#76 — Already finished\\|76]] | Already finished | ✅ Done | 08-14 | | Nova |

## #57 — More pages

Body text I must not touch.
"""


def _writer(monkeypatch, body=BOARD):
    seen = {}
    calls = []
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (body, "7-abc"))

    def fake_write(path, text, if_rev=None):
        calls.append(path)
        seen.update(path=path, body=text, if_rev=if_rev)
        return "written"

    monkeypatch.setattr(nova_capture, "vault_write_path", fake_write)
    return seen, calls


def test_set_project_writes_once_with_the_revision_it_read(monkeypatch):
    seen, calls = _writer(monkeypatch)
    ok, message = nova_capture.set_project("issues", 57, "Marcus")
    assert ok and "#57" in message
    assert len(calls) == 1
    assert seen["if_rev"] == "7-abc"
    row = [i for i in parse_board(seen["body"])["items"] if i["number"] == 57][0]
    assert row["project"] == "Marcus"
    # The rest of the row is the row, not a re-render of it.
    assert row["title"] == "More pages" and row["priority"] == "🔵 Medium"
    assert "Body text I must not touch." in seen["body"]


def test_a_name_no_row_carries_is_how_a_project_is_created(monkeypatch):
    """The create half of his ask, and it needs no second document.

    `board_projects` reads the project list back off the cells, so
    writing a name nothing else uses *is* creating the project. This test
    is the one that would fail if a later cycle added an allowed-projects
    list to the route.
    """
    seen, _ = _writer(monkeypatch)
    ok, _message = nova_capture.set_project("ideas", 57, "Infra")
    assert ok
    from agora_runner.nova_boards import board_projects
    assert "Infra" in board_projects(parse_board(seen["body"])["items"])


def test_set_project_does_not_write_a_name_that_would_break_the_cell(monkeypatch):
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (BOARD, "7-abc"))

    def refuse(*a, **k):
        raise AssertionError("must not write")

    monkeypatch.setattr(nova_capture, "vault_write_path", refuse)
    for bad in ("a|b", "**bold", "two\nlines", "   ", "x" * 41):
        ok, message = nova_capture.set_project("issues", 57, bad)
        assert not ok, bad
        assert "not a row" in message or "could not write" in message


def test_set_project_refuses_an_unknown_target(monkeypatch):
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (BOARD, "7-abc"))
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
