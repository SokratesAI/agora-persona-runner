"""Posts every capability-tool invocation to Agora's /audit for the Activity feed."""

from agora_runner.log import log
from agora_runner.http_util import agora_internal


def audit(persona_name, conversation_id, capability, detail, before=None, after=None,
          ephemeral=False):
    try:
        payload = {
            "personaName": persona_name,
            "conversationId": conversation_id,
            "capability": capability,
            "detail": detail[:500],
        }
        # Live tool-use narration (tool_activity.py). Agora keeps these on a
        # budget of their own, because one cycle emits up to
        # TOOL_ACTIVITY_MAX_PER_CALL of them and would otherwise evict every
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
        agora_internal("POST", "/audit", payload)
    except Exception as e:  # audit must never break a turn
        log(f"audit post failed: {e}")
