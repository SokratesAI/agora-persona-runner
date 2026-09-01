"""Regenerates `nova/catalog.md` on a timer, inside the runner pod.

Step 3 of the IDP roadmap (`resources/research/idp-2026-08.md`), and the
one the previous two steps make necessary rather than optional. Step 1
built the catalog; step 2 put it on a page in the Nova app. Both read a
vault document that only ever changed when a cycle typed the command --
so the page's own freshness line, which exists precisely because "a
catalog nobody regenerates is a screenshot", was the honest report of a
document last written by whichever cycle last remembered.

**A timer here rather than a line in `prompt.md`.** This loop has filed
the same class of failure repeatedly: a rule that lives only in a markdown
file is a rule that competes with everything else a cycle could do that
hour, and loses to correct prioritisation rather than to neglect. The
runner polls forever anyway; a daemon thread beside that loop costs
nothing and asks nobody to remember.

**Why the runner pod and not `nova-site`.** The site deliberately has no
cluster access at all -- no `serviceAccountName` on its Deployment, so it
runs as `default` and can read nothing -- and step 2's design note says
that is the point: a page with cluster RBAC is a much larger security
question than a page that reads one document. The runner already holds a
service account that can list Deployments, Ingresses, Applications and
the Crossplane claim kinds, because `kubectl_read` needs it. So the split
stays: the runner reads the cluster and writes the document, the site
reads the document.

Failure is logged and swallowed, every time. A refresher that can take the
runner down would be a worse bargain than a stale catalog, and a partial
read is not a failure -- `render` already replaces the coverage number
with a named refusal and omits the sections whose source did not answer,
which is a more useful page than the confident one it replaces.
"""

import os
import threading

from agora_runner.log import log

# Hourly, which is what the roadmap asked for. Long enough that the four
# `kubectl` calls are noise against the poll loop beside them, short enough
# that the freshness line on the page is never the interesting thing on it.
REFRESH_INTERVAL_SECONDS = float(os.environ.get("CATALOG_REFRESH_SECONDS", "3600"))

# The first run waits, rather than firing at startup. The runner restarts on
# every deploy of this repo, which is several times a day on a working
# afternoon, and a refresh on each of those would be a burst of cluster reads
# timed to the exact moment the cluster is changing under them.
FIRST_REFRESH_SECONDS = float(os.environ.get("CATALOG_FIRST_REFRESH_SECONDS", "300"))

_thread = None


def refresh_once():
    """One build-and-publish. Returns True if the vault was written."""
    from agora_runner import catalog_build

    try:
        _text, status = catalog_build.publish()
    except Exception as exc:
        log(f"catalog refresh failed: {type(exc).__name__}: {exc}")
        return False
    if status:
        # Published anyway -- see the module docstring. Logged because a
        # source that stops answering is worth noticing here, where it is
        # named, rather than only as a paragraph on a page.
        log("catalog refreshed, but at least one source was unreadable")
    else:
        log("catalog refreshed")
    return True


def _run(stop):
    if stop.wait(FIRST_REFRESH_SECONDS):
        return
    while True:
        refresh_once()
        if stop.wait(REFRESH_INTERVAL_SECONDS):
            return


def start_catalog_refresh(stop=None):
    """Start the refresher once. Returns the thread, or None if already up.

    `stop` is an Event the caller can set to end it; the runner does not
    pass one, because the thread is a daemon and the process exiting is
    the only shutdown it needs. The parameter exists so a test can run the
    loop for real and then stop it, rather than asserting on a sleep.
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return None
    stop = stop if stop is not None else threading.Event()
    _thread = threading.Thread(target=_run, args=(stop,), daemon=True, name="catalog-refresh")
    _thread.start()
    log(
        f"catalog refresh every {REFRESH_INTERVAL_SECONDS:.0f}s, "
        f"first in {FIRST_REFRESH_SECONDS:.0f}s"
    )
    return _thread
