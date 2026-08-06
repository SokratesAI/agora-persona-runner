"""Sync HTTP server: /invoke (Decisions/0005 -- tool-less by design, for
Ask/Preview), /tool-activity (the bridge's live tool-use callback, see
tool_activity.py) and /mcp (the same callback direction, carrying real
tool calls instead of chips -- see tools_mcp.py)."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from agora_runner.config import AGORA_TOKEN, NO_CAPS, RUNNER_PORT
from agora_runner.log import log
from agora_runner.agora_api import fetch_persona
from agora_runner.turns import build_system
from agora_runner.reply import generate_reply
from agora_runner.tool_activity import report as report_tool_activity
from agora_runner.tools_mcp import handle as handle_mcp


class InvokeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet default request logging
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_tool_activity(self):
        """One tool call, reported by the bridge mid-session, rendered as an
        inline Activity chip.

        Authenticated solely by the per-call grant token in the body --
        that is the entire point (tool_activity.py has the reasoning): this
        endpoint exists so the bridge never needs AGORA_TOKEN, so requiring
        AGORA_TOKEN here would defeat it. An unknown or expired token is a
        401 and writes nothing.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send(400, {"error": "invalid json body"})
            return
        token = payload.get("token") or ""
        capability = str(payload.get("capability", "")).strip()
        if not token or not capability:
            self._send(400, {"error": "token and capability are required"})
            return
        # tool_use_id/output/is_error are the bridge's second report for a
        # call -- what it returned, arriving after the chip for the call
        # itself. Optional throughout: an older bridge sends neither, and a
        # report carrying only detail still renders exactly as before.
        if not report_tool_activity(
            token,
            capability,
            str(payload.get("detail", "")),
            tool_use_id=str(payload.get("toolUseId", "")),
            output=payload.get("output"),
            is_error=payload.get("isError") is True,
        ):
            self._send(401, {"error": "unknown or expired activity token"})
            return
        self._send(202, {"status": "recorded"})

    def _handle_mcp(self):
        """One MCP JSON-RPC request from a claude-cli session's own CLI.

        Authenticated by the per-turn grant token in the Authorization
        header rather than by AGORA_TOKEN, for the same reason
        /tool-activity is (tools_mcp.py has the reasoning) -- and in the
        header rather than the body because the token has to travel on
        requests whose body shape is the MCP spec's, not ours.

        A 202 with no body is the correct, required answer to a JSON-RPC
        notification; tools_mcp.handle signals that by returning a None
        payload. Everything else, including tool failures, comes back as
        HTTP 200 with a JSON-RPC envelope.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send(400, {"error": "invalid json body"})
            return
        if not isinstance(request, dict):
            self._send(400, {"error": "jsonrpc request must be an object"})
            return
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        try:
            status, payload = handle_mcp(token, request)
        except Exception as e:
            log(f"/mcp failed: {e}")
            self._send(500, {"error": str(e)[:300]})
            return
        if payload is None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(status, payload)

    def do_POST(self):
        if self.path == "/tool-activity":
            self._handle_tool_activity()
            return
        if self.path == "/mcp":
            self._handle_mcp()
            return
        if self.path != "/invoke":
            self._send(404, {"error": "not found"})
            return
        if AGORA_TOKEN and self.headers.get("x-agora-token") != AGORA_TOKEN:
            self._send(401, {"error": "invalid agent token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            persona = None
            if payload.get("personaId"):
                persona = fetch_persona(payload["personaId"])
                if persona is None:
                    self._send(404, {"error": "persona not found"})
                    return
            elif payload.get("persona"):
                inline = payload["persona"]
                persona = {
                    "id": None,
                    "name": "Preview",
                    "personality": inline.get("personality", ""),
                    "model": inline.get("model", ""),
                    "thinking": bool(inline.get("thinking")),
                    "sharedMemory": "",
                }
            else:
                self._send(400, {"error": "personaId or persona required"})
                return

            raw = payload.get("messages") or []
            merged = []
            for message in raw:
                role = "user" if message.get("role") == "user" else "assistant"
                content = str(message.get("content", ""))
                if merged and merged[-1]["role"] == role:
                    merged[-1]["content"] += "\n\n" + content
                else:
                    merged.append({"role": role, "content": content})
            while merged and merged[0]["role"] != "user":
                merged.pop(0)
            if not merged:
                self._send(400, {"error": "messages must contain a user turn"})
                return

            system = build_system(persona)
            reply = generate_reply(persona, dict(NO_CAPS), system, merged, None)
            self._send(200, {"reply": reply})
        except Exception as e:
            log(f"/invoke failed: {e}")
            self._send(500, {"error": str(e)[:300]})


def start_invoke_server():
    server = ThreadingHTTPServer(("0.0.0.0", RUNNER_PORT), InvokeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log(f"/invoke server listening on :{RUNNER_PORT}")
