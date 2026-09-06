"""An answer he typed into a cycle's own thread clears that cycle's ask.

His issue #165's open half. The failure these pin is not "the wrong cycle
comes back" -- it is the two directions of being wrong, which are not
symmetric: an ask shown after he answered costs him a scroll, an ask hidden
before he answered costs him the question. Every unreadable case below
therefore asserts the ask stays open.
"""

import pytest

from agora_runner import nova_chat_answers
from agora_runner.nova_chat_answers import (
    answered_in_chat, cycle_threads, spoke)


def conv(cid, name, cycle_thread=True):
    return {"id": cid, "name": name, "cycleThread": cycle_thread}


def listing(*rows):
    return {"conversations": list(rows)}


def msg(sender, text="hi", partial=False):
    return {"sender": sender, "text": text, "partial": partial}


def test_a_message_from_him_is_an_answer():
    assert spoke({"messages": [msg("Nova"), msg("Edvard")]}) is True


def test_the_cycles_own_messages_are_not_an_answer():
    """The thread is full of them -- every cycle replies at the end of its
    run, so reading any message as an answer would clear every ask."""
    assert spoke({"messages": [msg("Nova"), msg("Nova")]}) is False


def test_narration_is_not_an_answer_even_if_it_is_attributed_to_him():
    """`partial` rows are the tool chips and the passages a cycle streams
    while it works. They are never his today; the flag is checked so a row
    that gains a sender later cannot start clearing asks."""
    assert spoke({"messages": [msg("Edvard", partial=True)]}) is False


def test_an_empty_or_missing_thread_is_not_an_answer():
    assert spoke({"messages": []}) is False
    assert spoke({}) is False
    assert spoke(None) is False


def test_cycle_threads_reads_the_number_off_the_name():
    """The `evolve-cycle:` tag carries the heartbeat id, not the number --
    the name is the only place the cycle number is written down."""
    assert cycle_threads(listing(conv("c1", "Nova — Cycle 1068"))) == {1068: ["c1"]}


def test_a_thread_he_started_himself_is_not_a_cycle_thread():
    """`cycleThread` is the site's flag for a thread a heartbeat opened. A
    conversation he named after a cycle is his own chat, and a message in
    it is not an answer to that cycle's ask."""
    rows = listing(conv("c1", "About Cycle 1068", cycle_thread=False))
    assert cycle_threads(rows) == {}


def test_a_name_with_no_number_is_skipped_rather_than_guessed_at():
    assert cycle_threads(listing(conv("c1", "Nova — Questions"))) == {}


def test_two_conversations_can_name_one_cycle():
    """Six cycles have written a second entry, and a rotation can leave two
    threads carrying one number. He only has to have spoken in one."""
    rows = listing(conv("a", "Nova — Cycle 900"), conv("b", "Nova — Cycle 900"))
    assert cycle_threads(rows) == {900: ["a", "b"]}
    reads = {"a": {"messages": [msg("Nova")]}, "b": {"messages": [msg("Edvard")]}}
    assert answered_in_chat([900], rows, reads.__getitem__) == [900]


def test_only_the_cycles_asked_about_are_fetched():
    """One Agora round trip per *open ask*, not per cycle in the store."""
    rows = listing(conv("a", "Nova — Cycle 1"), conv("b", "Nova — Cycle 2"))
    asked = []

    def read(cid):
        asked.append(cid)
        return {"messages": [msg("Edvard")]}

    assert answered_in_chat([2], rows, read) == [2]
    assert asked == ["b"]


def test_a_cycle_with_no_thread_stays_open():
    """The oldest asks predate the conversation window, and a cycle whose
    thread has been deleted has no chat to have answered in."""
    assert answered_in_chat([77], listing(), lambda cid: None) == []


def test_an_unreadable_thread_leaves_the_ask_open(monkeypatch):
    """The safe direction. A failed fetch must not read as an answer."""
    logged = []
    monkeypatch.setattr(nova_chat_answers, "log", logged.append)
    rows = listing(conv("a", "Nova — Cycle 5"))

    def boom(cid):
        raise RuntimeError("conversation fetch returned 502")

    assert answered_in_chat([5], rows, boom) == []
    assert any("unreadable" in line for line in logged), logged


def test_a_second_thread_is_still_tried_after_the_first_one_fails():
    """The precondition this asserts is that there really were two: without
    it the test passes on a version that gives up on the first error."""
    rows = listing(conv("a", "Nova — Cycle 5"), conv("b", "Nova — Cycle 5"))
    assert cycle_threads(rows)[5] == ["a", "b"]

    def read(cid):
        if cid == "a":
            raise RuntimeError("nope")
        return {"messages": [msg("Edvard")]}

    assert answered_in_chat([5], rows, read) == [5]


def test_the_answer_is_ascending_and_deduplicated():
    rows = listing(conv("a", "Nova — Cycle 9"), conv("b", "Nova — Cycle 4"))
    reads = {
        "a": {"messages": [msg("Edvard")]},
        "b": {"messages": [msg("Edvard")]},
    }
    assert answered_in_chat([9, 4, 9], rows, reads.__getitem__) == [4, 9]


def test_a_cycle_he_has_not_spoken_in_is_not_returned():
    rows = listing(conv("a", "Nova — Cycle 9"))
    reads = {"a": {"messages": [msg("Nova"), msg("Nova", partial=True)]}}
    assert answered_in_chat([9], rows, reads.__getitem__) == []


@pytest.mark.parametrize("bad", [None, {}, {"conversations": None}])
def test_a_missing_listing_is_no_answers_rather_than_a_crash(bad):
    assert cycle_threads(bad) == {}
    assert answered_in_chat([1], bad, lambda cid: None) == []
