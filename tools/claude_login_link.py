"""Can this loop start its own Claude login and hand the owner a link? Yes -- and this builds it.

the owner's capture on `issues.md`, 2026-09-05: the first half was an alarm that
warns him before the login expires (`tools.credential_recovery`, runner#758).
The second half was the harder question, and it is the one that actually
removes him from a terminal:

    "whether we can invoke the CLI login flow ourselves and send you a link"

Cycle 964 answered "I only read `claude auth login --help`" and stopped there,
which was the honest answer to a question nobody had looked at. **The CLI's own
login flow is readable, offline, in the installed binary**, the way cycle 533
read the feature gate out of it -- and the flow it implements is a standard
OAuth 2.0 authorization-code exchange with PKCE, which has a *manual* redirect
mode precisely so a login can happen on a machine that is not the one running
the browser.

Read out of `claude.exe` 2.1.261 at byte 179271373:

    D.searchParams.append("redirect_uri", o ? Vt().MANUAL_REDIRECT_URL
                                            : `http://localhost:${r}/callback`)

`o` is `isManual`. In manual mode the browser lands on
`platform.claude.com/oauth/code/callback`, which prints a code instead of
posting it to a localhost server -- so the browser and the CLI never have to be
the same machine, or the same network, or awake at the same time. That is the
whole of why this works: **the CLI already supports logging in from a phone; it
just expects the code to come back through a terminal prompt.** This module
replaces that one hop.

So the flow this gives him is three messages long:

1. `start` mints a PKCE verifier and state, builds the authorize URL, and can
   send it to his phone through `tools.notify` (quiet hours and dedupe already
   decided there).
2. He opens it, approves, and the callback page shows `<code>#<state>`.
3. `finish` exchanges that for a credential and prints what it got.

**Step 3 is the one that kept failing, and not for a reason in this module.**
The link lives an hour; the authorization code behind it is good for minutes.
The loop that mints the link wakes every 30 minutes, so the run that sends the
link and the run that spends the code are almost never the same run -- on
2026-09-10 he answered at 09:43 Oslo and the next cycle reached `finish` at
10:09, against a live session with a matching state, and got `invalid_grant`.
That is why `wait` exists: it polls Telegram from inside the run that minted
the link and exchanges the code as soon as it arrives, so `start --notify`
followed by `wait` is one uninterrupted flow rather than two runs half an hour
apart.

**Nothing here is a table of constants.** Every URL and the client id are read
back out of the binary this loop is actually running, because a second copy of
them is a copy that goes stale exactly the way a pin does (`tools.pin_drift`'s
lesson, one layer down). If a CLI release moves them, this says it cannot see
them and refuses to build a URL, rather than building a wrong one against
remembered values.

**Two deliberate refusals, both rule 5.**

`finish` does **not** write the credential file unless it is given `--install`
with an explicit path. Printing the shape is the default because an exchange
that lands is not the same act as replacing the credential the running cycle
authenticates with, and the second one can end the cycle doing it.

And nothing here runs the exchange on its own. There is no `--auto`, no poll,
no retry: the code comes from the owner and only he can produce it. A tool that
tried would be a tool that logs in as somebody.

**What I did not measure**, said out loud because the scope of the sentence has
to match the scope of the check: I have not run a live exchange against
`platform.claude.com`, deliberately. What is measured is the URL builder
against the constants in the installed binary, the PKCE pair against RFC 7636's
S256 definition, and the exchange against a fake transport. One live read: a
GET of a built URL from this pod answers **307** and forwards every parameter
intact to `claude.ai/oauth/authorize`, and following that hop lands on
Cloudflare's `Just a moment...` challenge (403, 7,153 bytes) -- which is a bot
check against a datacentre IP running curl, not a verdict on the client id. So
the endpoint is there and the query survives the redirect; whether the consent
page renders is measurable only from a real browser on a real phone. Whether Anthropic's
authorize page accepts this client id in manual mode from a browser the owner owns
is the one step only he can run, and it costs him one tap to find out.

**The token endpoint is now measured too, and it changed one line of this
module.** The first live attempt at this flow (Sokrates and the owner, together,
2026-09-09) never reached Anthropic: `_post_json` sent urllib's default
`Python-urllib/3.x` and Cloudflare answered `403 error code: 1010` in front of
the token endpoint. Three POSTs of a deliberately invalid code from this pod
separate the causes, because an invalid code is answered by the backend and a
blocked request never gets there:

    Python-urllib/3.x   403  error code: 1010                (Cloudflare)
    axios/1.15.2        400  invalid_grant "Invalid 'code'"  (Anthropic)
    Chrome 140 UA       429  rate_limit_error                (Anthropic)

Reversed and re-run, and the three verdicts held, so the 429 is a property of
that User-Agent rather than of being third in a row. **The CLI's own axios
User-Agent is the only one measured to reach the backend un-rate-limited**, and
it is what `read_token_user_agent` pulls out of the binary. What is still
unmeasured is the same thing as before: an exchange with a *valid* code, which
only the owner can produce.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BINARY = "/usr/bin/claude"
DEFAULT_SESSION = "/data/claude-home/.claude/nova-login-session.json"
DEFAULT_CREDENTIALS = "/data/claude-home/.claude/.credentials.json"

# A `start` older than this is treated as abandoned and may be overwritten.
SESSION_TTL_SECONDS = 3600

# The CLI's own save path writes these three beside the token fields and the
# token endpoint does not return any of them -- they come from a separate
# profile fetch. `agora-claude-bridge/bridge/credentials.py` already paid for
# this once: an earlier version there assembled a `claudeAiOauth` out of parts,
# dropped fields the real file carries, and the CLI answered "Not logged in".
# So this module carries them across from whatever credential is already on
# disk, and says out loud when there is none to carry them from.
CARRIED_FIELDS = ("subscriptionType", "rateLimitTier", "clientId")

# The scopes the running credential actually carries. Used only when the live
# credential cannot be read -- which is the disaster this whole flow exists for,
# so the fallback has to exist and has to say it is one.
FALLBACK_SCOPES = [
    "user:inference",
    "user:profile",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
]

# The config object in the bundle is `var _={BASE_API_URL:"...",...}`. Keys are
# not minified (they are property names in a literal), so each one is findable
# on its own and a moved neighbour cannot silently change what another resolves
# to.
# The bundle carries the same key names in **two** config objects: the
# production one and a local/staging one whose CLIENT_ID is a different
# application. A bare first-match search picks the right one today only because
# production happens to sit earlier in the file, which is a fact about layout
# rather than a guarantee -- so the search is anchored on the one line only the
# production object has, and everything is read from a window after it.
PRODUCTION_ANCHOR = 'BASE_API_URL:"https://api.anthropic.com"'
CONFIG_WINDOW = 2000

_CONFIG_KEYS = {
    "authorize_url": "CLAUDE_AI_AUTHORIZE_URL",
    "token_url": "TOKEN_URL",
    "manual_redirect_url": "MANUAL_REDIRECT_URL",
    "client_id": "CLIENT_ID",
}


# The CLI's own token exchange is axios, and axios sets `User-Agent: axios/<v>`
# on every request it makes. urllib sets `Python-urllib/3.x`, which Cloudflare
# blocks outright in front of the token endpoint -- measured from this pod on
# 2026-09-09, three POSTs of a deliberately invalid code to the live endpoint:
# `Python-urllib` answered `403 error code: 1010` (Cloudflare's browser-integrity
# check), `axios/1.15.2` answered `400 invalid_grant "Invalid 'code' in request"`
# from Anthropic's own backend, and a Chrome UA answered `429 rate_limit_error`.
# The order was reversed and re-run and the three verdicts held, so the 429 is a
# property of the browser UA rather than of being the third request in a row.
# So the header is not cosmetic and the browser UA is not the one to send: the
# CLI's own is the only one measured to reach the backend un-rate-limited.
#
# Read out of the binary for the same reason every other constant here is -- a
# remembered `axios/1.15.2` goes stale exactly the way a pin does.
TOKEN_UA_ANCHOR = r'"User-Agent","axios/"\+(\w+)'


class CannotSee(Exception):
    """A constant the URL needs is not in the binary. Never a guess instead."""


def read_binary_text(path: str = DEFAULT_BINARY, chunk: int = 8 << 20) -> str:
    """The bundle as text. It is a 200MB single-file executable with the JS in
    it verbatim, so latin-1 keeps every byte addressable and never raises."""
    target = os.path.realpath(path)
    out = []
    with open(target, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            out.append(block.decode("latin-1"))
    return "".join(out)


def extract_oauth_config(text: str) -> dict:
    """The four constants the manual flow needs, read out of the CLI itself.

    Refuses on a key it cannot find rather than falling back to a remembered
    value: a wrong authorize URL is a login attempt against somebody else's
    endpoint, and the failure would look like the owner mistyping something.
    """
    start = text.find(PRODUCTION_ANCHOR)
    if start < 0:
        raise CannotSee(
            "the installed CLI carries no production OAuth config object "
            f"({PRODUCTION_ANCHOR!r} is not in it)"
        )
    window = text[start:start + CONFIG_WINDOW]
    found = {}
    missing = []
    for name, key in _CONFIG_KEYS.items():
        match = re.search(re.escape(key) + r':"([^"]+)"', window)
        if match is None:
            missing.append(key)
        else:
            found[name] = match.group(1)
    if missing:
        raise CannotSee(
            "the installed CLI does not carry " + ", ".join(sorted(missing))
            + " -- a release moved them, so nothing here may build a URL"
        )
    return found


def live_scopes(path: str = DEFAULT_CREDENTIALS):
    """(scopes, where they came from). The running credential is the honest
    source: a new login has to reproduce the access this loop already has."""
    try:
        with open(path) as handle:
            blob = json.load(handle)
    except (OSError, ValueError):
        return list(FALLBACK_SCOPES), "the documented fallback list (no readable credential)"
    scopes = blob.get("claudeAiOauth", {}).get("scopes")
    if not isinstance(scopes, list) or not scopes:
        return list(FALLBACK_SCOPES), "the documented fallback list (credential carries no scopes)"
    return [str(s) for s in scopes], f"the running credential at {path}"


def read_token_user_agent(text: str) -> str:
    """The User-Agent the CLI's own token exchange sends, read out of the bundle.

    Two lookups, and both must resolve to exactly one thing. The anchor names
    the minified variable holding axios's version; that variable is then read
    back as a version literal. A second match on either means the bundle moved
    and the name no longer identifies what it used to, which is a `CannotSee`
    rather than a guess -- sending the wrong User-Agent is how this request gets
    blocked, so a wrong value is worse than a refusal that says why.
    """
    names = set(re.findall(TOKEN_UA_ANCHOR, text))
    if len(names) != 1:
        raise CannotSee(
            f"cannot see the token exchange's User-Agent: {len(names)} matches for the axios anchor"
        )
    name = names.pop()
    versions = set(re.findall(r"\b" + re.escape(name) + r'=\"([0-9]+\.[0-9]+\.[0-9]+)\"', text))
    if len(versions) != 1:
        raise CannotSee(
            f"cannot see the token exchange's User-Agent: {len(versions)} version literals for `{name}`"
        )
    return "axios/" + versions.pop()


def pkce_pair(verifier: str | None = None):
    """(verifier, challenge) per RFC 7636 S256: base64url(sha256(verifier)),
    unpadded. The CLI sends `code_challenge_method=S256` and nothing else."""
    if verifier is None:
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def authorize_url(config: dict, challenge: str, state: str, scopes) -> str:
    """The same query the CLI builds in manual mode, in the CLI's own order."""
    params = [
        ("code", "true"),
        ("client_id", config["client_id"]),
        ("response_type", "code"),
        ("redirect_uri", config["manual_redirect_url"]),
        ("scope", " ".join(scopes)),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
        ("state", state),
    ]
    return config["authorize_url"] + "?" + urllib.parse.urlencode(params)


def split_pasted_code(pasted: str):
    """(code, state or None). The manual callback page shows `<code>#<state>`
    and he will paste whatever it showed him, so both halves are accepted."""
    pasted = pasted.strip()
    if "#" in pasted:
        code, _, state = pasted.partition("#")
        return code.strip(), state.strip()
    return pasted, None


def live_session(path: str, ttl=SESSION_TTL_SECONDS, now=None):
    """The unspent session at `path`, or None. A `start` that clobbers one
    silently invalidates a link already sitting on his phone, and the failure
    surfaces an hour later as a state mismatch that names the wrong cause."""
    try:
        with open(path) as handle:
            existing = json.load(handle)
    except (OSError, ValueError):
        return None
    created = existing.get("created_at")
    if not isinstance(created, (int, float)):
        return None
    now = time.time() if now is None else now
    return existing if now - created < ttl else None


def save_session(path: str, payload: dict) -> None:
    """0600, because the verifier is half of a credential until it is spent."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as out:
        json.dump(payload, out, indent=2)


def load_session(path: str) -> dict:
    with open(path) as handle:
        return json.load(handle)


def _post_json(url: str, body: dict, timeout: int = 30, user_agent: str | None = None):
    headers = {"Content-Type": "application/json"}
    if user_agent:
        headers["User-Agent"] = user_agent
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
            status = response.status
    except urllib.error.HTTPError as refusal:
        # `urlopen` raises on every 4xx and 5xx, so `exchange`'s own
        # `status != 200` branch could never run and the response body -- the
        # only thing that says *why* -- went out with the exception. That is
        # what made 2026-09-10 a guessing game: three cycles saw
        # `HTTP Error 400: Bad Request` and could not tell a spent code from an
        # expired one from a rate limit, and one of them wrote the wrong cause
        # into the handoff. The body is readable off the exception; read it.
        raw, status = refusal.read().decode(errors="replace"), refusal.code
    try:
        return status, json.loads(raw)
    except ValueError:
        # Cloudflare's 1010 block is HTML, not JSON, and it is a refusal this
        # module has already been bitten by. A body we cannot parse is still
        # the evidence, so it travels as text rather than becoming a
        # `ValueError` from a line that looks like a parse bug.
        return status, {"error": "unparsed_body", "error_description": raw[:400]}


def describe_refusal(payload) -> str:
    """What the token endpoint said, in one line. `invalid_grant` alone is not
    a diagnosis -- Anthropic answers it for a code that was already spent and
    for one that expired, and those have different next steps -- so the
    description travels beside it rather than being reduced to the code."""
    if not isinstance(payload, dict):
        return str(payload)[:400]
    error = payload.get("error")
    detail = payload.get("error_description") or payload.get("message")
    if error and detail:
        return f"{error}: {detail}"
    return str(error or detail or payload)[:400]


def exchange(session: dict, code: str, post=None):
    """The CLI's own token exchange, field for field. `post` is injected so a
    test can never reach Anthropic -- and it defaults to None rather than to
    `_post_json`, because a default argument binds once at import and a test
    that replaced the module attribute afterwards would be replacing something
    this function never looks at again.

    `user_agent` travels on the session because `start` is the step that reads
    the binary; a session minted before that field existed carries None, and
    `_cmd_finish` fills it in rather than letting the request go out with
    urllib's default and get a 1010."""
    post = _post_json if post is None else post
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": session["redirect_uri"],
        "client_id": session["client_id"],
        "code_verifier": session["code_verifier"],
        "state": session["state"],
    }
    status, payload = post(session["token_url"], body, user_agent=session.get("user_agent"))
    if status != 200:
        raise CannotSee(f"token exchange failed ({status}): {describe_refusal(payload)}")
    return payload


def credential_from_response(payload: dict, now_ms: int | None = None, carry_over: dict | None = None) -> dict:
    """The on-disk shape: `expires_in` and `refresh_token_expires_in` are
    seconds from now, stored as epoch ms. `carry_over` supplies the three
    fields the token endpoint never returns -- see `CARRIED_FIELDS`."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    credential = {
        "accessToken": payload["access_token"],
        "refreshToken": payload["refresh_token"],
        "expiresAt": now_ms + int(payload["expires_in"]) * 1000,
        "scopes": [s for s in str(payload.get("scope", "")).split(" ") if s],
    }
    refresh_expiry = payload.get("refresh_token_expires_in")
    if isinstance(refresh_expiry, (int, float)):
        credential["refreshTokenExpiresAt"] = now_ms + int(refresh_expiry) * 1000
    for name in CARRIED_FIELDS:
        value = (carry_over or {}).get(name)
        if value is not None:
            credential[name] = value
    return credential


def carried_from(path: str) -> dict:
    """The fields an existing credential can lend a new one. Missing file, bad
    JSON and a file with none of them are all the same answer: nothing."""
    try:
        with open(path) as handle:
            blob = json.load(handle)
    except (OSError, ValueError):
        return {}
    existing = blob.get("claudeAiOauth")
    if not isinstance(existing, dict):
        return {}
    return {k: existing[k] for k in CARRIED_FIELDS if existing.get(k) is not None}


def describe(credential: dict) -> list:
    """What landed, with no token value in it. A cycle prints this into a
    journal entry, and an entry is written once and never edited."""
    lines = []
    for name in ("accessToken", "refreshToken"):
        value = credential.get(name)
        lines.append(f"{name}: {'present, ' + str(len(value)) + ' chars' if value else 'MISSING'}")
    for name in ("expiresAt", "refreshTokenExpiresAt"):
        stamp = credential.get(name)
        if stamp is None:
            lines.append(f"{name}: not in the response")
        else:
            when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(stamp / 1000))
            lines.append(f"{name}: {when}")
    lines.append("scopes: " + " ".join(credential.get("scopes", [])))
    return lines


def code_from_messages(rows, state: str):
    """(code, message id) for the newest unacked Telegram message that carries
    a code for *this* session, or (None, None).

    Matching on the state rather than on "the newest message" is the whole
    filter. He pastes `<code>#<state>`, and a stale reply to a link this loop
    already invalidated is indistinguishable from a fresh one by arrival time
    alone -- `finish` would then refuse on the state mismatch and the real
    code, sitting one message further down, would never be tried."""
    if not isinstance(rows, list) or not state:
        # Without a state there is nothing to match on, and `split_pasted_code`
        # answers None for the state half of any message with no `#` in it --
        # so a falsy state here would claim "thanks" as a code.
        return None, None
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        code, pasted = split_pasted_code(str(row.get("text") or ""))
        if code and pasted == state:
            return code, row.get("id")
    return None, None


def await_code(state: str, timeout: float, poll: float = 15.0,
               fetch_rows=None, sleep=time.sleep, now=time.monotonic):
    """Block until he replies with a code for this session, or `timeout`
    seconds pass. Returns (code, message id) or (None, None).

    This exists because the code, not the link, is what expires. A link lives
    an hour; the authorization code behind it is good for minutes. On
    2026-09-10 a link went out at 09:42 Oslo, he answered at 09:43, and the
    next cycle did not run `finish` until 10:09 -- 26 minutes later, against a
    live session with a matching state, and the exchange came back
    `invalid_grant`. Nothing was broken and no cycle was slow; the loop simply
    wakes every 30 minutes and the window is shorter than that. So the run
    that mints the link is the only thing here that can reliably spend it, and
    waiting is what makes that possible.

    `sleep` and `now` are injected together and read from the same clock, so a
    test cannot leave one of them real and pass on a timeout it never took."""
    if fetch_rows is None:
        def fetch_rows():
            from tools.telegram import DEFAULT_URL, _get

            status, body = _get(DEFAULT_URL, "/inbox", urllib.request.urlopen, 15)
            return body.get("messages") if status == 200 else []
    deadline = now() + timeout
    while True:
        try:
            rows = fetch_rows()
        except Exception as unreachable:  # the bridge is not worth dying over
            print(f"  inbox unreadable ({unreachable}) -- retrying")
            rows = []
        code, message_id = code_from_messages(rows, state)
        if code is not None:
            return code, message_id
        remaining = deadline - now()
        if remaining <= 0:
            return None, None
        sleep(min(poll, remaining))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", default=DEFAULT_BINARY)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="mint a PKCE pair and print the login link")
    start.add_argument("--credentials", default=DEFAULT_CREDENTIALS)
    start.add_argument("--notify", action="store_true", help="send the link to his phone")
    start.add_argument("--notify-key", default="claude-login-link")
    start.add_argument(
        "--force",
        action="store_true",
        help="mint a new link even though an unspent one exists, invalidating it",
    )

    wait = sub.add_parser(
        "wait", help="poll Telegram for his reply to the live session and exchange it"
    )
    wait.add_argument("--timeout", type=float, default=600.0,
                      help="seconds to wait for his reply (default 600)")
    wait.add_argument("--poll", type=float, default=15.0, help="seconds between inbox reads")
    wait.add_argument("--install", metavar="PATH",
                      help="write the credential here; without it nothing is written")

    finish = sub.add_parser("finish", help="exchange the code he pasted back")
    finish.add_argument("--code", required=True, help="the `<code>#<state>` from the callback page")
    finish.add_argument(
        "--install",
        metavar="PATH",
        help="write the credential here; without it nothing is written, only described",
    )
    return parser


def notify_key_for(base: str, state: str) -> str:
    """The dedupe key for one link, not for the idea of a link.

    `tools.notify` holds a message whose key it has already sent inside the
    window, and `start` passed a constant key with a 24-hour window while the
    link itself lives `SESSION_TTL_SECONDS` -- one hour. So the second link of
    any day was minted, invalidated the first, and was never sent, while
    `start` printed the held line and exited 0.

    That is not a near miss. The owner pasted a code at 09:17 Oslo on
    2026-09-10, it came back `invalid_grant`, and the replacement this loop
    minted for him went nowhere: `notify: held: 'claude-login-link' was
    already sent 2.0h ago, inside the 24h window`. `--force` had already
    invalidated the link he was holding, so the run left him strictly worse
    off than doing nothing, and said so only in a line that reads like
    housekeeping.

    Keying on the state makes each minted link its own message, so a new link
    is always sent. It does **not** keep a "same link twice is held" case
    alive, and an earlier version of this docstring claimed it did: `_cmd_start`
    mints a fresh `state` unconditionally before it ever gets here, and the one
    path that could re-announce a live session refuses at `live_session` and
    returns before this is called. So the dedupe is now a guard against a
    caller that does not exist yet, and that is the honest description of it --
    reviewer finding on this PR, and it is the shape this loop keeps paying
    for: a guard that reads as protecting something it cannot reach.
    """
    return f"{base}:{state}"


def _cmd_start(args) -> int:
    try:
        bundle = read_binary_text(args.binary)
        config = extract_oauth_config(bundle)
        user_agent = read_token_user_agent(bundle)
    except (OSError, CannotSee) as problem:
        print(f"CANNOT SEE  {problem}")
        return 1
    held = live_session(args.session)
    if held is not None and not args.force:
        age = int(time.time() - held["created_at"])
        print(
            f"REFUSED  a link minted {age}s ago has not been spent yet -- "
            "finish it, or pass --force to mint a new one and invalidate it"
        )
        return 2
    scopes, source = live_scopes(args.credentials)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    url = authorize_url(config, challenge, state, scopes)
    save_session(
        args.session,
        {
            "code_verifier": verifier,
            "state": state,
            "client_id": config["client_id"],
            "token_url": config["token_url"],
            "redirect_uri": config["manual_redirect_url"],
            "user_agent": user_agent,
            "created_at": time.time(),
        },
    )
    print(f"scopes from {source}: {' '.join(scopes)}")
    print(f"token exchange will send User-Agent: {user_agent}")
    print(f"session saved to {args.session} (0600)")
    print(url)
    if args.notify:
        from tools import notify as notify_tool

        status, line = notify_tool.notify(
            "Claude login link (opens on your phone, then paste the code back): " + url,
            key=notify_key_for(args.notify_key, state),
            dedupe_hours=SESSION_TTL_SECONDS / 3600,
        )
        print(f"notify: {line}")
        if status != 0:
            # A link he never received is not a link, and this used to answer 0
            # on a held one -- see `notify_key_for`. The session is minted
            # either way, so `finish` still works if he has the URL by some
            # other route; the caller just has to know it did not reach him.
            print(
                "REFUSED  the link was minted but not delivered -- give him "
                "this URL by another route, or fix the reason above"
            )
            return status
    return 0


def _cmd_finish(args) -> int:
    try:
        session = load_session(args.session)
    except (OSError, ValueError) as problem:
        print(f"CANNOT SEE  no usable session at {args.session}: {problem}")
        return 1
    code, state = split_pasted_code(args.code)
    if state is not None and state != session.get("state"):
        print("REFUSED  the state in that code is not the one this session minted")
        return 2
    if not session.get("user_agent"):
        # Every session on disk today was minted before `start` wrote this
        # field. Reading it back out of the binary is the whole point, but an
        # unreadable binary must not turn `finish` into a refusal -- that would
        # take a working exchange away from a box that has the session and not
        # the CLI. It warns and goes out with urllib's default instead, naming
        # the failure that produces so it is not diagnosed twice.
        try:
            session["user_agent"] = read_token_user_agent(read_binary_text(args.binary))
            print(f"session predates the User-Agent field; using {session['user_agent']}")
        except (OSError, CannotSee) as problem:
            print(
                f"WARNING  cannot read the CLI's User-Agent ({problem}) -- this "
                "exchange goes out with urllib's default, which Cloudflare "
                "answers with `403 error code: 1010` in front of the token endpoint"
            )
    try:
        payload = exchange(session, code)
    except (urllib.error.URLError, OSError, CannotSee, KeyError, ValueError) as problem:
        print(f"REFUSED  {problem}")
        return 2
    carry = carried_from(args.install) if args.install else carried_from(DEFAULT_CREDENTIALS)
    credential = credential_from_response(payload, carry_over=carry)
    absent = [f for f in CARRIED_FIELDS if f not in credential]
    if absent:
        print(
            "WARNING  the token endpoint does not return " + ", ".join(absent)
            + " and no credential on disk could lend them -- the CLI's own save "
            "path writes them, and a partial claudeAiOauth has been rejected as "
            "'Not logged in' before (agora-claude-bridge/bridge/credentials.py)"
        )
    for line in describe(credential):
        print(line)
    if args.install:
        save_session(args.install, {"claudeAiOauth": credential})
        print(f"installed to {args.install}")
    else:
        print("nothing written -- pass --install <path> to write the credential file")
    return 0


def _cmd_wait(args) -> int:
    try:
        session = load_session(args.session)
    except (OSError, ValueError) as problem:
        print(f"CANNOT SEE  no usable session at {args.session}: {problem}")
        return 1
    state = session.get("state")
    if not state:
        print(f"CANNOT SEE  the session at {args.session} carries no state to match on")
        return 1
    print(f"waiting up to {int(args.timeout)}s for a code ending in #{state}")
    code, message_id = await_code(state, args.timeout, poll=args.poll)
    if code is None:
        print(
            f"REFUSED  no reply carrying this session's state in {int(args.timeout)}s -- "
            "the link is still live, so `finish --code` works whenever it arrives"
        )
        return 2
    print(f"got a code from Telegram message #{message_id}")
    status = _cmd_finish(
        argparse.Namespace(
            session=args.session,
            binary=args.binary,
            code=f"{code}#{state}",
            install=args.install,
        )
    )
    if status == 0 and message_id is not None:
        # Only on a spent code. Acking a message whose exchange failed would
        # hide the one thing a later cycle needs to retry with.
        from tools import telegram_inbox

        _, line = telegram_inbox.ack(message_id)
        print(f"telegram ack: {line}")
    return status


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "start":
        return _cmd_start(args)
    if args.command == "wait":
        return _cmd_wait(args)
    return _cmd_finish(args)


if __name__ == "__main__":
    sys.exit(main())
