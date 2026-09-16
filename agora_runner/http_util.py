"""Low-level HTTP helpers and thin wrappers around Agora's own public/internal APIs."""

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request

from agora_runner.config import AGORA_URL, AGORA_INTERNAL_URL, AGORA_TOKEN, GEMINI_TRANSIENT_STATUSES
from agora_runner.log import log
from agora_runner.otel import outgoing_headers


def _decoded_body(response):
    """Read a response, gunzipping it if the server says it is gzipped.

    `urllib` sends no `Accept-Encoding` of its own and never decodes one,
    so this pairs with the header set in `http_json` -- without both
    halves we would either keep paying full price or hand `json.loads`
    a gzip stream.
    """
    raw = response.read()
    if (response.headers.get("Content-Encoding") or "").strip().lower() == "gzip":
        raw = gzip.decompress(raw)
    return raw


def http_json(method, url, payload=None, headers=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    # gzip only, not br: Agora prefers brotli when offered both (nova_site
    # and agora both do), and there is no brotli decoder in the stdlib --
    # asking for it would mean a new dependency to save ~850 bytes a tick
    # on top of what gzip already saves. Measured live 2026-08-10 on
    # GET /conversations: 109,135 raw, 7,075 gzip, 6,222 br.
    all_headers = {"Content-Type": "application/json", "Accept-Encoding": "gzip"}
    if headers:
        all_headers.update(headers)
    # Every JSON call this repo makes goes through here, which is why the
    # trace context is attached here and not at the eleven call sites. It
    # adds a `traceparent` only while a span is open, so a cron tick or a
    # tool run from a shell sends exactly what it sent before.
    all_headers = outgoing_headers(all_headers)
    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(_decoded_body(resp).decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(_decoded_body(e).decode() or "{}")
        except Exception:
            body = {}
        return e.code, body


def http_bytes(url, timeout=30):
    """GET a URL and return (status, raw_bytes) -- for fetching attachment
    content, not JSON APIs."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def fetch_attachment_bytes(attachment_id):
    """Raw bytes for one message attachment (Issues.md: 'sending
    images... does not work' -- the upload/storage/UI side shipped in
    agora#12, but the runner never actually read attachments at all, so
    Gemini/Claude never saw the image; an image-only message became a
    genuinely empty turn, which Gemini rejects outright -- see
    GEMINI_TRANSIENT_STATUSES's docstring for the related fallback fix)."""
    status, data = http_bytes(f"{AGORA_URL}/attachments/{attachment_id}")
    if status != 200:
        log(f"fetch_attachment_bytes: {attachment_id} returned HTTP {status}")
        return None
    return data


def agora_get(path):
    status, body = http_json("GET", f"{AGORA_URL}{path}")
    return status, body


def agora_internal(method, path, payload=None):
    headers = {"x-agora-token": AGORA_TOKEN} if AGORA_TOKEN else {}
    return http_json(method, f"{AGORA_INTERNAL_URL}{path}", payload, headers)


def agora_public(method, path, payload=None):
    """A write against Agora's *public* app, which is where his browser writes.

    `agora_internal` is the agent-facing app (ADR 0007) and carries the token;
    this is the same app `agora_get` reads. It exists because two of the
    conversation routes the dock needs -- `DELETE /conversations/:id` above
    all -- are registered on the public app only, so a delete over the
    internal one is a 404 rather than a permission error, which reads as
    "that conversation is gone" and is the opposite of what happened.
    """
    return http_json(method, f"{AGORA_URL}{path}", payload)


def unauthorized_hint(status):
    """Name the cause when Agora's agent-facing app refuses a write for want of a token.

    `agora_internal` sends `x-agora-token` only when the environment holds
    one, and the bridge pod holds none -- so every write from the shell that
    reads the vault comes back 401, and the caller prints "HTTP 401", which
    reads like Agora rejected the message. It did not; the request never
    carried a credential. Cycle 1677 lost two re-announcements to that
    sentence before running the same call from the runner pod, where it
    returned 200 for both.

    Returns a clause to append to an error message, or "" when the status is
    not an auth refusal or a token was in fact sent -- a 401 with a token
    present is a real rejection and must not be explained away.
    """
    if status in (401, 403) and not AGORA_TOKEN:
        return (" — this pod holds no AGORA_TOKEN, so the request carried no "
                "credential at all; run it from the runner pod (terminal_exec)")
    return ""
