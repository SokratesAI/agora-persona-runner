"""A draining process waits for its cycles and polls nothing.

Issue #130 asked the opposite question and got the opposite answer, and
both are right for their own deployment strategy. Under `Recreate` the
replacement pod was not created until this one exited, so a drain meant
every persona answered nobody for up to 48 minutes, and the fix was to
keep polling conversations while waiting.

Under `RollingUpdate` (maxSurge 1, maxUnavailable 1) the replacement is
created in the same reconcile that requests this pod's deletion, so a
draining pod that kept polling would be a *second* poller against the
same conversations -- a duplicate reply to the owner rather than a silent
one. The heartbeat half never changed: a shutting-down process must start
no new cycle, because that run would be SIGKILLed part-way.
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


def test_drain_joins_the_in_flight_cycle(monkeypatch, _threads):
    cycle = _FakeThread(3)
    _threads["nova"] = [cycle]
    joined = []
    monkeypatch.setattr(main_mod, "join_running_heartbeats", lambda: joined.append(True))

    main_mod._drain_and_exit()

    assert joined == [True], "the drain must wait for the cycle already running"


def test_drain_polls_nothing(monkeypatch, _threads):
    """The precondition matters: a cycle IS in flight, so there is something
    to wait for. Without it the assertion below passes on any implementation,
    because an idle pod would poll nothing either way."""
    cycle = _FakeThread(3)
    _threads["nova"] = [cycle]
    assert hb_mod.running_heartbeat_count() == 1
    calls = []
    monkeypatch.setattr(
        main_mod, "poll_once", lambda *a, **k: calls.append(a or k or True)
    )
    monkeypatch.setattr(main_mod, "join_running_heartbeats", lambda: None)

    main_mod._drain_and_exit()

    assert calls == [], (
        "the replacement pod is already polling; a second poller double-answers"
    )


def test_drain_does_not_sleep(monkeypatch, _threads):
    _threads["nova"] = [_FakeThread(2)]
    slept = []
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(main_mod, "join_running_heartbeats", lambda: None)

    main_mod._drain_and_exit()

    assert slept == [], "sleeping holds the replacement pod out for nothing"


def test_poll_once_always_starts_due_heartbeats(monkeypatch):
    """There is no conversations-only variant any more. A caller that wants
    a tick without heartbeats must not poll at all."""
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

    poll_mod.poll_once()
    assert started == [[]], "the ordinary tick must start due heartbeats"

    with pytest.raises(TypeError):
        poll_mod.poll_once(start_heartbeats=False)
