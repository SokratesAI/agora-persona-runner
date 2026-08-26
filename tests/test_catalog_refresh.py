"""The timer that keeps `nova/catalog.md` from being a screenshot.

Step 3 of the IDP roadmap. Two properties are worth protecting and they pull
in opposite directions: the refresher must actually publish, and it must
never be able to take the runner's poll loop down with it.
"""

import threading

from agora_runner import catalog_refresh


def test_refresh_once_publishes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "agora_runner.catalog_build.publish", lambda: (calls.append(1), ("text", 0))[1]
    )

    assert catalog_refresh.refresh_once() is True
    assert calls == [1]


def test_a_failed_publish_is_logged_and_swallowed(monkeypatch):
    """A stale catalog is a much better bargain than a runner that stops
    polling, so nothing in here may raise into the caller."""
    def boom():
        raise RuntimeError("vault said no")

    monkeypatch.setattr("agora_runner.catalog_build.publish", boom)
    logged = []
    monkeypatch.setattr(catalog_refresh, "log", logged.append)

    assert catalog_refresh.refresh_once() is False
    assert "RuntimeError" in logged[0]


def test_a_partial_read_still_counts_as_refreshed(monkeypatch):
    logged = []
    monkeypatch.setattr("agora_runner.catalog_build.publish", lambda: ("text", 1))
    monkeypatch.setattr(catalog_refresh, "log", logged.append)

    assert catalog_refresh.refresh_once() is True
    assert "unreadable" in logged[0]


def test_the_loop_refreshes_repeatedly_and_stops_when_told(monkeypatch):
    """Run the real loop rather than asserting on a sleep. The stop Event is
    the only reason this is testable at all -- without it the thread is a
    daemon nothing can join."""
    monkeypatch.setattr(catalog_refresh, "FIRST_REFRESH_SECONDS", 0.0)
    monkeypatch.setattr(catalog_refresh, "REFRESH_INTERVAL_SECONDS", 0.01)
    done = threading.Event()
    calls = []

    def counted():
        calls.append(1)
        if len(calls) >= 3:
            done.set()
        return True

    monkeypatch.setattr(catalog_refresh, "refresh_once", counted)
    stop = threading.Event()
    catalog_refresh._thread = None
    thread = catalog_refresh.start_catalog_refresh(stop=stop)

    assert done.wait(5), f"only refreshed {len(calls)} time(s)"
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    catalog_refresh._thread = None


def test_starting_twice_does_not_start_a_second_thread(monkeypatch):
    monkeypatch.setattr(catalog_refresh, "FIRST_REFRESH_SECONDS", 30.0)
    catalog_refresh._thread = None
    stop = threading.Event()
    first = catalog_refresh.start_catalog_refresh(stop=stop)
    second = catalog_refresh.start_catalog_refresh(stop=stop)

    assert first is not None
    assert second is None
    stop.set()
    first.join(timeout=5)
    catalog_refresh._thread = None


def test_main_starts_the_refresher(monkeypatch):
    """The wire, not the callee. Cycle 445's finding: when the change is
    "call X from Y", a test that calls X directly asserts nothing about the
    change -- the mutation that catches it is deleting the call site."""
    # `import agora_runner.main` binds the *function* `main`, which
    # `agora_runner/__init__` re-exports over the module of the same name.
    import importlib

    runner_main = importlib.import_module("agora_runner.main")

    started = []
    monkeypatch.setattr(runner_main, "start_catalog_refresh", lambda: started.append(1))
    monkeypatch.setattr(runner_main, "start_invoke_server", lambda: None)
    monkeypatch.setattr(runner_main, "poll_once", lambda: None)
    monkeypatch.setattr(runner_main, "join_running_heartbeats", lambda *a, **k: None)
    # The poll loop runs until shutdown is requested; ask for it immediately.
    monkeypatch.setattr(runner_main, "_shutdown_requested", True)
    monkeypatch.setattr(runner_main.signal, "signal", lambda *a: None)

    runner_main.main()

    assert started == [1]
