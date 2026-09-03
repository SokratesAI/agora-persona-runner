"""`tools.ticket_migrate --write` -- the read-back is the check, not the count.

`store()` is what decides whether a board really made it into CouchDB, and
the failure it exists to catch is the quiet one: a bulk write that reports
`ok` for every document while one of them lost a field, so the board no
longer renders. A tool that counted documents would call that a success.
These tests pin the three verdicts apart.
"""

import pytest

from agora_runner import ticket_store
from tools import ticket_migrate

from tests.test_ticket_store import BOARD


PATH = "projects/sokrates/projects/nova/issues.md"


@pytest.fixture
def records():
    return ticket_store.to_records(BOARD)


def _wire(monkeypatch, rendered, summary=None, ensure=(True, 201)):
    monkeypatch.setattr(ticket_migrate.ticket_docs, "ensure_database", lambda: ensure)
    monkeypatch.setattr(
        ticket_migrate.ticket_docs, "write_board",
        lambda path, recs: summary or {"written": 4, "deleted": 0, "failures": []})
    monkeypatch.setattr(
        ticket_migrate.ticket_docs, "render_from_couch", lambda path: rendered)


def test_an_identical_read_back_is_clean(monkeypatch, records):
    _wire(monkeypatch, BOARD)
    assert ticket_migrate.store(PATH, BOARD, records) == 0


def test_a_read_back_that_lost_a_word_exits_2(monkeypatch, records):
    _wire(monkeypatch, BOARD.replace("A third thing", "A third"))
    assert ticket_migrate.store(PATH, BOARD, records) == 2


def test_padding_only_on_the_read_back_does_not_raise(monkeypatch, records):
    # The one difference his live `issues.md` has: an empty cell written
    # `| |` on one row and `|  |` on the other 397. No character of his
    # text moves, so it must not read as data loss.
    _wire(monkeypatch, BOARD.replace("| ✅ Done | 09-01 |  |", "| ✅ Done | 09-01 | |"))
    assert ticket_migrate.store(PATH, BOARD, records) == 0


def test_a_refused_document_exits_2_even_when_the_render_matches(monkeypatch, records):
    _wire(monkeypatch, BOARD,
          summary={"written": 4, "deleted": 0,
                   "failures": [{"id": "ticket:x:1", "error": "conflict"}]})
    assert ticket_migrate.store(PATH, BOARD, records) == 2


def test_a_database_that_cannot_be_created_is_not_clean(monkeypatch, records):
    _wire(monkeypatch, BOARD, ensure=(False, "401 unauthorized"))
    assert ticket_migrate.store(PATH, BOARD, records) == 1


def test_a_couch_error_is_reported_as_unreadable_not_as_damage(monkeypatch, records):
    def boom(path, recs):
        raise RuntimeError("writing: 503")

    monkeypatch.setattr(ticket_migrate.ticket_docs, "ensure_database", lambda: (True, 201))
    monkeypatch.setattr(ticket_migrate.ticket_docs, "write_board", boom)
    assert ticket_migrate.store(PATH, BOARD, records) == 1
