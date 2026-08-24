"""Ask the live Nova site whether the page a cycle just shipped is there.

This loop ships changes to the app the owner reads and has never once looked
at the result. Cycle 179 built the `/retro` page and wrote "only you can
see it"; Cycle 184 tried to reach the pod, got nothing, and filed *"no
cycle can reach nova-site"* as a fact. That fact was false. The service
listens on **8083**; the probe tried 80, 8000, 8080 and 3000, and the
`Connection refused` it got back is what a resolved name on a closed port
says. DNS had answered the whole time, and `allow-intra-namespace-ingress`
has allowed this for 142 days.

So this exists to make that check one command instead of a rediscovery:

    python3 -m agora_runner.site_check      # from the runner pod

Silent and exit 0 when the site is serving what the client reads; prints
what is wrong and exits 1 otherwise -- the same contract as
`cycle_health`, because a cycle runs both for the same reason.

**Run it from `terminal_exec`, not `Bash`.** The bridge pod has no route
into the namespace; the runner pod does, and ships this module at
`/app/agora_runner`.

## The negative control is the point

Every page route here returns the same HTML shell, because this is a
single-page app -- so "GET /retro came back 200" is not on its own
evidence that `/retro` is routed. A server that served the shell for
literally everything would pass a check built only out of positives, and
that is the Cycle 53 failure written into a tool: a test whose negative
result was impossible is not evidence.

`ABSENT_PATH` is therefore checked first and its expected answer is 404.
If that comes back 200, every page assertion below it is meaningless and
this says so instead of reporting a healthy site.
"""

import json
import os
import urllib.error
import urllib.request

from agora_runner.nova_site import PAGE_ROUTES

DEFAULT_BASE = "http://nova-site.agents.svc.cluster.local:8083"

# A path no route can ever claim. If this answers 200 the server is
# serving the shell for everything and no page check below means anything.
ABSENT_PATH = "/__site_check_absent__"

# The top-level keys each payload must carry, named for what the client
# reads rather than for what the builder happens to return -- a payload
# that quietly stopped carrying `entries` is a blank feed, and a 200 with
# valid JSON hides that completely.
API_KEYS = {
    "/api/journal": ("entries", "status"),
    # `needsthe owner` was here until #236. It stopped being served when the
    # Needs the owner block's server half was deleted, and a key listed here
    # that the payload no longer carries is a permanent false alarm on a
    # healthy site -- the exact failure this module exists to catch, aimed
    # at itself. `test_the_digest_keys_are_ones_the_payload_still_carries`
    # is what stops the next deletion doing it again.
    "/api/digest": ("nextCycle", "lines"),
    "/api/comments": ("byCycle",),
    "/api/costs": ("generatedAt", "cycleColumns"),
    "/api/notes": ("notes", "waitingTotal"),
    "/api/retro": ("scoreKeys", "retros"),
    "/api/health": ("ok", "databases"),
}

# What the shell must actually contain. A 200 carrying an error page is
# still a 200, and `index.html` is the one response every page shares, so
# the marker is the script tag the client cannot run without.
SHELL_MARKER = "/app.js"

TIMEOUT_SECONDS = 20


def fetch(base, path, timeout=TIMEOUT_SECONDS):
    """`(status, body_bytes)`. An HTTP error is an answer, not an exception.

    A 404 is a result this tool asserts on -- `ABSENT_PATH` needs one --
    so `HTTPError` is unwrapped rather than raised. Anything below HTTP,
    a refused connection or a name that will not resolve, is a different
    kind of failure and comes back as `(None, reason)` so the caller can
    say "could not reach" rather than mislabelling it as a bad page.
    """
    try:
        with urllib.request.urlopen(base.rstrip("/") + path, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # URLError, socket.timeout, ssl, DNS
        return None, f"{type(e).__name__}: {e}".encode("utf-8")


def findings(base=DEFAULT_BASE, fetcher=fetch):
    """Everything wrong with the live site, as a list of sentences.

    Empty means the site is serving what the client reads. The order
    matters: the control comes first, and when it fails the page checks
    are dropped rather than reported, because their answers cannot be
    interpreted.
    """
    out = []

    status, body = fetcher(base, ABSENT_PATH)
    if status is None:
        return [f"could not reach {base}: {body.decode('utf-8', 'replace')}"]
    if status != 404:
        return [
            f"control probe {ABSENT_PATH} answered {status}, not 404 -- the server is "
            "answering paths it does not route, so no page check below can mean anything"
        ]

    for path in PAGE_ROUTES:
        status, body = fetcher(base, path)
        if status is None:
            out.append(f"{path}: could not reach: {body.decode('utf-8', 'replace')}")
        elif status != 200:
            out.append(f"{path}: {status}, expected 200")
        elif SHELL_MARKER not in body.decode("utf-8", "replace"):
            out.append(f"{path}: 200 but the body does not load {SHELL_MARKER}")

    for path, keys in sorted(API_KEYS.items()):
        status, body = fetcher(base, path)
        if status is None:
            out.append(f"{path}: could not reach: {body.decode('utf-8', 'replace')}")
            continue
        if status != 200:
            out.append(f"{path}: {status}, expected 200")
            continue
        try:
            payload = json.loads(body)
        except ValueError as e:
            out.append(f"{path}: 200 but the body is not JSON: {e}")
            continue
        if not isinstance(payload, dict):
            out.append(f"{path}: 200 but the payload is {type(payload).__name__}, not an object")
            continue
        missing = [key for key in keys if key not in payload]
        if missing:
            out.append(f"{path}: 200 but missing {', '.join(missing)}")

    return out


def main(argv=None):
    base = os.environ.get("NOVA_SITE_URL") or DEFAULT_BASE
    if argv:
        base = argv[0]
    problems = findings(base)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main(sys.argv[1:]))
