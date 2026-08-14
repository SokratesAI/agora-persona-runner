"""Entrypoint for the `nova-site` pod: serve Nova's site, and nothing else.

Split out of `agora_runner.main` on 2026-08-09. The site used to be a
second port on the runner process, which meant it inherited the runner's
deploy shape -- `strategy: Recreate` with
`terminationGracePeriodSeconds: 2880`, both of which exist to protect a
Nova cycle's reply from being destroyed mid-flight. The consequence for
the site was that its pod sat `Terminating` (and therefore out of the
Service's endpoints) for the whole 30-45 minutes a cycle takes, so the
site was simply down while Nova was working.

That was cosmetic while the site was read-only. It stopped being
cosmetic when the capture box shipped the same day: the box Edvard types
an idea into was dead at exactly the moment he was most likely to be
reading about a cycle in progress.

So the site gets its own Deployment -- same image, so there is still
exactly one vault client in this repo rather than a third copy (which is
the whole reason nova_site.py lives in the runner and not in a service of
its own; see its module docstring), but its own process, its own
lifecycle, and a rollout that does not wait on a cycle.

This process holds far less than the runner does: no Anthropic/Gemini/
GitHub credentials, no ServiceAccount token, no kubectl access. It needs
CouchDB to read the journal and write a capture, and Agora's internal API
to record that capture in the Activity feed. It is also the only one of
the two reachable from the tailnet.
"""

import signal
import time

from agora_runner.log import log
from agora_runner.nova_site import start_nova_site

# Set by the SIGTERM/SIGINT handler, read by the loop below. A plain bool
# rather than a threading.Event for the same reason main.py gives: a signal
# handler runs on the main thread between bytecodes, so Event.set() could
# re-enter a lock that thread already holds inside Event.wait(). A bool
# assignment cannot deadlock.
_shutdown_requested = False

# How long the main thread sleeps between checks of the flag above. PEP 475
# makes time.sleep resume after an interrupt rather than return early, so
# one long sleep would ignore a signal until it elapsed.
SHUTDOWN_POLL_SECONDS = 1.0


def shutdown_requested():
    return _shutdown_requested


def _request_shutdown(signum, _frame):
    global _shutdown_requested
    _shutdown_requested = True
    log(f"nova-site received signal {signum}, shutting down")


def main():
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    server = start_nova_site()
    try:
        while not _shutdown_requested:
            time.sleep(SHUTDOWN_POLL_SECONDS)
    finally:
        # serve_forever runs on a daemon thread, so shutdown() is called
        # from a different thread than the one it stops, which is the
        # documented way round. server_close() releases the listening
        # socket -- without it the port stays bound for the rest of the
        # process's life, which matters in tests far more than in the pod.
        server.shutdown()
        server.server_close()
    log("nova-site stopped")


# The pod starts this through `run_nova_site.py`, so for months nothing
# needed this guard. What it costs is a silent one: `python -m
# agora_runner.nova_site_main` imports the module, defines `main`, calls
# nothing, and exits 0 with no output -- which reads exactly like a server
# that started and immediately died. Cycle 196 spent a third of its hour on
# that, concluded the local run was broken, and merged the wrong command
# into `tools/see_page`'s docstring before catching it. Both spellings work
# now.
if __name__ == "__main__":
    main()
