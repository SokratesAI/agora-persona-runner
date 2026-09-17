"""A board written from a shell asks the site to redraw his file.

Cycle 1734 found #235 and #312 closed in the records and still open in his
`issues.md` / `ideas.md`: the site redraws the file after its own writes
(`nova_site.invalidate`), and every command-line writer goes through
`board_write`, which the site never imports, so none of those writes ever
asked. The cases pin the three things that matter: a landed write on the real
store asks for its own board, a refused write or a caller's own store asks
nothing, and a redraw that could not be asked for is printed with the command
that does it by hand. The writes are real ones against the fake CouchDB, so a
writer that lost its hook would fail here rather than pass on a stub.
"""
import json

import pytest

from agora_runner import board_publish, board_write, nova_site
from tests.test_board_publish_site import couch, vault  # noqa: F401 -- fixtures
from tests.test_board_records import writable
from tests.test_nova_site import _post

IN_PROGRESS = {"status": "🟡 In progress", "statusKey": "in-progress"}


@pytest.fixture
def asked(monkeypatch):
    calls = []

    def fake(board, timeout=10):
        calls.append(board)
        return True, ""

    monkeypatch.setattr(board_write, "ask_site_to_redraw", fake)
    return calls


def test_a_landed_write_on_his_store_asks_for_that_board(vault, asked):
    board_write.change_row("issue", 1, IN_PROGRESS)

    assert asked == ["issue"]


def test_a_note_asks_once_not_twice(vault, asked):
    """`append_note` writes through `change_row`; one write, one request."""
    board_write.append_note("issue", 1, "checked", "09-17", author="nova")

    assert asked == ["issue"]


def test_a_refused_write_asks_nothing(vault, asked):
    with pytest.raises(board_write.WriteRefused):
        board_write.change_row("issue", 9999, IN_PROGRESS)

    assert asked == []


def test_a_callers_own_store_is_not_his_board(asked):
    _parsed, store = writable()
    board_write.change_row("issue", 42, IN_PROGRESS, store=store)

    assert asked == []


def test_every_writer_that_writes_directly_carries_the_hook():
    for writer in (board_write.change_row, board_write.remove_row,
                   board_write.add_row, board_write.change_capture_text):
        assert getattr(writer, "__wrapped__", None) is not None, writer.__name__


def test_a_redraw_that_was_not_requested_prints_the_command(
        vault, monkeypatch, capsys):
    monkeypatch.setattr(board_write, "ask_site_to_redraw",
                        lambda board, timeout=10: (False, "connection refused"))

    board_write.change_row("issue", 1, IN_PROGRESS)

    err = capsys.readouterr().err
    assert "connection refused" in err
    assert "tools.board_publish --board issue --publish" in err


def test_asking_with_no_network_returns_why_and_never_raises():
    """conftest blocks sockets, which is the unreachable-site case exactly."""
    requested, why = board_write.ask_site_to_redraw("issue", timeout=1)

    assert requested is False
    assert why


def test_the_route_invalidates_the_board_and_reports_the_request(monkeypatch):
    dropped, requested = [], []
    real_invalidate = nova_site.invalidate
    monkeypatch.setattr(nova_site, "invalidate",
                        lambda name: (dropped.append(name), real_invalidate(name)))
    monkeypatch.setattr(board_publish, "request",
                        lambda board: requested.append(board) or True)
    monkeypatch.setattr(board_publish, "running", lambda: True)

    status, _, body = _post("/api/board/redraw", {"board": "idea"})

    assert status == 200
    assert json.loads(body) == {"requested": True}
    assert dropped == ["board:ideas"]
    assert requested == ["idea"]


def test_the_route_says_false_when_no_publisher_runs(monkeypatch):
    monkeypatch.setattr(board_publish, "_publisher", None)

    status, _, body = _post("/api/board/redraw", {"board": "issue"})

    assert status == 200
    assert json.loads(body) == {"requested": False}


@pytest.mark.parametrize("payload", [{}, {"board": "issues"}, {"board": "notes"}])
def test_the_route_refuses_anything_but_a_record_board(payload):
    status, _, body = _post("/api/board/redraw", payload)

    assert status == 400
    assert "board must be one of" in json.loads(body)["error"]
