"""A board write in the app also updates the CouchDB ticket store.

Slice 2 loaded all 441 tickets into `nova_tickets` as one document each
and slice 3 built the check that watches them go stale. This is the write
side: the store only stays current if it follows the markdown write that
just happened, and the markdown write lands in exactly one place.

The tests pin the two things a future edit is most likely to break: that
the push happens for a board and *only* after the write actually
succeeded, and that a failure in the store never turns into a failed
board edit. Nothing reads a board from CouchDB yet, so a store outage
must not be able to stop the owner editing his own file.
"""

import pytest

from agora_runner import ticket_docs, vault


BOARD = ticket_docs.BOARDS[0]
NOT_A_BOARD = "projects/sokrates/projects/agora/nova/resources/claims.json"


@pytest.fixture
def pushes(monkeypatch):
    seen = []
    monkeypatch.setattr(ticket_docs, "push_markdown",
                        lambda path, content: seen.append((path, content)))
    return seen


def _put_returns(monkeypatch, result):
    monkeypatch.setattr(
        vault, "_vault_put_raw",
        lambda path, content, if_rev=None, allow_shrink=False: result)


def test_a_successful_board_write_pushes_the_new_markdown(monkeypatch, pushes):
    _put_returns(monkeypatch, "written")
    assert vault.vault_write_path(BOARD, "# board\n") == "written"
    assert pushes == [(BOARD, "# board\n")]


def test_a_failed_board_write_pushes_nothing(monkeypatch, pushes):
    # The store must never hold tickets from a body the vault refused --
    # a collapse refusal and a lost compare-and-swap both land here.
    _put_returns(monkeypatch, "FAILED(409 conflict: changed since it was read)")
    result = vault.vault_write_path(BOARD, "# truncated\n")
    assert result.startswith("FAILED(")
    assert pushes == []


def test_an_ordinary_vault_write_pushes_nothing(monkeypatch, pushes):
    # Every journal entry, digest and note goes through this function too.
    _put_returns(monkeypatch, "written")
    assert vault.vault_write_path(NOT_A_BOARD, "{}") == "written"
    assert pushes == []


def test_a_store_failure_does_not_fail_the_board_write(monkeypatch):
    """The markdown is the source of truth and it has already landed."""
    def explode(path, content):
        raise RuntimeError("nova_tickets is unreachable")

    monkeypatch.setattr(ticket_docs, "push_markdown", explode)
    _put_returns(monkeypatch, "written")
    assert vault.vault_write_path(BOARD, "# board\n") == "written"


def test_the_failure_is_reported_rather_than_swallowed(monkeypatch):
    def explode(path, content):
        raise RuntimeError("nova_tickets is unreachable")

    monkeypatch.setattr(ticket_docs, "push_markdown", explode)
    detail = vault._push_ticket_documents(BOARD, "# board\n")
    assert detail == "FAILED(nova_tickets is unreachable)"
