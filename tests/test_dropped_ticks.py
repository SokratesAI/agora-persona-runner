"""The durable half of a dropped heartbeat tick (idea #267).

`_drop_tick` knew why it declined a slot and printed it to stdout, which is
collected with the Pod. These hold the properties that make the vault copy
worth having: it carries the reason, it never blocks or breaks the poll loop,
and it cannot grow without bound.

Nothing here may touch the real vault. Every test patches
`vault_read_path_rev`/`vault_write_path` on the module under test, which is
where the code looks them up.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from agora_runner import dropped_ticks
from agora_runner import heartbeats


class FakeVault:
    """A one-document CouchDB with revisions, and a scripted conflict."""

    def __init__(self, raw=None, rev=None, results=None):
        self.raw = raw
        self.rev = rev
        self.results = list(results or [])
        self.reads = []
        self.writes = []

    def read(self, path):
        self.reads.append(path)
        return self.raw, self.rev

    def write(self, path, content, if_rev=None, allow_shrink=False):
        self.writes.append({"path": path, "content": content, "if_rev": if_rev})
        result = self.results.pop(0) if self.results else "written"
        if result == "written":
            self.raw = content
        # A conflict means somebody else's write landed, so the stored
        # revision has moved either way. A fake that leaves it alone lets a
        # retry-without-re-reading look like a retry that re-read.
        self.rev = f"{len(self.writes)}-fake"
        return result

    def records(self):
        return json.loads(self.raw)


def _install(monkeypatch, vault):
    monkeypatch.setattr(dropped_ticks, "vault_read_path_rev", vault.read)
    monkeypatch.setattr(dropped_ticks, "vault_write_path", vault.write)
    return vault


AT = datetime(2026, 9, 8, 13, 36, tzinfo=timezone.utc)


def _record(**kw):
    args = {"hb_id": "hb1", "name": "Nova",
            "reason": "3 run(s) in flight, limit 3", "n": 1, "now": AT}
    args.update(kw)
    dropped_ticks.record(args["hb_id"], args["name"], args["reason"],
                         args["n"], now=args["now"]).join(timeout=5)


def test_a_dropped_tick_is_stored_with_its_reason_and_its_slot(monkeypatch):
    """The whole point: the reason and WHEN, so a missed slot can be matched.

    `heartbeat_gaps` names missed firings by their UTC slot time and can say
    nothing about why. Without both fields on the record there is still no
    way to put a cause beside a slot.
    """
    vault = _install(monkeypatch, FakeVault())

    _record()

    assert vault.records() == [{
        "at": "2026-09-08T13:36:00+00:00",
        "heartbeatId": "hb1",
        "heartbeat": "Nova",
        "reason": "3 run(s) in flight, limit 3",
        "dropsSinceLastStart": 1,
    }]


def test_the_write_is_conditional_on_the_revision_it_read(monkeypatch):
    """Two runner Pods overlap during a rollout, so an unconditional write
    silently adopts and overwrites the other one's records."""
    vault = _install(monkeypatch, FakeVault(raw="[]", rev="7-abc"))

    _record()

    assert vault.writes[0]["if_rev"] == "7-abc"


def test_a_conflicted_write_is_retried_from_a_fresh_read(monkeypatch):
    """Re-sending the same body against the same stale revision 409s forever;
    the retry has to re-read, or it is not a retry."""
    vault = _install(monkeypatch, FakeVault(raw="[]", rev="7-abc",
                                            results=["conflict"]))

    _record()

    assert len(vault.reads) == 2, "the retry did not re-read the ledger"
    assert len(vault.writes) == 2
    assert vault.writes[1]["if_rev"] != "7-abc", \
        "the retry resent the revision that had already lost"


def test_a_write_that_keeps_conflicting_gives_up_rather_than_looping(monkeypatch):
    """This runs on a background thread inside a failing system. A retry loop
    there costs more than the record is worth."""
    vault = _install(monkeypatch, FakeVault(
        raw="[]", rev="7-abc", results=["conflict"] * 10))

    _record()

    assert len(vault.writes) == dropped_ticks.ATTEMPTS


def test_the_ledger_is_capped_at_the_newest_records(monkeypatch):
    """A wedged heartbeat writes at every doubling for as long as it is
    wedged, and the document must not grow without bound."""
    old = [{"at": f"2026-09-0{1 + i % 8}T00:00:00+00:00", "heartbeatId": "hb1",
            "heartbeat": "Nova", "reason": f"old {i}", "dropsSinceLastStart": 1}
           for i in range(dropped_ticks.KEEP + 40)]
    vault = _install(monkeypatch, FakeVault(raw=json.dumps(old), rev="7-abc"))

    _record(reason="the newest one")

    kept = vault.records()
    assert len(kept) == dropped_ticks.KEEP
    assert kept[-1]["reason"] == "the newest one"
    assert kept[0]["reason"] == f"old {41}", \
        "the cap dropped from the wrong end — the newest records are the ones worth keeping"


def test_an_unparseable_ledger_does_not_stop_the_next_record(monkeypatch):
    """A hand-edited or half-written document must not silence the
    instrument permanently."""
    vault = _install(monkeypatch, FakeVault(raw="{not json", rev="7-abc"))

    with patch.object(dropped_ticks, "log"):
        _record()

    assert [r["reason"] for r in vault.records()] == ["3 run(s) in flight, limit 3"]


def test_a_ledger_holding_something_other_than_a_list_is_replaced(monkeypatch):
    """`json.loads` succeeds on `{}` and `append` then raises, which would
    take the recording thread down on every drop."""
    vault = _install(monkeypatch, FakeVault(raw='{"a": 1}', rev="7-abc"))

    with patch.object(dropped_ticks, "log"):
        _record()

    assert len(vault.records()) == 1


def test_a_vault_that_raises_does_not_take_the_caller_with_it(monkeypatch):
    """`_drop_tick` runs on the poll loop that decides whether ANY heartbeat
    fires. An exception escaping this must not reach it."""
    def explode(path):
        raise RuntimeError("CouchDB answered HTTP 500")

    monkeypatch.setattr(dropped_ticks, "vault_read_path_rev", explode)
    monkeypatch.setattr(dropped_ticks, "vault_write_path", lambda *a, **k: "written")

    with patch.object(dropped_ticks, "log") as logged:
        dropped_ticks.record("hb1", "Nova", "reason", 1, now=AT).join(timeout=5)

    assert any("RuntimeError" in str(c) for c in logged.call_args_list), \
        "the failure was swallowed without saying so anywhere"


def test_the_write_does_not_happen_on_the_calling_thread(monkeypatch):
    """A CouchDB write can stall for the full HTTP timeout. The poll loop
    cannot wait for it, so `record` must return before the write lands."""
    import threading

    released = threading.Event()
    started = threading.Event()

    def blocking_read(path):
        started.set()
        released.wait(5)
        return "[]", "7-abc"

    monkeypatch.setattr(dropped_ticks, "vault_read_path_rev", blocking_read)
    monkeypatch.setattr(dropped_ticks, "vault_write_path", lambda *a, **k: "written")

    thread = dropped_ticks.record("hb1", "Nova", "reason", 1, now=AT)
    try:
        assert started.wait(5), "the write never started"
        assert thread.is_alive(), \
            "record() waited for the vault — that stalls the poll loop"
    finally:
        released.set()
        thread.join(timeout=5)


def test_read_records_returns_what_was_stored(monkeypatch):
    """The reader side: `heartbeat_gaps` runs outside the runner Pod and this
    is how it gets the reasons back."""
    stored = [{"at": "2026-09-08T13:36:00+00:00", "heartbeatId": "hb1",
               "heartbeat": "Nova", "reason": "why", "dropsSinceLastStart": 4}]
    _install(monkeypatch, FakeVault(raw=json.dumps(stored), rev="7-abc"))

    assert dropped_ticks.read_records() == stored


def test_read_records_on_a_ledger_that_does_not_exist_yet(monkeypatch):
    """`vault_read_path_rev` answers `(None, None)` for a missing file, and a
    reader that raised there would report a healthy loop as broken."""
    _install(monkeypatch, FakeVault(raw=None, rev=None))

    assert dropped_ticks.read_records() == []


# --- the wiring in heartbeats._drop_tick ------------------------------------


def _drop(times, recorder):
    heartbeats._heartbeat_dropped_ticks.pop("hb1", None)
    with patch.object(heartbeats, "log"), patch.object(heartbeats, "debug_log"), \
         patch.object(dropped_ticks, "record", recorder):
        for _ in range(times):
            heartbeats._drop_tick("hb1", "Nova", "claim not visible yet")
    heartbeats._heartbeat_dropped_ticks.pop("hb1", None)


def test_a_drop_is_recorded_at_the_same_doublings_the_log_uses(monkeypatch):
    """1, 2, 4, 8 — a wedged heartbeat drops a tick on every poll, so a write
    per drop is hundreds of vault writes an hour."""
    calls = []
    _drop(9, lambda *a, **k: calls.append(a))

    assert [c[3] for c in calls] == [1, 2, 4, 8]


def test_the_recorded_reason_is_the_one_the_poller_gave(monkeypatch):
    """The precondition the count assertion above would pass without: a
    record carrying the wrong reason is worse than no record."""
    calls = []
    _drop(1, lambda *a, **k: calls.append(a))

    assert calls[0][:3] == ("hb1", "Nova", "claim not visible yet")
