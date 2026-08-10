"""Nova's own site: the journal, styled, on the tailnet.

Agora ideas.md #34 -- items 1-4 (journal timeline, status header, digest
strip, per-cycle deep links) and item 6, the capture box.

**The two writes, and where their boundary actually is.** Everything here
was GET until the capture box; `POST /api/capture` and `POST /api/comment`
(ideas.md #44) are the only routes in this module that change anything.
Their safety is not the tailnet alone -- it is that both endpoints are too
narrow to misuse:

- The tailnet is the *authentication* boundary. Reaching this port at all
  means being on Edvard's tailnet, which in practice means his own
  devices. A shared token would have to live in the served JavaScript or
  be typed on a phone, so it would add real friction and no real secrecy.
- The endpoint's shape is the *authorization* boundary, and it is what is
  actually load-bearing. `target` indexes a two-entry dict of literal
  paths (nova_capture.CAPTURE_TARGETS); `/api/comment` does not even take
  one, writing only to nova_comments.COMMENTS_PATH. No path, no marker and
  no position ever comes from the client in either. The worst a request
  can do is add a bullet or a comment to a file Edvard reads and can
  delete, and the vault's daily git snapshot holds the prior version
  regardless.
- `Content-Type: application/json` is required, which is a CSRF defence
  rather than a formality: it is not a CORS "simple request", so a
  browser must preflight it, and this server answers no OPTIONS and sends
  no CORS headers. A page on another origin therefore cannot post here
  even from a browser that is on the tailnet.

Tailscale's identity headers are recorded in the audit entry rather than
trusted, because whether this Ingress forwards them has not been
measured. If a future cycle confirms they arrive, they are the basis for
tightening this further -- see the audit call in do_POST.

**Why this lives in the runner's repo rather than in its own.** Idea #34
sketched a separate `nova-pwa` service, and named the thing that makes
that expensive: the vault client already exists twice (here and in the
bridge) with nothing detecting drift between them, so a third service
reading CouchDB would mean a third copy -- a bug knowingly introduced.
Staying in this repo keeps it at two.

**But it no longer runs in the runner's process.** As of 2026-08-09 it
has its own entrypoint (`run_nova_site.py`) and its own Deployment,
built from this same image. The reasoning is in nova_site_main.py; the
short version is that the runner's `Recreate` + 2880s drain exists to
protect a cycle's reply, and the site inherited it, so the site was down
for the length of every cycle. Sharing the repo was always the point;
sharing the process was incidental.

**Why a second port instead of a second path on 8082.** The /invoke
Service is documented as having no public-facing surface at all, and it
carries /invoke, /mcp and /tool-activity. The Tailscale operator's
Ingress does not reliably filter by path, so exposing 8082 at all would
expose those three. A separate port gets its own Service, its own
Ingress and a NetworkPolicy scoped to this port only -- exactly the
shape platform-config already uses to expose Agora's 8080 while leaving
its 8081 unreachable.

**The limitation this used to record is fixed.** It read: "this
deployment is `Recreate` with a 2880s grace period, so while a cycle is
draining the pod is Terminating and out of the Service's endpoints --
the site is unreachable for that window", and called that the one
argument for splitting the site out later. The capture box turned it
from cosmetic into functional, and the split happened. The site's own
Deployment is `RollingUpdate` with `maxUnavailable: 0`, so a Nova cycle
no longer takes it down and neither does a deploy.

No caching layer: the full 204KB journal assembles from CouchDB in
285ms measured end-to-end (2026-08-09), which is cheaper than the
staleness a cache would buy.

**Responses are gzipped when the client asks.** Measured against the
live pod on 2026-08-10, a cold load of this site was 588,998 bytes and
none of it was compressed -- `/api/journal` alone was 453,239 -- while
every browser that fetched it was already sending
`Accept-Encoding: gzip, deflate, br, zstd` and getting nothing back.
Compressed, the same load is 154,726 bytes. This is the largest payload
anywhere in this system and it is the page Edvard reads on a phone.

Only gzip: the runtime has no brotli or zstd binding (checked), and the
stdlib gives gzip and raw deflate. That is a smaller ceiling than the
Brotli that agora#50 negotiates via Express, and it is also why this is
less likely to go wrong here -- the encoding this serves is the encoding
its tests exercise. Cycle 70 shipped four passing gzip tests for a path
production never took, because `compression` handed real browsers
Brotli. There is no such gap to fall into with one encoding, but the
test asserting a real browser's header gets `gzip` back is there so the
claim stays checked rather than argued.
"""

import gzip
import json
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agora_runner.audit import audit
from agora_runner.config import NOVA_PORT
from agora_runner.log import log
from agora_runner.nova_capture import (
    CAPTURE_TARGETS,
    MAX_BODY_BYTES,
    capture,
)
from agora_runner.nova_comments import (
    add_comment,
    add_needs_comment,
    clean_comment_text,
    comments_by_cycle,
    format_stamp,
    needs_comments,
)
from agora_runner.nova_journal import (
    build_status,
    parse_digest,
    parse_journal,
    render_blocks,
)
from agora_runner.nova_replies import (
    WAITING_AFTER_SECONDS,
    enqueue as enqueue_reply,
    failed as failed_replies,
    pending_since,
)
from agora_runner.nova_sources import comments_markdown, digest_markdown, journal_markdown

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nova_public")

# An explicit route -> filename map rather than joining the request path
# onto PUBLIC_DIR. Nothing the client sends reaches the filesystem, so
# path traversal is not something this has to defend against -- it
# cannot be expressed.
STATIC_ROUTES = {
    "/app.js": "app.js",
    "/style.css": "style.css",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/sw.js": "sw.js",
    "/icon.svg": "icon.svg",
}

# gzip's header and trailer are a fixed 18 bytes, so a short body comes
# back *bigger*: `/api/comments` is 15 bytes on the live pod and gzips to
# 35. Measured crossover on realistic JSON is around 100 bytes. The
# threshold is 1024 because that is what Express's `compression` uses by
# default, which is the same compressor now sitting in front of Agora
# (agora#50) -- one number for both halves of this system beats two
# defensible ones. This is a limit with a measurement behind it, not a
# tidiness cap: below it, compressing costs bytes rather than saving them.
MIN_COMPRESS_BYTES = 1024

# zlib's default. Measured on the live 453,239-byte journal: level 1 is
# 3.0x in 5.9ms, level 6 is 3.6x in 13.8ms, level 9 is 3.6x in 20.9ms.
# Level 9 buys 880 more bytes for 7ms more CPU, and level 6's 13.8ms sits
# against the ~285ms this endpoint already spends assembling itself out
# of CouchDB.
COMPRESS_LEVEL = 6

# Everything this server sends is text except the SVG, which is also
# text. Listed explicitly rather than compressing whatever is not on a
# deny-list, so a future binary route has to opt in rather than silently
# getting spent CPU for nothing.
COMPRESSIBLE_TYPES = (
    "application/json",
    "application/manifest+json",
    "image/svg+xml",
    "text/",
)


def accepts_gzip(header):
    """Does this `Accept-Encoding` value permit gzip?

    Parsed rather than substring-matched because `gzip;q=0` is how the
    header spells *"not this one"* -- it contains the string "gzip" and
    means the opposite. `*` stands in for anything not otherwise named,
    and carries a q-value of its own for the same reason.
    """
    if not header:
        return False
    wildcard = False
    for part in header.split(","):
        token, _, params = part.strip().partition(";")
        token = token.strip().lower()
        if token not in ("gzip", "*"):
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        if token == "gzip":
            return quality > 0
        wildcard = quality > 0
    return wildcard


def journal_payload():
    """Every entry, rendered. The raw `body` is dropped rather than sent
    alongside the blocks -- it is the same 200KB twice, and the client has
    no use for markdown it is not allowed to interpret."""
    markdown, times = journal_markdown(with_times=True)
    entries = parse_journal(markdown, times)
    status = build_status(entries)
    rendered = []
    for entry in entries:
        entry = dict(entry)
        entry["blocks"] = render_blocks(entry.pop("body", ""))
        rendered.append(entry)
    return {"entries": rendered, "status": status}


def digest_payload():
    payload = parse_digest(digest_markdown())
    payload["needsEdvardBlocks"] = render_blocks(payload["needsEdvard"])
    return payload


def comments_payload():
    """Every comment, grouped by the cycle it is about.

    Keyed by cycle number as a string because that is what JSON object keys
    are; the client looks up `byCycle[String(entry.cycle)]`. The comment
    text is sent as plain text rather than rendered blocks -- unlike the
    journal, this is Edvard's own prose and nothing here interprets it as
    markdown, so there is no markup for the client to be unable to build.
    """
    markdown = comments_markdown()
    grouped = comments_by_cycle(markdown)
    # A reply the worker is still waiting on the bridge for. Sent from the
    # server rather than remembered by the client, so the "replying…" line
    # survives a reload, a second device, and the minutes this can take
    # while a Nova cycle holds the bridge's lock -- see nova_replies.
    queued = pending_since()
    gave_up = failed_replies()
    now = time.time()
    for cycle, items in grouped.items():
        for comment in items:
            key = (cycle, comment.get("stamp"))
            asked_at = queued.get(key)
            comment["replyPending"] = asked_at is not None
            # Two different waits, and the card must not call the second one
            # the first: under the threshold a reply is genuinely being
            # written, over it the bridge is busy with a cycle and this is a
            # queue. Saying "Nova is replying…" for forty minutes is what
            # Edvard reported as the conversation not working at all.
            comment["replyWaiting"] = (
                asked_at is not None and (now - asked_at) >= WAITING_AFTER_SECONDS
            )
            # And when it is not coming at all, say so rather than letting
            # the line disappear as if the answer had arrived.
            comment["replyFailed"] = asked_at is None and key in gave_up
    return {
        "byCycle": {str(cycle): items for cycle, items in grouped.items()},
        # Replies to the digest's Needs Edvard block, which belong to no
        # cycle and so cannot ride in `byCycle`.
        "needs": needs_comments(markdown),
    }


class NovaSiteHandler(BaseHTTPRequestHandler):
    server_version = "nova-site"

    def log_message(self, *args):  # quiet default request logging
        pass

    def _send(self, status, body, content_type):
        if isinstance(body, str):
            body = body.encode("utf-8")

        compressible = content_type.startswith(COMPRESSIBLE_TYPES)
        encoded = None
        if compressible and len(body) >= MIN_COMPRESS_BYTES:
            if accepts_gzip(self.headers.get("Accept-Encoding")):
                # mtime=0 rather than the default: gzip stamps the current
                # time into its header, so the same bytes would otherwise
                # produce a different response every second.
                encoded = gzip.compress(body, COMPRESS_LEVEL, mtime=0)

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if compressible:
            # Sent whether or not this particular response was compressed:
            # it is a statement that the body *varies* by the request
            # header, which is what stops a shared cache handing a gzipped
            # body to a client that never asked for one.
            self.send_header("Vary", "Accept-Encoding")
        if encoded is not None:
            self.send_header("Content-Encoding", "gzip")
            body = encoded
        # After the swap, so this is the length of what actually goes on
        # the wire -- including for HEAD, which sends the header and no
        # body and must still describe the GET it stands in for.
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status, payload):
        self._send(status, json.dumps(payload), "application/json")

    def _send_static(self, filename):
        path = os.path.join(PUBLIC_DIR, filename)
        try:
            with open(path, "rb") as handle:
                body = handle.read()
        except OSError:
            self._send_json(404, {"error": "not found"})
            return
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if content_type.startswith("text/") or filename.endswith((".js", ".webmanifest")):
            content_type += "; charset=utf-8"
        self._send(200, body, content_type)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        # `/cycle/49` is a real URL so an Agora reply can link straight at
        # one entry (item 4). The server has no per-cycle view -- it serves
        # the same shell and app.js reads the path -- but the URL must
        # resolve, or the link is dead on a cold load.
        if path == "/" or path.startswith("/cycle/"):
            self._send_static("index.html")
            return
        if path in STATIC_ROUTES:
            self._send_static(STATIC_ROUTES[path])
            return
        try:
            if path == "/api/journal":
                self._send_json(200, journal_payload())
                return
            if path == "/api/digest":
                self._send_json(200, digest_payload())
                return
            if path == "/api/comments":
                self._send_json(200, comments_payload())
                return
        except Exception as e:
            log(f"nova-site {path} failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        self._send_json(404, {"error": "not found"})

    def do_HEAD(self):
        self.do_GET()

    def _read_json_body(self):
        """Body -> dict, or None having already sent the error response.

        The length is checked *before* the read, not after: `rfile.read(n)`
        allocates whatever Content-Length claims, and this pod's memory
        limit is 256Mi.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0:
            self._send_json(411, {"error": "a Content-Length is required"})
            return None
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"error": f"body over {MAX_BODY_BYTES} bytes"})
            return None
        # Not a formality -- see the CSRF note in the module docstring.
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type != "application/json":
            self._send_json(415, {"error": "Content-Type must be application/json"})
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "expected a JSON object"})
            return None
        return payload

    def _post_comment(self, payload):
        """`/api/comment` -- Edvard replying to one cycle (ideas.md #44).

        `{"target": "needs"}` instead of a `cycle` answers the digest's
        Needs Edvard block (2026-08-10) -- see `nova_comments`.

        The same two boundaries as the capture box, and the same reason
        they hold: the tailnet authenticates, and the endpoint's shape
        authorizes. `cycle` is coerced to an int, `target` is checked
        against a one-value allow-list, and `text` must be a string, so
        nothing a client sends addresses a document -- the path is the
        module-level COMMENTS_PATH constant and there is no target to
        choose. The worst a request can do is add a comment to a file Nova
        reads and Edvard can delete.
        """
        cycle = payload.get("cycle")
        target = payload.get("target")
        text = payload.get("text")
        if not isinstance(text, str):
            self._send_json(400, {"error": "text must be a string"})
            return
        if target is not None:
            if target != "needs":
                self._send_json(400, {"error": "target must be 'needs'"})
                return
            if not clean_comment_text(text):
                self._send_json(400, {"error": "nothing to comment"})
                return
            self._store_comment(lambda: add_needs_comment(text), text, "Needs Edvard")
            return
        # `True` is an int in Python and would silently become cycle 1.
        if isinstance(cycle, bool) or not isinstance(cycle, (int, str)):
            self._send_json(400, {"error": "cycle must be a number"})
            return
        try:
            cycle = int(cycle)
        except ValueError:
            self._send_json(400, {"error": f"cycle must be a number, got {cycle!r}"})
            return
        if cycle < 0:
            self._send_json(400, {"error": "cycle must not be negative"})
            return
        if not clean_comment_text(text):
            self._send_json(400, {"error": "nothing to comment"})
            return

        # The stamp is minted here rather than inside `add_comment` because
        # it is this comment's identity: it is what the reply worker uses
        # to find the comment again, and a second call to `format_stamp`
        # can land in the next minute.
        stamp = format_stamp()
        if self._store_comment(lambda: add_comment(cycle, text, stamp), text, f"cycle {cycle}"):
            # Only cycle comments get a reply. A `Needs Edvard` answer is a
            # decision for a cycle to act on, not a conversation -- replying
            # to it would put a paragraph where a piece of work belongs.
            enqueue_reply(cycle, stamp)

    def _store_comment(self, store, text, label):
        """Write one comment and audit it, whichever target it names. -> ok.

        `store` is a no-argument callable so this stays ignorant of which
        writer it is driving and what that writer's signature looks like;
        `text` and `label` are only ever used to describe the write in the
        audit trail.

        Every bad request is answered by the caller, so anything `store`
        rejects from here is the vault failing rather than the client
        asking for something wrong -- which is what makes 502 correct
        below without having to read the failure message to decide.
        """
        try:
            ok, message = store()
        except Exception as e:
            log(f"nova-site comment failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return False

        audit(
            "Nova",
            "",
            "nova_comment",
            f"Comment on {label} · {'ok' if ok else message}",
            after=text[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        self._send_json(200 if ok else 502, {"ok": ok, "message": message})
        return ok

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path not in ("/api/capture", "/api/comment"):
            self._send_json(404, {"error": "not found"})
            return

        payload = self._read_json_body()
        if payload is None:
            return
        if path == "/api/comment":
            self._post_comment(payload)
            return
        target = payload.get("target")
        text = payload.get("text")
        if target not in CAPTURE_TARGETS:
            self._send_json(400, {"error": f"target must be one of {sorted(CAPTURE_TARGETS)}"})
            return
        if not isinstance(text, str):
            self._send_json(400, {"error": "text must be a string"})
            return

        try:
            ok, message = capture(target, text)
        except Exception as e:
            log(f"nova-site capture failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        # Recorded whether or not it succeeded, and the Tailscale identity
        # headers go in as evidence rather than as a check -- nothing here
        # trusts them yet. A future cycle reading real values in the
        # Activity feed is what would justify tightening the boundary.
        audit(
            "Nova",
            "",
            "nova_capture",
            f"Capture to {target} · {'ok' if ok else message}",
            after=text[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        self._send_json(200 if ok else 502, {"ok": ok, "message": message})


def start_nova_site():
    server = ThreadingHTTPServer(("0.0.0.0", NOVA_PORT), NovaSiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log(f"nova site listening on :{NOVA_PORT}")
    return server
