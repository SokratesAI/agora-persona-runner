"""`/api/cycle-threads` -- which cycles still have a live chat thread.

Idea #182: the chat bubble on a journal card should open the cycle's own
heartbeat conversation rather than the comment box. The card cannot know
the conversation id -- the listing lives in Agora -- so the site hands it
down. The rule for what counts as a cycle thread is `cycle_threads`, tested
next door; what is here is the wiring and the failure direction.
"""

from agora_runner import nova_site


def test_it_maps_a_cycle_number_to_its_thread_id(monkeypatch):
    nova_site.reset_cache()
    monkeypatch.setattr(nova_site, "conversation_list", lambda: {"conversations": [
        {"id": "a", "name": "Nova — Cycle 11", "cycleThread": True},
        {"id": "b", "name": "Nova — Cycle 12", "cycleThread": True},
    ]})
    assert nova_site.cycle_threads_payload() == {"cycles": {"11": "a", "12": "b"}}


def test_a_thread_he_started_himself_is_not_a_cycles_thread(monkeypatch):
    """`cycleThread` is the site's own flag for a heartbeat's thread. Without
    it the bubble would open a conversation he named after a cycle, which is
    not where that cycle's ask went out."""
    nova_site.reset_cache()
    monkeypatch.setattr(nova_site, "conversation_list", lambda: {"conversations": [
        {"id": "a", "name": "Cycle 11 notes", "cycleThread": False},
    ]})
    assert nova_site.cycle_threads_payload() == {"cycles": {}}


def test_two_threads_for_one_cycle_yield_the_first(monkeypatch):
    """Six cycles have written two entries and two conversations can name
    one number, but a button opens exactly one thread. Listing order is
    newest-message-first, so the first is the one with something in it."""
    nova_site.reset_cache()
    monkeypatch.setattr(nova_site, "conversation_list", lambda: {"conversations": [
        {"id": "newer", "name": "Nova — Cycle 11", "cycleThread": True},
        {"id": "older", "name": "Nova — Cycle 11", "cycleThread": True},
    ]})
    assert nova_site.cycle_threads_payload() == {"cycles": {"11": "newer"}}


def test_an_unreadable_listing_costs_the_shortcut_not_the_page(monkeypatch):
    """The failure direction. An empty map leaves every card on the comment
    box, which is the button that worked before this existed; raising here
    would take the whole journal feed down with Agora."""
    nova_site.reset_cache()

    def boom():
        raise RuntimeError("agora is down")

    monkeypatch.setattr(nova_site, "conversation_list", boom)
    assert nova_site.cycle_threads_payload() == {"cycles": {}}
