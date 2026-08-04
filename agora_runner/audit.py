"""Posts every capability-tool invocation to Agora's /audit for the Activity feed."""

from agora_runner.log import log
from agora_runner.http_util import agora_internal


def audit(persona_name, conversation_id, capability, detail, before=None, after=None,
          ephemeral=False, tool_use_id="", output=None, is_error=False):
    try:
        payload = {
            "personaName": persona_name,
            "conversationId": conversation_id,
            "capability": capability,
            "detail": detail[:500],
        }
        # Live tool-use narration (tool_activity.py). Agora keeps these on a
        # budget of their own, because one cycle emits hundreds of them --
        # unbounded, since the cap came off -- and would otherwise evict every
        # vault_write and heartbeat entry in the store.
        if ephemeral:
            payload["ephemeral"] = True
        # before/after carry the whole file so Agora's Activity feed can
        # render a real diff for vault_write -- server-side truncated at
        # AuditStore.CONTENT_CHARS_MAX, not here.
        if before is not None:
            payload["before"] = before
        if after is not None:
            payload["after"] = after
        # What a tool returned, and the id that lets Agora's client pair it
        # with the chip for the call itself. Deliberately NOT folded into
        # `detail` above: detail is a one-line chip label truncated at 500,
        # and output is a transcript -- Agora truncates it at
        # AuditStore.CONTENT_CHARS_MAX (20_000), same as before/after.
        if tool_use_id:
            payload["toolUseId"] = tool_use_id
        if output is not None:
            payload["output"] = output
            if is_error:
                payload["isError"] = True
        agora_internal("POST", "/audit", payload)
    except Exception as e:  # audit must never break a turn
        log(f"audit post failed: {e}")
