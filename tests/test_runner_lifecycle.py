"""The runner process's own lifecycle ledger (idea #267).

A `silent` cycle is a conversation created with nothing in it, and the reason
it cannot be diagnosed is that the Pod which was going to speak is gone. These
pin the three properties that make the vault copy worth having: it separates a
process that was asked to stop from one that was killed, the `drained` row is
written on a thread that will still exist when it lands, and nothing here can
stop the poll loop.

Nothing may touch the real vault. Every test patches the writer the module
imports, or the vault functions on `dropped_ticks`, which is where the write
actually looks them up.
"""

import json
import sys
from datetime import datetime, timezone
from unittest.mock import patch


import agora_runner.main
from agora_runner import dropped_ticks, runner_lifecycle

#: `agora_runner/__init__.py` re-exports every public name flat, so
#: `from agora_runner import main` hands back the *function* main() and every
#: patch below would fail on the module attribute. Reach for the module.
main = sys.modules["agora_runner.main"]


AT = datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)


def _row(event, at):
    return {"event": event, "at": at}


class FakeVault:
    """A one-document CouchDB with revisions."""

    def __init__(self, raw=None, rev=None):
        self.raw = raw
        self.rev = rev
        self.writes = []

    def read(self, path):
        return self.raw, self.rev

    def write(self, path, content, if_rev=None, allow_shrink=False):
        self.writes.append({"path": path, "content": content})
        self.raw, self.rev = content, "next"
        return "written"


def test_record_appends_to_its_own_document():
    vault = FakeVault()
    with patch.object(dropped_ticks, "vault_read_path_rev", vault.read), \
         patch.object(dropped_ticks, "vault_write_path", vault.write):
        runner_lifecycle.record("started", now=AT, blocking=True)
    assert len(vault.writes) == 1
    assert vault.writes[0]["path"] == runner_lifecycle.PATH
    # Not the dropped-tick ledger: a reader asks a different question of this
    # document, and one merged list is how a reader starts filtering.
    assert vault.writes[0]["path"] != dropped_ticks.PATH
    stored = json.loads(vault.writes[0]["content"])
    assert stored == [{"event": "started", "at": AT.isoformat()}]


def test_record_is_bounded_by_keep():
    existing = [_row("started", f"2026-09-0{n % 9 + 1}T00:00:00+00:00")
                for n in range(dropped_ticks.KEEP + 5)]
    vault = FakeVault(raw=json.dumps(existing), rev="r1")
    with patch.object(dropped_ticks, "vault_read_path_rev", vault.read), \
         patch.object(dropped_ticks, "vault_write_path", vault.write):
        runner_lifecycle.record("signal", now=AT, blocking=True)
    stored = json.loads(vault.writes[0]["content"])
    assert len(stored) == dropped_ticks.KEEP
    assert stored[-1]["event"] == "signal"


def test_a_write_that_raises_does_not_reach_the_caller():
    """The whole point of the swallow: a diagnostic must not kill main()."""
    def boom(path):
        raise RuntimeError("couchdb is down")

    with patch.object(dropped_ticks, "vault_read_path_rev", boom):
        assert runner_lifecycle.record("started", now=AT, blocking=True) is None


def test_blocking_record_writes_before_it_returns():
    """`drained` cannot be backgrounded -- the interpreter is about to exit."""
    vault = FakeVault()
    with patch.object(dropped_ticks, "vault_read_path_rev", vault.read), \
         patch.object(dropped_ticks, "vault_write_path", vault.write):
        result = runner_lifecycle.record("drained", now=AT, blocking=True)
    assert result is None
    assert vault.writes, "the row must be on disk by the time record() returns"


def test_non_blocking_record_hands_back_the_thread():
    vault = FakeVault()
    with patch.object(dropped_ticks, "vault_read_path_rev", vault.read), \
         patch.object(dropped_ticks, "vault_write_path", vault.write):
        thread = runner_lifecycle.record("started", now=AT)
        assert thread is not None
        thread.join(timeout=5)
    assert vault.writes


def test_detail_is_omitted_when_absent():
    vault = FakeVault()
    with patch.object(dropped_ticks, "vault_read_path_rev", vault.read), \
         patch.object(dropped_ticks, "vault_write_path", vault.write):
        runner_lifecycle.record("signal", now=AT, detail="signal 15", blocking=True)
    stored = json.loads(vault.writes[0]["content"])
    assert stored[0]["detail"] == "signal 15"


# --- lives(): the reading half -------------------------------------------

def test_a_start_with_no_signal_before_it_is_no_signal():
    """The verdict a silent cycle is made of: nobody asked this pod to stop."""
    rows = [_row("started", "2026-09-07T09:00:00+00:00"),
            _row("started", "2026-09-07T10:00:00+00:00")]
    lives = runner_lifecycle.lives(rows)
    assert [life["verdict"] for life in lives] == ["no_signal", "running"]


def test_signal_then_drained_is_clean():
    rows = [_row("started", "2026-09-07T09:00:00+00:00"),
            _row("signal", "2026-09-07T09:30:00+00:00"),
            _row("drained", "2026-09-07T09:40:00+00:00"),
            _row("started", "2026-09-07T09:41:00+00:00")]
    assert [life["verdict"] for life in runner_lifecycle.lives(rows)] == [
        "clean", "running"]


def test_signal_with_no_drained_is_killed_mid_drain():
    """Asked to stop and SIGKILLed at grace expiry -- a different bug from
    never being asked, and until this ledger they left identical traces."""
    rows = [_row("started", "2026-09-07T09:00:00+00:00"),
            _row("signal", "2026-09-07T09:30:00+00:00"),
            _row("started", "2026-09-07T10:00:00+00:00")]
    assert [life["verdict"] for life in runner_lifecycle.lives(rows)] == [
        "killed_mid_drain", "running"]


def test_the_newest_life_is_never_judged():
    """It is this process. A check that judged it would call every runner
    un-drained forever."""
    rows = [_row("started", "2026-09-07T09:00:00+00:00")]
    assert [life["verdict"] for life in runner_lifecycle.lives(rows)] == ["running"]


def test_rows_before_the_first_start_are_dropped():
    """The ledger is capped, so its oldest life is routinely half a life --
    a `drained` with no `started` must not invent one."""
    rows = [_row("drained", "2026-09-07T08:00:00+00:00"),
            _row("started", "2026-09-07T09:00:00+00:00")]
    lives = runner_lifecycle.lives(rows)
    assert len(lives) == 1
    assert lives[0]["started"]["at"] == "2026-09-07T09:00:00+00:00"


def test_a_second_signal_in_one_life_does_not_overwrite_the_first():
    """Kubernetes re-sends SIGTERM; the first one is when the drain began."""
    rows = [_row("started", "2026-09-07T09:00:00+00:00"),
            _row("signal", "2026-09-07T09:30:00+00:00"),
            _row("signal", "2026-09-07T09:35:00+00:00"),
            _row("started", "2026-09-07T10:00:00+00:00")]
    lives = runner_lifecycle.lives(rows)
    assert lives[0]["signal"]["at"] == "2026-09-07T09:30:00+00:00"


def test_lives_of_nothing_is_nothing():
    assert runner_lifecycle.lives([]) == []
    assert runner_lifecycle.lives(None) == []


# --- main(): the three call sites ----------------------------------------

def test_main_records_started_before_it_polls():
    """The row has to land before the first tick, or a pod killed in its
    first minute leaves the same nothing this is built to end."""
    calls = []
    with patch.object(main, "runner_lifecycle") as ledger, \
         patch.object(main, "init_tracing"), \
         patch.object(main, "start_invoke_server"), \
         patch.object(main, "start_catalog_refresh"), \
         patch.object(main, "start_heartbeat_pass"), \
         patch.object(main, "join_running_heartbeats"), \
         patch.object(main, "poll_once", side_effect=lambda: calls.append("poll")), \
         patch.object(main, "signal"):
        ledger.record.side_effect = lambda *a, **k: calls.append(("record",) + a)
        main._shutdown_requested = True
        try:
            main.main()
        finally:
            main._shutdown_requested = False
    assert calls[0] == ("record", "started")
    assert "poll" in calls


def test_main_records_drained_blocking_after_the_drain():
    order = []
    with patch.object(main, "runner_lifecycle") as ledger, \
         patch.object(main, "init_tracing"), \
         patch.object(main, "start_invoke_server"), \
         patch.object(main, "start_catalog_refresh"), \
         patch.object(main, "start_heartbeat_pass"), \
         patch.object(main, "join_running_heartbeats",
                      side_effect=lambda: order.append("join")), \
         patch.object(main, "poll_once"), \
         patch.object(main, "signal"):
        ledger.record.side_effect = lambda *a, **k: order.append((a, k))
        main._shutdown_requested = True
        try:
            main.main()
        finally:
            main._shutdown_requested = False
    assert order[-1] == (("drained",), {"blocking": True}), order
    # After the join, not before it: a drain that is still running has not
    # drained, and a row written early would say it had.
    assert order.index("join") < len(order) - 1


def test_the_signal_handler_writes_nothing_itself():
    """`Thread.start()` takes a lock the main thread may already hold, and a
    handler runs on that thread -- so the handler records nothing at all."""
    seen = []
    with patch.object(main, "runner_lifecycle") as ledger:
        ledger.record.side_effect = lambda *a, **k: seen.append((a, k))
        try:
            main._request_shutdown(15, None)
            assert seen == []
            assert main._shutdown_signum == 15
        finally:
            main._shutdown_requested = False
            main._shutdown_signum = None


def test_the_signal_row_lands_before_the_join():
    """The join is the whole drain, minutes of it. A row written after it
    would be missing on exactly the kill it exists to name."""
    order = []
    with patch.object(main, "runner_lifecycle") as ledger, \
         patch.object(main, "join_running_heartbeats",
                      side_effect=lambda: order.append("join")):
        ledger.record.side_effect = lambda *a, **k: order.append((a, k))
        main._shutdown_signum = 15
        try:
            main._drain_and_exit()
        finally:
            main._shutdown_signum = None
    assert order == [(("signal",), {"detail": "signal 15"}), "join"]
    # Not blocking: there is a live process behind this one, and a CouchDB
    # write on the drain's own thread delays the drain.
    assert order[0][1].get("blocking") is None
