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

    assert heartbeat_pass.pass_once() is None
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

    # The exception itself comes back, not a bare `False`: the loop has to
    # record WHICH error, and swallowing used to leave the only copy of that
    # in a log that dies with the container.
    returned = heartbeat_pass.pass_once()
    assert isinstance(returned, RuntimeError) and str(returned) == "agora is down"
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


# --- a pass that raised leaves a record -----------------------------------


def _reset_failures():
    heartbeat_pass._failed_since_healthy = 0


def test_a_failed_pass_is_recorded():
    _reset_failures()
    seen = []
    error = RuntimeError("agora unreachable")
    heartbeat_pass.note_failure(error, record=lambda *a: seen.append(a))
    assert seen == [(error, 1)]


def test_a_healthy_pass_records_nothing_and_resets_the_counter():
    _reset_failures()
    seen = []
    heartbeat_pass.note_failure(RuntimeError("one"), record=lambda *a: seen.append(a))
    assert heartbeat_pass.note_failure(None, record=lambda *a: seen.append(a)) is None
    assert len(seen) == 1
    assert heartbeat_pass._failed_since_healthy == 0
    # The counter really reset: the next failure is the FIRST again, which is
    # a doubling and therefore writes.
    heartbeat_pass.note_failure(RuntimeError("two"), record=lambda *a: seen.append(a))
    assert [a[1] for a in seen] == [1, 1]


def test_a_run_of_failures_writes_at_the_doublings_only():
    _reset_failures()
    seen = []
    for _ in range(9):
        heartbeat_pass.note_failure(RuntimeError("x"), record=lambda *a: seen.append(a))
    assert [a[1] for a in seen] == [1, 2, 4, 8]


def test_lateness_and_failure_are_counted_separately():
    # A punctual scheduler that raises on every pass must still be reported:
    # sharing one counter would let a healthy-pass reset on one silence the
    # other.
    _reset_failures()
    heartbeat_pass._late_since_healthy = 0
    seen = []
    heartbeat_pass.note_failure(RuntimeError("x"), record=lambda *a: seen.append(a))
    heartbeat_pass.note_pass(1.0, 0.1, interval_seconds=5.0, record=lambda *a: seen.append(a))
    heartbeat_pass.note_failure(RuntimeError("x"), record=lambda *a: seen.append(a))
    assert len(seen) == 2  # the two failures; the on-time pass wrote nothing
    assert heartbeat_pass._failed_since_healthy == 2


def test_loop_survives_a_recorder_that_raises(monkeypatch):
    """The guard `pass_once` has, extended to the calls around it.

    `note_pass` and `note_failure` sat outside every guard in this module,
    and this thread is a daemon nothing supervised -- so a `RuntimeError`
    out of `threading.Thread.start` (the process at its thread limit, which
    is when a diagnostic is most likely to fire) ended every heartbeat in
    the Pod for as long as it lived.
    """
    passes = []
    logged = []
    monkeypatch.setattr(heartbeat_pass, "INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(heartbeat_pass, "log", logged.append)
    monkeypatch.setattr(heartbeat_pass, "pass_once", lambda: passes.append(1))

    def boom(*_a, **_k):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(heartbeat_pass, "note_pass", boom)

    heartbeat_pass._loop(lambda: len(passes) >= 3 or len(logged) >= 3)

    # Every pass after the first raise still happened: the loop is alive.
    assert len(logged) == 3, logged
    assert all("can't start new thread" in line for line in logged)


def test_loop_survives_note_failure_raising(monkeypatch):
    """The second bookkeeping call, guarded for the same reason as the first.

    It is reached only when a pass raised, so a scheduler already in trouble
    is exactly the one that would have died here.
    """
    logged = []
    monkeypatch.setattr(heartbeat_pass, "INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(heartbeat_pass, "log", logged.append)
    monkeypatch.setattr(heartbeat_pass, "pass_once", lambda: RuntimeError("agora is down"))

    def boom(*_a, **_k):
        raise ValueError("recorder is broken")

    monkeypatch.setattr(heartbeat_pass, "note_failure", boom)

    heartbeat_pass._loop(lambda: len(logged) >= 2)

    assert len(logged) == 2, logged
    assert all("recorder is broken" in line for line in logged)


def test_a_raising_recorder_costs_the_pass_beside_it_and_nothing_after(monkeypatch):
    """The guard is around the pair, so a raise in `note_pass` costs that
    iteration's pass as well as its record -- and the NEXT iteration runs.

    That is the trade this catch makes and it is the right way round: one
    lost pass is one 5-second beat, and a dead thread is every heartbeat in
    the Pod. Asserted rather than left implicit, because a future guard moved
    inside `note_pass` would change this number and should have to say so.
    """
    passes = []
    iterations = []
    monkeypatch.setattr(heartbeat_pass, "INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(heartbeat_pass, "log", lambda _m: iterations.append(1))
    monkeypatch.setattr(heartbeat_pass, "pass_once", lambda: passes.append(1))
    monkeypatch.setattr(heartbeat_pass, "note_pass",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))

    heartbeat_pass._loop(lambda: len(iterations) >= 2)

    assert len(iterations) == 2, "the loop stopped at the first raise"
    assert passes == []


def test_main_asks_for_the_scheduler_on_every_tick(monkeypatch):
    """`start_heartbeat_pass` is idempotent, so calling it each tick makes the
    poll loop the scheduler's supervisor. Called once before the loop, a
    thread that died stayed dead for the life of the Pod."""
    main_mod = importlib.import_module("agora_runner.main")

    given = []
    ticks = []
    monkeypatch.setattr(main_mod, "init_tracing", lambda _n: None)
    monkeypatch.setattr(main_mod, "start_invoke_server", lambda: None)
    monkeypatch.setattr(main_mod, "start_catalog_refresh", lambda: None)
    monkeypatch.setattr(main_mod, "start_heartbeat_pass", lambda s: given.append(s))
    monkeypatch.setattr(main_mod, "poll_once", lambda: ticks.append(1))
    monkeypatch.setattr(main_mod, "_sleep_between_ticks", lambda _s: None)
    monkeypatch.setattr(main_mod, "_drain_and_exit", lambda: None)
    monkeypatch.setattr(main_mod.signal, "signal", lambda *a: None)

    # Two ticks: shut down only once the loop has been round twice.
    monkeypatch.setattr(main_mod, "_shutdown_requested", False)
    real_sleep = main_mod._sleep_between_ticks

    def stop_after_two(_s):
        if len(ticks) >= 2:
            monkeypatch.setattr(main_mod, "_shutdown_requested", True)

    monkeypatch.setattr(main_mod, "_sleep_between_ticks", stop_after_two)
    main_mod.main()

    assert ticks == [1, 1]
    assert given == [main_mod.shutdown_requested, main_mod.shutdown_requested]
    assert real_sleep is not None
