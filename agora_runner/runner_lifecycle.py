"""A durable record of how the runner process itself started and stopped.

`heartbeats.run_heartbeat` names the hole this fills, in its own comment, on
the line that hoists the opening chip: *"a rollout leaves two pods polling and
then SIGKILLs the old one at grace expiry whatever it is holding; an OOM kill
and a node eviction land the same way. None of those raise, so none of them
write a chip, a closing line, a `lastResult` or a stub"*. That is
`cycle_postmortem`'s `silent` verdict -- a conversation created, numbered and
tagged with not one message in it -- and the same comment says why it could not
be diagnosed: *"I did not read the kill itself -- every pod from that window is
gone, which is the second half of the problem."*

`agora-claude-bridge#112` gave the **bridge** process exactly this record and
`tools.lifecycle_health` reads it. The runner Pod, which is where
`run_heartbeat` actually executes and therefore where a silent cycle dies, has
never had one. The bridge writes to a PVC; this process has no volume, so the
ledger goes in the vault beside the three `dropped_ticks` already keeps.

**Three events, and the one that matters is the one that is missing.** A
`signal` row means Kubernetes asked this process to stop and `main` turned that
into a drain. A `started` row that follows another `started` with no `signal`
between them means the process was never asked: a SIGKILL at grace expiry, an
OOM kill, a node eviction. That is the same `no_signal` verdict
`tools.lifecycle_health` reports for the bridge, and it is the whole reason to
write anything down here -- "the pod restarted" and "the pod was killed without
warning" leave identical traces once the ReplicaSet is garbage-collected.

Two properties inherited deliberately from `dropped_ticks`, which is why this
imports that module's writer instead of copying it: the write is bounded at
`KEEP` records, and every failure inside it is swallowed after a log line. A
diagnostic that can stop the process it is diagnosing is worse than none.

**`drained` is written on the calling thread and the other two are not, and
that asymmetry is load-bearing.** `dropped_ticks.record` hands its write to a
daemon thread so the poll loop never waits on CouchDB. `drained` is the last
thing `main` does before it returns, and a daemon thread does not survive
interpreter shutdown -- backgrounding that one write would drop exactly the row
that separates a completed drain from a drain that ran out of grace. `started`
and `signal` both have a live process behind them and stay off-thread:
`signal` in particular runs inside a signal handler on the main thread, where a
blocking HTTP call is how a drain turns into a hang.
"""

import threading
from datetime import datetime, timezone

from agora_runner.dropped_ticks import _load, _write_quietly

#: Its own document, for the same reason `dropped_ticks` keeps three: a reader
#: asks a different question of this one, and a `kind` field on a single list is
#: how a reader starts filtering instead of reading.
PATH = "projects/sokrates/projects/agora/nova/resources/runner-lifecycle.json"

LABEL = "runner-lifecycle"


def record(event, now=None, detail=None, blocking=False):
    """Persist one lifecycle event. -> the Thread, or None when blocking.

    `now` is read here rather than bound as a default argument, which would
    freeze it at import time.
    """
    entry = {"event": event,
             "at": (now or datetime.now(timezone.utc)).isoformat()}
    if detail is not None:
        entry["detail"] = detail
    if blocking:
        _write_quietly(entry, PATH, LABEL)
        return None
    thread = threading.Thread(
        target=_write_quietly, args=(entry, PATH, LABEL), daemon=True)
    thread.start()
    return thread


def read_records():
    """Every stored lifecycle row, oldest first. -> list

    For readers inside the runner Pod. `agora_runner.vault` has no working
    credentials on the bridge Pod, so a tool reading this from there goes
    through `vault_tool.py` the way `tools.heartbeat_gaps` already does.
    """
    from agora_runner.vault import vault_read_path_rev

    raw, _rev = vault_read_path_rev(PATH)
    return _load(raw, PATH)


def lives(records):
    """Lifecycle rows -> one dict per process life, oldest first.

    Each life is `{started, signal, drained, verdict}`. The verdicts are the
    bridge's, on purpose -- `tools.lifecycle_health` already teaches this loop
    to read those three words and a fourth spelling would be a second
    vocabulary for one fact:

    * `running` -- the newest life, which is this process. Never judged: a
      check that judged the life it runs inside would report every runner as
      un-drained forever.
    * `clean` -- asked to stop, and the drain finished.
    * `killed_mid_drain` -- asked to stop, and it never got to `drained`.
    * `no_signal` -- never asked at all. This is the one a silent cycle is
      made of.

    Rows before the first `started` are dropped rather than guessed at: the
    ledger is capped, so the oldest life in it is routinely half a life.
    """
    grouped = []
    for row in records or []:
        event = (row or {}).get("event")
        if event == "started":
            grouped.append({"started": row, "signal": None, "drained": None})
        elif grouped and event in ("signal", "drained"):
            if grouped[-1][event] is None:
                grouped[-1][event] = row
    for index, life in enumerate(grouped):
        if index == len(grouped) - 1:
            life["verdict"] = "running"
        elif life["drained"] is not None:
            life["verdict"] = "clean"
        elif life["signal"] is not None:
            life["verdict"] = "killed_mid_drain"
        else:
            life["verdict"] = "no_signal"
    return grouped
