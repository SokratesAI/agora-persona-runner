"""One toolset for every agent -- Agora's own capability tools, served to
claude-cli personas over MCP.

The owner, 2026-08-06, reading a digest line that mentioned "both tools that
made them -- the one I use and the one your Gemini/Anthropic personas
use": *"There are different tools for you and Gemini? That should not be
the case. Gemini and other agents should use the same custom tools as you
do."*

He was right, and the split was worse than he knew. Gemini and
Anthropic-API personas run `client_tool_schemas` + `execute_tool` in this
process (providers/gemini.py, providers/anthropic.py). A claude-cli
persona ran neither: providers/claude_cli.py has no client-side tool loop
at all, so `vault_read`, `kubectl_read`, `create_pr`, `merge_pr`,
`terminal_exec` and the rest simply did not exist for it -- while
turns.py:build_system cheerfully told it, in prose, that it had every one
of them. Nova has been reading "You have merge_pr -- merges an open PR,
but only once every check-run on it is green. It refuses otherwise; there
is no override" and then merging with a raw `gh pr merge`, which has no
such guard. The system prompt was describing a toolset nobody had wired
up for that provider.

So this module closes it from the side that keeps ONE implementation:
the CLI session reaches back into this process and calls the same
`execute_tool` everyone else calls. Not a second copy of the tools -- the
same function, the same capability gate, the same audit trail.

## Why an HTTP MCP server, and why the grant token

The transport is MCP over plain HTTP, hosted on invoke_server alongside
/tool-activity, and authenticated exactly the same way and for exactly
the same reason (tool_activity.py has the long version): the bridge pod
runs an unrestricted shell and deliberately cannot read AGORA_TOKEN, so
the runner mints a random single-purpose token per generate() call and
revokes it in a finally when the call returns. A grant is scoped to one
persona, one conversation, one set of capabilities, for the lifetime of
one turn. The bridge never learns anything durable.

Capabilities are frozen into the grant rather than re-read per request:
the caps that were true when the turn started are the caps the turn runs
under, and a persona edited mid-cycle cannot widen its own reach in
flight.

## Measured facts about the CLI end (v2.1.197, 2026-08-06)

Verified live against a probe server in the bridge pod before any of
this was written, because two of the three would have been guesses:

  * The handshake is `initialize` -> `notifications/initialized` (a
    JSON-RPC notification: no `id`, so no result may be returned) ->
    `tools/list` -> `tools/call`. The Authorization header is carried on
    every one of them, including the notification.
  * An UNREACHABLE server does not abort the run -- the CLI logs the
    failed server and answers normally (exit 0). So a runner that is
    somehow not serving this endpoint degrades to exactly today's
    behaviour rather than breaking the cycle.
  * A MALFORMED --mcp-config file DOES abort the run, hard, before the
    model is ever called ("Error: Invalid MCP configuration", exit 1).
    That one is the real hazard, and it is the bridge's side to hold --
    see agora-claude-bridge/bridge/cli.py, which builds that file with
    json.dump and drops the flag entirely if it cannot.

## Errors are results, not faults

A tool that fails returns `isError: true` with the failure as text,
never a JSON-RPC error. MCP draws that line deliberately and it matters
here: a JSON-RPC error is a broken server, which the CLI may stop
talking to, while an isError result is handed to the model, which can
read "[not found: foo.md]" and try something else. Every path below that
can throw is wrapped for the same reason -- an unhandled exception on
this thread would surface as a dead connection mid-cycle.

## Audited against the NSA MCP guidance, 2026-08-31 (idea #175)

The owner's idea says the reason to do this: every security review on this
estate so far has been our own reasoning about our own design, which is the
review most likely to miss the thing everyone else already knows to check.
The external baseline is the NSA AI Security Center's Cybersecurity
Information Sheet *Model Context Protocol (MCP): Security Design
Considerations for AI-Driven Automation* (U/OO/6030316-26, May 2026).

**What I actually read, because it changes how much this is worth.** Both
pods are refused the primary PDF -- `media.defense.gov` and `nsa.gov` each
answer 403 to `curl` from the bridge pod and from the runner pod, with a
browser user-agent, so this audit is built on two secondary reports of the
CSI rather than on the document. The four operational requirements below
are quoted as those reports quote them. A cycle with a way to fetch the PDF
should redo this against the original; what is here is a real audit of a
faithful summary, not a reading of the source.

Per recommendation: what we do, what we do not, and what we decided
otherwise. The third answer is legitimate and is written rather than
assumed.

1. **Cryptographic message integrity -- sign and verify every MCP message
   at the protocol layer. NOT DONE, deliberately.** The transport is plain
   HTTP between two pods in one Kubernetes namespace with no intermediary,
   and signing would need a key distribution story this estate does not
   have. The honest cost of that decision: the grant token travels in a
   header in cleartext on the cluster network, so anything that can read
   that network reads a live token. That is the same exposure `AGORA_TOKEN`
   already has, which is why it is a decision and not an oversight -- but
   it is a decision, and a service mesh would close it.

2. **Least-privilege tool-call scoping, no ambient authority. DONE, and
   enforced twice.** A grant is one persona, one conversation, one frozen
   capability set, for one turn, revoked in a `finally`. `tools/call`
   rebuilds the allowlist from the same caps that produced `tools/list`, so
   "the model can only call what it was shown" is true rather than
   intended. Where we fall short of the letter of it: the CSI wants
   authorisation attached to an *invocation*, and ours attaches to a turn.
   A turn is much narrower than a session and still wider than a call.

3. **Tamper-evident audit covering every agent action. COVERAGE DONE,
   TAMPER-EVIDENCE NOT.** Measured 2026-08-31 by walking every `if name ==`
   branch in `tools_dispatch.execute_tool`: 32 tools, and all 32 audit --
   29 call `audit()` directly and `vault_write`, `vault_append` and
   `scoped_write` go through `_audit_vault_write`. There is no hash chain:
   entries are rows in Agora, and anything that can write them can rewrite
   them. Chaining is a real piece of work and is not scoped here.

4. **Trust chains -- gateway as a trust boundary, certificates verified in
   both directions. NOT DONE.** There is no gateway and no TLS. What stands
   in for it is the NetworkPolicy plus the bearer token, which authenticates
   the *turn* and nothing about the host.

Of the supporting controls those reports name:

  * **Content-length checks.** Was missing on `invoke_server`'s three POST
    routes and is not any more (Cycle 698). `nova_site.py` had held the
    same line for months; the two servers were built from one piece of
    reasoning and only one got it.
  * **Rate limiting. MISSING, and it is the next gap here.** Nothing limits
    how many `tools/call` a grant may make.
  * **Message expiry and replay protection. PARTIAL.** The token is single
    purpose and dies with the turn, but carries no expiry of its own and no
    nonce, so a replay inside the turn window succeeds.
  * **Tool execution sandboxing. PARTIAL, and on purpose.** `--restricted`
    and `claudeCliRestricted` exist (idea #168) and no persona is started
    restricted yet; `terminal_exec` is an unrestricted shell by design and
    the owner has said so repeatedly.
  * **Tool name collision and drift detection. NOT APPLICABLE.** One server,
    one schema source (`client_tool_schemas`), which is the whole point of
    this module.
  * **Scanning for open MCP listeners. NOT DONE.** `tools.nas_ports` sweeps
    the NAS; nothing sweeps the cluster for this.
  * **Filtering egress proxy / DLP. PARTIAL.** NetworkPolicies bound where
    this pod can reach, and `redact()` masks secret-sourced values on the
    way out. There is no content inspection of a tool result.
  * **Indirect prompt injection and toolchain pivot detection. NOT DONE,
    and it is the largest hole on this list.** It is already on my own
    board as issue #15: `POST /api/board/comment` accepts any `author`
    string and nothing establishes who the caller is, while `prompt.md`
    tells every cycle that an unprocessed capture from the owner outranks
    everything. The CSI's framing is the useful half -- this is not a data
    integrity problem, it is an authorisation problem on the highest
    priority instruction this loop accepts.
"""

import json
import secrets
import threading
import traceback

from agora_runner.log import log, debug_log
from agora_runner.tools_schemas import client_tool_schemas
from agora_runner.tools_dispatch import ToolImage, execute_tool

# The MCP revision we implement. The CLI sends its own in initialize's
# params and we echo that back when it is a string, which is what the
# spec asks for and what keeps this working across CLI upgrades without
# a code change; this is only the fallback for a client that sends none.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

SERVER_NAME = "agora"


class _Grant:
    __slots__ = ("persona", "caps", "conversation_id")

    def __init__(self, persona, caps, conversation_id):
        self.persona = persona
        self.caps = caps
        self.conversation_id = conversation_id


# Guards _grants only -- execute_tool is deliberately called outside the
# lock. Tool calls arrive on invoke_server's handler threads and a single
# terminal_exec can block for minutes; holding the lock across one would
# stall every other persona's tool calls behind it.
_lock = threading.Lock()
_grants = {}


def grant(persona, caps, conversation_id):
    """Authorise one turn's tool access, returning the bearer token.

    Returns None when there is nothing to authorise -- no capabilities
    means an empty tool list, and an MCP server advertising no tools is
    strictly worse than no MCP server at all (it costs the CLI a
    handshake to learn it is useless).
    """
    if not any(caps.values()):
        return None
    token = secrets.token_urlsafe(32)
    with _lock:
        _grants[token] = _Grant(persona, dict(caps), conversation_id)
    return token


def revoke(token):
    """End a grant. Called in a finally, so a failed turn cannot leave a
    live token behind for the rest of the process's life."""
    if token:
        with _lock:
            _grants.pop(token, None)


def _result(request_id, payload):
    return 200, {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message):
    return 200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _mcp_tools(caps):
    """client_tool_schemas' Anthropic-shaped tools, in MCP's shape.

    The only difference is the key holding the JSON Schema --
    `input_schema` there, `inputSchema` here. Converting rather than
    branching inside client_tool_schemas is the whole point of this
    module: there stays exactly one definition of what each tool is and
    what it takes, and providers translate at the edge.
    """
    tools = []
    for tool in client_tool_schemas(caps):
        tools.append({
            "name": tool["name"],
            "description": tool.get("description", ""),
            "inputSchema": tool.get("input_schema") or {"type": "object", "properties": {}},
        })
    return tools


def handle_http(auth_header, body):
    """One MCP request straight off the wire. Returns (status, payload_or_None).

    Two HTTP servers in this process family now expose /mcp -- the runner's
    invoke_server for persona turns, and nova_site for the journal-card
    reply, which mints its own grant because it runs in a different pod.
    Everything between the socket and `handle` is identical for both, so it
    lives here once: the bearer token is pulled from the Authorization
    header rather than the body because it has to travel on requests whose
    body shape is the MCP spec's and not ours, and a body that will not
    parse is a 400 rather than a JSON-RPC error because there is no
    envelope yet to put an error in.

    `body` is the raw request bytes. A None payload out means "this status,
    no body" -- see `handle`.
    """
    try:
        request = json.loads(body or b"{}")
    except Exception:
        return 400, {"error": "invalid json body"}
    if not isinstance(request, dict):
        return 400, {"error": "jsonrpc request must be an object"}
    auth = auth_header or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return handle(token, request)


def handle(token, request):
    """One JSON-RPC request. Returns (http_status, payload_or_None).

    A None payload means "send this status with no body", which is the
    correct answer to a notification -- a JSON-RPC message with no `id`
    must not be replied to, and returning a result for one is a protocol
    violation the CLI would be entitled to reject the connection over.
    """
    with _lock:
        entry = _grants.get(token)
    if entry is None:
        return 401, {"error": "unknown or expired mcp token"}

    method = str(request.get("method", ""))
    request_id = request.get("id")
    if request_id is None:
        debug_log(f"mcp notification: {method}")
        return 202, None

    if method == "initialize":
        params = request.get("params") or {}
        client_version = params.get("protocolVersion")
        version = client_version if isinstance(client_version, str) else DEFAULT_PROTOCOL_VERSION
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "1"},
        })

    if method == "tools/list":
        tools = _mcp_tools(entry.caps)
        debug_log(f"mcp tools/list: {len(tools)} tools for {entry.persona.get('name', '?')}")
        return _result(request_id, {"tools": tools})

    if method == "tools/call":
        params = request.get("params") or {}
        name = str(params.get("name", ""))
        args = params.get("arguments")
        if not isinstance(args, dict):
            args = {}
        if not name:
            return _error(request_id, -32602, "tools/call requires a tool name")
        # The grant is a gate, not a menu. `execute_tool` dispatches purely
        # on the tool name and has never checked a capability -- until now
        # the only thing standing between a caps dict and the full toolset
        # was that unlisted tools were merely not *advertised*. Measured
        # live 2026-08-10 against a real server on this handler: a grant of
        # {vaultRead, novaCapture} ran `terminal_exec` and got back
        # `uid=10001(bridge)`. Nothing in the roster was violated because
        # nothing was ever enforced.
        #
        # That was survivable while every grant belonged to a persona whose
        # own caps were the thing being widened. It stopped being
        # survivable when the journal-card reply turn got a grant
        # (nova_replies.py): that turn is triggered by an HTTP POST
        # carrying text from a comment box, and an unenforced allowlist
        # there means a comment can reach a shell.
        #
        # Rebuilt per call rather than frozen alongside the grant so it
        # cannot drift from what `tools/list` actually advertised -- the
        # same caps produce both, which is the property that makes "the
        # model can only call what it was shown" true rather than intended.
        allowed = {tool["name"] for tool in _mcp_tools(entry.caps)}
        if name not in allowed:
            log(f"mcp tools/call refused {name}: not granted to {entry.persona.get('name', '?')}")
            return _result(request_id, {
                "content": [{
                    "type": "text",
                    "text": f"[{name} is not available to this turn]",
                }],
                "isError": True,
            })
        try:
            output = execute_tool(name, args, entry.persona, entry.conversation_id)
            is_error = False
        except Exception as e:
            # Deliberately a result, not a JSON-RPC error: the model can
            # act on "that tool blew up", it cannot act on a dropped
            # connection. Logged with a traceback because nothing else
            # would ever see it -- the model gets one line.
            log(f"mcp tools/call {name} raised: {e}\n{traceback.format_exc()}")
            output = f"[{name} failed: {e}]"
            is_error = True
        # An image comes back as a `ToolImage`, which *is* a string (its
        # own text fallback), so it survives everything below untouched --
        # but MCP can carry the picture itself and nothing else in this
        # process can. Text block first, then the image, so a client that
        # renders only text still gets a true sentence about the file
        # rather than a silently empty result.
        if isinstance(output, ToolImage) and not is_error:
            return _result(request_id, {
                "content": [
                    {"type": "text", "text": str(output)},
                    {"type": "image", "data": output.data_b64, "mimeType": output.mime},
                ],
                "isError": False,
            })
        if not isinstance(output, str):
            output = json.dumps(output) if output is not None else ""
        # A tool that failed *logically* rather than by raising was reported
        # to the model as a success: `isError` was only ever set by the
        # `except` above, so a 409 from a vault write, a missing target file
        # or a refused capture all arrived looking like they worked. The
        # writers in tools_dispatch already say so in their return string
        # ("FAILED(...)" from vault_write_path, "FAILED: ..." from the
        # capture), so the information was there and the protocol was
        # throwing it away. Matching on the prefix keeps one convention
        # rather than adding a second return shape to every tool.
        if not is_error and output.startswith("FAILED"):
            is_error = True
        return _result(request_id, {
            "content": [{"type": "text", "text": output}],
            "isError": is_error,
        })

    return _error(request_id, -32601, f"unknown method: {method}")
