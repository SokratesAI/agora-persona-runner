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


def _load(raw):
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
        log(f"dropped-ticks ledger at {PATH} is not JSON — starting a new one")
        return []
    if not isinstance(records, list):
        log(f"dropped-ticks ledger at {PATH} is not a list — starting a new one")
        return []
    return records


def _write(record):
    for attempt in range(ATTEMPTS):
        raw, rev = vault_read_path_rev(PATH)
        records = _load(raw)
        records.append(record)
        body = json.dumps(records[-KEEP:], indent=2) + "\n"
        result = vault_write_path(PATH, body, if_rev=rev)
        if result == "written":
            debug_log(
                f"dropped-ticks: recorded {record['reason']!r} for "
                f"{record['heartbeat']}, {len(records[-KEEP:])} record(s) held"
            )
            return True
        debug_log(f"dropped-ticks: write {attempt + 1} of {ATTEMPTS} said {result!r}")
    log(f"dropped-ticks: could not record {record['reason']!r} for "
        f"{record['heartbeat']} after {ATTEMPTS} attempt(s)")
    return False


def _write_quietly(record):
    try:
        _write(record)
    except Exception as e:  # noqa: BLE001 -- see the module docstring
        log(f"dropped-ticks: {type(e).__name__} recording a dropped tick: {e}")


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


def read_records():
    """Every stored record, oldest first. -> list

    For readers outside the runner Pod: `tools.heartbeat_gaps` calls this to
    put a reason beside a slot it can only otherwise report as unexplained.
    """
    raw, _rev = vault_read_path_rev(PATH)
    return _load(raw)
