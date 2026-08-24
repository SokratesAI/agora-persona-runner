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
from agora_runner.conversation_rotation import rotate_cycle_conversation


WORKFLOW_MAX_DEPTH = 5  # defense in depth — Agora already rejects a
                         # cyclic workflowRef at save time (Decisions/0009);
                         # this is the runtime backstop, same "deterministic
                         # rule + hard cap" shape as AI_TURN_CAP.


def run_workflow_steps(steps, conversation_id, detail, participants,
                        last_speaker_idx=-1, depth=0, push=True):
    """Decisions/0009 execution engine. One continuous round-robin turn
    pointer across step (and sub-workflow) boundaries — never resets, so
    a step never immediately repeats whoever just spoke — scoped, per
    step, to that step's own `personaIds` subset when set (2026-07-30:
    round-robin across the WHOLE conversation's personas[] is right for
    genuine multi-agent discussion, wrong for a pipeline step with a
    known single owner at authoring time — see Step.personaIds's own
    docstring in workflow-store.ts). Empty/unset `personaIds` falls back
    to the full `participants` list, unchanged from before.

    Re-fetches the conversation's messages fresh at the top of every
    round (2026-07-30) rather than working from a static snapshot taken
    once at the start of the run — a heartbeat is bound to a real
    conversation specifically so the owner can steer a run that's going off
    the rails by just typing into it; a snapshot silently defeated that
    for any round after the first. Best-effort only: a run never waits
    for input, it just picks up whatever's there by the time the next
    round starts.

    Non-streamed, one notify() per completed round (matches
    run_heartbeat's 2026-07-25 decision — a round's
    HEARTBEAT_NO_REPORT_SENTINEL suppress-or-post choice needs the full
    reply first, before anything is posted). Returns (last_speaker_idx,
    rounds_run, replies_posted) so the caller can build a meaningful
    lastResult summary."""
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
                sub.get("steps", []), conversation_id, detail, participants,
                last_speaker_idx, depth + 1, push,
            )
            rounds_run += sub_rounds
            replies_posted += sub_replies
            continue

        persona_ids_filter = step.get("personaIds") or []
        if persona_ids_filter:
            step_participants = [p for p in participants if p.get("personaId") in persona_ids_filter]
            if not step_participants:
                log(f"workflow: step {step_index + 1}'s personaIds match none of this "
                    f"conversation's participants, skipping step")
                continue
        else:
            step_participants = participants

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
            last_speaker_idx = (last_speaker_idx + 1) % len(step_participants)
            link = step_participants[last_speaker_idx]
            persona = fetch_persona_uncached(link.get("personaId"))
            if persona is None:
                log(f"workflow: persona {link.get('personaId')!r} not found, skipping this round")
                continue
            caps = capabilities_for_step(persona, step)
            system = build_system(persona, detail, participants, extra)
            status, msgs_body = agora_get(f"/conversations/{conversation_id}/messages?limit={FETCH_LIMIT}")
            thread = msgs_body.get("messages", []) if status == 200 else []
            # Explicit synthetic trigger, same pattern as run_heartbeat —
            # merge_history maps EVERY persona sender to assistant role
            # (Architecture §3's multi-persona convention treats the
            # whole roster as one collective assistant voice), so without
            # this a round after the first would hand the provider a
            # history with no trailing user turn. Based on the WHOLE
            # conversation's participant count, not this step's own
            # subset — a message from a persona outside this step is
            # still "someone else on this thread," not a fresh human turn.
            history = merge_history(thread, persona["name"], len(participants) > 1)
            history.append({
                "role": "user",
                "content": "[Automatic workflow turn — continue the discussion directly.]",
            })
            rounds_run += 1
            try:
                reply = generate_reply(persona, caps, system, history, conversation_id,
                                        sticky=False, active_step=active_step, unattended=True)
            except Exception as e:
                log(f"workflow round failed (step {step_index + 1}, round {_round + 1}): {e}")
                continue
            if not reply.strip().upper().startswith(HEARTBEAT_NO_REPORT_SENTINEL):
                notify(conversation_id, reply, persona["name"], push=push)
                replies_posted += 1
    return last_speaker_idx, rounds_run, replies_posted


def run_workflow_heartbeat(heartbeat):
    """Decisions/0009 — runs on its own thread (run_due_heartbeats), not
    the main poll thread. Mirrors run_heartbeat's fetch/execute/PATCH
    shape exactly, just delegating the "execute" step to the multi-step
    engine above instead of one generate_reply call."""
    # Claim the run BEFORE running it (2026-08-02). A workflow run can
    # take many minutes (an Evolve cycle is ~11); until this PATCH
    # existed, the heartbeat's PERSISTED state said "never ran, still
    # forced" for that whole window, and the only thing preventing a
    # second, duplicate run was `_heartbeat_threads` in heartbeats.py —
    # an in-process dict that doesn't survive a pod restart and doesn't
    # exist for any other replica or caller. Measured: 7 of the 19 PRs
    # opened on this repo since #6 were same-work duplicates.
    # Writing forceRun/lastRunAt up front makes the claim durable, and
    # gives a human a visible "running" state mid-cycle instead of a
    # stale one. Note this deliberately anchors the next scheduled run
    # to run START rather than run END (schedule_due reads lastRunAt) —
    # for a workflow that can outlive its own interval, that's the
    # point. The end-of-run PATCH below still overwrites both fields.
    claim_status, _ = agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                                     {"forceRun": False,
                                      "lastRunAt": datetime.now(timezone.utc).isoformat(),
                                      "lastResult": "running"})
    if claim_status not in (200, 201):
        # Deliberately continue rather than return: a transient Agora
        # blip shouldn't block the cycle entirely. But an unlogged
        # failure here silently reopens the exact duplicate window this
        # claim exists to close, so it must not pass quietly — if
        # duplicate runs ever show up again, this line is the evidence.
        log(f"workflow heartbeat {heartbeat['name']}: claim PATCH failed (HTTP {claim_status}), "
            "run is unclaimed and may be duplicated by a restart or another replica")
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

    # Per-cycle conversation rotation (2026-08-02) -- see
    # conversation_rotation.py's own module docstring. No-op (returns
    # heartbeat["conversationId"] unchanged) unless rotateConversationEachRun
    # is set. When it does rotate, `detail` is stale (it's the OLD
    # conversation's), so re-fetch it for the new one -- cheap (empty
    # message list), and build_system's own conversation-memory lookup
    # should reflect the conversation actually being run against.
    conversation_id = rotate_cycle_conversation(heartbeat, participants)
    if conversation_id != heartbeat["conversationId"]:
        _status, detail = agora_get(f"/conversations/{conversation_id}/messages?limit={FETCH_LIMIT}")

    result = ""
    try:
        # A workflow heartbeat honours pushNotifications the same way a
        # single-turn one does (2026-08-14). Without this the Studio would
        # draw the heartbeat as muted while every round still buzzed the
        # phone -- a mute that lies is worse than no mute, because there is
        # nothing on screen to tell you it is not working. Every round of
        # every step, and every sub-workflow, carries the same flag.
        push = heartbeat.get("pushNotifications") is not False
        _idx, rounds_run, replies_posted = run_workflow_steps(
            workflow.get("steps", []), conversation_id, detail, participants,
            push=push,
        )
        result = (
            f"workflow: {len(workflow.get('steps', []))} steps, {rounds_run} rounds, "
            f"{replies_posted} replies posted"
        )
        audit("workflow", conversation_id, "heartbeat",
              f"{heartbeat['name']} ({heartbeat['schedule']}) -> {workflow.get('name')}")
    except Exception as e:
        result = f"failed: {e}"[:200]
        log(f"workflow heartbeat {heartbeat['name']} failed: {e}")
    agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                   {"forceRun": False,
                    "lastRunAt": datetime.now(timezone.utc).isoformat(),
                    "lastResult": result})
    log(f"workflow heartbeat {heartbeat['name']}: {result}")
