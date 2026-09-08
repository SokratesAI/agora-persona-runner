"""`run_due_heartbeats` on its own thread, so a reply cannot delay a firing.

Until 2026-09-08 the heartbeat decision was the last thing `poll_once` did,
after it had walked every active conversation. That coupling is what made a
scheduled cycle arrive minutes late or not at all, and it is structural
rather than intermittent: `conversations.speak` calls `generate_reply` **on
the calling thread**, and for a `claude-cli:` persona that is a real Claude
Code session -- minutes, not milliseconds. So one chat message from the
owner in one conversation held the scheduler for the length of the answer,
and `heartbeat_gaps` then reported a slot that "produced no run" with a
runner up the whole time.

The reason a late pass *loses* a slot rather than merely delaying it:
`schedule_due` asks only about the most recent anchored occurrence, so a
pass that lands after two slot boundaries never asks about the older one.
At a 24-minute cadence, a pass 25 minutes late costs a cycle outright.
`heartbeats._skipped_occurrences` makes that visible; this makes it rarer.

Two things this deliberately does not do. It does not touch conversation
polling -- a slow reply still holds the conversation loop, which is that
loop's own business and costs nobody a scheduled run. And it does not
widen anything: `run_due_heartbeats` is called from exactly one place
again, so the in-flight registry and the spawn marks it keeps are still
read and written by a single thread and need no lock.
"""

import os
import threading
import time

from agora_runner.config import POLL_INTERVAL_SECONDS
from agora_runner.log import log

# The same cadence the coupled version was *supposed* to have. The point of
# this module is that it is now the cadence that actually happens, rather
# than an upper bound on how often the scheduler gets a look in.
INTERVAL_SECONDS = float(os.environ.get("HEARTBEAT_PASS_SECONDS", POLL_INTERVAL_SECONDS))

_thread = None


def pass_once():
    """One scheduler look. Never raises -- returns False if the pass failed.

    Swallowing here rather than in the loop below is deliberate: this is
    the thread that decides whether anything fires at all, and an
    exception escaping it stops every heartbeat in this pod for as long as
    the pod lives, silently. `poll.py` wrapped the same call for the same
    reason before this module existed.
    """
    from agora_runner.heartbeats import run_due_heartbeats

    try:
        run_due_heartbeats()
        return True
    except Exception as exc:
        log(f"heartbeat pass failed: {exc}")
        return False


def _loop(should_stop):
    while not should_stop():
        pass_once()
        # Sliced like main's own sleep, so a SIGTERM arriving while idle is
        # noticed inside a second instead of at the end of the interval.
        remaining = INTERVAL_SECONDS
        while remaining > 0 and not should_stop():
            step = min(1.0, remaining)
            time.sleep(step)
            remaining -= step


def start_heartbeat_pass(should_stop):
    """Start the scheduler thread. `should_stop` is checked before every pass.

    That check is what keeps the drain contract from `main._drain_and_exit`:
    a pod that has been signalled must start no new heartbeat run, because
    that run would be SIGKILLed part-way through a cycle.
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    _thread = threading.Thread(
        target=_loop, args=(should_stop,), daemon=True, name="heartbeat-pass")
    _thread.start()
    return _thread
