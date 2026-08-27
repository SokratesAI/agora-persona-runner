"""Read-only questions about the owner's home NAS: Sonarr and Radarr.

Cycle 511, on his own capture:

    A future idea or goal would be to connect to my home nas (i have
    allready written an idea about this). This nas runs some Docker
    containers, Plex, sonarr and radarr and nzbget. It would be very fun
    to also connect you through a Google Home and we can talk to you and
    ask you questions example "when is great British bakeoff airing? And
    can you grab it through sonarr when it does?" And you are able to do
    so.

`nova/resources/research/nas-voice-front-end-2026-08.md` staged that into
five steps and this file is step 2, deliberately taken before the voice
half. His literal example question -- *when is great British bakeoff
airing* -- maps onto two documented GET calls and nothing else, so it is
answerable today, in the chat dock, with no Google Home and no Home
Assistant in the picture at all.

    python3 -m tools.nas status
    python3 -m tools.nas airing 'great british bake off'
    python3 -m tools.nas calendar --days 7

**Read-only is enforced here, not merely intended.** Every request this
module makes is built by `_get`, which is the only function that touches
the network: it hardcodes the method, refuses any path not in `READ_ONLY`,
and never carries a body. The second half of his question -- *and can you
grab it through sonarr when it does* -- is `POST /api/v3/series`, a write
that puts files on his disk, and it is deliberately not reachable from
here. The reason is not caution about the API. It is that I read untrusted
text all cycle and act on it with an unrestricted shell, so a standing
add-series capability is a standing capability to fill his disk on a
stranger's say-so. Step 3 of the research file puts that behind a
confirmation, as a proposal rather than an unattended act.

**Nothing here can reach the NAS until he does step 1**, which is his and
physical: Tailscale installed on the NAS itself, the node tagged rather
than owned by his personal account, and a grant narrow enough that the
`agents` namespace reaches only these two ports. So the tool is
configuration-gated and says exactly which four values are missing rather
than failing with a connection error that reads like a bug.

Exit status, matching the health checks in this package so a cycle can
read it without parsing the text: 0 means the question was answered, 1
means a service was unreachable, unconfigured, or refused the key. There
is no 2 -- this is a question, not an alarm, and nothing it finds is a
finding about the loop.

Endpoints and field names are taken from the published OpenAPI documents
(`Sonarr.Api.V3/openapi.json`, `Radarr.Api.V3/openapi.json`, both
`3.0.0`), not from memory: the key is the `X-Api-Key` header, the calendar
is `GET /api/v3/calendar?start=&end=`, and Sonarr's episode carries
`airDateUtc` while Radarr's movie carries three date-only release fields.
"""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "nova-nas/1"

try:  # tzdata is present on both pods; a fixed offset would be wrong all winter
    from zoneinfo import ZoneInfo

    OSLO = ZoneInfo("Europe/Oslo")
except Exception:  # pragma: no cover - only if the image loses tzdata
    OSLO = dt.timezone(dt.timedelta(hours=1))

# The whole read-only boundary. A path not on this list cannot be fetched,
# and there is no entry here that is not a GET.
READ_ONLY = {
    "/api/v3/system/status",
    "/api/v3/calendar",
    "/api/v3/series",
    "/api/v3/series/lookup",
    "/api/v3/movie",
    "/api/v3/movie/lookup",
}

SERVICES = ("sonarr", "radarr")


class NotConfigured(Exception):
    """A service has no URL or no API key set."""


class Unreachable(Exception):
    """A service is configured but did not answer usefully."""


def config(env=None):
    """The four values this tool needs, read from the environment.

    Returns `{service: {"url":..., "key":...}}` for services that have
    both, so a NAS running only Radarr is a usable NAS rather than an
    error. `SONARR_URL` may carry a scheme or not; a bare host gets
    `http://`, because the whole point of step 1 is that the transport
    security is Tailscale's rather than a certificate on a home box.
    """
    env = os.environ if env is None else env
    out = {}
    for name in SERVICES:
        url = (env.get(f"{name.upper()}_URL") or "").strip().rstrip("/")
        key = (env.get(f"{name.upper()}_API_KEY") or "").strip()
        if not url or not key:
            continue
        if "://" not in url:
            url = "http://" + url
        out[name] = {"url": url, "key": key}
    return out


def _get(service, conf, path, params=None, opener=urllib.request.urlopen):
    """The only function here that touches the network.

    Hardcodes GET, refuses a path outside `READ_ONLY`, and never sends a
    body. Callers pass a path and a query dict; they cannot pass a method.
    """
    if path not in READ_ONLY:
        raise ValueError(f"{path} is not a read-only endpoint this tool may call")
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = conf["url"] + path + (f"?{query}" if query else "")
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"X-Api-Key": conf["key"], "User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with opener(req, timeout=15) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise Unreachable(f"{service} refused the API key ({exc.code})") from exc
        raise Unreachable(f"{service} answered {exc.code} on {path}") from exc
    except Exception as exc:  # socket errors, DNS, timeouts
        raise Unreachable(f"{service} did not answer: {exc}") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        # A captive portal or a proxy answers 200 with HTML. A 200 is not
        # the measurement; JSON that parses is the cheapest thing only the
        # real service produces.
        raise Unreachable(f"{service} answered {len(body)} bytes that are not JSON") from exc


def _to_oslo(stamp):
    """`2026-08-27T19:00:00Z` -> `27 Aug 21:00`, in Oslo time.

    Sonarr hands out `airDateUtc` and this loop writes Oslo, so the
    conversion happens once, here, rather than in each caller. Anything
    unparseable is handed back untouched -- a wrong-looking string is
    better than a crash or an invented time.
    """
    if not stamp:
        return None
    try:
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(OSLO).strftime("%d %b %H:%M")


def _window(days, today=None):
    start = today or dt.date.today()
    return start.isoformat(), (start + dt.timedelta(days=days)).isoformat()


def calendar(conf_all, days=7, get=_get, today=None):
    """Everything airing or releasing in the next `days`, both services.

    Returns `(lines, failures)`. A service that is down does not take the
    other one with it: half an answer plus a named failure is more useful
    than one error, and it is the difference between "your NAS is off" and
    "Radarr is off".
    """
    start, end = _window(days, today)
    lines, failures = [], []
    for name in SERVICES:
        conf = conf_all.get(name)
        if not conf:
            continue
        try:
            rows = get(name, conf, "/api/v3/calendar", {"start": start, "end": end})
        except Unreachable as exc:
            failures.append(str(exc))
            continue
        for row in rows or []:
            line = _episode_line(row) if name == "sonarr" else _movie_line(row)
            if line:
                lines.append(line)
    return lines, failures


def _episode_line(ep):
    series = (ep.get("series") or {}).get("title") or "unknown series"
    when = _to_oslo(ep.get("airDateUtc")) or ep.get("airDate") or "date unknown"
    code = f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}"
    have = "have it" if ep.get("hasFile") else "not downloaded"
    return f"{when}  {series} {code} — {ep.get('title') or 'untitled'} ({have})"


def _movie_line(movie):
    # Radarr's three release fields are date-only and any of them may be
    # absent; the earliest one present is the one the calendar row is for.
    dates = [movie.get(k) for k in ("inCinemas", "digitalRelease", "physicalRelease")]
    when = next((d for d in dates if d), None)
    when = (when or "")[:10] or "date unknown"
    have = "have it" if movie.get("hasFile") else "not downloaded"
    return f"{when}  {movie.get('title') or 'untitled'} ({movie.get('year') or '?'}) ({have})"


def airing(conf_all, term, get=_get, today=None):
    """His literal question: when is <term> next on?

    Two steps, and the order matters. First the library -- `GET
    /api/v3/series` returns every series he actually has, and each carries
    `nextAiring`, so a monitored show answers in one call with no window
    at all. Only if nothing in the library matches does it fall back to
    `series/lookup`, which asks the metadata provider about a show he does
    not have; that answer is real information ("it exists, it is not
    yours") and is labelled as such rather than dressed up as a hit.
    """
    conf = conf_all.get("sonarr")
    if not conf:
        raise NotConfigured("sonarr")
    needle = _normalise(term)
    series = get("sonarr", conf, "/api/v3/series") or []
    hits = [s for s in series if needle in _normalise(s.get("title") or "")]
    lines = []
    for s in hits:
        nxt = _to_oslo(s.get("nextAiring"))
        if nxt:
            lines.append(f"{s['title']}: next episode {nxt} (Oslo)")
        elif s.get("ended"):
            prev = _to_oslo(s.get("previousAiring"))
            lines.append(f"{s['title']}: ended{f', last aired {prev}' if prev else ''}")
        else:
            prev = _to_oslo(s.get("previousAiring"))
            lines.append(
                f"{s['title']}: in your library, no next air date known"
                f"{f' (last aired {prev})' if prev else ''}"
            )
    if lines:
        return lines
    found = get("sonarr", conf, "/api/v3/series/lookup", {"term": term}) or []
    return [
        f"not in your library — {s.get('title')} ({s.get('year') or '?'}), "
        f"{s.get('status') or 'status unknown'}"
        for s in found[:5]
    ]


def _normalise(title):
    """Fold case and drop everything but letters and digits.

    He types "great British bakeoff"; Sonarr holds "The Great British Bake
    Off". Spaces are not a reliable separator between those two, so they
    are removed rather than split on -- `greatbritishbakeoff` is a
    substring of `thegreatbritishbakeoff`, and it would not be if either
    side kept its spaces.
    """
    return "".join(c for c in title.lower() if c.isalnum())


def status(conf_all, get=_get):
    """One line per configured service: version, or why not."""
    lines, ok = [], True
    for name in SERVICES:
        conf = conf_all.get(name)
        if not conf:
            lines.append(f"not configured  {name} — set {name.upper()}_URL and {name.upper()}_API_KEY")
            ok = False
            continue
        try:
            info = get(name, conf, "/api/v3/system/status")
        except Unreachable as exc:
            lines.append(f"unreachable     {name} — {exc}")
            ok = False
            continue
        lines.append(f"ok              {name} {info.get('version') or '?'} at {conf['url']}")
    return lines, ok


UNCONFIGURED_HELP = (
    "Nothing on this NAS is reachable yet, and that is expected rather than broken.\n"
    "Step 1 of nova/resources/research/nas-voice-front-end-2026-08.md is his own and\n"
    "physical: Tailscale on the NAS itself, the node tagged, one narrow grant to the\n"
    "agents namespace. Once that is done, four values make this tool work:\n"
    "  SONARR_URL / SONARR_API_KEY   RADARR_URL / RADARR_API_KEY\n"
    "The key is Settings -> General -> API Key in each app. The URL may be a bare\n"
    "host:port; it gets http:// because the transport security here is Tailscale's."
)


def main(argv=None, env=None, get=_get, out=sys.stdout):
    parser = argparse.ArgumentParser(prog="python3 -m tools.nas", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="is each service reachable")
    cal = sub.add_parser("calendar", help="what is airing or releasing soon")
    cal.add_argument("--days", type=int, default=7)
    air = sub.add_parser("airing", help="when is a named show next on")
    air.add_argument("term", nargs="+")
    args = parser.parse_args(argv)

    conf_all = config(env)
    if not conf_all:
        print(UNCONFIGURED_HELP, file=out)
        return 1

    if args.cmd == "status":
        lines, ok = status(conf_all, get=get)
        for line in lines:
            print(line, file=out)
        return 0 if ok else 1

    if args.cmd == "calendar":
        lines, failures = calendar(conf_all, days=args.days, get=get)
        for line in sorted(lines):
            print(line, file=out)
        if not lines and not failures:
            print(f"nothing airing or releasing in the next {args.days} day(s).", file=out)
        for failure in failures:
            print(f"could not read: {failure}", file=out)
        return 1 if failures else 0

    term = " ".join(args.term)
    try:
        lines = airing(conf_all, term, get=get)
    except NotConfigured:
        print("airing needs Sonarr: set SONARR_URL and SONARR_API_KEY.", file=out)
        return 1
    except Unreachable as exc:
        print(f"could not read: {exc}", file=out)
        return 1
    if not lines:
        print(f"nothing called {term!r} in your library, and the lookup found nothing either.", file=out)
        return 0
    for line in lines:
        print(line, file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
