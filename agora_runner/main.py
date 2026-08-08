"""Entrypoint: starts the /invoke server, then polls forever."""

import signal
import time

from agora_runner.config import AGORA_URL, POLL_INTERVAL_SECONDS
from agora_runner.log import log
from agora_runner.heartbeats import join_running_heartbeats
from agora_runner.poll import poll_once
from agora_runner.invoke_server import start_invoke_server

# Set by the SIGTERM/SIGINT handler, read by the poll loop between ticks.
# A plain module flag rather than a threading.Event on purpose: a signal
# handler runs on the main thread between bytecodes, so Event.set() could
# in principle re-enter a lock that same thread is already holding inside
# Event.wait(). A bool assignment cannot deadlock.
_shutdown_requested = False


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
    global _shutdown_requested
    _shutdown_requested = True
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


def main():
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    start_invoke_server()
    log(f"polling {AGORA_URL}/conversations every {POLL_INTERVAL_SECONDS}s")
    while True:
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
            join_running_heartbeats()
            log("drain complete, exiting")
            return


if __name__ == "__main__":
    main()
