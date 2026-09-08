"""Claude Code CLI bridge provider -- calls the agora-claude-bridge
service instead of talking to Anthropic's Messages API directly.

Structurally different from anthropic_generate/gemini_generate_with_fallback
in one important way: those are stateless HTTP APIs, so every call resends
the FULL history. This provider does not -- the bridge holds a persistent
Claude Code CLI session per conversation_id (--resume across turns and pod
restarts, agora-claude-bridge/bridge/sessions.py), so only the newest
message is sent as this turn's prompt; everything earlier already lives in
that session. The bridge itself decides whether to prepend the system/
persona prompt (only on a conversation's first-ever turn there).

No client-side tool loop here, but as of 2026-08-06 that no longer means
no capability tools. It used to: caps was accepted purely for
interface-compatibility with the other two providers and then ignored, so
a claude-cli persona had the CLI's own built-in tools (Bash, Read, Write,
...) and none of Agora's -- no vault_read, no kubectl_read, no create_pr,
no merge_pr -- while turns.py:build_system described every one of them to
it in prose regardless. The owner spotted the split from the outside:
*"There are different tools for you and Gemini? That should not be the
case. Gemini and other agents should use the same custom tools as you
do."* They do now -- caps is handed to tools_mcp, which serves the very
same client_tool_schemas/execute_tool pair the other two providers run
in-process, over MCP, back into this process.

What has NOT changed: Agora's capability checkboxes still don't bound
this provider the way they bound the other two. The CLI's own built-in
tools stay unrestricted by default (the owner's explicit 2026-08-01 call,
below), so caps widen what this persona can reach rather than limiting
it. The calls also still happen in another pod and are invisible to this
process while they run, which is why it hands the bridge a scoped
callback to narrate them -- see tool_activity.py. A capability tool
called through MCP is consequently narrated twice: once by the bridge as
the CLI-side call (`mcp__agora__vault_write`) and once by execute_tool's
own audit(), which is the half carrying the before/after diff.

2026-08-01 design call (reversed from the original "chat mode, no tools"
v1 plan): unrestricted by default, same as an interactive Claude Code
session -- the earlier always-on tool denylist was live-tested and found
incomplete (the model found and used an unlisted tool to run real shell
commands anyway), and the owner's call was that restriction should be an
explicit choice, not an incomplete default. `persona["claudeCliRestricted"]`
(off unless a persona sets it) requests the bridge's full known-tool
denylist for that persona's calls -- see agora-claude-bridge's
DISCOVERED_FULL_TOOL_ROSTER for exactly what that blocks.

Same day: `persona["claudeCliStateless"]` (also off by default) requests
that the bridge skip session persistence entirely for this call -- built
for the Evolve workflow, whose steps are deliberately bounded and should
only see their own prompt's context, not an ever-accumulating CLI-side
memory across every cycle (cross-cycle memory is meant to live in the
vault journal, per identity.md, not in raw session replay). An ordinary
chat persona wants the opposite -- continuity across turns -- which is
why this stays opt-in.

2026-08-10: attachments. This provider was the only one of the three that
dropped them -- anthropic_generate and gemini_generate have built real
image content blocks since 2026-07-24, and this one sent the text and
nothing else, so an image reached the model as if it had never been
attached. Harmless while claude-cli was Nova's own text-only lane; a live
regression the moment Cycle 78 moved six of the owner's chat personas onto
it to get them off the metered API. The bridge now takes an `attachments`
list and hands the CLI a real user message over --input-format stream-json
(agora-claude-bridge's cli.write_stream_json_input) instead of a text-only
`-p` argument. Sending none is still exactly the old wire shape.
"""
import base64
import json
import socket
import time
import urllib.error

from agora_runner.config import (CLAUDE_BRIDGE_URL, CLAUDE_BRIDGE_TOKEN,
                                 CLAUDE_CLI_CONCURRENT, RUNNER_CALLBACK_URL)
from agora_runner.log import log
from agora_runner.http_util import http_json, fetch_attachment_bytes
from agora_runner.tool_activity import grant as grant_tool_activity, revoke as revoke_tool_activity
from agora_runner.tools_mcp import grant as grant_mcp, revoke as revoke_mcp


class ClaudeBridgeUsageLimited(Exception):
    """Real subscription/API usage cap reported by the bridge (HTTP 429) --
    distinct from a generic failure. Callers should not retry immediately."""


# How long to keep offering a turn to a bridge that is not there yet, and
# how long to wait between offers. The owner lost a chat message on
# 2026-09-08 at ~14:52: no reply, the chat spun forever, and the cluster
# events said why -- the bridge Deployment uses strategy Recreate, so the
# old pod is scaled to 0 and only then is the replacement created, and
# there was a ~7 minute window with no bridge at all. His message landed
# in it. The bridge has always implemented its half of this: a pod that is
# draining answers /generate with 503 {"error": "shutting_down"} and the
# comment above that line says it is there "so the caller can retry
# against the replacement pod" (bridge/server.py). Nothing implemented the
# caller's half. 600s is chosen against that measured ~7 minute gap with
# room over it, not against a generation time -- every wait here happens
# while no turn is running anywhere.
BRIDGE_RETRY_SECONDS = 600
BRIDGE_RETRY_BACKOFF = (5, 10, 20, 30)


def _bridge_never_took_it(exc):
    """True only when the request provably never reached the bridge.

    Retrying is safe exactly when nothing was delivered. A refused
    connection and a DNS failure both happen before a byte of the body is
    sent, so no turn can have started. A connection *reset* after the
    request was accepted is deliberately not in here: from this side it is
    indistinguishable from the refusal above, and retrying it would ask a
    live pod to run the same turn a second time.

    `BrokenPipeError` is the third state the owner's capture names, and it
    is safe for a narrower reason than the other two rather than the same
    one. EPIPE is raised only by a *write* to a socket the peer has already
    closed, and `http.client` makes no write after the request body on a
    non-chunked POST -- so an EPIPE here means some of our bytes never
    arrived, the bridge's Content-Length is unsatisfied, and it has no
    complete request to run. The moment a full request has landed there is
    nothing left for us to write, so the failure surfaces as a reset on the
    *read* instead, which is the case above and stays out.
    """
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ConnectionRefusedError, BrokenPipeError, socket.gaierror))


def _post_generate(body, headers, timeout):
    """POST /generate, waiting out a bridge that is mid-rollout.

    Returns `(status, resp)` exactly as `http_json` does, and raises what
    `http_json` raises once the retry budget is spent. Only the two states
    that mean "this pod has not run your turn and will not" are retried:
    a 503 whose error is `shutting_down`, which the bridge sends before
    doing any work, and a connection the pod never accepted.
    """
    deadline = time.monotonic() + BRIDGE_RETRY_SECONDS
    attempt = 0
    while True:
        try:
            status, resp = http_json(
                "POST", f"{CLAUDE_BRIDGE_URL}/generate", body, headers,
                timeout=timeout)
            if not (status == 503 and (resp or {}).get("error") == "shutting_down"):
                return status, resp
            why = "503 shutting_down"
            last = None
        except urllib.error.URLError as e:
            if not _bridge_never_took_it(e):
                raise
            why = f"unreachable ({getattr(e, 'reason', e)})"
            last = e
        wait = BRIDGE_RETRY_BACKOFF[min(attempt, len(BRIDGE_RETRY_BACKOFF) - 1)]
        if time.monotonic() + wait >= deadline:
            log(f"claude_cli: bridge {why} for the whole "
                f"{BRIDGE_RETRY_SECONDS}s budget, giving up")
            if last is not None:
                raise last
            return status, resp
        log(f"claude_cli: bridge {why}, retrying in {wait}s")
        time.sleep(wait)
        attempt += 1


def _bridge_attachments(message):
    """This turn's attachments in the bridge's wire shape -- mirrors
    _anthropic_content / _gemini_parts, which is the point: those two
    built real image blocks from 2026-07-24 and this provider built
    nothing, so a claude-cli persona saw an image as if it had never
    been sent. Harmless while claude-cli was Nova's own text-only lane;
    a live regression the moment Cycle 78 moved six of the owner's chat
    personas onto it to get them off the metered API.

    Only the newest message, because unlike the stateless APIs this
    provider sends only this turn (see the module docstring) -- an
    earlier message's image already reached the CLI session when it was
    the newest one.

    An attachment that isn't an image, or whose fetch failed, is passed
    on with no `data` and the bridge renders the same "[attached file:
    ...]" note the other two providers emit."""
    out = []
    for att in message.get("attachments") or []:
        mime = att.get("mimeType", "")
        data = fetch_attachment_bytes(att["id"]) if mime.startswith("image/") else None
        entry = {"filename": att.get("filename", "?"), "mimeType": mime}
        if data is not None:
            entry["data"] = base64.b64encode(data).decode()
        out.append(entry)
    return out


def _bridge_persona_id(persona):
    """Which persona to tell the bridge about, for the memory pin only.

    Every persona is itself, Nova included. Nova used to be excepted here
    and sent nothing, because the bridge picked its memory directory off the
    *message* -- `is_cycle_opening` matches the heartbeat text, so a cycle
    got `nova-memory` -- and sending an id on a non-heartbeat turn would have
    pinned `persona-memory/<nova id>` instead: a second working memory that
    no cycle reads. That split is gone. The bridge now knows which id is
    Nova's (`quota.NOVA_PERSONA_ID`) and resolves it to the same
    `nova-memory` directory a cycle gets, so a live Nova conversation and a
    heartbeat write one brain -- which is what idea #186 asks for: "talking
    to Nova anywhere should feel like talking to the same person".

    Ordering: the bridge half has to be live first. A runner that sends the
    id to a bridge that predates that constant is exactly the split this
    docstring used to describe, so do not ship this ahead of it.
    """
    return (persona or {}).get("id") or ""


def claude_cli_generate(model_id, thinking, system, history, caps, persona, conversation_id,
                         on_text=None, active_step=None, on_thinking=None, ephemeral=False):
    """ephemeral (2026-09-06): this turn is a side question, not a turn of the
    conversation -- the /invoke path behind Ask, which Decisions/0005 defines
    as tool-less and persisting nothing. It needs `conversation_id` anyway,
    because the bridge refuses a request without one (400 "conversation_id and
    prompt (or attachments) are required"), which is why Ask answered 502 on
    every claude-cli persona until today and therefore only ever worked on the
    metered `anthropic:` models production may not use.

    Handing the real id over is not enough on its own, and passing it alone
    would have been worse than the 502: `generate` below resumes the
    conversation's stored CLI session by that id and writes the new session id
    back, so a side question would land inside the conversation's own history;
    and `grant_tool_activity` returns a token for any truthy id, so the CLI's
    tool calls would render as chips in a conversation Ask is not supposed to
    write to. So ephemeral forces the two flags that already exist for exactly
    this shape -- `stateless` (never read or write the stored session) and
    `restricted` (block the tool roster) -- and takes no activity grant.
    """
    if not history:
        raise RuntimeError("empty history after normalization")
    # `or ""`, not a default: merge_history copies `text` through
    # unnormalised (turns.py), so a null there would arrive as None and the
    # guard below no longer stops it once attachments are present. Agora
    # coerces at the only route that accepts attachments (server.ts, and
    # 16,475 live messages carry no null), so this is a boundary being
    # closed rather than a bug being fixed -- but the failure if it ever
    # opens is a 500 per retry and "N consecutive failed reply attempts".
    prompt = history[-1].get("content") or ""
    attachments = _bridge_attachments(history[-1])
    # An image with no caption is a real message. This used to raise on it,
    # which is the empty-turn crash _gemini_parts documents -- there the
    # message became an empty part and the API rejected the turn.
    if not prompt and not attachments:
        raise RuntimeError("claude_cli: last history entry has no content to send")

    headers = {}
    if CLAUDE_BRIDGE_TOKEN:
        headers["x-bridge-token"] = CLAUDE_BRIDGE_TOKEN

    body = {
        "conversation_id": conversation_id,
        "system": system,
        "prompt": prompt,
        "model": model_id,
        "restricted": bool(persona.get("claudeCliRestricted")) or ephemeral,
        "stateless": bool(persona.get("claudeCliStateless")) or ephemeral,
        # Off unless CLAUDE_CLI_CONCURRENT is set on this deployment. See
        # config.py: this is the 18-minute-cadence switch, and until it is
        # on, a second heartbeat blocks on the bridge's lock rather than
        # overlapping the first.
        "allow_concurrent": CLAUDE_CLI_CONCURRENT,
        # Which persona is speaking. The bridge uses it for one thing --
        # pinning this persona's own auto-memory directory, so a chat
        # persona remembers across conversations the way a Nova cycle
        # remembers across cycles (idea #165). Until this field existed the
        # bridge had no idea who it was running, so it pinned a memory
        # directory only for a Nova cycle and every other turn fell back to
        # the CLI's per-working-directory default -- which on a concurrent
        # slot is a fresh, empty directory every single turn. That is why
        # all three of Agora's memory stores measured empty: not a missing
        # feature, a missing identity on this request.
        "persona_id": _bridge_persona_id(persona),
    }
    if attachments:
        body["attachments"] = attachments
    # Live tool-use chips (2026-08-03): this call is about to block for up
    # to 45 minutes while the CLI does real work in another pod, and
    # nothing about that work is visible to the owner until it returns. The
    # grant token lets the bridge narrate it as it happens, scoped to this
    # conversation and revoked the moment the call ends -- tool_activity.py
    # explains why it is a callback here rather than the bridge posting to
    # Agora directly.
    #
    # Both callbacks address *this pod*, not the Service (RUNNER_CALLBACK_URL
    # in config.py says why): the tokens below live in this process's memory,
    # so a call that lands on the other pod of a rolling update is a 401 and
    # the CLI comes up with no Agora tools at all.
    activity_token = None if ephemeral else grant_tool_activity(persona.get("name", ""), conversation_id)
    if activity_token:
        body["activity"] = {
            "url": f"{RUNNER_CALLBACK_URL}/tool-activity",
            "token": activity_token,
        }

    # Agora's own capability tools, over MCP, for the length of this turn
    # (tools_mcp.py). Same shape and same lifecycle as the activity grant
    # above: a random per-turn token, revoked in the finally below. None
    # when the persona has no capabilities at all, in which case no `mcp`
    # block is sent and the bridge runs the CLI exactly as it did before.
    mcp_token = grant_mcp(persona, caps or {}, conversation_id)
    if mcp_token:
        body["mcp"] = {
            "url": f"{RUNNER_CALLBACK_URL}/mcp",
            "token": mcp_token,
        }

    debug_status_log = f"claude_cli request: model={model_id} conversation={conversation_id}"
    log(debug_status_log)
    try:
        # Must exceed the bridge's own CLI_TIMEOUT_SECONDS (2700s as of
        # 2026-08-03 -- bumped from 900s after Cycle 8 hit that wall running
        # the full v2 single-session arc: read state, decide, implement,
        # review, merge, health-check, journal, all in one call) or this HTTP
        # call gives up before the bridge itself would.
        status, resp = _post_generate(body, headers, timeout=2760)
    finally:
        revoke_tool_activity(activity_token)
        revoke_mcp(mcp_token)

    if status == 429:
        detail = resp.get("detail", "usage limit")
        log(f"claude_cli usage limit: {detail}")
        raise ClaudeBridgeUsageLimited(detail)
    if status != 200:
        log(f"claude_cli {status}: {json.dumps(resp)[:300]}")
        raise RuntimeError(f"claude_cli {status}: {json.dumps(resp)[:300]}")

    text = resp.get("text", "")
    thought = resp.get("thinking", "")
    if thought and on_thinking:
        on_thinking(thought)
    if not text:
        raise RuntimeError("claude_cli returned no text")
    if on_text:
        on_text(text, True)
    return text
