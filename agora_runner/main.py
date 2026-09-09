"""Entrypoint: starts the /invoke server, then polls forever.

Nova's site is deliberately NOT started here. It was until 2026-08-09,
and it has its own process and its own Deployment now, because this one
had `strategy: Recreate` and a 2880s drain -- so the site went down for
the whole length of every cycle. `run_nova_site.py` is its entrypoint; the
reasoning is in agora_runner/nova_site_main.py.
"""

import signal
import time

from agora_runner.config import AGORA_URL, POLL_INTERVAL_SECONDS
from agora_runner.log import log
from agora_runner.heartbeats import join_running_heartbeats
from agora_runner.poll import poll_once
from agora_runner.invoke_server import start_invoke_server
from agora_runner.otel import init_tracing
from agora_runner.catalog_refresh import start_catalog_refresh
from agora_runner.heartbeat_pass import start_heartbeat_pass
from agora_runner import runner_lifecycle

# Set by the SIGTERM/SIGINT handler, read by the poll loop between ticks.
# A plain module flag rather than a threading.Event on purpose: a signal
# handler runs on the main thread between bytecodes, so Event.set() could
# in principle re-enter a lock that same thread is already holding inside
# Event.wait(). A bool assignment cannot deadlock.
_shutdown_requested = False

# The signal number the handler saw, read by _drain_and_exit when it writes the
# lifecycle row. A plain assignment for the same reason _shutdown_requested is
# one: it is the only operation a signal handler can do without taking a lock.
_shutdown_signum = None


def shutdown_requested():
    return _shutdown_requested


def _request_shutdown(signum, _frame):
    """Deliberately does NOT exit -- that is the whole point.

    Python's default SIGTERM disposition kills the process immediately,
    and run_heartbeat posts the persona's reply (notify()) only AFTER
    generate_reply returns, which for a claude-cli persona is minutes of
    real work. So a redeploy landing mid-cycle destroys the reply that
    cycle was in the middle of producing. Measured three cycles running
    on the Evolve heartbeat (2026-08-02), each time because merging a PR
    into this very repo rolled the pod hosting the cycle that merged it.

    Handling the signal turns that into a drain: finish the tick already
    in flight (reply posted, heartbeat row PATCHed), start no new one,
    then exit 0. This only works if terminationGracePeriodSeconds is long
    enough to cover a real cycle -- it was 10s, which is nowhere near;
    raised alongside this in agora-persona-runner-config.

    Every heartbeat run is on its own thread as of 2026-08-08, so
    "finish the in-flight tick" is no longer enough on its own — the
    tick returns in milliseconds now. main() joins the running heartbeat
    threads too (heartbeats.join_running_heartbeats), which closes the
    gap this docstring used to record for workflow-mode heartbeats."""
    global _shutdown_requested, _shutdown_signum
    _shutdown_requested = True
    _shutdown_signum = signum
    log(f"received signal {signum}, draining: finishing the in-flight tick, then exiting")


def _sleep_between_ticks(seconds):
    """Sleep in slices so a signal arriving while idle is noticed
    promptly. PEP 475 makes time.sleep resume after an interrupt rather
    than return early, so one long sleep would ignore the flag until it
    elapsed."""
    remaining = seconds
    while remaining > 0 and not _shutdown_requested:
        step = min(1.0, remaining)
        time.sleep(step)
        remaining -= step


def _drain_and_exit():
    """Wait for the cycles already in flight, then let the process end.

    This deliberately polls nothing at all, and that is a change from
    2026-08-31 to 2026-09-06 rather than an oversight. While the strategy
    was `Recreate`, the replacement pod was not created until this one
    exited, so a drain meant every persona in Agora (Claude, Opus, Gemini,
    Haiku, Plain assistant, Study buddy ...) answered nobody for up to the
    full 48 minutes -- the owner's issue #130, measured Cycle 692. The
    answer then was to spend the wait answering conversations, because
    this process was the only one alive.

    It is no longer the only one alive. The strategy is `RollingUpdate`
    with `maxSurge: 1, maxUnavailable: 1`, so the replacement pod is
    created in the same reconcile that requests this one's deletion and is
    serving within seconds. A draining pod that kept polling would now be
    a *second* poller against the same conversations, and the duplicate it
    produces is a second reply to the owner rather than a silent one.

    Starting a new heartbeat run stays forbidden for the same reason it
    always was -- that run would be SIGKILLed part-way -- and here it is
    forbidden by not polling at all.

    What this does not do is shorten the wait by a second. The in-flight
    cycle keeps its full budget; `join_running_heartbeats` below is what
    holds the process open, and an idle pod still exits immediately.
    """
    # First, before the join: the join is the whole drain, minutes of it, and
    # the row that says "Kubernetes asked" has to be on disk before the SIGKILL
    # at grace expiry that this is built to distinguish from an OOM kill.
    #
    # Here rather than in _request_shutdown, and that placement is the point.
    # `threading.Thread.start()` takes a lock the main thread may already hold
    # -- it starts a thread per heartbeat run -- and a signal handler runs on
    # that same thread between bytecodes, so recording from inside the handler
    # can deadlock the process it is diagnosing. Same reasoning, and the same
    # main thread, as the module comment on _shutdown_requested being a plain
    # bool. The cost is up to one poll slice of delay; _sleep_between_ticks
    # slices at 1.0s, and a kill inside that window reads as `no_signal`, which
    # errs toward "nobody asked" rather than inventing a request that was made.
    runner_lifecycle.record("signal", detail=f"signal {_shutdown_signum}")
    join_running_heartbeats()


def main():
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    # The first of three rows that outlive this Pod. A `started` with no
    # `signal` before it is a process that was killed rather than asked to
    # stop, which is the cause `cycle_postmortem` cannot recover for a
    # `silent` cycle once the ReplicaSet is gone.
    runner_lifecycle.record("started")
    # Before the server binds, so the first /invoke or /mcp call is
    # traced too. The name is passed rather than left to the module
    # default: this process and nova-site share an image, and an unnamed
    # runner would file its spans under the other one's service.
    init_tracing("agora-persona-runner")
    start_invoke_server()
    # Regenerates nova/catalog.md on a timer. Here rather than in a
    # cycle's prompt because a cycle has to choose to run it, and a
    # catalog nobody regenerates is a screenshot -- see the module.
    start_catalog_refresh()
    # The scheduler runs beside the conversation loop rather than at the end
    # of it. `conversations.speak` generates a reply on its caller's thread,
    # which for a claude-cli persona is minutes, and while that was the same
    # thread as `run_due_heartbeats` a single chat message could push a
    # scheduled cycle past its own slot -- and an anchored schedule only ever
    # asks about the most recent occurrence, so a slot pushed past is lost
    # rather than late. See agora_runner/heartbeat_pass.py.
    log(f"polling {AGORA_URL}/conversations every {POLL_INTERVAL_SECONDS}s")
    while True:
        # Called every tick, not once before the loop, and it is the same
        # call: `start_heartbeat_pass` returns the live thread untouched and
        # only starts one when there is none running. So this is the start
        # AND the supervisor, in one line -- the scheduler is a daemon thread
        # that nothing was watching, and a thread that dies for a reason
        # `heartbeat_pass._loop` does not enumerate takes every future
        # heartbeat in this Pod with it, silently. The poll loop below has
        # been supervised by its own `try` since it was written; this thread
        # decides whether anything fires at all and had less.
        start_heartbeat_pass(shutdown_requested)
        try:
            poll_once()
        except Exception as e:
            log(f"poll failed: {e}")
        # Checked before the sleep as well as after it, so a signal
        # arriving while idle doesn't buy one more tick on the way out.
        # That tick used to be harmless; now it could START a fresh
        # heartbeat run (claiming it, then having it killed part-way)
        # during the shutdown we are already committed to.
        if not _shutdown_requested:
            _sleep_between_ticks(POLL_INTERVAL_SECONDS)
        if _shutdown_requested:
            _drain_and_exit()
            # On this thread, not a daemon one: the process is about to
            # return and a daemon thread does not survive interpreter
            # shutdown, so backgrounding this would drop the one row that
            # separates a finished drain from a drain that ran out of grace.
            runner_lifecycle.record("drained", blocking=True)
            log("drain complete, exiting")
            return


if __name__ == "__main__":
    main()
