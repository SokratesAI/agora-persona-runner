"""Has Marcus's state document grown past what its hourly backup can carry?

Idea #233 asked a capacity question about Marcus -- *"1Gi with photos on the
roadmap (#202, #203) is a capacity question that arrives before the backup
question does"* -- and the measurement says the question was pointed at the
wrong number.

**What I measured, 2026-09-04.** Marcus's entire state is one JSON document:
11,256 bytes live at `GET /api/state`. Its volume is a 1Gi `local-path` PVC,
and the kubelet reports that volume's capacity as the *node* filesystem's
80,279,486,464 bytes -- there is no separate filesystem under it, so the
`1Gi` is a number in a manifest with nothing enforcing it. Raising it would
change no byte anywhere. That half of the question is `tools.disk_health`'s:
the ceiling Marcus can actually hit is the node's free disk, not its request.

**The number that does bind is the backup, and it binds first by a factor of
twenty.** `platform-config#603` commits the whole state document to
`SokratesAI/marcus-backup` every hour. Git stores each revision, so at N bytes
that job writes about 24N bytes of new objects a day, and GitHub warns on any
single file over 50 MiB and rejects a push carrying one over 100 MiB. So the
repo breaks while the volume is still at five per cent of its 1Gi request.
Photos going into that one document -- which is what #202 and #203 would do
if nobody says otherwise -- reach the git ceiling long before they reach the
disk.

**So the threshold here is GitHub's, not mine.** `personality.md` says a
limit needs a danger I measured; I have not measured a good size for a JSON
document, and any number I invented would be a preference wearing a
constant's clothes. `WARN_BYTES` is GitHub's own published warning threshold
for a single file, and the failure at that point is concrete and external:
the push starts producing warnings, and at twice it the backup stops working
altogether.

**Exit contract**, the same as its siblings in `tools.preflight`. 2 when the
document has crossed GitHub's warning threshold and the hourly backup is on
its way to refusing. 1 when Marcus could not be reached or its answer could
not be read -- an app that is down must never read as an app with a small
state document, which is the negative-result-guaranteed-in-advance failure
`prompt.md` spends four paragraphs on. 0 when the document is inside the
threshold, and the line says how much of the threshold it is using.
"""

import argparse
import json
import urllib.error
import urllib.request

# Repo root on sys.path so `python3 tools/marcus_capacity.py` works and not
# only `-m`. See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

DEFAULT_URL = "http://marcus.agents.svc.cluster.local:8080/api/state"
TIMEOUT = 15

#: GitHub's own documented limits for a single file in a repository: a warning
#: over 50 MiB, a rejected push over 100 MiB. Both are theirs, so neither can
#: drift from a preference of mine. The backup commits the whole document, so
#: the document's size *is* the file's size.
WARN_BYTES = 50 * 1024 * 1024
BLOCK_BYTES = 100 * 1024 * 1024

#: Commits a day the backup CronJob makes at most (`20 * * * *`). Used only to
#: state the daily git-object cost beside the size, never to decide anything.
BACKUPS_PER_DAY = 24


def read_state(url=DEFAULT_URL, opener=urllib.request.urlopen):
    """The raw bytes of Marcus's state document, or None.

    None means "I could not measure", never "it is small". Every failure
    collapses to it on purpose: unreachable, a non-200, and a body that is not
    JSON are three different problems and none of them is evidence about the
    document's size.
    """
    try:
        with opener(url, timeout=TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                return None
            body = response.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not body:
        # Redundant rather than load-bearing: `json.loads(b"")` raises below
        # and returns None by that route too. A mutation removing this line
        # survives for that reason, and the honest note is cheaper than a
        # test asserting a branch that changes no outcome.
        return None
    try:
        json.loads(body)
    except (ValueError, UnicodeDecodeError):
        # A proxy's HTML error page is bytes with a length, and a length is
        # exactly what this check reports. Refusing to measure it is the
        # point: any static responder can produce a plausible-looking number.
        return None
    return body


def human(count):
    """A byte count as the unit a person would say it in."""
    if count < 1024:
        return f"{count} B"
    for unit, scale in (("KiB", 1024), ("MiB", 1024 ** 2), ("GiB", 1024 ** 3)):
        if count < scale * 1024 or unit == "GiB":
            return f"{count / scale:.1f} {unit}"
    return f"{count} B"


def report(size, out=print, url=DEFAULT_URL):
    """One block and the exit code. Reads nothing and writes nothing.

    `url` is the address that was actually tried, not the default. Printing
    the default on a failure names an address the run never touched, which
    sends whoever reads it to check the wrong endpoint.
    """
    if size is None:
        out("CANNOT SEE  Marcus did not answer with a readable state document, "
            "so its size is unknown. That is not the same as a small document "
            f"and is not reported as one. Tried: {url}")
        return 1

    daily = size * BACKUPS_PER_DAY
    share = size / WARN_BYTES * 100
    detail = (f"         the hourly backup commits the whole document to "
              f"SokratesAI/marcus-backup, so it writes up to {human(daily)} of "
              f"new git objects a day at this size.")

    if size >= BLOCK_BYTES:
        out(f"TOO BIG  Marcus's state document is {human(size)}, past GitHub's "
            f"{human(BLOCK_BYTES)} per-file limit — a push carrying it is "
            "rejected outright, so the backup is already failing or about to.")
        out(detail)
        out("         move whatever is large out of the state document and "
            "back it up as its own object; see idea #233.")
        return 2

    if size >= WARN_BYTES:
        out(f"TOO BIG  Marcus's state document is {human(size)}, past GitHub's "
            f"{human(WARN_BYTES)} warning threshold for a single file and "
            f"{human(BLOCK_BYTES - size)} short of the size that stops the "
            "push altogether.")
        out(detail)
        out("         move whatever is large out of the state document and "
            "back it up as its own object; see idea #233.")
        return 2

    out(f"OK  Marcus's state document is {human(size)}, {share:.2f}% of "
        f"GitHub's {human(WARN_BYTES)} warning threshold for a single file.")
    out(detail)
    out("         The 1Gi PVC request is deliberately not judged here: "
        "local-path enforces no quota, so the real disk ceiling is the node's "
        "free space and that is tools.disk_health's reading.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL,
                        help="Marcus's state endpoint (default: %(default)s)")
    args = parser.parse_args(argv)
    # One read, not two: a second call is a second HTTP request that can
    # answer differently, and then the size reported is not the one measured.
    body = read_state(args.url)
    return report(None if body is None else len(body), url=args.url)


if __name__ == "__main__":
    raise SystemExit(main())
