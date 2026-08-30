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
import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "nova-nas/1"

# The SSH transport. Cycle 631.
#
# Everything above was written for a direct HTTP call from a pod to the NAS,
# and that call cannot be made: `allow-nas-ssh-egress` in the `agents`
# namespace opens port 22 and nothing else, so `:8989` is refused by my own
# kernel in 0.000000s (measured Cycle 630, and again this cycle). Port 22 is
# open and a sealed key is already mounted at `/etc/nas-ssh/id_ed25519` on the
# runner pod, and the NAS has `curl`. So the request is made *on the NAS*,
# over the one port that is open, instead of waiting for a NetworkPolicy and a
# Tailscale grant that nobody has asked for.
#
# Two things this deliberately does not do. It does not put the URL or the API
# key on a remote command line: the remote command is the constant string
# `curl --config -` and everything else arrives on stdin, so nothing derived
# from a search term the owner typed is ever parsed by a shell, and the key is
# not visible in `ps` to the other accounts on that box. And it does not
# follow redirects -- the `_NoRedirect` opener above exists because urllib
# copies headers onto a redirect target, and curl's default of not following
# one is the same guarantee, so the config below never says `location`.
SSH_DEFAULTS = {"host": "100.89.37.25", "user": "nova", "key": "/etc/nas-ssh/id_ed25519"}

# Loopback on the NAS itself, which is where the SSH session lands.
LOOPBACK_PORTS = {"sonarr": 8989, "radarr": 7878}

SSH_OPTS = (
    "-o", "BatchMode=yes",
    # The runner pod mounts a read-only home, so ssh cannot write a
    # known_hosts file at all; pinning the host key would have to happen in
    # the sealed secret beside the private key, and it does not today. The
    # hop is tailnet-only (port 22 is the single thing the NetworkPolicy
    # opens) and this is worth naming rather than hiding: it is trust in
    # WireGuard's peer keys standing in for trust in an SSH host key.
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "LogLevel=ERROR",
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib copies every header onto a redirect target, with no same-origin
    check, so a host that answers 302 would forward the API key wherever it
    liked. Nothing here has any reason to follow one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"refused a redirect to {newurl}", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)

try:  # tzdata is present on both pods; a fixed offset would be wrong all winter
    from zoneinfo import ZoneInfo

    OSLO = ZoneInfo("Europe/Oslo")
except Exception:  # pragma: no cover - only if the image loses tzdata
    OSLO = dt.timezone(dt.timedelta(hours=1))

# Half the read-only boundary: a path not on this list cannot be fetched.
# The other half, and the load-bearing one, is `method="GET"` in `_get` --
# `/api/v3/series` also accepts POST, so the list is not by itself a list of
# read-only operations. It holds exactly the four paths this module calls and
# nothing else, because an allowlist that grants more than the code uses is
# the one thing an allowlist must not do.
READ_ONLY = {
    "/api/v3/system/status",
    "/api/v3/calendar",
    "/api/v3/series",
    "/api/v3/series/lookup",
    # Read by `tools.nas_watch`, which asks the two apps whether anything is
    # configured to run a command when an event fires. A GET, listed here for
    # the same reason as the four above: this set is the only thing that
    # decides what this module may ask for.
    "/api/v3/notification",
    # Read by `tools.nas_egress`, which asks where each app is configured to
    # send its downloads. A GET, listed here for the same reason as the five
    # above: this set is the only thing that decides what this module may ask
    # for.
    "/api/v3/downloadclient",
}

SERVICES = ("sonarr", "radarr")

# nzbget is the third media service on the NAS and it is not an *arr: no API
# key, no `/api/v3`, and a JSON-RPC control interface behind HTTP basic auth.
# It is here rather than in `SERVICES` because every function above that takes
# a service assumes the *arr shape, and widening that shape to fit one
# different service would make three call sites lie about what they accept.
NZBGET_PORT = 6789

# The nzbget half of the read-only boundary, and the same rule as `READ_ONLY`
# above: exactly the two paths this module calls and nothing else. Both are
# reads. The one that matters by its absence is `/jsonrpc/saveconfig`, which
# is how a caller sets `Extensions` -- that is the write this module must
# never be able to make, so it is not on the list.
NZBGET_READ_ONLY = {"/jsonrpc/version", "/jsonrpc/config"}

# Plex is the fourth media service on that box and the third shape: a Synology
# package rather than a docker-compose container, with no API key and no basic
# auth on the one endpoint this module calls. It is not in `SERVICES` for the
# same reason nzbget is not -- every function that takes a service assumes the
# *arr shape.
PLEX_PORT = 32400

# `/identity` is the whole Plex surface this module may touch. It is the one
# Plex endpoint that answers without a token (measured Cycle 645: HTTP 200,
# 213 bytes of XML) and it carries the running version and nothing about the
# library. Everything else on :32400 needs `X-Plex-Token`, and none of it is
# on this list, so there is no path from here to his media or his account.
PLEX_READ_ONLY = {"/identity"}

_UNSET = object()  # `ssh=None` means "no hop"; not passing it at all means "find out"


class NotConfigured(Exception):
    """A service has no URL or no API key set."""


class Unreachable(Exception):
    """A service is configured but did not answer usefully."""


class NothingToSearchFor(Exception):
    """The search term had no letters or digits in it."""


def ssh_config(env=None, exists=os.path.exists, which=shutil.which):
    """The SSH hop to the NAS, or `None` if this pod has no way to make it.

    Two things have to be true and both are measurements rather than
    settings: an `ssh` binary on `PATH` (the bridge pod has none, the runner
    pod does) and a readable private key. Host, user and key path each take
    an env override so nothing here is pinned to one box, but they default to
    the values that are already true today -- the tailnet address is in the
    vault write-up, not a secret, and the key itself stays a sealed secret in
    the cluster.
    """
    env = os.environ if env is None else env
    key = (env.get("NAS_SSH_KEY") or SSH_DEFAULTS["key"]).strip()
    if not which("ssh") or not exists(key):
        return None
    return {
        "host": (env.get("NAS_SSH_HOST") or SSH_DEFAULTS["host"]).strip(),
        "user": (env.get("NAS_SSH_USER") or SSH_DEFAULTS["user"]).strip(),
        "key": key,
    }


def _run_ssh(ssh, stdin, timeout=25, run=subprocess.run):
    """Run the one remote command, feeding it `stdin`. Returns its stdout.

    The remote command is a constant. Everything variable travels on stdin,
    which is the whole reason a shell on the far side cannot be made to do
    anything with it.
    """
    argv = ["ssh", "-i", ssh["key"], *SSH_OPTS, f"{ssh['user']}@{ssh['host']}", "curl --config -"]
    try:
        done = run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise Unreachable(f"the NAS did not answer within {timeout}s") from exc
    except OSError as exc:
        raise Unreachable(f"could not start ssh: {exc}") from exc
    if done.returncode == 255:
        raise Unreachable(f"ssh to {ssh['host']} failed: {(done.stderr or '').strip() or 'no detail'}")
    if done.returncode != 0:
        raise Unreachable(f"curl on the NAS exited {done.returncode}: {(done.stderr or '').strip()}")
    return done.stdout


def _curl_config(url, headers=()):
    """A curl config file, as text, carrying the URL and headers on stdin.

    `write-out` puts the status code on its own last line, because curl's
    exit status alone does not separate a 401 from a 200 and this module
    reports "refused the API key" differently from "answered 500".
    """
    for value in (url, *headers):
        # A quote, a backslash or a newline in a value ends or re-opens the
        # entry, which is the same class of bug as the one measured on the NAS
        # below -- curl keeps going and drops what it did not understand. The
        # URL is built by `urlencode` and the key is hex, so nothing reaches
        # here with one today; the values are still env-overridable and an
        # allowlist that trusts its inputs is not one.
        if any(c in value for c in '"\\\n\r'):
            raise ValueError(f"a curl config value cannot carry a quote, a backslash or a newline: {value!r}")
    lines = [f'url = "{url}"']
    lines += [f'header = "{h}"' for h in headers]
    # The two characters backslash-n, not a newline: this is a value inside a
    # quoted string in a curl config file, and a real newline there ends the
    # entry. Measured on the NAS -- with a real newline curl kept the `url`
    # line, silently dropped every line after it, and returned a body with no
    # status code and no API key header, which reads exactly like a service
    # that answered.
    lines += ['max-time = 15', 'silent', 'show-error', 'write-out = "\\n%{http_code}"']
    return "\n".join(lines) + "\n"


def _fetch_over_ssh(ssh, url, headers=(), run=subprocess.run):
    """`(body, status)` for one GET made on the NAS."""
    out = _run_ssh(ssh, _curl_config(url, headers), run=run)
    body, _, code = out.rpartition("\n")
    try:
        return body, int(code)
    except ValueError as exc:
        raise Unreachable(f"could not read a status code off curl's output: {out[:120]!r}") from exc


def _plex_url(path, port=None):
    if path not in PLEX_READ_ONLY:
        raise ValueError(f"{path} is not a read-only endpoint this tool may call")
    return f"http://127.0.0.1:{port or PLEX_PORT}{path}"


def plex_version(ssh, port=None, run=subprocess.run):
    """The Plex Media Server version running on the NAS, read over the hop.

    Raises `Unreachable` when Plex did not answer in a way that carries one.
    There is no "assume it is fine" branch: a version this cannot read must
    never come back looking like a version that is current, which is the same
    contract `tools.nas_versions` already holds for the two *arr apps.

    `/identity` answers unauthenticated and returns a `MediaContainer` element
    whose `version` attribute is the build string -- `1.41.6.9685-d301f511a`
    on the NAS today. It is parsed as XML rather than matched with a regular
    expression because an attribute is a structure and a substring search over
    a document is the failure `agora_runner.md_sections` exists to end.
    """
    body, code = _fetch_over_ssh(ssh, _plex_url("/identity", port), run=run)
    if code != 200:
        raise Unreachable(f"plex answered {code} on /identity")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise Unreachable(f"plex answered {len(body)} bytes that are not XML") from exc
    version = (root.get("version") or "").strip()
    if not version:
        raise Unreachable("plex's /identity carried no `version` attribute")
    return version


def nzbget_credential(env=None):
    """`(user, password)` for nzbget's control interface, or `None`.

    There is no discovery path for this one and the absence is measured, not
    assumed. Sonarr and Radarr hand out their API key on an unauthenticated
    `/initialize.js`, which is what `discover_key` below reads; nzbget's
    equivalent is `/jsonrpc/config`, and that is behind the very credential we
    would be trying to find. The other obvious route -- reading
    `nzbget.conf` off the NAS filesystem through the SSH hop -- is closed:
    `curl` on that box is built without the `file` protocol and answers
    `Protocol "file" not supported or disabled in libcurl` (measured Cycle
    640 against `file:///volume1/docker/nzbget/nzbget.conf`).

    So the credential has to be handed in, and when it has not been, the
    caller says which check it could not run rather than guessing at a
    password. Nothing in this repo carries a default to try: a list of vendor
    default passwords in source is a credential-stuffing list, and it would
    also be the wrong instrument -- `nzbget_unlocked` below answers "is the
    lock on" without needing to open it.
    """
    env = os.environ if env is None else env
    user = (env.get("NZBGET_USER") or "").strip()
    password = (env.get("NZBGET_PASS") or "").strip()
    return (user, password) if user and password else None


def _nzbget_url(path, port=None):
    if path not in NZBGET_READ_ONLY:
        raise ValueError(f"{path} is not a read-only endpoint this tool may call")
    return f"http://127.0.0.1:{port or NZBGET_PORT}{path}"


def nzbget_unlocked(ssh, port=None, run=subprocess.run):
    """Does nzbget's control interface answer with no credential at all?

    Returns `True` if it does, `False` if it refuses. Raises `Unreachable` if
    it did not answer in a way that separates the two.

    This is the whole check that needs no password, and it is the one worth
    having: nzbget binds `0.0.0.0` and its JSON-RPC carries `saveconfig`, so a
    caller that gets in without a credential can set `ScriptDir` and
    `Extensions` and have the NAS run an executable on every download. A 401
    is therefore the healthy answer and the only healthy answer.

    Note what a 401 does *not* say: it says the lock is on, not that the key
    is hard to guess. The strength of the password is a separate question and
    this cannot see it -- measuring it would mean trying passwords, which is
    not a thing a monitoring check should do to its owner's box.
    """
    body, code = _fetch_over_ssh(ssh, _nzbget_url("/jsonrpc/version", port), run=run)
    if code in (401, 403):
        return False
    if code == 200:
        return True
    raise Unreachable(f"nzbget answered {code} on /jsonrpc/version, which is neither open nor locked")


def nzbget_config(ssh, credential, port=None, run=subprocess.run):
    """nzbget's full configuration as `{name: value}`, read over the hop.

    `/jsonrpc/config` returns `{"result": [{"Name":..., "Value":...}, ...]}`.
    Names are folded to lower case here because nzbget is inconsistent about
    them across versions -- the running box serves `ControlIp` where its own
    documentation says `ControlIP` -- and a check that misses a setting
    because of one letter reads exactly like a check that found nothing.
    """
    user, password = credential
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    body, code = _fetch_over_ssh(
        ssh, _nzbget_url("/jsonrpc/config", port), headers=(f"Authorization: Basic {token}",), run=run
    )
    if code in (401, 403):
        raise Unreachable(f"nzbget refused the control credential ({code})")
    if code != 200:
        raise Unreachable(f"nzbget answered {code} on /jsonrpc/config")
    try:
        rows = json.loads(body)["result"]
    except (ValueError, KeyError, TypeError) as exc:
        raise Unreachable(f"nzbget answered {len(body)} bytes that are not a JSON-RPC config") from exc
    if not isinstance(rows, list):
        raise Unreachable(f"nzbget's config is {type(rows).__name__}, not a list")
    return {str(r.get("Name", "")).lower(): str(r.get("Value", "")) for r in rows if isinstance(r, dict)}


def discover_key(service, ssh, run=subprocess.run):
    """Read a service's API key off its own unauthenticated `/initialize.js`.

    This is not a clever trick and it is not a new exposure: Cycle 630
    measured that Sonarr and Radarr serve that file to anyone who loads the
    page, and the owner was told exactly that and chose on 2026-08-29 to leave
    both apps without a login. So the key is already readable by every device
    on the LAN, and reading it from the box I already hold SSH on adds
    nothing. `SONARR_API_KEY` in the environment still wins, so the day he
    puts a login on these, this stops being the path.

    Returns the key, or `None` if the page did not carry one.
    """
    port = LOOPBACK_PORTS.get(service)
    if not port:
        return None
    try:
        body, code = _fetch_over_ssh(ssh, f"http://127.0.0.1:{port}/initialize.js", run=run)
    except Unreachable:
        return None
    if code != 200:
        return None
    found = re.search(r"""apiKey:\s*['"]([0-9a-fA-F]{16,})['"]""", body)
    return found.group(1) if found else None


def config(env=None, ssh=_UNSET, run=subprocess.run):
    """The four values this tool needs, read from the environment.

    Returns `{service: {"url":..., "key":...}}` for services that have
    both, so a NAS running only Radarr is a usable NAS rather than an
    error. `SONARR_URL` may carry a scheme or not; a bare host gets
    `http://`, because the whole point of step 1 is that the transport
    security is Tailscale's rather than a certificate on a home box.
    """
    env = os.environ if env is None else env
    ssh = ssh_config(env) if ssh is _UNSET else ssh
    out = {}
    for name in SERVICES:
        url = (env.get(f"{name.upper()}_URL") or "").strip().rstrip("/")
        key = (env.get(f"{name.upper()}_API_KEY") or "").strip()
        if ssh:
            # Over SSH the request is made on the NAS, so the service's own
            # loopback address is the right one and the env URL is only an
            # override for a box that runs these on other ports.
            url = url or f"http://127.0.0.1:{LOOPBACK_PORTS[name]}"
            key = key or (discover_key(name, ssh, run=run) or "")
        if not url or not key:
            continue
        if "://" not in url:
            url = "http://" + url
        out[name] = {"url": url, "key": key}
        if ssh:
            out[name]["ssh"] = ssh
    return out


def _get(service, conf, path, params=None, opener=None, run=subprocess.run):
    """The only function here that touches the network.

    Hardcodes GET, refuses a path outside `READ_ONLY`, and never sends a
    body. Callers pass a path and a query dict; they cannot pass a method.
    """
    opener = _OPENER.open if opener is None else opener
    if path not in READ_ONLY:
        raise ValueError(f"{path} is not a read-only endpoint this tool may call")
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = conf["url"] + path + (f"?{query}" if query else "")
    if conf.get("ssh"):
        return _get_over_ssh(service, conf, url, run=run)
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


def _get_over_ssh(service, conf, url, run=subprocess.run):
    """The same request as `_get`, made on the NAS instead of from this pod.

    It reports through the same `Unreachable` exception and makes the same
    two judgements: 401/403 is a refused key rather than a dead service, and
    a 200 that is not JSON is not an answer -- a captive portal or a Synology
    login page returns 200 with HTML, and JSON that parses is the cheapest
    thing only the real service produces.
    """
    body, code = _fetch_over_ssh(
        conf["ssh"],
        url,
        headers=(f"X-Api-Key: {conf['key']}", f"User-Agent: {USER_AGENT}", "Accept: application/json"),
        run=run,
    )
    if code in (401, 403):
        raise Unreachable(f"{service} refused the API key ({code})")
    if code >= 400:
        raise Unreachable(f"{service} answered {code} on {url}")
    try:
        return json.loads(body)
    except ValueError as exc:
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


def _to_oslo_date(stamp):
    """A UTC `date-time` as the Oslo calendar day it falls on."""
    if not stamp:
        return None
    try:
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp[:10] or None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(OSLO).date().isoformat()


def _window(days, today=None, now=None):
    """Oslo's today, not the pod's.

    The pod runs UTC, so between 22:00 and midnight Oslo `date.today()` is
    still yesterday -- the window would start a day early and quietly drop the
    last day he asked for. `now` exists so a test can stand at that hour
    rather than passing only when the two zones happen to agree.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    start = today or now.astimezone(OSLO).date()
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
            params = {"start": start, "end": end}
            if name == "sonarr":
                # `includeSeries` defaults to false in the spec, and without it
                # `EpisodeResource.series` comes back empty -- so every line
                # would say "unknown series" against a real Sonarr while the
                # tests, which supply their own fixture, stayed green.
                params["includeSeries"] = "true"
            rows = get(name, conf, "/api/v3/calendar", params)
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
    # All three release fields are `date-time` in the spec, not date-only, and
    # any of them may be absent. Slicing the first present one to ten
    # characters gets both halves wrong: it shows the UTC calendar day while
    # every other line here is Oslo, and a film on the calendar for its
    # digital release still carries a cinema date from months earlier, which
    # would sort to the top of a seven-day window.
    dates = [_to_oslo_date(movie.get(k)) for k in ("inCinemas", "digitalRelease", "physicalRelease")]
    present = sorted(d for d in dates if d)
    when = present[0] if present else "date unknown"
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
    if not needle:
        # `_normalise` throws away punctuation, so "???" folds to "" and
        # `"" in anything` is true -- the whole library came back as a hit.
        raise NothingToSearchFor(term)
    series = get("sonarr", conf, "/api/v3/series") or []
    hits = [s for s in series if needle in _normalise(s.get("title") or "")]
    lines = []
    for s in hits:
        title = s.get("title") or "untitled"
        nxt = _to_oslo(s.get("nextAiring"))
        if nxt:
            lines.append(f"{title}: next episode {nxt} (Oslo)")
        elif s.get("ended"):
            prev = _to_oslo(s.get("previousAiring"))
            lines.append(f"{title}: ended{f', last aired {prev}' if prev else ''}")
        else:
            prev = _to_oslo(s.get("previousAiring"))
            lines.append(
                f"{title}: in your library, no next air date known"
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


#: What a service that never answered gets said about it. `config` drops a
#: service whose API-key discovery failed -- `discover_key` swallows an
#: unreachable app and returns None, and `config` continues past it -- so a
#: consumer that iterates its result alone silently stops judging that app.
UNDISCOVERED_REASON = ("no configuration came back for it -- its API key could not be "
                       "discovered, so it was never asked")


def unconfigured(conf_all):
    """The services in `SERVICES` that `config` did not return, sorted.

    Every check over the *arr apps has to reconcile what came back against
    what exists, because the failure is silent on both ends: one transient
    fetch of sonarr's `/initialize.js` removes sonarr from `config`, and a
    report whose denominator is `len(conf_all)` then prints `1 of 1` and
    exits 0 over an app it never asked. `tools.nas_versions` carried that
    reconciliation inline and `tools.nas_watch` and `tools.nas_egress` did
    not; three copies of one rule is the rule living in the wrong place, so
    it lives here and the denominator is always `len(SERVICES)`.
    """
    return sorted(set(SERVICES) - set(conf_all or {}))


UNCONFIGURED_HELP = (
    "Nothing on this NAS is reachable from here, and it is worth knowing which half\n"
    "is missing before changing anything.\n"
    "  * On the runner pod this should just work: it has ssh, and the sealed key at\n"
    "    /etc/nas-ssh/id_ed25519. The request is then made on the NAS itself, and the\n"
    "    API key is read off each app's own unauthenticated /initialize.js.\n"
    "  * On the bridge pod there is no ssh binary at all, so this cannot run there.\n"
    "  * A direct HTTP call is not the path today: allow-nas-ssh-egress in the agents\n"
    "    namespace opens port 22 and nothing else, so :8989 and :7878 are refused by\n"
    "    this pod's own kernel before a packet leaves.\n"
    "Overrides, all optional: NAS_SSH_HOST / NAS_SSH_USER / NAS_SSH_KEY for the hop,\n"
    "and SONARR_URL / SONARR_API_KEY / RADARR_URL / RADARR_API_KEY to skip discovery\n"
    "or to reach a box that is not behind SSH at all."
)


def _at_least_one_day(raw):
    days = int(raw)
    if days < 1:
        raise argparse.ArgumentTypeError("a window shorter than one day has nothing in it")
    return days


def main(argv=None, env=None, get=_get, out=sys.stdout, ssh=_UNSET):
    parser = argparse.ArgumentParser(prog="python3 -m tools.nas", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="is each service reachable")
    cal = sub.add_parser("calendar", help="what is airing or releasing soon")
    cal.add_argument("--days", type=_at_least_one_day, default=7)
    air = sub.add_parser("airing", help="when is a named show next on")
    air.add_argument("term", nargs="+")
    args = parser.parse_args(argv)

    conf_all = config(env, ssh=ssh)
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
    except NothingToSearchFor:
        print("give me something with a letter or a number in it to search for.", file=out)
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
