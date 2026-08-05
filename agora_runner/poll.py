"""poll_once -- one tick of the main loop: every conversation, then due heartbeats."""

from agora_runner.log import log, debug_log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.agora_api import clear_persona_cache
from agora_runner.conversations import poll_conversation
from agora_runner.deferred import acknowledge_deferred
from agora_runner.heartbeats import (
    run_due_heartbeats,
    workflow_bound_conversation_ids,
    cycle_bound_conversation_ids,
)


def poll_once():
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

    # Fetched once per tick and handed to both this loop (to skip
    # heartbeat-driven conversations below) and run_due_heartbeats (so
    # it isn't fetched twice) -- see each skip helper's own docstring
    # for why ordinary turn-taking must never touch these. The two have
    # separate rationales (a workflow's steps already decide who acts;
    # a cycle transcript defers Edvard's message to the next scheduled
    # run instead of firing an immediate one), so they stay separate
    # functions rather than one merged predicate.
    hb_status, hb_body = agora_internal("GET", "/heartbeats")
    heartbeats_list = hb_body.get("heartbeats", []) if hb_status == 200 else []
    # Kept apart rather than merged into one skip set, because only one of
    # the two owes Edvard an answer later. A cycle transcript defers his
    # message to the next scheduled run and can therefore promise him one
    # (deferred.acknowledge_deferred says so out loud); a workflow-bound
    # conversation makes no such promise, and telling him it did would be
    # a lie in the exact place he already can't see what happened.
    workflow_ids = workflow_bound_conversation_ids(heartbeats_list)
    cycle_ids = cycle_bound_conversation_ids(heartbeats_list, conversations)
    skip_ids = workflow_ids | cycle_ids

    debug_log(f"poll_once: {len(conversations)} conversations fetched, "
              f"{len(skip_ids)} heartbeat-driven (skipped)")
    for summary in conversations:
        if summary.get("id") in skip_ids:
            debug_log(f"[{summary.get('name', summary.get('id'))}] skipped: heartbeat-driven conversation")
            if summary.get("id") in cycle_ids and not summary.get("archived"):
                try:
                    acknowledge_deferred(summary)
                except Exception as e:
                    log(f"[{summary.get('name', summary.get('id'))}] deferred ack failed: {e}")
            continue
        try:
            poll_conversation(summary)
        except Exception as e:
            log(f"[{summary.get('name', summary.get('id'))}] poll failed: {e}")
    try:
        run_due_heartbeats(heartbeats_list)
    except Exception as e:
        log(f"heartbeat pass failed: {e}")
