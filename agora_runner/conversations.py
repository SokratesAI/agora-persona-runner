"""notify/speak/auto_pause/poll_conversation -- one conversation's turn each tick."""

from agora_runner.config import AI_TURN_CAP, FAILURE_PAUSE_CAP, FETCH_LIMIT, NO_CAPS, POLL_INTERVAL_SECONDS
from agora_runner.log import log, debug_log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.agora_api import fetch_persona
from agora_runner.turns import build_system, decide_turn, merge_history, PAUSE_SENTINEL
from agora_runner.reply import generate_reply


# conversation id -> consecutive speak() failure count. Persists across
# poll_once() calls (per-conversation, not per-tick) so repeated failures
# actually accumulate. Cleared on any successful reply.
_conversation_failures = {}


def notify(conversation_id, text, sender, system=False, push=True, thinking=False):
    """`push` (2026-07-24, live streaming): False posts the message
    without sending a phone push -- used for every chunk but the last
    in a streamed turn, so watching a turn arrive doesn't ring the
    phone once per sentence. `thinking` (2026-07-31): marks an extended-
    thinking chunk -- rendered distinctly, excluded from every LLM
    context the same as `system`/`activity` (turns.py). Returns (status,
    message_id); message_id is None on any failure response (no message
    was ever appended)."""
    status, body = agora_internal(
        "POST", f"/conversations/{conversation_id}/notify",
        {"text": text, "sender": sender, "system": system, "push": push, "thinking": thinking},
    )
    message_id = (body.get("message") or {}).get("id")
    return status, message_id


def speak(conversation, detail, thread, speaker_name, model_override=None):
    participants = detail.get("personas") or []
    link = next((p for p in participants if p.get("name") == speaker_name), None)
    persona = fetch_persona(link["personaId"]) if link else None
    if persona is None:
        # Old-Agora degradation: inline fields, conservative tools-off.
        persona = {
            "id": None,
            "name": detail.get("name", speaker_name),
            "personality": detail.get("personality", ""),
            "model": detail.get("model", ""),
            "thinking": detail.get("thinking", False),
            "capabilities": dict(NO_CAPS),
            "sharedMemory": "",
        }
    caps = persona.get("capabilities") or dict(NO_CAPS)
    multi = len(participants) > 1
    system = build_system(persona, detail, participants)
    history = merge_history(thread, persona["name"], multi)
    sticky = bool(detail.get("stickyFallback", False))

    # Live streaming (2026-07-24): each text block posts as its own
    # message the moment it's generated (push only on the turn's last
    # chunk), instead of the whole reply landing in one message at the
    # end. posted_ids tracks every chunk's message id so a turn that
    # fails partway through can roll its own preamble back out --
    # without that, a leftover "Now let's..." chunk would become the
    # thread's last message and decide_turn would think a persona had
    # already replied, silently breaking retry (last-sender-must-be-
    # Edvard is the only signal decide_turn has).
    posted_ids = []

    def on_text(chunk, is_final):
        _status, message_id = notify(conversation["id"], chunk, persona["name"], push=is_final)
        if message_id:
            posted_ids.append(message_id)

    # Same posted_ids rollback-on-failure as on_text -- a thinking chunk is
    # still a real posted message, so a turn that fails partway through must
    # roll it back too, or it's left behind as a stray reply-less "thought".
    def on_thinking(chunk):
        _status, message_id = notify(conversation["id"], chunk, persona["name"], push=False, thinking=True)
        if message_id:
            posted_ids.append(message_id)

    try:
        reply = generate_reply(persona, caps, system, history, conversation["id"], model_override, sticky,
                                on_text=on_text, on_thinking=on_thinking)
    except Exception:
        for message_id in posted_ids:
            try:
                agora_internal("DELETE", f"/conversations/{conversation['id']}/messages/{message_id}")
            except Exception:
                pass  # best-effort rollback -- the outer failure is what matters
        raise
    log(f"[{detail.get('name')}] {persona['name']} replied via "
        f"{model_override or persona.get('model')} ({len(posted_ids)} chunk(s)): {reply[:120]!r}")
    return reply


def auto_pause(conversation_id, conversation_name, reason=None):
    agora_internal("PATCH", f"/conversations/{conversation_id}", {"status": "paused"})
    # system=True (2026-07-24): found live that without this, a persona
    # asked an unrelated question shortly after an auto-pause answered
    # about the pause notice instead -- merge_history was feeding this
    # control-plane message into the model's own context as if it were a
    # real previous reply. See Message.system's docstring (agora repo).
    notify(
        conversation_id,
        reason or (
            f"⏸️ Paused automatically after {AI_TURN_CAP} consecutive automated turns. "
            "Resume this conversation from its menu when you want the discussion to continue."
        ),
        "Agora",
        system=True,
    )
    log(f"[{conversation_name}] auto-paused: {reason or 'AI turn cap'}")


def poll_conversation(summary):
    name = summary.get("name", summary.get("id"))
    if summary.get("archived"):
        # A conversation being silently invisible to the poll loop (skipped
        # here, no log, nothing else checked) cost real debugging time on
        # 2026-07-23 -- an archived conversation looked identical to a
        # genuinely hung poll loop from the outside (same zero log output)
        # until this was traced back to the archived flag specifically.
        debug_log(f"[{name}] skipped: archived")
        return
    if summary.get("status", "active") != "active":
        debug_log(f"[{name}] skipped: status={summary.get('status')}")
        return
    status, detail = agora_get(f"/conversations/{summary['id']}/messages?limit={FETCH_LIMIT}")
    if status != 200:
        debug_log(f"[{name}] skipped: conversation fetch returned {status}")
        return
    thread = detail.get("messages", [])
    personas = detail.get("personas") or [
        {"name": detail.get("name", ""), "role": "curator", "personaId": None}
    ]
    speakers = decide_turn(thread, personas)
    if not speakers:
        debug_log(f"[{name}] no turn needed (last sender not Edvard, or no @mention match)")
        return
    if speakers == [PAUSE_SENTINEL]:
        auto_pause(summary["id"], detail.get("name"))
        return
    debug_log(f"[{name}] turn decided: speakers={speakers}")

    visible = [m for m in thread if not m.get("forgotten")]
    override = None
    if visible and visible[-1].get("sender") == "Edvard":
        override = visible[-1].get("modelOverride")

    local_thread = list(thread)
    try:
        for index, speaker in enumerate(speakers):
            reply = speak(summary, detail, local_thread, speaker,
                          model_override=override if index == 0 else None)
            # Later speakers in the same poll see earlier replies
            # (Architecture §3: sequential, each seeing the prior one's answer).
            local_thread.append({"sender": speaker, "text": reply})
    except Exception as exc:
        # No reply got appended, so the turn-taking rule still sees the same
        # "needs a reply" state next tick — without this cap, a persistently
        # failing turn (rate limit, outage) retries every POLL_INTERVAL_SECONDS
        # forever with zero backoff (see FAILURE_PAUSE_CAP above).
        count = _conversation_failures.get(summary["id"], 0) + 1
        _conversation_failures[summary["id"]] = count
        log(f"[{name}] speak failed ({count}/{FAILURE_PAUSE_CAP}): {exc}")
        if count >= FAILURE_PAUSE_CAP:
            # Edvard's own complaint: the old generic "(rate limit or outage)"
            # label gave no way to tell a real bug from a transient 429 without
            # digging through pod logs -- surface the actual exception instead.
            error_detail = f"{type(exc).__name__}: {exc}"[:300]
            auto_pause(
                summary["id"], detail.get("name"),
                reason=(
                    f"⏸️ Paused automatically after {FAILURE_PAUSE_CAP} consecutive "
                    f"failed reply attempts. Last error: {error_detail} Resume from "
                    "this conversation's menu once the underlying issue has cleared."
                ),
            )
            _conversation_failures.pop(summary["id"], None)
        raise
    else:
        _conversation_failures.pop(summary["id"], None)
