"""The draining process keeps answering conversations while a cycle finishes.

Issue #130: `Recreate` plus a 2880s termination grace means that from the
moment a redeploy lands there is no other persona runner anywhere, and the
drain used to spend that whole wait answering nobody.
"""

import importlib

import pytest

# `from agora_runner import main` hands back the *function* main(), because
# agora_runner/__init__ re-exports it -- importlib is the only way to the module.
main_mod = importlib.import_module("agora_runner.main")
hb_mod = importlib.import_module("agora_runner.heartbeats")


class _FakeThread:
    def __init__(self, alive_for):
        self._left = alive_for

    def is_alive(self):
        return self._left > 0

    def tick(self):
        self._left -= 1

    def join(self):
        self._left = 0


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(main_mod.time, "sleep", lambda _s: None)


@pytest.fixture
def _threads(monkeypatch):
    store = {}
    monkeypatch.setattr(hb_mod, "_heartbeat_threads", store)
    return store


def test_running_heartbeat_count_counts_only_live_threads(_threads):
    _threads["a"] = [_FakeThread(1), _FakeThread(0)]
    _threads["b"] = [_FakeThread(2)]
    assert hb_mod.running_heartbeat_count() == 2


def test_drain_polls_conversations_until_the_cycle_finishes(monkeypatch, _threads):
    cycle = _FakeThread(3)
    _threads["nova"] = [cycle]
    calls = []

    def fake_poll(start_heartbeats=True):
        calls.append(start_heartbeats)
        cycle.tick()

    monkeypatch.setattr(main_mod, "poll_once", fake_poll)
    main_mod._serve_while_draining()

    assert calls, "a drain with a cycle still running must keep polling"
    # No new cycle may be started by a process that is shutting down.
    assert calls == [False, False, False]


def test_drain_with_nothing_in_flight_polls_nothing(monkeypatch, _threads):
    calls = []
    monkeypatch.setattr(
        main_mod, "poll_once", lambda start_heartbeats=True: calls.append(start_heartbeats)
    )
    main_mod._serve_while_draining()
    assert calls == [], "an idle pod must exit at once, not hold the replacement out"


def test_drain_does_not_sleep_after_the_last_cycle_ends(monkeypatch, _threads):
    cycle = _FakeThread(1)
    _threads["nova"] = [cycle]
    slept = []
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(
        main_mod, "poll_once", lambda start_heartbeats=True: cycle.tick()
    )
    main_mod._serve_while_draining()
    assert slept == [], "sleeping past the last cycle holds the new pod out for nothing"


def test_drain_survives_a_failing_tick(monkeypatch, _threads):
    cycle = _FakeThread(2)
    _threads["nova"] = [cycle]
    calls = []

    def boom(start_heartbeats=True):
        calls.append(start_heartbeats)
        cycle.tick()
        raise RuntimeError("agora is down")

    monkeypatch.setattr(main_mod, "poll_once", boom)
    main_mod._serve_while_draining()  # must not propagate
    assert len(calls) == 2


def test_poll_once_skips_heartbeats_when_asked(monkeypatch):
    poll_mod = importlib.import_module("agora_runner.poll")

    started = []
    monkeypatch.setattr(poll_mod, "clear_persona_cache", lambda: None)
    monkeypatch.setattr(poll_mod, "agora_get", lambda _p: (200, {"conversations": []}))
    monkeypatch.setattr(poll_mod, "agora_internal", lambda *a, **k: (200, {"heartbeats": []}))
    monkeypatch.setattr(poll_mod, "prune_message_window_cache", lambda _c: None)
    monkeypatch.setattr(poll_mod, "workflow_bound_conversation_ids", lambda _h: set())
    monkeypatch.setattr(poll_mod, "cycle_bound_conversation_ids", lambda _h, _c: set())
    monkeypatch.setattr(poll_mod, "in_flight_cycle_conversation_ids", lambda _h: set())
    monkeypatch.setattr(poll_mod, "run_due_heartbeats", lambda h: started.append(h))

    poll_mod.poll_once(start_heartbeats=False)
    assert started == []
    poll_mod.poll_once()
    assert started == [[]], "the ordinary tick must still start due heartbeats"
