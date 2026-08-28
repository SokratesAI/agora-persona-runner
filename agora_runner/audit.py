"""Posts every capability-tool invocation to Agora's /audit for the Activity feed."""

from agora_runner.log import log
from agora_runner.http_util import agora_internal
from agora_runner.redact import redact

# `detail` is a one-line chip label -- "Read vault file · journal.md" -- and
# 500 is generous for one. It is the wrong ceiling for exactly one capability:
# NARRATION_TEXT, which is not a tool call at all but a passage the persona
# wrote between two of them (agora-claude-bridge#12). That is prose, it is
# rendered as prose, and it is routinely longer than this paragraph. Clipping
# it here would end every second passage mid-sentence -- the same "block of
# text" problem it exists to fix, moved one hop upstream.
DETAIL_CHARS_MAX = 500
NARRATION_TEXT = "assistant_text"


def audit(persona_name, conversation_id, capability, detail, before=None, after=None,
          ephemeral=False, tool_use_id="", output=None, is_error=False):
    try:
        # Every field below is text a human reads, assembled from whatever a
        # tool was handed or returned, and this is the one point all of it
        # leaves for Agora -- so the credential filter goes here rather than
        # at ~30 call sites, one of which would eventually be added without
        # it (redact.py). Before the truncation on the next line, not after:
        # clipping first can cut a token in half, and half a token is still
        # half a token in the feed.
        detail = redact(detail)
        payload = {
            "personaName": persona_name,
            "conversationId": conversation_id,
            "capability": capability,
            "detail": detail if capability == NARRATION_TEXT else detail[:DETAIL_CHARS_MAX],
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
            payload["before"] = redact(before)
        if after is not None:
            payload["after"] = redact(after)
        # What a tool returned, and the id that lets Agora's client pair it
        # with the chip for the call itself. Deliberately NOT folded into
        # `detail` above: detail is a one-line chip label truncated at 500,
        # and output is a transcript -- Agora truncates it at
        # AuditStore.CONTENT_CHARS_MAX (20_000), same as before/after.
        if tool_use_id:
            payload["toolUseId"] = tool_use_id
        if output is not None:
            payload["output"] = redact(output)
            if is_error:
                payload["isError"] = True
        agora_internal("POST", "/audit", payload)
    except Exception as e:  # audit must never break a turn
        log(f"audit post failed: {e}")


def narration_passage(message):
    """The passage a persona wrote on its way to the answer, or None.

    Every capability call the bridge narrates is appended to the conversation
    as an `activity` message (agora/src/server.ts:1552), and one of those
    "capabilities" is not a tool call at all: NARRATION_TEXT is a paragraph
    the persona wrote between two tools, pushed live while the turn is still
    running (agora-claude-bridge bridge/activity.py report_text). The reply
    the owner finally sees is only the *last* such passage -- cli.py picks
    `pending[-1]` whenever narration is enabled -- so the earlier ones are
    not a duplicate of the answer, they are the earlier parts of it.

    Both Nova chat surfaces used to drop them along with the tool chips,
    which is why a turn looked like four minutes of nothing followed by one
    block of text. Returns the passage itself rather than the message's
    `text`, because Agora prefixes that with the capability name
    ("assistant_text: ...") for its own search.
    """
    activity = message.get("activity")
    if not isinstance(activity, dict):
        return None
    if activity.get("capability") != NARRATION_TEXT:
        return None
    return (activity.get("detail") or "").strip() or None
