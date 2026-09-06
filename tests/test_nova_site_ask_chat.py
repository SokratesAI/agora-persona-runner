"""`/api/asks/chat` -- the I/O around `nova_chat_answers`.

The rule is tested next door in `test_nova_chat_answers.py`. What is here
is the wiring: which cycles get asked about, and that a failure costs the
badge rather than the feed.
"""

from agora_runner import nova_site


def journal_with_asks(*cycles):
    return {
        "status": {"asks": [{"cycle": c, "date": "", "time": ""} for c in cycles]},
        "entries": [],
    }


def use_journal(monkeypatch, payload):
    nova_site.reset_cache()
    monkeypatch.setattr(nova_site, "journal_payload", lambda: payload)


def test_it_asks_only_about_the_open_asks(monkeypatch):
    """Not about every cycle in the store: this costs one Agora fetch per
    cycle it names, and the journal has a thousand of them."""
    use_journal(monkeypatch, journal_with_asks(11, 12))
    monkeypatch.setattr(nova_site, "conversation_list", lambda: {"conversations": [
        {"id": "a", "name": "Nova — Cycle 11", "cycleThread": True},
        {"id": "b", "name": "Nova — Cycle 12", "cycleThread": True},
        {"id": "c", "name": "Nova — Cycle 13", "cycleThread": True},
    ]})
    asked = []

    def read(cid):
        asked.append(cid)
        return {"messages": [{"sender": "Edvard", "partial": False}]}

    monkeypatch.setattr(nova_site, "conversation_thread", read)
    assert nova_site.ask_chat_payload() == {"cycles": [11, 12]}
    assert sorted(asked) == ["a", "b"]


def count_listings(monkeypatch):
    """Records the listing calls instead of raising on them.

    Raising was the first version and it pinned nothing: `ask_chat_payload`
    catches every exception on that call by design, so an `AssertionError`
    raised from the fake was swallowed and the test passed against a build
    with the early exit deleted. Caught by mutating the exit away.
    """
    calls = []
    monkeypatch.setattr(nova_site, "conversation_list", lambda: calls.append(1) or {
        "conversations": [{"id": "a", "name": "Nova \u2014 Cycle 1", "cycleThread": True}]})
    monkeypatch.setattr(nova_site, "conversation_thread",
                        lambda cid: {"messages": [{"sender": "Edvard", "partial": False}]})
    return calls


def test_no_open_asks_costs_no_agora_call(monkeypatch):
    """The common case: nothing is waiting on him and this must be free."""
    use_journal(monkeypatch, journal_with_asks())
    calls = count_listings(monkeypatch)
    assert nova_site.ask_chat_payload() == {"cycles": []}
    assert calls == [], "listed conversations with no ask to check"


def test_an_ask_with_no_cycle_number_is_skipped(monkeypatch):
    """`open_asks` already drops these, so reaching the listing at all would
    mean asking Agora about `None`."""
    use_journal(monkeypatch, {"status": {"asks": [{"cycle": None}]}, "entries": []})
    calls = count_listings(monkeypatch)
    assert nova_site.ask_chat_payload() == {"cycles": []}
    assert calls == [], "listed conversations for an ask with no cycle number"


def test_an_unreachable_agora_leaves_every_ask_open(monkeypatch):
    """The safe direction, and the precondition is asserted: there really is
    an open ask here, so an empty answer means "found none", not "asked
    about none"."""
    payload = journal_with_asks(42)
    assert payload["status"]["asks"], "precondition: an ask to be wrong about"
    use_journal(monkeypatch, payload)

    def boom():
        raise RuntimeError("conversation listing returned 502")

    monkeypatch.setattr(nova_site, "conversation_list", boom)
    assert nova_site.ask_chat_payload() == {"cycles": []}
