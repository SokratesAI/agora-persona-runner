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
it in prose regardless. Edvard spotted the split from the outside:
*"There are different tools for you and Gemini? That should not be the
case. Gemini and other agents should use the same custom tools as you
do."* They do now -- caps is handed to tools_mcp, which serves the very
same client_tool_schemas/execute_tool pair the other two providers run
in-process, over MCP, back into this process.

What has NOT changed: Agora's capability checkboxes still don't bound
this provider the way they bound the other two. The CLI's own built-in
tools stay unrestricted by default (Edvard's explicit 2026-08-01 call,
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
commands anyway), and Edvard's call was that restriction should be an
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
"""
import json

from agora_runner.config import CLAUDE_BRIDGE_URL, CLAUDE_BRIDGE_TOKEN, RUNNER_SELF_URL
from agora_runner.log import log
from agora_runner.http_util import http_json
from agora_runner.tool_activity import grant as grant_tool_activity, revoke as revoke_tool_activity
from agora_runner.tools_mcp import grant as grant_mcp, revoke as revoke_mcp


class ClaudeBridgeUsageLimited(Exception):
    """Real subscription/API usage cap reported by the bridge (HTTP 429) --
    distinct from a generic failure. Callers should not retry immediately."""


def claude_cli_generate(model_id, thinking, system, history, caps, persona, conversation_id,
                         on_text=None, active_step=None, on_thinking=None):
    if not history:
        raise RuntimeError("empty history after normalization")
    prompt = history[-1].get("content", "")
    if not prompt:
        raise RuntimeError("claude_cli: last history entry has no content to send")

    headers = {}
    if CLAUDE_BRIDGE_TOKEN:
        headers["x-bridge-token"] = CLAUDE_BRIDGE_TOKEN

    body = {
        "conversation_id": conversation_id,
        "system": system,
        "prompt": prompt,
        "model": model_id,
        "restricted": bool(persona.get("claudeCliRestricted")),
        "stateless": bool(persona.get("claudeCliStateless")),
    }
    # Live tool-use chips (2026-08-03): this call is about to block for up
    # to 45 minutes while the CLI does real work in another pod, and
    # nothing about that work is visible to Edvard until it returns. The
    # grant token lets the bridge narrate it as it happens, scoped to this
    # conversation and revoked the moment the call ends -- tool_activity.py
    # explains why it is a callback here rather than the bridge posting to
    # Agora directly.
    activity_token = grant_tool_activity(persona.get("name", ""), conversation_id)
    if activity_token:
        body["activity"] = {
            "url": f"{RUNNER_SELF_URL}/tool-activity",
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
            "url": f"{RUNNER_SELF_URL}/mcp",
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
        status, resp = http_json(
            "POST", f"{CLAUDE_BRIDGE_URL}/generate", body, headers, timeout=2760)
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
