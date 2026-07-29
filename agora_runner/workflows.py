"""Decisions/0009 -- multi-step, multi-persona workflow execution (heartbeat-triggered only)."""

from datetime import datetime, timezone

from agora_runner.config import AI_TURN_CAP, FETCH_LIMIT, HEARTBEAT_NO_REPORT_SENTINEL
from agora_runner.log import log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.audit import audit
from agora_runner.agora_api import fetch_persona_uncached, fetch_workflow
from agora_runner.turns import build_system, merge_history
from agora_runner.reply import generate_reply
from agora_runner.conversations import notify
from agora_runner.tools_schemas import capabilities_for_step


WORKFLOW_MAX_DEPTH = 5  # defense in depth — Agora already rejects a
                         # cyclic workflowRef at save time (Decisions/0009);
                         # this is the runtime backstop, same "deterministic
                         # rule + hard cap" shape as AI_TURN_CAP.


def run_workflow_steps(steps, conversation_id, detail, participants, local_thread,
                        last_speaker_idx=-1, depth=0):
    """Decisions/0009 execution engine. One continuous round-robin turn
    pointer across step (and sub-workflow) boundaries — never resets, so
    a step never immediately repeats whoever just spoke. Non-streamed,
    one notify() per completed round (matches run_heartbeat's 2026-07-25
    decision — a round's HEARTBEAT_NO_REPORT_SENTINEL suppress-or-post
    choice needs the full reply first, before anything is posted).
    Returns (last_speaker_idx, rounds_run, replies_posted) so the caller
    can build a meaningful lastResult summary."""
    if depth > WORKFLOW_MAX_DEPTH:
        raise RuntimeError(f"workflow recursion depth exceeded ({WORKFLOW_MAX_DEPTH})")
    rounds_run = 0
    replies_posted = 0
    for step_index, step in enumerate(steps):
        if step.get("workflowRef"):
            sub = fetch_workflow(step["workflowRef"])
            if sub is None:
                log(f"workflow step references unknown workflow {step['workflowRef']!r}, skipping")
                continue
            last_speaker_idx, sub_rounds, sub_replies = run_workflow_steps(
                sub.get("steps", []), conversation_id, detail, participants, local_thread,
                last_speaker_idx, depth + 1,
            )
            rounds_run += sub_rounds
            replies_posted += sub_replies
            continue

        # Mutable per-step-run copy — scoped_write locks a resolved
        # folder-mode path into this dict, shared across every round of
        # THIS step only (a fresh dict next step, even if the same
        # filepath string is reused, so two steps never accidentally
        # share a lock).
        active_step = dict(step)
        extra_parts = [
            "## Workflow round",
            f"This is an automated workflow turn (step {step_index + 1}/{len(steps)}). "
            "Write to the conversation directly, continuing the discussion so far.",
        ]
        if step.get("prompt"):
            extra_parts.append(f"Instructions for this step: {step['prompt']}")
        extra = "\n\n".join(extra_parts)

        for _round in range(max(1, step.get("loopCount", 1))):
            last_speaker_idx = (last_speaker_idx + 1) % len(participants)
            link = participants[last_speaker_idx]
            persona = fetch_persona_uncached(link.get("personaId"))
            if persona is None:
                log(f"workflow: persona {link.get('personaId')!r} not found, skipping this round")
                continue
            caps = capabilities_for_step(persona, step)
            system = build_system(persona, detail, participants, extra)
            # Explicit synthetic trigger, same pattern as run_heartbeat —
            # merge_history maps EVERY persona sender to assistant role
            # (Architecture §3's multi-persona convention treats the
            # whole roster as one collective assistant voice), so without
            # this a round after the first would hand the provider a
            # history with no trailing user turn.
            history = merge_history(local_thread, persona["name"], len(participants) > 1)
            history.append({
                "role": "user",
                "content": "[Automatic workflow turn — continue the discussion directly.]",
            })
            rounds_run += 1
            try:
                reply = generate_reply(persona, caps, system, history, conversation_id,
                                        sticky=False, active_step=active_step)
            except Exception as e:
                log(f"workflow round failed (step {step_index + 1}, round {_round + 1}): {e}")
                continue
            local_thread.append({"sender": persona["name"], "text": reply})
            if not reply.strip().upper().startswith(HEARTBEAT_NO_REPORT_SENTINEL):
                notify(conversation_id, reply, persona["name"])
                replies_posted += 1
    return last_speaker_idx, rounds_run, replies_posted


def run_workflow_heartbeat(heartbeat):
    """Decisions/0009 — runs on its own thread (run_due_heartbeats), not
    the main poll thread. Mirrors run_heartbeat's fetch/execute/PATCH
    shape exactly, just delegating the "execute" step to the multi-step
    engine above instead of one generate_reply call."""
    workflow = fetch_workflow(heartbeat["workflowId"])
    if workflow is None:
        agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                       {"forceRun": False, "lastRunAt": datetime.now(timezone.utc).isoformat(),
                        "lastResult": "failed: workflow not found"})
        return
    status, detail = agora_get(
        f"/conversations/{heartbeat['conversationId']}/messages?limit={FETCH_LIMIT}"
    )
    if status != 200:
        agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                       {"forceRun": False, "lastRunAt": datetime.now(timezone.utc).isoformat(),
                        "lastResult": f"failed: conversation fetch {status}"})
        return
    participants = detail.get("personas") or []
    if not participants:
        agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                       {"forceRun": False, "lastRunAt": datetime.now(timezone.utc).isoformat(),
                        "lastResult": "failed: conversation has no personas"})
        return

    local_thread = list(detail.get("messages", []))
    result = ""
    try:
        _idx, rounds_run, replies_posted = run_workflow_steps(
            workflow.get("steps", []), heartbeat["conversationId"], detail, participants, local_thread,
        )
        result = (
            f"workflow: {len(workflow.get('steps', []))} steps, {rounds_run} rounds, "
            f"{replies_posted} replies posted"
        )
        audit("workflow", heartbeat["conversationId"], "heartbeat",
              f"{heartbeat['name']} ({heartbeat['schedule']}) -> {workflow.get('name')}")
    except Exception as e:
        result = f"failed: {e}"[:200]
        log(f"workflow heartbeat {heartbeat['name']} failed: {e}")
    agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                   {"forceRun": False,
                    "lastRunAt": datetime.now(timezone.utc).isoformat(),
                    "lastResult": result})
    log(f"workflow heartbeat {heartbeat['name']}: {result}")
