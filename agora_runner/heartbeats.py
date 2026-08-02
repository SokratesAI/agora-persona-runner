"""Heartbeat scheduling: due-check, vault-context injection, and the workflow-mode thread dispatch."""

import threading
from datetime import datetime, timezone

from agora_runner.config import FETCH_LIMIT, HEARTBEAT_NO_REPORT_SENTINEL, NO_CAPS
from agora_runner.log import log, debug_log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.audit import audit
from agora_runner.agora_api import fetch_persona
from agora_runner.vault import fetch_vault_context
from agora_runner.turns import build_system, merge_history, schedule_due
from agora_runner.reply import generate_reply
from agora_runner.conversations import notify
from agora_runner.workflows import run_workflow_heartbeat
from agora_runner.conversation_rotation import rotate_cycle_conversation


def run_heartbeat(heartbeat):
    persona = fetch_persona(heartbeat["personaId"])
    if persona is None:
        agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                       {"forceRun": False, "lastRunAt": datetime.now(timezone.utc).isoformat(),
                        "lastResult": "failed: persona not found"})
        return
    status, detail = agora_get(
        f"/conversations/{heartbeat['conversationId']}/messages?limit={FETCH_LIMIT}"
    )
    if status != 200:
        agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                       {"forceRun": False, "lastRunAt": datetime.now(timezone.utc).isoformat(),
                        "lastResult": f"failed: conversation fetch {status}"})
        return

    # Per-cycle conversation rotation (2026-08-02, same mechanism
    # workflows.py's run_workflow_heartbeat already uses) -- no-op unless
    # heartbeat["rotateConversationEachRun"] is set. `detail` is stale
    # (the OLD conversation's) when it does rotate, so re-fetch it.
    conversation_id = rotate_cycle_conversation(heartbeat, detail.get("personas") or [])
    if conversation_id != heartbeat["conversationId"]:
        _status, detail = agora_get(f"/conversations/{conversation_id}/messages?limit={FETCH_LIMIT}")

    extra_parts = [
        "## Heartbeat turn",
        f"This message is an automatic scheduled turn ({heartbeat['schedule']}), "
        "not a direct reply to Edvard. Write to Edvard proactively.",
    ]
    if heartbeat.get("task"):
        extra_parts.append(f"Task for this turn: {heartbeat['task']}")
    if heartbeat.get("vaultPaths"):
        context = fetch_vault_context(heartbeat["vaultPaths"])
        if context:
            extra_parts.append(
                "## Reference material from Edvard's vault\n"
                "Already fetched for you — answer from it directly rather than "
                "browsing the vault with tools, unless something essential is "
                f"missing.\n\n{context}"
            )
    heartbeat_extra = "\n\n".join(extra_parts)

    caps = persona.get("capabilities") or dict(NO_CAPS)
    participants = detail.get("personas") or []
    system = build_system(persona, detail, participants, heartbeat_extra)
    history = merge_history(detail.get("messages", []), persona["name"],
                            len(participants) > 1)
    # A heartbeat may fire into an empty/assistant-ended thread — providers
    # need a user turn, so the trigger itself becomes a synthetic one.
    #
    # 2026-08-02: claude-cli personas only ever see this LAST history entry
    # (bridge/cli.py's generate_reply forwards history[-1], not the full
    # thread) -- so if Edvard's real last message was just sitting in
    # `history` unaddressed, a claude-cli persona would never actually see
    # it, only this synthetic trigger. Folding his real content into the
    # trigger when it's genuinely his turn (last message role is "user")
    # fixes that without changing anything for Anthropic/Gemini, which
    # already see the full thread regardless.
    trigger = "[Automatic heartbeat trigger — address Edvard directly.]"
    if history and history[-1]["role"] == "user":
        trigger += f" Edvard's most recent message in this conversation: {history[-1]['content']}"
    history.append({"role": "user", "content": trigger})

    result = ""
    try:
        # 2026-07-24: heartbeats always run non-sticky regardless of the
        # bound conversation's own stickyFallback setting -- a scheduled
        # proactive message shouldn't permanently downgrade a persona that
        # other conversations may also use via the same Gemini model.
        # 2026-07-25: deliberately NOT streamed (no on_text) -- unlike a
        # live chat turn, a monitoring heartbeat's prompt may ask for a
        # silent HEARTBEAT_NO_REPORT_SENTINEL reply when there's nothing
        # worth Edvard's attention, and that decision can only be made
        # once the full reply is in hand, before anything is posted.
        reply = generate_reply(persona, caps, system, history, conversation_id, sticky=False)
        if reply.strip().upper().startswith(HEARTBEAT_NO_REPORT_SENTINEL):
            result = "checked, nothing to report (not posted to chat)"
        else:
            notify(conversation_id, reply, persona["name"])
            result = f"replied {len(reply)} chars"
            audit(persona["name"], conversation_id, "heartbeat",
                  f"{heartbeat['name']} ({heartbeat['schedule']})")
    except Exception as e:
        result = f"failed: {e}"[:200]
        log(f"heartbeat {heartbeat['name']} failed: {e}")
    agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                   {"forceRun": False,
                    "lastRunAt": datetime.now(timezone.utc).isoformat(),
                    "lastResult": result})
    log(f"heartbeat {heartbeat['name']}: {result}")


# Decisions/0009 — heartbeat id -> Thread, module-level so it survives
# across ticks. The poll loop (poll_once/main) is otherwise fully
# sequential and blocking (one urllib call after another, no asyncio,
# no thread pool); a multi-step, multi-round workflow can run for
# minutes and must not stall every other conversation's turn-taking
# and every other heartbeat's schedule for that whole time.
_workflow_threads = {}


def workflow_bound_conversation_ids(heartbeats_list):
    """Conversation ids driven by an enabled, workflow-mode heartbeat.
    poll_once (2026-07-30) skips ordinary curator/@mention turn-taking
    for these entirely: a workflow step's own personaIds already decides
    who acts and when, so decide_turn's @mention-chain logic has nothing
    legitimate to do there -- and worse, can crash outright. Found live:
    a workflow persona's reply naturally included "@OtherPersona", the
    ordinary poll loop picked that up as a real mention and tried to
    continue the exchange via speak(), but a workflow-only conversation
    may never have a real Edvard message to anchor on (unlike one Edvard
    started himself) -- merge_history pops every leading non-user turn,
    so the history came back empty, speak() raised, and three such
    crashes auto-paused the conversation via FAILURE_PAUSE_CAP. The
    workflow engine's own turns are unaffected either way (run_workflow_steps
    already appends its own synthetic user turn every round)."""
    return {
        hb["conversationId"] for hb in heartbeats_list
        if hb.get("enabled") and hb.get("workflowId") and hb.get("conversationId")
    }


def run_due_heartbeats(heartbeats_list=None):
    if heartbeats_list is None:
        status, body = agora_internal("GET", "/heartbeats")
        if status != 200:
            return  # old Agora — feature not there yet
        heartbeats_list = body.get("heartbeats", [])
    now = datetime.now(timezone.utc)
    for heartbeat in heartbeats_list:
        try:
            # forceRun (POST /heartbeats/:id/run) must bypass enabled --
            # otherwise "run now" silently no-ops on a disabled heartbeat.
            due = heartbeat.get("forceRun") or (
                heartbeat.get("enabled") and schedule_due(
                    heartbeat.get("schedule", ""), heartbeat.get("lastRunAt"),
                    heartbeat.get("createdAt", now.isoformat()), now,
                )
            )
            if not due:
                continue
            if heartbeat.get("workflowId"):
                # Runs off the main thread — see _workflow_threads'
                # comment above. In-flight guard: skip re-spawning if a
                # prior run of the SAME heartbeat hasn't finished yet (a
                # workflow can legitimately outlive its own schedule
                # interval, e.g. a 5-minute "every@1m" workflow).
                hb_id = heartbeat["id"]
                existing = _workflow_threads.get(hb_id)
                if existing is not None and existing.is_alive():
                    debug_log(f"workflow heartbeat {hb_id} still running, skipping this tick")
                    continue
                thread = threading.Thread(target=run_workflow_heartbeat, args=(heartbeat,), daemon=True)
                _workflow_threads[hb_id] = thread
                thread.start()
            else:
                run_heartbeat(heartbeat)  # unchanged, existing synchronous path
        except Exception as e:
            log(f"heartbeat {heartbeat.get('name')} scheduling error: {e}")
