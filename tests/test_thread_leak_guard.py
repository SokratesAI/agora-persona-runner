"""The leak check in conftest.py is only worth having if it actually fires.

Everything else in this suite is protected by the guard; the guard itself
is protected by nothing, and deleting it would go green. So this runs the
*real* `tests/conftest.py` -- copied, not reimplemented -- over a throwaway
test that leaks a thread on purpose, in a subprocess, and asserts the run
comes back red.

Copied rather than rewritten deliberately. A guard reimplemented beside
its test is the failure `nova_replies.run_once` was split out to fix: the
test drove a copy of the body written next to it and passed under a
mutation that broke the real thing.
"""
import os
import shutil
import subprocess
import sys
import textwrap

CONFTEST = os.path.join(os.path.dirname(__file__), "conftest.py")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LEAKY = """
import threading, time

def test_leaves_a_thread_running():
    threading.Thread(
        target=lambda: time.sleep(60), name="stray-worker", daemon=True
    ).start()


def test_starts_and_joins_a_thread():
    thread = threading.Thread(target=lambda: None, name="tidy-worker")
    thread.start()
    thread.join()
"""

SLOW_REFRESH = """
import time

from agora_runner import nova_site


def _build():
    time.sleep(0.25)
    return {"entries": []}


def test_leaves_a_refresh_running_behind_it():
    # First call has nothing stale to serve, so it builds inline.
    nova_site.cached_payload("journal", _build)
    # Second one serves that payload immediately and rebuilds on a thread,
    # which is the whole point of the cache -- and the thread is still in
    # `_build` when this returns.
    nova_site.CACHE_FRESH_SECONDS = 0
    nova_site.cached_payload("journal", _build)
"""


def _run_pytest_on(tmp_path):
    """Subprocess, not an in-process run: the guard is a session-wide hook
    and installing a second copy of it inside this session would have it
    judging the tests that are already running."""
    env = dict(os.environ, PYTHONPATH=REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path), "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120,
    )


def test_a_leaked_thread_fails_its_own_test(tmp_path):
    """Named after the test that leaked, not the one that ran next.

    That attribution is the whole point: a stray thread does its damage
    while some later test is running, so without this the suite goes red
    somewhere innocent -- or, as it did here for a while, stays green and
    talks to production in the background.
    """
    shutil.copy(CONFTEST, tmp_path / "conftest.py")
    (tmp_path / "test_leaky.py").write_text(textwrap.dedent(LEAKY), encoding="utf-8")

    result = _run_pytest_on(tmp_path)

    assert result.returncode != 0, result.stdout
    assert "test_leaves_a_thread_running" in result.stdout
    assert "stray-worker" in result.stdout
    assert "tidy-worker" not in result.stdout, "a thread that was joined is not a leak"


def test_a_clean_test_is_not_flagged(tmp_path):
    """The other half, and the one that decides whether anyone keeps the
    guard: a check that fires on tests doing nothing wrong gets deleted."""
    shutil.copy(CONFTEST, tmp_path / "conftest.py")
    (tmp_path / "test_clean.py").write_text(
        "def test_does_nothing():\n    pass\n", encoding="utf-8"
    )

    result = _run_pytest_on(tmp_path)

    assert result.returncode == 0, result.stdout


def test_a_background_journal_refresh_is_joined_before_the_test_ends(tmp_path):
    """The `nova-site-*` join in `_clear_nova_site_cache`, pinned without a
    race.

    Reverting that join and running the real suite only goes red about one
    run in five: the refresh there is a mocked read that finishes in
    microseconds, so it usually beats teardown by luck. Luck is not a
    regression test, and in a pipeline that merges on one green run a fix
    caught four times in five is a fix that lands broken one time in five.

    So this makes the race deterministic instead of sampling it. The build
    sleeps for 250ms -- four orders of magnitude longer than the teardown it
    has to outlive -- so with the join the run is green and without it the
    leak check fires every time. Nothing here waits on that 250ms: the join
    is what the test is about, and it returns the moment the build does.
    """
    shutil.copy(CONFTEST, tmp_path / "conftest.py")
    (tmp_path / "test_slow_refresh.py").write_text(textwrap.dedent(SLOW_REFRESH), encoding="utf-8")

    result = _run_pytest_on(tmp_path)

    assert result.returncode == 0, result.stdout
    assert "nova-site-journal" not in result.stdout
