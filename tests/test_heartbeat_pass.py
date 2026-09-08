"""The scheduler runs on its own thread, not behind the conversation loop.

`conversations.speak` calls `generate_reply` on its caller's thread and a
claude-cli reply takes minutes; while `run_due_heartbeats` was the last
statement of `poll_once`, one chat message from the owner held the
scheduler for the length of the answer. An anchored schedule only asks
about its most recent occurrence, so a pass that lands a slot late does not
fire late -- it does not fire at all.
"""

import importlib
import threading
import time

import pytest

from agora_runner import heartbeat_pass


@pytest.fixture(autouse=True)
def _no_leftover_thread():
    heartbeat_pass._thread = None
    yield
    heartbeat_pass._thread = None


def test_pass_once_runs_due_heartbeats(monkeypatch):
    calls = []
    monkeypatch.setattr("agora_runner.heartbeats.run_due_heartbeats",
                        lambda *a, **k: calls.append((a, k)))

    assert heartbeat_pass.pass_once() is True
    # No listing argument: this thread fetches its own, which is one cheap
    # GET and keeps it independent of whatever the conversation loop read.
    assert calls == [((), {})]


def test_pass_once_swallows_a_failure_and_says_so(monkeypatch):
    """An exception escaping here would stop every heartbeat in this pod for
    as long as the pod lives, and print nothing."""
    def boom():
        raise RuntimeError("agora is down")

    logged = []
    monkeypatch.setattr("agora_runner.heartbeats.run_due_heartbeats", boom)
    monkeypatch.setattr(heartbeat_pass, "log", logged.append)

    assert heartbeat_pass.pass_once() is False
    assert logged and "agora is down" in logged[0]


def test_loop_starts_nothing_once_shutdown_is_requested(monkeypatch):
    """The drain contract: a signalled pod must start no new heartbeat run,
    because that run would be SIGKILLed part-way through a cycle. The flag
    is therefore read BEFORE the first pass, not after it."""
    calls = []
    monkeypatch.setattr(heartbeat_pass, "pass_once", lambda: calls.append(1))

    heartbeat_pass._loop(lambda: True)

    assert calls == []


def test_loop_keeps_passing_until_it_is_told_to_stop(monkeypatch):
    calls = []
    monkeypatch.setattr(heartbeat_pass, "INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(heartbeat_pass, "pass_once", lambda: calls.append(1))

    heartbeat_pass._loop(lambda: len(calls) >= 3)

    assert len(calls) == 3


def test_start_heartbeat_pass_runs_passes_on_a_daemon_thread(monkeypatch):
    passed = threading.Event()
    stop = threading.Event()
    monkeypatch.setattr(heartbeat_pass, "INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(heartbeat_pass, "pass_once", lambda: passed.set())

    thread = heartbeat_pass.start_heartbeat_pass(stop.is_set)
    try:
        assert passed.wait(5), "the scheduler thread never took a pass"
        assert thread.daemon, "a non-daemon thread would hold the process open"
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()


def test_start_heartbeat_pass_does_not_start_a_second_thread(monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(heartbeat_pass, "INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(heartbeat_pass, "pass_once", lambda: None)

    first = heartbeat_pass.start_heartbeat_pass(stop.is_set)
    try:
        second = heartbeat_pass.start_heartbeat_pass(stop.is_set)
        # Two schedulers in one process is two runs for one slot: the
        # in-flight registry and the spawn marks are plain dicts with no lock.
        assert second is first
    finally:
        stop.set()
        first.join(5)


def test_main_starts_the_scheduler_with_its_own_shutdown_flag(monkeypatch):
    """Wiring, not behaviour -- but if `main` passes the wrong predicate the
    drain silently starts cycles it is about to kill."""
    main_mod = importlib.import_module("agora_runner.main")

    given = []
    monkeypatch.setattr(main_mod, "init_tracing", lambda _n: None)
    monkeypatch.setattr(main_mod, "start_invoke_server", lambda: None)
    monkeypatch.setattr(main_mod, "start_catalog_refresh", lambda: None)
    monkeypatch.setattr(main_mod, "start_heartbeat_pass", lambda s: given.append(s))
    monkeypatch.setattr(main_mod, "poll_once", lambda: None)
    monkeypatch.setattr(main_mod, "_sleep_between_ticks", lambda _s: None)
    monkeypatch.setattr(main_mod, "_drain_and_exit", lambda: None)
    monkeypatch.setattr(main_mod.signal, "signal", lambda *a: None)

    monkeypatch.setattr(main_mod, "_shutdown_requested", True)
    main_mod.main()

    assert given == [main_mod.shutdown_requested]


def test_default_interval_is_the_poll_interval():
    """The cadence the coupled version was supposed to have, and now the one
    that actually happens."""
    from agora_runner.config import POLL_INTERVAL_SECONDS

    assert heartbeat_pass.INTERVAL_SECONDS == float(POLL_INTERVAL_SECONDS)


def test_a_slow_conversation_tick_no_longer_delays_a_firing(monkeypatch):
    """The end-to-end shape of the bug, with the minutes compressed.

    `poll_once` blocks in `poll_conversation` the way a real claude-cli
    reply does. Before this module the scheduler sat after that call and
    took no pass at all while it ran; now it takes several.
    """
    poll_mod = importlib.import_module("agora_runner.poll")
    stop = threading.Event()
    passes = []

    monkeypatch.setattr(heartbeat_pass, "INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(heartbeat_pass, "pass_once", lambda: passes.append(time.monotonic()))
    monkeypatch.setattr(poll_mod, "clear_persona_cache", lambda: None)
    monkeypatch.setattr(poll_mod, "agora_get",
                        lambda _p: (200, {"conversations": [{"id": "chat", "name": "Chat"}]}))
    monkeypatch.setattr(poll_mod, "agora_internal", lambda *a, **k: (200, {"heartbeats": []}))
    monkeypatch.setattr(poll_mod, "prune_message_window_cache", lambda _c: None)
    monkeypatch.setattr(poll_mod, "workflow_bound_conversation_ids", lambda _h: set())
    monkeypatch.setattr(poll_mod, "cycle_bound_conversation_ids", lambda _h, _c: set())
    monkeypatch.setattr(poll_mod, "in_flight_cycle_conversation_ids", lambda _h: set())
    monkeypatch.setattr(poll_mod, "poll_conversation", lambda _s: time.sleep(0.5) or True)
    monkeypatch.setattr(poll_mod, "mark_answered_live", lambda _s: None)

    thread = heartbeat_pass.start_heartbeat_pass(stop.is_set)
    try:
        poll_mod.poll_once()
        during = len(passes)
    finally:
        stop.set()
        thread.join(5)

    assert during >= 2, (
        f"the scheduler took {during} pass(es) while one reply was generating")
