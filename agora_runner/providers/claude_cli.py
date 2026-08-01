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

Chat mode only (2026-07-31 design decision): the bridge's CLI session has
its own tools disabled entirely (agora-claude-bridge's
CHAT_MODE_DISALLOWED_TOOLS), so there is no client-side tool loop here --
caps/active_step are accepted for interface-compatibility with the other
two providers but unused. A dev-agent mode (real git/gh access, for the
Evolve-Coder use case) is a deliberately separate later phase.
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
    }
    debug_status_log = f"claude_cli request: model={model_id} conversation={conversation_id}"
    log(debug_status_log)
    status, resp = http_json("POST", f"{CLAUDE_BRIDGE_URL}/generate", body, headers, timeout=300)

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
