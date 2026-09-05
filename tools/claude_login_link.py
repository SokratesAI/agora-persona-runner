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
_CONFIG_KEYS = {
    "authorize_url": "CLAUDE_AI_AUTHORIZE_URL",
    "token_url": "TOKEN_URL",
    "manual_redirect_url": "MANUAL_REDIRECT_URL",
    "client_id": "CLIENT_ID",
}


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
    found = {}
    missing = []
    for name, key in _CONFIG_KEYS.items():
        match = re.search(re.escape(key) + r':"([^"]+)"', text)
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


def _post_json(url: str, body: dict, timeout: int = 30):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode())


def exchange(session: dict, code: str, post=None):
    """The CLI's own token exchange, field for field. `post` is injected so a
    test can never reach Anthropic -- and it defaults to None rather than to
    `_post_json`, because a default argument binds once at import and a test
    that replaced the module attribute afterwards would be replacing something
    this function never looks at again."""
    post = _post_json if post is None else post
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": session["redirect_uri"],
        "client_id": session["client_id"],
        "code_verifier": session["code_verifier"],
        "state": session["state"],
    }
    status, payload = post(session["token_url"], body)
    if status != 200:
        raise CannotSee(f"token exchange failed ({status})")
    return payload


def credential_from_response(payload: dict, now_ms: int | None = None) -> dict:
    """The on-disk shape, built the way the CLI builds it: `expires_in` and
    `refresh_token_expires_in` are seconds from now, stored as epoch ms."""
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
    return credential


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


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", default=DEFAULT_BINARY)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="mint a PKCE pair and print the login link")
    start.add_argument("--credentials", default=DEFAULT_CREDENTIALS)
    start.add_argument("--notify", action="store_true", help="send the link to his phone")
    start.add_argument("--notify-key", default="claude-login-link")

    finish = sub.add_parser("finish", help="exchange the code he pasted back")
    finish.add_argument("--code", required=True, help="the `<code>#<state>` from the callback page")
    finish.add_argument(
        "--install",
        metavar="PATH",
        help="write the credential here; without it nothing is written, only described",
    )
    return parser


def _cmd_start(args) -> int:
    try:
        config = extract_oauth_config(read_binary_text(args.binary))
    except (OSError, CannotSee) as problem:
        print(f"CANNOT SEE  {problem}")
        return 1
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
            "created_at": time.time(),
        },
    )
    print(f"scopes from {source}: {' '.join(scopes)}")
    print(f"session saved to {args.session} (0600)")
    print(url)
    if args.notify:
        from tools import notify as notify_tool

        status, line = notify_tool.notify(
            "Claude login link (opens on your phone, then paste the code back): " + url,
            key=args.notify_key,
            dedupe_hours=24,
        )
        print(f"notify: {line}")
        return 0 if status in (0, 3) else status
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
    try:
        payload = exchange(session, code)
    except (urllib.error.URLError, OSError, CannotSee, KeyError) as problem:
        print(f"REFUSED  {problem}")
        return 2
    credential = credential_from_response(payload)
    for line in describe(credential):
        print(line)
    if args.install:
        save_session(args.install, {"claudeAiOauth": credential})
        print(f"installed to {args.install}")
    else:
        print("nothing written -- pass --install <path> to write the credential file")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "start":
        return _cmd_start(args)
    return _cmd_finish(args)


if __name__ == "__main__":
    sys.exit(main())
