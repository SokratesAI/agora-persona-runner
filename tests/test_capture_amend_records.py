"""The capture box's Edit and Delete on his two boards write the #203 record
store, not his markdown.

`nova_capture.amend` read-modified-wrote `issues.md`/`ideas.md` for every
edit of one of his bullets. Those two boards live in the record store now, so
the edit is `board_write.change_capture_text` on the capture's document and
the delete is `board_store.delete_capture`; only `notes`, which has no
records, still goes to the file.
"""

import pytest

import agora_runner.nova_capture as nova_capture
from agora_runner import board_records, board_store
from agora_runner.nova_capture import STALE_CAPTURE, amend

# The fixture board carries two captures, and the first has a reply under it.
FIRST = "His first capture"
SECOND = "His second capture"
REPLY = "Nova, cycle 1: answered."


def _records(monkeypatch):
    """A fake record store holding the fixture as the issues board, and a vault
    that must not be touched -- Edit and Delete write the records."""
    from tests.test_board_records import writable

    _, fake = writable(board="issue")
    monkeypatch.setattr(nova_capture, "board_store", fake)

    def landmine(*a, **k):
        raise AssertionError("the capture edit touched the markdown")

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", landmine)
    monkeypatch.setattr(nova_capture, "vault_write_path", landmine)
    return fake


def _captures(store):
    held = board_records.contents("issue", store=store)
    return held["captures"], held["captureReplies"]


def test_an_edit_rewrites_his_words_and_keeps_the_reply(monkeypatch):
    store = _records(monkeypatch)
    ok, message = amend("issues", 0, FIRST, "His first capture, reworded")
    assert ok, message
    assert message == "edited in issues"
    captures, replies = _captures(store)
    assert captures == ["His first capture, reworded", SECOND]
    assert replies[0] == [REPLY], "an edit must not lose the answer under it"


def test_the_edit_lands_on_the_bullet_he_pointed_at(monkeypatch):
    store = _records(monkeypatch)
    ok, message = amend("issues", 1, SECOND, "the second, reworded")
    assert ok, message
    assert _captures(store)[0] == [FIRST, "the second, reworded"]


def test_a_delete_removes_the_bullet_and_its_replies(monkeypatch):
    store = _records(monkeypatch)
    ok, message = amend("issues", 0, FIRST, "")
    assert ok, message
    assert message == "deleted in issues"
    captures, replies = _captures(store)
    assert captures == [SECOND]
    assert REPLY not in [line for thread in replies for line in thread]


def test_words_that_moved_are_stale_and_nothing_is_written(monkeypatch):
    """The position says which bullet, the words say it has not moved."""
    store = _records(monkeypatch)
    ok, message = amend("issues", 0, SECOND, "x")
    assert not ok
    assert STALE_CAPTURE in message
    assert store.calls == []


def test_a_position_past_the_end_is_stale(monkeypatch):
    store = _records(monkeypatch)
    ok, message = amend("issues", 5, FIRST, "")
    assert not ok
    assert STALE_CAPTURE in message
    assert store.calls == []


def test_a_second_delete_that_finds_it_gone_is_stale_not_a_failure(monkeypatch):
    """A double tap: the capture was there when read and gone at the delete."""
    store = _records(monkeypatch)
    monkeypatch.setattr(store, "delete_capture", lambda doc: False)
    ok, message = amend("issues", 0, FIRST, "")
    assert not ok
    assert STALE_CAPTURE in message


def test_an_unchanged_save_succeeds_without_a_write(monkeypatch):
    """`change_capture_text` refuses a no-op; the Save button must not 502."""
    store = _records(monkeypatch)
    ok, message = amend("issues", 0, FIRST, FIRST)
    assert ok, message
    assert store.calls == []


def test_a_conflict_is_retried_against_a_fresh_read(monkeypatch):
    """A reply landing between the read and the write is a lost race, and the
    retry re-reads rather than resending the old revision."""
    store = _records(monkeypatch)
    real = store.write_capture
    attempts = []

    def racing(doc):
        attempts.append(doc["_rev"])
        if len(attempts) == 1:
            raise board_store.CaptureConflict("409 conflict")
        return real(doc)

    monkeypatch.setattr(store, "write_capture", racing)
    ok, message = amend("issues", 0, FIRST, "reworded after a race")
    assert ok, message
    assert len(attempts) == 2
    assert _captures(store)[0][0] == "reworded after a race"


def test_a_conflict_that_loses_to_a_boarding_does_not_resurrect_it(monkeypatch):
    """The retry's re-read no longer finds his words: a cycle boarded the
    bullet in between. Nothing is written back."""
    store = _records(monkeypatch)

    def boarded_meanwhile(doc):
        store.docs = [held for held in store.docs
                      if held.get("captureId") != doc["captureId"]]
        raise board_store.CaptureConflict("409 conflict")

    monkeypatch.setattr(store, "write_capture", boarded_meanwhile)
    ok, message = amend("issues", 0, FIRST, "reworded")
    assert not ok
    assert STALE_CAPTURE in message
    assert FIRST not in _captures(store)[0]


def test_a_multi_line_edit_is_refused_rather_than_half_written(monkeypatch):
    store = _records(monkeypatch)
    ok, message = amend("issues", 0, FIRST, "one\ntwo")
    assert not ok
    assert "one bullet" in message
    assert store.calls == []


def test_an_unreadable_store_is_a_failure_not_stale(monkeypatch):
    store = _records(monkeypatch)
    store.registry = {}
    ok, message = amend("issues", 0, FIRST, "x")
    assert not ok
    assert message.startswith("could not read issues")
    assert STALE_CAPTURE not in message


@pytest.mark.parametrize("target", ["issues", "ideas"])
def test_both_boards_route_to_the_records(monkeypatch, target):
    seen = []
    monkeypatch.setattr(
        nova_capture, "_amend_records",
        lambda t, board, *a: seen.append((t, board)) or (True, "ok"))
    monkeypatch.setattr(nova_capture, "vault_read_path_rev",
                        lambda *a: pytest.fail("touched the markdown"))
    assert amend(target, 0, "x", "y") == (True, "ok")
    assert seen == [(target, nova_capture.RECORD_BOARDS[target])]
