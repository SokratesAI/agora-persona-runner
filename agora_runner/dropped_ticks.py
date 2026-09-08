"""A durable record of due heartbeat ticks that never became a run.

`_drop_tick` in `heartbeats.py` has always known exactly why it declined a
slot -- "3 run(s) in flight, limit 3", "claim for lastRunAt=... not visible
yet" -- and has always written that reason to stdout and nowhere else. Stdout
dies with the pod. So `tools.heartbeat_gaps` can count the firings that
produced no conversation, and can even tell whether a rollout explains them,
but it has never been able to say *why* any individual slot was lost: on
2026-09-08 it reported four unexplained missed firings in 24 hours and the
one artefact that named their cause had already been garbage-collected with
the ReplicaSet. That is the still-open half of idea #267 -- eleven historical
silent cycles that cannot be diagnosed, because the diagnosis was printed to
a pipe.

This writes the same fact to the vault instead, where it outlives the pod and
`heartbeat_gaps` can read it back.

Two properties this file exists to hold, because both are easy to lose:

* **It never blocks the poller.** `run_due_heartbeats` is the loop that
  decides whether any heartbeat fires at all; a CouchDB write on that thread
  can stall for the full HTTP timeout and cost real cycles. `record` hands
  the write to a daemon thread and returns immediately, and every failure
  inside that thread is swallowed after a log line. A diagnostic that can
  stop the thing it is diagnosing is worse than no diagnostic.
* **It is bounded.** A wedged heartbeat drops a due tick on every poll, which
  is hundreds per hour. `_drop_tick` already solved that for the log by
  writing at the 1st, 2nd, 4th, 8th ... drop, and only those calls reach
  here, so an outage costs about ten writes rather than nine hundred. The
  ledger itself is capped at `KEEP` records, newest last.
"""

import json
import threading
from datetime import datetime, timezone

from agora_runner.log import log, debug_log
from agora_runner.vault import vault_read_path_rev, vault_write_path

PATH = "projects/sokrates/projects/agora/nova/resources/dropped-ticks.json"

# The second half of the same question, in its own document. A dropped tick is
# a slot the poller LOOKED at and declined; a lagged pass is the poller not
# looking at all, which loses an anchored slot outright rather than declining
# it (`heartbeats._skipped_occurrences` names that loss, and this says how long
# the scheduler was away). They share this module because they share every
# property that made it worth writing -- off-thread, failure-swallowing,
# bounded, doubling-gated -- and they are kept in two documents because
# `heartbeat_gaps` reads them for two different sentences and a `kind` field on
# one list is how a reader starts filtering instead of reading.
LAG_PATH = "projects/sokrates/projects/agora/nova/resources/scheduler-lag.json"

# Enough to cover several days of a healthy loop and a whole outage of a sick
# one: the 24h window `heartbeat_gaps` judges saw four drops on the day this
# was written, and a heartbeat wedged for its full 45-minute cap contributes
# about ten. This is a cap on a diagnostic ledger, not on a capability -- the
# danger it is measured against is an unbounded vault document, which is a
# real one this loop has hit before.
KEEP = 200

# One retry, not a loop. Two writers here are two runner Pods mid-rollout, so
# a conflict is rare and a second attempt from a fresh read settles it; a
# retry loop on a background thread is how a diagnostic starts costing more
# than it reports.
ATTEMPTS = 2


def _load(raw, path=PATH):
    """The stored records, or `[]` for anything that is not a JSON list.

    A ledger that has been hand-edited into something unparseable must not
    stop the next drop being recorded -- the record in hand is the one that
    matters, and the alternative is a permanently silent instrument.
    """
    if not raw:
        return []
    try:
        records = json.loads(raw)
    except ValueError:
        log(f"ledger at {path} is not JSON — starting a new one")
        return []
    if not isinstance(records, list):
        log(f"ledger at {path} is not a list — starting a new one")
        return []
    return records


def _write(record, path=PATH, label="dropped-ticks"):
    for attempt in range(ATTEMPTS):
        raw, rev = vault_read_path_rev(path)
        records = _load(raw, path)
        records.append(record)
        body = json.dumps(records[-KEEP:], indent=2) + "\n"
        result = vault_write_path(path, body, if_rev=rev)
        if result == "written":
            debug_log(
                f"{label}: recorded {record!r}, "
                f"{len(records[-KEEP:])} record(s) held"
            )
            return True
        debug_log(f"{label}: write {attempt + 1} of {ATTEMPTS} said {result!r}")
    log(f"{label}: could not record {record!r} after {ATTEMPTS} attempt(s)")
    return False


def _write_quietly(record, path=PATH, label="dropped-ticks"):
    try:
        _write(record, path, label)
    except Exception as e:  # noqa: BLE001 -- see the module docstring
        log(f"{label}: {type(e).__name__} recording a record: {e}")


def record(hb_id, name, reason, n, now=None):
    """Persist one dropped tick, off the calling thread. -> the Thread.

    `now` is injected so a test can pin the timestamp; the default is read
    here rather than bound as a default argument, which would freeze it at
    import time.
    """
    at = (now or datetime.now(timezone.utc)).isoformat()
    entry = {"at": at, "heartbeatId": hb_id, "heartbeat": name,
             "reason": reason, "dropsSinceLastStart": n}
    thread = threading.Thread(target=_write_quietly, args=(entry,), daemon=True)
    thread.start()
    return thread


def record_pass_lag(gap_seconds, pass_seconds, interval_seconds, n, now=None):
    """Persist one late scheduler pass, off the calling thread. -> the Thread.

    `gap_seconds` is start-to-start between two passes and `pass_seconds` is
    how long `run_due_heartbeats` itself took inside the EARLIER one, which
    is the pass whose duration the gap is made of. Both are
    stored because the difference is the whole diagnosis and neither number
    carries it alone: a gap that is almost all pass means the Agora calls in
    `run_due_heartbeats` are slow, and a long gap around a short pass means
    the scheduler thread was starved by something else in this process. Until
    this existed the only way to tell those apart was to guess, which is what
    "the poll loop stalls for minutes and I do not know why" has meant.
    """
    at = (now or datetime.now(timezone.utc)).isoformat()
    entry = {"at": at, "gapSeconds": round(gap_seconds, 3),
             "passSeconds": round(pass_seconds, 3),
             "intervalSeconds": round(interval_seconds, 3),
             "lateSinceHealthy": n}
    thread = threading.Thread(
        target=_write_quietly, args=(entry, LAG_PATH, "scheduler-lag"), daemon=True)
    thread.start()
    return thread


def read_records():
    """Every stored record, oldest first. -> list

    For readers outside the runner Pod: `tools.heartbeat_gaps` calls this to
    put a reason beside a slot it can only otherwise report as unexplained.
    """
    raw, _rev = vault_read_path_rev(PATH)
    return _load(raw, PATH)


def read_lag_records():
    """Every stored late-pass record, oldest first. -> list"""
    raw, _rev = vault_read_path_rev(LAG_PATH)
    return _load(raw, LAG_PATH)
