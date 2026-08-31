"""poll_once -- one tick of the main loop: every conversation, then due heartbeats."""

from agora_runner.log import log, debug_log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.agora_api import clear_persona_cache
from agora_runner.conversations import poll_conversation, prune_message_window_cache
from agora_runner.deferred import acknowledge_deferred, mark_answered_live
from agora_runner.heartbeats import (
    run_due_heartbeats,
    workflow_bound_conversation_ids,
    cycle_bound_conversation_ids,
    in_flight_cycle_conversation_ids,
)


def poll_once(start_heartbeats=True):
    """One tick. `start_heartbeats=False` answers conversations only.

    That is the draining process's tick. A pod that has been told to shut
    down must start no new cycle -- the run it starts would be killed
    part-way -- but it is going to sit here until the in-flight cycle
    finishes anyway, and until 2026-08-31 it spent that whole wait
    answering nobody. See main.py's `_serve_while_draining`.
    """
    clear_persona_cache()
    status, body = agora_get("/conversations")
    if status != 200:
        # This is the one failure mode that silently skips EVERY
        # conversation for the whole tick with no per-conversation log at
        # all -- worth knowing about even outside DEBUG_LOGGING, since a
        # sustained version of this looks identical to a hung process from
        # the outside (see the poll_conversation archived-flag comment).
        log(f"poll_once: GET /conversations returned {status}, skipping this tick entirely")
        return
    conversations = body.get("conversations", [])
    prune_message_window_cache(c.get("id") for c in conversations)

    # Fetched once per tick and handed to both this loop (to skip
    # heartbeat-driven conversations below) and run_due_heartbeats (so
    # it isn't fetched twice) -- see each skip helper's own docstring
    # for why ordinary turn-taking must never touch these. The two have
    # separate rationales (a workflow's steps already decide who acts;
    # a cycle transcript defers the owner's message to the next scheduled
    # run instead of firing an immediate one), so they stay separate
    # functions rather than one merged predicate.
    hb_status, hb_body = agora_internal("GET", "/heartbeats")
    heartbeats_list = hb_body.get("heartbeats", []) if hb_status == 200 else []
    # Kept apart rather than merged into one skip set, because only one of
    # the two owes the owner an answer later. A cycle transcript defers his
    # message to the next scheduled run and can therefore promise him one
    # (deferred.acknowledge_deferred says so out loud); a workflow-bound
    # conversation makes no such promise, and telling him it did would be
    # a lie in the exact place he already can't see what happened.
    workflow_ids = workflow_bound_conversation_ids(heartbeats_list)
    cycle_ids = cycle_bound_conversation_ids(heartbeats_list, conversations)
    # 2026-08-20, the owner's ask: EVERY cycle transcript answers him in real
    # time, not just the one a heartbeat currently points at. He got the
    # Noted chip after writing in a retired cycle's conversation and said
    # "you should actually answer my responds and do actual work
    # immediately. Like the good old days."
    #
    # This is the second widening of the same rule (2026-08-19 restored
    # replies for the live transcript only) and it leaves exactly one
    # conversation deferring: the one a run is writing into right now.
    # That is not a leftover of the old policy, it is the one case with a
    # real hazard -- two concurrent `--resume` calls against one CLI
    # session -- and `in_flight_cycle_conversation_ids` says why the
    # retired ones cannot have it.
    deferred_ids = in_flight_cycle_conversation_ids(heartbeats_list) & cycle_ids
    live_ids = cycle_ids - deferred_ids
    # Workflow ids stay in the skip set even when they are also live: a
    # workflow's own steps decide who acts, and that is a different
    # rationale this ask did not touch. Union, not difference -- being
    # workflow-bound wins over being live, and the chip below is behind
    # the same `continue` so a skipped conversation never gets one.
    skip_ids = workflow_ids | deferred_ids

    debug_log(f"poll_once: {len(conversations)} conversations fetched, "
              f"{len(skip_ids)} heartbeat-driven (skipped), "
              f"{len(live_ids - workflow_ids)} live cycle conversation(s)")
    for summary in conversations:
        if summary.get("id") in skip_ids:
            debug_log(f"[{summary.get('name', summary.get('id'))}] skipped: heartbeat-driven conversation")
            if summary.get("id") in deferred_ids and not summary.get("archived"):
                try:
                    acknowledge_deferred(summary)
                except Exception as e:
                    log(f"[{summary.get('name', summary.get('id'))}] deferred ack failed: {e}")
            continue
        try:
            spoke = poll_conversation(summary)
        except Exception as e:
            log(f"[{summary.get('name', summary.get('id'))}] poll failed: {e}")
            continue
        # Only for the live cycle conversation, and only when a reply
        # actually went out. The chip is what stops the next scheduled
        # run carrying this message in its trigger and answering it a
        # second time -- see heartbeats._unread_from_edvard.
        if spoke and summary.get("id") in live_ids:
            try:
                mark_answered_live(summary)
            except Exception as e:
                log(f"[{summary.get('name', summary.get('id'))}] answered-live chip failed: {e}")
    if not start_heartbeats:
        return
    try:
        run_due_heartbeats(heartbeats_list)
    except Exception as e:
        log(f"heartbeat pass failed: {e}")
