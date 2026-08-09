"""Nova's own read-only site: the journal, styled, on the tailnet.

First slice of Agora ideas.md #34 -- items 1-4 (journal timeline, status
header, digest strip, per-cycle deep links). Read-only: every handler
here is a GET, and nothing in this module writes to the vault.

**Why this lives in the runner rather than in its own repo.** Idea #34
sketched a separate `nova-pwa` service, and named the thing that makes
that expensive: the vault client already exists twice (here and in the
bridge) with nothing detecting drift between them, so a third service
reading CouchDB would mean a third copy -- a bug knowingly introduced.
The runner already holds a vault client and an HTTP server, so serving
the site from here costs neither.

**Why a second port instead of a second path on 8082.** The /invoke
Service is documented as having no public-facing surface at all, and it
carries /invoke, /mcp and /tool-activity. The Tailscale operator's
Ingress does not reliably filter by path, so exposing 8082 at all would
expose those three. A separate port gets its own Service, its own
Ingress and a NetworkPolicy scoped to this port only -- exactly the
shape platform-config already uses to expose Agora's 8080 while leaving
its 8081 unreachable.

**Known limitation, recorded rather than papered over.** This
deployment is `Recreate` with a 2880s grace period, so while a cycle is
draining the pod is Terminating and out of the Service's endpoints --
the site is unreachable for that window. That is honest (Nova really is
being replaced) but it is the one argument for splitting this out
later, and it is why the split is worth reconsidering rather than
settled.

No caching layer: the full 204KB journal assembles from CouchDB in
285ms measured end-to-end (2026-08-09), which is cheaper than the
staleness a cache would buy.
"""

import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agora_runner.config import NOVA_PORT
from agora_runner.log import log
from agora_runner.nova_journal import (
    DIGEST_PATH,
    JOURNAL_PATH,
    build_status,
    parse_digest,
    parse_journal,
    render_blocks,
)
from agora_runner.vault import vault_read_path

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


def journal_payload():
    """Every entry, rendered. The raw `body` is dropped rather than sent
    alongside the blocks -- it is the same 200KB twice, and the client has
    no use for markdown it is not allowed to interpret."""
    entries = parse_journal(vault_read_path(JOURNAL_PATH) or "")
    status = build_status(entries)
    rendered = []
    for entry in entries:
        entry = dict(entry)
        entry["blocks"] = render_blocks(entry.pop("body", ""))
        rendered.append(entry)
    return {"entries": rendered, "status": status}


def digest_payload():
    payload = parse_digest(vault_read_path(DIGEST_PATH) or "")
    payload["needsEdvardBlocks"] = render_blocks(payload["needsEdvard"])
    return payload


class NovaSiteHandler(BaseHTTPRequestHandler):
    server_version = "nova-site"

    def log_message(self, *args):  # quiet default request logging
        pass

    def _send(self, status, body, content_type):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
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
        except Exception as e:
            log(f"nova-site {path} failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        self._send_json(404, {"error": "not found"})

    def do_HEAD(self):
        self.do_GET()


def start_nova_site():
    server = ThreadingHTTPServer(("0.0.0.0", NOVA_PORT), NovaSiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log(f"nova site listening on :{NOVA_PORT}")
    return server
