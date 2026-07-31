"""Gemini generation loop: fallback cascade, salvage call, and the client-side tool round-trip."""

import base64
import json

from agora_runner.config import (
    GEMINI_API_KEY, GEMINI_MAX_OUTPUT_TOKENS, GEMINI_FALLBACK_CHAIN, GEMINI_LABELS,
    GEMINI_MODEL_FALLBACK, GeminiRateLimited, GEMINI_TRANSIENT_STATUSES, TOOL_ROUNDS_MAX,
)
from agora_runner.log import log, debug_log
from agora_runner.http_util import http_json, agora_internal, fetch_attachment_bytes
from agora_runner.tools_schemas import client_tool_schemas
from agora_runner.tools_dispatch import execute_tool


def _gemini_parts(message):
    """Build one message's `parts` list -- text plus a real inline_data
    part per image attachment (2026-07-24, Issues.md 'sending images...
    does not work'). Non-image attachments and any that fail to fetch
    degrade to a text placeholder rather than being silently dropped --
    critically, this also guarantees `parts` is never empty, which used
    to happen for an image-only message with no caption and produced a
    genuinely empty turn Gemini rejects with 400 'Requests ending with a
    model turn are not supported' (found live, not caught by the 429/503
    fallback since it isn't a rate/availability problem)."""
    parts = []
    if message.get("content"):
        parts.append({"text": message["content"]})
    for att in message.get("attachments") or []:
        mime = att.get("mimeType", "")
        data = fetch_attachment_bytes(att["id"]) if mime.startswith("image/") else None
        if data is not None:
            parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
        else:
            parts.append({"text": f"[attached file: {att.get('filename', '?')} ({mime or 'unknown type'}) -- not loaded]"})
    if not parts:
        parts.append({"text": ""})
    return parts


def gemini_generate(model_id, thinking, system, history, caps, persona, conversation_id, on_text=None,
                     active_step=None, on_thinking=None):
    """See anthropic_generate's on_text docstring -- same contract:
    fires per round that produces text, is_final=False for a preamble
    round that goes on to call a tool, True for the round (or salvage
    call) that actually ends the turn.

    on_thinking(text): fires once per round with that round's thought-summary
    text, whenever thinkingConfig.includeThoughts got Gemini to return any --
    2026-07-31, see the matching thinkingConfig comment below for why this
    previously returned nothing to show at all."""
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": _gemini_parts(m)}
        for m in history
    ]

    def build_tools():
        tools = []
        if caps.get("codeExecution"):
            tools.append({"code_execution": {}})
        # See anthropic_generate's matching comment — active_step can add
        # scoped_write even when every capability flag is False.
        client_tools = client_tool_schemas(caps, active_step) if (any(caps.values()) or active_step) else []
        if client_tools:
            tools.append({"function_declarations": [
                {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
                for t in client_tools
            ]})
        return tools

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_id}:generateContent?key={GEMINI_API_KEY}"
    )

    # 2026-07-23 (Issues #1 revisited): this used to be a 5-variant ladder
    # working around Gemini rejecting google_search combined with function
    # declarations on some models. google_search is gone now (see
    # anthropic_generate's comment -- replaced by the shared web_search_tinyfish
    # client tool on both providers), so that specific conflict no longer
    # exists and there's only ever one tool set to try per round.
    tools = build_tools()
    # 2026-07-27 (Gemini pause investigation): code_execution is ALSO a
    # built-in tool, same family as google_search above -- combining it with
    # function_declarations (any persona with codeExecution + any other
    # capability, e.g. the Agora persona itself) 400s every round with
    # "Please enable tool_config.include_server_side_tool_invocations to use
    # Built-in tools with Function calling." Found live: this 400 isn't a
    # GeminiRateLimited (only 429/500/503 are), so it skips the fallback
    # cascade entirely and burns a FAILURE_PAUSE_CAP strike directly --
    # explains why conversations paused with "(rate limit or outage)" even
    # while the 429 cascade itself was working fine model-to-model.
    has_builtin_tool = any("code_execution" in t for t in tools)
    has_function_declarations = any("function_declarations" in t for t in tools)

    for _round in range(TOOL_ROUNDS_MAX + 1):
        final_round = _round == TOOL_ROUNDS_MAX
        # thinkingBudget 0 is rejected by whatever gemini-*-latest
        # currently resolves to (verified live 2026-07-22) — thinking
        # off means omitting thinkingConfig, not sending 0.
        generation_config = {
            "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS.get(model_id, 65536)
        }
        if thinking:
            # includeThoughts (2026-07-31): thinkingBudget alone makes the
            # model think for real (better answers) but returns none of it
            # -- Gemini only puts thought-summary parts (marked "thought":
            # true) in the response when this is also set. Found live: this
            # is the whole reason "Gemini's thoughts" were never visible in
            # Agora, not a UI gap -- the API was never asked to send them.
            generation_config["thinkingConfig"] = {"thinkingBudget": -1, "includeThoughts": True}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": generation_config,
        }
        if tools:
            body["tools"] = tools
            tool_config = {}
            if has_builtin_tool and has_function_declarations:
                tool_config["includeServerSideToolInvocations"] = True
            if final_round and has_function_declarations:
                # Out of tool rounds — force a text answer (see the
                # matching Anthropic branch).
                tool_config["functionCallingConfig"] = {"mode": "NONE"}
            if tool_config:
                body["toolConfig"] = tool_config
        debug_log(f"gemini request: model={model_id} url_host={url.split('?')[0]}")
        status, resp = http_json("POST", url, body, timeout=300)
        if status in GEMINI_TRANSIENT_STATUSES:
            # Always log Google's actual error body, not just the status
            # code -- this was the single biggest gap in diagnosing the
            # 2026-07-23 fallback investigation. For 429 the body's error
            # message names the specific quota metric AND model it
            # attributes the block to (e.g. "...free_tier_requests, limit:
            # 5, model: gemini-3-flash-preview"), which is the only way to
            # tell a genuine per-model quota hit from a request-routing bug
            # from an account-wide burst throttle. 503 bodies are just
            # "high demand, try later" with no such detail, but logging it
            # anyway costs nothing and keeps one code path.
            log(f"gemini {status} detail for model={model_id}: {json.dumps(resp)[:500]}")
            raise GeminiRateLimited(model_id, status)
        if status != 200:
            log(f"gemini {status}: {json.dumps(resp)[:300]}")
            raise RuntimeError(f"gemini {status}: {json.dumps(resp)[:300]}")

        parts = resp["candidates"][0]["content"].get("parts", [])
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        # Same reasoning as anthropic_generate's round_text: a preamble
        # can sit alongside a functionCall part in the same round, and
        # was previously discarded whenever calls were present. Posted
        # immediately now (2026-07-24) instead of only the final round.
        # thought-marked parts (2026-07-31) are excluded here -- they're
        # not the answer, they go to on_thinking instead.
        round_text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        thought_text = "".join(p.get("text", "") for p in parts if p.get("thought")).strip()
        if thought_text and on_thinking:
            on_thinking(thought_text)
        if calls:
            if round_text and on_text:
                on_text(round_text, False)
            contents.append(resp["candidates"][0]["content"])
            responses = []
            for call in calls:
                output = execute_tool(call["name"], call.get("args") or {}, persona, conversation_id,
                                       active_step)
                responses.append({"functionResponse": {"name": call["name"], "response": {"result": output}}})
            contents.append({"role": "user", "parts": responses})
            continue

        if round_text:
            if on_text:
                on_text(round_text, True)
            return round_text
        raise RuntimeError("no text in Gemini response")
    # Rounds exhausted and the model was STILL calling tools (observed live
    # 2026-07-22: functionCallingConfig NONE did not suppress calls on
    # whatever gemini-flash-latest resolves to). Salvage: flatten the whole
    # tool transcript into plain text, declare no tools at all, demand an
    # answer — schema-valid by construction, so this terminates in text.
    return _gemini_salvage(system, contents, url, thinking, model_id, on_text=on_text, on_thinking=on_thinking)


def _gemini_salvage(system, contents, url, thinking, model_id, on_text=None, on_thinking=None):
    plain = []
    for content in contents:
        pieces = []
        for part in content.get("parts", []):
            if "text" in part and part["text"]:
                pieces.append(part["text"])
            elif "functionCall" in part:
                pieces.append(f"[you called tool {part['functionCall'].get('name')}]")
            elif "functionResponse" in part:
                result = str(part["functionResponse"].get("response", {}).get("result", ""))[:600]
                pieces.append(f"[tool result]: {result}")
            elif "inline_data" in part:
                pieces.append("[attached image]")
        if not pieces:
            continue
        role = content.get("role") if content.get("role") in ("user", "model") else "user"
        joined = "\n".join(pieces)
        if plain and plain[-1]["role"] == role:
            plain[-1]["parts"][0]["text"] += "\n\n" + joined
        else:
            plain.append({"role": role, "parts": [{"text": joined}]})
    plain.append({"role": "user", "parts": [{"text":
        "You are out of tool calls. Answer now in plain text using what you already have."}]})
    generation_config = {
        "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS.get(model_id, 65536)
    }
    if thinking:
        generation_config["thinkingConfig"] = {"thinkingBudget": -1, "includeThoughts": True}
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": plain,
        "generationConfig": generation_config,
    }
    debug_log(f"gemini salvage request: model={model_id} url_host={url.split('?')[0]}")
    status, resp = http_json("POST", url, body, timeout=300)
    if status in GEMINI_TRANSIENT_STATUSES:
        log(f"gemini salvage {status} detail for model={model_id}: {json.dumps(resp)[:500]}")
        raise GeminiRateLimited(model_id, status)
    if status != 200:
        raise RuntimeError(f"gemini salvage {status}: {json.dumps(resp)[:200]}")
    salvage_parts = resp["candidates"][0]["content"].get("parts", [])
    text = "".join(p.get("text", "") for p in salvage_parts if not p.get("thought")).strip()
    thought_text = "".join(p.get("text", "") for p in salvage_parts if p.get("thought")).strip()
    if thought_text and on_thinking:
        on_thinking(thought_text)
    if not text:
        raise RuntimeError("gemini salvage returned no text")
    if on_text:
        on_text(text, True)
    return text


def _gemini_fallback_note(model_id, current, err, sticky, conversation_id):
    """Shared by gemini_generate_with_fallback's on_text wrapping (needs
    the note BEFORE the call, to prefix the first streamed chunk) and
    its return-value composition (needs it AFTER, once success is
    confirmed) -- one wording, computed the same way both times."""
    original_label = GEMINI_LABELS.get(model_id, model_id)
    used_label = GEMINI_LABELS.get(current, current)
    reason = "rate-limited" if err.status == 429 else "temporarily unavailable"
    if sticky and conversation_id:
        return (
            f"_(Note: {original_label} was {reason} — this conversation "
            f"has switched to {used_label}. Change it back from the conversation "
            "menu once it recovers.)_"
        )
    return (
        f"_(Note: {original_label} was {reason} — this reply used "
        f"{used_label} instead. Still trying {original_label} first next time.)_"
    )


def gemini_generate_with_fallback(model_id, thinking, system, history, caps, persona, conversation_id,
                                   sticky=False, on_text=None, active_step=None, on_thinking=None):
    """2026-07-23 redesign (Issues #2): on a 429, hop to that model's single
    designated fallback (GEMINI_MODEL_FALLBACK), never jumping to something
    "better" than what was configured — only ever degrading further. May
    hop more than once in a turn if several adjacent models are all
    rate-limited.

    `sticky` (2026-07-24, made per-conversation): when True, once a
    DIFFERENT model than the one requested actually answers, that switch is
    PERSISTED onto the conversation (not just used for this one reply) --
    otherwise every subsequent poll re-attempts the still-exhausted
    original model first, wasting a request against it every single tick
    (which, at a 5s poll interval, can itself exceed a low-RPM model's cap
    and keep it perpetually rate-limited). When False (the default), the
    fallback model's answer is used for this reply only -- the conversation
    keeps trying its configured model from scratch next turn. Heartbeats
    always pass sticky=False regardless of the conversation's own setting
    (see run_heartbeat) -- a scheduled proactive message shouldn't
    permanently downgrade a persona other conversations may also use.
    Unknown/custom model ids have no known fallback and are tried exactly
    once either way."""
    debug_log(
        f"gemini fallback: starting at model={model_id} sticky={sticky} conversation={conversation_id} "
        f"chain_from_here={[model_id] + [m for m in [GEMINI_MODEL_FALLBACK.get(model_id)] if m]}"
    )
    current = model_id
    tried = []
    last_err = None
    while current is not None and current not in tried:
        tried.append(current)

        # Streaming (2026-07-24): if this attempt is itself a fallback,
        # prefix the note to the FIRST chunk on_text actually posts --
        # computed proactively from last_err (the failure that led to
        # trying `current`), since by the time gemini_generate below
        # returns, its rounds have already streamed their chunks and
        # it'd be too late to prepend anything to what's already posted.
        wrapped_on_text = on_text
        if on_text and current != model_id and last_err is not None:
            note = _gemini_fallback_note(model_id, current, last_err, sticky, conversation_id)
            state = {"noted": False}

            def wrapped_on_text(chunk, is_final, _note=note, _state=state):
                if not _state["noted"]:
                    on_text(f"{_note}\n\n{chunk}", is_final)
                    _state["noted"] = True
                else:
                    on_text(chunk, is_final)

        try:
            text = gemini_generate(current, thinking, system, history, caps, persona, conversation_id,
                                    on_text=wrapped_on_text, active_step=active_step, on_thinking=on_thinking)
        except GeminiRateLimited as e:
            next_hop = GEMINI_MODEL_FALLBACK.get(current)
            log(f"{e}, falling back to {next_hop!r}")
            last_err = e
            current = next_hop
            continue

        if current != model_id:
            log(f"gemini fallback succeeded: {model_id} -> {current} (conversation={conversation_id}, sticky={sticky})")
            if sticky and conversation_id:
                patch_status, patch_resp = agora_internal(
                    "PATCH", f"/conversations/{conversation_id}", {"model": f"gemini:{current}"}
                )
                # Always logged, not debug-gated: this is the one call that
                # actually makes the switch "sticky", so its outcome matters
                # every time it fires, not just when debugging.
                log(
                    f"gemini fallback persist: PATCH /conversations/{conversation_id} "
                    f"model=gemini:{current} -> HTTP {patch_status}"
                    + ("" if patch_status == 200 else f" body={json.dumps(patch_resp)[:300]}")
                )
            else:
                if sticky:
                    debug_log("gemini fallback: no conversation_id, skipping persist (ask/preview invoke)")
            note = _gemini_fallback_note(model_id, current, last_err, sticky, conversation_id)
            text = f"{note}\n\n{text}"
        else:
            debug_log(f"gemini fallback: {model_id} answered directly, no fallback needed")
        return text
    log(f"gemini fallback exhausted for conversation={conversation_id}: tried {tried}")
    raise last_err or RuntimeError(f"gemini: no candidates for {model_id!r}")
