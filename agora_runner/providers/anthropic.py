"""Anthropic (Claude) generation loop, including the client-side tool round-trip."""

import base64
import json

from agora_runner.config import ANTHROPIC_API_KEY, ANTHROPIC_MAX_OUTPUT_TOKENS, ANTHROPIC_NO_THINKING_TOGGLE, TOOL_ROUNDS_MAX
from agora_runner.log import log, debug_log
from agora_runner.http_util import http_json, fetch_attachment_bytes
from agora_runner.tools_schemas import client_tool_schemas
from agora_runner.tools_dispatch import execute_tool


def _anthropic_content(message):
    """A message's `content` -- plain string when it has no attachments
    (unchanged behavior), or a list of content blocks (text + real image
    blocks) when it does (2026-07-24, Issues.md 'sending images... does
    not work' -- mirrors _gemini_parts, see its docstring for why this
    also fixes an empty-turn crash, not just adds a feature)."""
    attachments = message.get("attachments") or []
    if not attachments:
        return message["content"]
    blocks = []
    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})
    for att in attachments:
        mime = att.get("mimeType", "")
        data = fetch_attachment_bytes(att["id"]) if mime.startswith("image/") else None
        if data is not None:
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(data).decode()},
            })
        else:
            blocks.append({"type": "text", "text": f"[attached file: {att.get('filename', '?')} ({mime or 'unknown type'}) -- not loaded]"})
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    return blocks


def anthropic_generate(model_id, thinking, system, history, caps, persona, conversation_id, on_text=None,
                        active_step=None, on_thinking=None):
    """`on_text(chunk, is_final)`, when given, fires once per round that
    produces text -- the preamble round(s) before a tool call
    (is_final=False) and the round that actually ends the turn
    (is_final=True, either the normal return or the salvage fallback).
    This is what makes a turn stream into the chat as it's generated
    (2026-07-24) instead of only appearing once, batched, at the end.
    The return value is unchanged either way (final round's text) --
    callers that don't care about streaming just pass nothing.

    `on_thinking(text)` (2026-07-31): fires once per round with that
    round's extended-thinking block text, when `thinking` is on. The
    round_text join below already only reads type=="text" blocks, so
    thinking blocks were always excluded from the answer correctly --
    they just had nowhere to go. Content is still passed back to the
    API verbatim either way (Anthropic requires it for a tool-use
    continuation); on_thinking is purely an extra look at it."""
    tools = []
    betas = []
    # 2026-07-23 (Issues #1 revisited): dropped Anthropic's server-side
    # web_search tool in favor of the shared web_search_tinyfish client tool
    # below (see client_tool_schemas) -- Gemini's equivalent server-side
    # google_search grounding turned out to have a zero/near-zero free-tier
    # quota (confirmed live: every model 429s the instant google_search is
    # in the request, succeeds instantly without it), and Issues #3 already
    # asks for one shared tool set instead of per-provider mechanisms. A
    # single scraped-search implementation means both providers behave
    # identically and neither depends on a provider's own search billing.
    if caps.get("codeExecution"):
        tools.append({"type": "code_execution_20250522", "name": "code_execution"})
        betas.append("code-execution-2025-05-22")
    # active_step can add scoped_write even when every persona capability
    # flag is False (a read-only-flavored step whitelisting only
    # scoped_write, say) — the `any(caps.values())` short-circuit alone
    # would silently drop it, so active_step widens the gate too.
    client_tools = client_tool_schemas(caps, active_step) if (any(caps.values()) or active_step) else []
    tools.extend(client_tools)

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }
    if betas:
        headers["anthropic-beta"] = ",".join(betas)

    messages = [{"role": m["role"], "content": _anthropic_content(m)} for m in history]
    for _round in range(TOOL_ROUNDS_MAX + 1):
        final_round = _round == TOOL_ROUNDS_MAX
        body = {
            "model": model_id,
            "max_tokens": ANTHROPIC_MAX_OUTPUT_TOKENS.get(model_id, 64000),
            "system": system,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
            if final_round:
                # Out of tool rounds — force a text answer from what it has
                # instead of failing the whole turn (found live 2026-07-22:
                # Gemini happily browsed the vault one file per round).
                body["tool_choice"] = {"type": "none"}
        if model_id not in ANTHROPIC_NO_THINKING_TOGGLE:
            body["thinking"] = {"type": "adaptive"} if thinking else {"type": "disabled"}
        debug_log(f"anthropic request: model={model_id} round={_round} tools={len(tools)}")
        status, resp = http_json(
            # Higher timeout than before: max_tokens now goes up to each
            # model's real ceiling (up to 128k), and a non-streaming call for
            # a genuinely long response can legitimately take several minutes.
            "POST", "https://api.anthropic.com/v1/messages", body, headers, timeout=600
        )
        if status != 200:
            # Always logged (not debug-gated) -- same reasoning as the
            # Gemini 429 body logging above: the error body often names the
            # actual cause (rate_limit_error vs overloaded_error vs a
            # genuine request problem), which a bare status code can't.
            log(f"anthropic {status} detail for model={model_id}: {json.dumps(resp)[:500]}")
            raise RuntimeError(f"anthropic {status}: {json.dumps(resp)[:300]}")

        # Every text block in THIS round, joined -- same reasoning as the
        # final-round join below (a round can carry a preamble across
        # several text blocks). Previously this preamble was silently
        # discarded when the round also called a tool; now it's posted
        # immediately (2026-07-24) so "Now let's update X..." shows up
        # in the chat right before the tool-use chip for X, not lost.
        round_text = "\n".join(
            block["text"].strip() for block in resp.get("content", [])
            if block.get("type") == "text" and block.get("text", "").strip()
        )
        round_thinking = "\n".join(
            block["thinking"].strip() for block in resp.get("content", [])
            if block.get("type") == "thinking" and block.get("thinking", "").strip()
        )
        if round_thinking and on_thinking:
            on_thinking(round_thinking)

        if resp.get("stop_reason") == "tool_use":
            if round_text and on_text:
                on_text(round_text, False)
            results = []
            for block in resp.get("content", []):
                if block.get("type") == "tool_use":
                    output = execute_tool(block["name"], block.get("input") or {}, persona, conversation_id,
                                           active_step)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": output,
                    })
            # Content passed back verbatim — required for thinking blocks.
            messages.append({"role": "assistant", "content": resp["content"]})
            messages.append({"role": "user", "content": results})
            continue

        # Join every text block, not just the first — a server-side tool
        # (web_search) can produce a preamble ("I'll search for...") as one
        # text block, then more text blocks after the search result, all in
        # the SAME response (no tool_use round-trip, since it's server-side,
        # not a client tool). Returning only the first block silently
        # dropped the actual synthesized answer and sent the preamble
        # instead (found live 2026-07-23, Learning-Agent's web-search
        # replies). Mirrors how the Gemini path already joins all parts.
        if round_text:
            if on_text:
                on_text(round_text, True)
            return round_text
        raise RuntimeError("no text block in Anthropic response")
    # Same salvage guarantee as the Gemini path: flatten and force text.
    plain = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            text = content
        else:
            pieces = []
            for block in content:
                kind = block.get("type")
                if kind == "text":
                    pieces.append(block.get("text", ""))
                elif kind == "tool_use":
                    pieces.append(f"[you called tool {block.get('name')}]")
                elif kind == "tool_result":
                    pieces.append(f"[tool result]: {str(block.get('content'))[:600]}")
                elif kind == "image":
                    pieces.append("[attached image]")
            text = "\n".join(p for p in pieces if p)
        if not text:
            continue
        if plain and plain[-1]["role"] == message["role"]:
            plain[-1]["content"] += "\n\n" + text
        else:
            plain.append({"role": message["role"], "content": text})
    plain.append({"role": "user", "content":
        "You are out of tool calls. Answer now in plain text using what you already have."})
    body = {
        "model": model_id,
        "max_tokens": ANTHROPIC_MAX_OUTPUT_TOKENS.get(model_id, 64000),
        "system": system,
        "messages": plain,
    }
    if model_id not in ANTHROPIC_NO_THINKING_TOGGLE:
        body["thinking"] = {"type": "adaptive"} if thinking else {"type": "disabled"}
    status, resp = http_json(
        "POST", "https://api.anthropic.com/v1/messages", body, headers, timeout=600
    )
    if status != 200:
        raise RuntimeError(f"anthropic salvage {status}: {json.dumps(resp)[:200]}")
    text = "\n".join(
        block["text"].strip() for block in resp.get("content", [])
        if block.get("type") == "text" and block.get("text", "").strip()
    )
    salvage_thinking = "\n".join(
        block["thinking"].strip() for block in resp.get("content", [])
        if block.get("type") == "thinking" and block.get("thinking", "").strip()
    )
    if salvage_thinking and on_thinking:
        on_thinking(salvage_thinking)
    if text:
        if on_text:
            on_text(text, True)
        return text
    raise RuntimeError("anthropic salvage returned no text")
