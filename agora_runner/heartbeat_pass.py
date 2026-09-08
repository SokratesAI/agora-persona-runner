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

from agora_runner import dropped_ticks
from agora_runner.config import POLL_INTERVAL_SECONDS
from agora_runner.log import log

# The same cadence the coupled version was *supposed* to have. The point of
# this module is that it is now the cadence that actually happens, rather
# than an upper bound on how often the scheduler gets a look in.
INTERVAL_SECONDS = float(os.environ.get("HEARTBEAT_PASS_SECONDS", POLL_INTERVAL_SECONDS))

# A pass is LATE when the gap from the previous pass's start is more than two
# whole intervals. One interval is the sleep this loop takes on purpose; a
# second full interval elapsed on top of it means the scheduler missed a beat
# it was supposed to take, which is the thing that loses an anchored slot --
# `schedule_due` only ever asks about the most recent occurrence, so a
# scheduler that is away across two slot boundaries loses the older one
# outright. The factor is not a feel: it is the smallest gap that cannot be
# explained by the loop doing exactly what it says.
LATE_FACTOR = 2.0

# Same doubling as `_drop_tick`, for the same reason and against the same
# danger. A permanently starved scheduler is late on EVERY pass, which is
# hundreds of records an hour; writing at the 1st, 2nd, 4th, 8th ... late pass
# keeps a sick loop talking for as long as it is sick and costs about ten
# writes rather than nine hundred. The counter resets on a healthy pass, so a
# second stall an hour later is reported as loudly as the first.
_late_since_healthy = 0

# Same doubling and the same reset rule, counted separately from lateness on
# purpose: a scheduler can be perfectly punctual and raise on every pass, and
# sharing one counter would let a healthy pass on one silence the other.
_failed_since_healthy = 0

_thread = None


def pass_once():
    """One scheduler look. Never raises -- returns the exception, or `None`.

    Swallowing here rather than in the loop below is deliberate: this is
    the thread that decides whether anything fires at all, and an
    exception escaping it stops every heartbeat in this pod for as long as
    the pod lives, silently. `poll.py` wrapped the same call for the same
    reason before this module existed.

    It hands the exception back rather than a bare `False` because the loop
    has to record *which* error, and swallowing used to mean the only copy of
    that lived in a log that dies with the container.
    """
    from agora_runner.heartbeats import run_due_heartbeats

    try:
        run_due_heartbeats()
        return None
    except Exception as exc:
        log(f"heartbeat pass failed: {exc}")
        return exc


def note_pass(gap_seconds, pass_seconds, interval_seconds=None,
              record=dropped_ticks.record_pass_lag):
    """Judge one pass against the interval and record it if it was late.

    Returns the record's thread when it wrote one and `None` otherwise, so a
    test can tell "not late" from "late but not at a doubling". `gap_seconds`
    is `None` for the very first pass of the process, which has nothing to be
    late against and must not be reported as late by default.
    """
    global _late_since_healthy
    interval = INTERVAL_SECONDS if interval_seconds is None else interval_seconds
    # A non-positive interval is "as fast as this thread can go" and has no
    # beat to be late against, so there is nothing here to judge. Nothing sets
    # it that way in production; a test does, and a rule that calls every pass
    # late under it is measuring its own configuration.
    if interval <= 0:
        return None
    if gap_seconds is None or gap_seconds <= interval * LATE_FACTOR:
        _late_since_healthy = 0
        return None
    _late_since_healthy += 1
    n = _late_since_healthy
    if n & (n - 1) != 0:  # 1, 2, 4, 8, ... — never silent, never a flood
        return None
    log(f"heartbeat scheduler: pass {gap_seconds:.1f}s after the previous one "
        f"(interval {interval:.1f}s, the pass itself took {pass_seconds:.1f}s), "
        f"{n} late pass(es) since the last healthy one")
    return record(gap_seconds, pass_seconds, interval, n)


def note_failure(error, record=dropped_ticks.record_pass_failure):
    """Record a pass that raised, at a doubling. -> the Thread, or `None`.

    `error` is `None` for a pass that worked, which resets the counter --- so
    a second outage an hour later is reported as loudly as the first, the same
    contract `note_pass` keeps for lateness.

    This is the third way an anchored slot is lost and it was the only one
    that left nothing outside the Pod. A raising scheduler declines no tick
    (nothing is ever judged) and keeps its cadence (the raise is caught in
    milliseconds), so it writes neither of the other two ledgers, and the slot
    it loses reads as `unevaluated` in `heartbeat_gaps` --- which is the name
    of the poller sleeping through a slot, a different bug with a different
    fix.
    """
    global _failed_since_healthy
    if error is None:
        _failed_since_healthy = 0
        return None
    _failed_since_healthy += 1
    n = _failed_since_healthy
    if n & (n - 1) != 0:  # 1, 2, 4, 8, ... -- never silent, never a flood
        return None
    log(f"heartbeat scheduler: pass raised {type(error).__name__}: {error}, "
        f"{n} failed pass(es) since the last healthy one")
    return record(error, n)


def _loop(should_stop):
    previous_start = None
    previous_pass_seconds = 0.0
    while not should_stop():
        started = time.monotonic()
        # The gap is start-to-start, so what fills it is the PREVIOUS pass
        # plus the sleep after it -- reporting this pass's own duration
        # against it would pair a number with a gap it did not cause.
        gap = None if previous_start is None else started - previous_start
        previous_start = started
        try:
            note_pass(gap, previous_pass_seconds)
            note_failure(pass_once())
        except Exception as exc:  # noqa: BLE001 -- see below
            # `pass_once` guards the one call this loop was built around and
            # the two bookkeeping calls sat OUTSIDE that guard, which put the
            # whole scheduler behind them: this thread is started once and
            # supervised by nothing, so an escape here ends every heartbeat in
            # this Pod for as long as it lives, with one log line and no
            # ledger record. Both recorders do their vault I/O on a thread of
            # their own behind `_write_quietly`, but the synchronous part they
            # run first can still raise -- `threading.Thread.start` answers
            # `RuntimeError: can't start new thread` when the process is at
            # its thread limit, which is exactly the state a runner under load
            # reaches, and `str(exc)` on a custom exception can raise anything
            # at all. So the guard is around the bookkeeping rather than
            # inside either recorder: it has to hold for whatever the next
            # instrument added here does too.
            log(f"heartbeat scheduler: bookkeeping raised "
                f"{type(exc).__name__}: {exc} -- carrying on")
        previous_pass_seconds = time.monotonic() - started
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
