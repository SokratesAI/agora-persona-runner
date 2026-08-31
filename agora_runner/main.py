"""Entrypoint: starts the /invoke server, then polls forever.

Nova's site is deliberately NOT started here. It was until 2026-08-09,
and it has its own process and its own Deployment now, because this one
is `Recreate` with a 2880s drain -- so the site went down for the whole
length of every cycle. `run_nova_site.py` is its entrypoint; the
reasoning is in agora_runner/nova_site_main.py.
"""

import signal
import time

from agora_runner.config import AGORA_URL, POLL_INTERVAL_SECONDS
from agora_runner.log import log
from agora_runner.heartbeats import join_running_heartbeats, running_heartbeat_count
from agora_runner.poll import poll_once
from agora_runner.invoke_server import start_invoke_server
from agora_runner.catalog_refresh import start_catalog_refresh

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


def _serve_while_draining():
    """Keep answering ordinary conversation turns until the drain ends.

    `terminationGracePeriodSeconds` on this Deployment is 2880s and the
    strategy is `Recreate`, so from the moment a redeploy lands there is
    no other persona runner anywhere -- the replacement pod is not
    created until this one exits. Until 2026-08-31 the drain set the
    shutdown flag and went straight to `join_running_heartbeats`, so for
    the whole of that wait every persona in Agora (Claude, Opus, Gemini,
    Haiku, Plain assistant, Study buddy ...) answered nothing at all, and
    `tools.workload_health` deliberately does not raise inside a
    workload's own drain budget, so nothing reported it either. Measured
    Cycle 692: the pod was told to drain at 03:36 Oslo, a message to the
    Claude persona posted at 03:43 was still unanswered at 03:47, and the
    Deployment had read `Available: False` for twelve minutes with a
    SIGKILL deadline of 04:24. That is the owner's issue #130.

    The wait itself is not shortened by a single second and must not be:
    it lasts exactly as long as the in-flight cycle, which is why an idle
    pod still exits within a tick. What changes is that the wait is spent
    working. It can be *lengthened*, by at most one conversation turn: a
    tick runs synchronously, so if the cycle finishes early in a tick the
    replacement pod waits out the rest of it. That is the honest cost and
    it is bounded by one turn, against a window that is otherwise up to
    the full 48 minutes with every persona silent. Starting a *new* heartbeat run stays forbidden -- that run
    would be killed part-way, which is the regression the drain was built
    for -- so this passes `start_heartbeats=False`.

    There is no second replica to race with: `Recreate` guarantees this
    process is the only one alive. That is what makes this safe here and
    is the reason it is not simply "keep polling" -- the heartbeat claim
    is a plain PATCH rather than a compare-and-swap
    (`heartbeats.py`: "may be duplicated by a restart or another
    replica"), so a strategy that overlaps two pollers is a separate,
    larger change.
    """
    while running_heartbeat_count():
        try:
            poll_once(start_heartbeats=False)
        except Exception as e:
            log(f"poll failed while draining: {e}")
        # Re-checked after the tick as well as before it: a tick takes
        # real time, and sleeping a full interval after the last cycle
        # has finished would hold the replacement pod out for no reason.
        if not running_heartbeat_count():
            return
        time.sleep(POLL_INTERVAL_SECONDS)


def main():
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    start_invoke_server()
    # Regenerates nova/catalog.md on a timer. Here rather than in a
    # cycle's prompt because a cycle has to choose to run it, and a
    # catalog nobody regenerates is a screenshot -- see the module.
    start_catalog_refresh()
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
            _serve_while_draining()
            join_running_heartbeats()
            log("drain complete, exiting")
            return


if __name__ == "__main__":
    main()
