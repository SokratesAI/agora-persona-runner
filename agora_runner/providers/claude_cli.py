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

No client-side tool loop here -- caps/active_step are accepted for
interface-compatibility with the other two providers but unused. Unlike
Gemini/Anthropic-API personas (whose tools are enforced server-side from
capability flags, Decisions/0007), this persona's tools -- if any -- live
entirely inside the CLI's own session; Agora's capability checkboxes don't
apply to it at all.

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

from agora_runner.config import CLAUDE_BRIDGE_URL, CLAUDE_BRIDGE_TOKEN
from agora_runner.log import log
from agora_runner.http_util import http_json


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
    debug_status_log = f"claude_cli request: model={model_id} conversation={conversation_id}"
    log(debug_status_log)
    # Must exceed the bridge's own CLI_TIMEOUT_SECONDS (2700s as of
    # 2026-08-03 -- bumped from 900s after Cycle 8 hit that wall running
    # the full v2 single-session arc: read state, decide, implement,
    # review, merge, health-check, journal, all in one call) or this HTTP
    # call gives up before the bridge itself would.
    status, resp = http_json("POST", f"{CLAUDE_BRIDGE_URL}/generate", body, headers, timeout=2760)

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
