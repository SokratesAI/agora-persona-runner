"""poll_once -- one tick of the main loop: every conversation, then due heartbeats."""

from agora_runner.log import log, debug_log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.agora_api import clear_persona_cache
from agora_runner.conversations import poll_conversation
from agora_runner.heartbeats import run_due_heartbeats, workflow_bound_conversation_ids


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
    # workflow-bound conversations below) and run_due_heartbeats (so it
    # isn't fetched twice) -- see workflow_bound_conversation_ids' own
    # docstring for why ordinary turn-taking must never touch these.
    hb_status, hb_body = agora_internal("GET", "/heartbeats")
    heartbeats_list = hb_body.get("heartbeats", []) if hb_status == 200 else []
    skip_ids = workflow_bound_conversation_ids(heartbeats_list)

    debug_log(f"poll_once: {len(conversations)} conversations fetched, "
              f"{len(skip_ids)} workflow-bound (skipped)")
    for summary in conversations:
        if summary.get("id") in skip_ids:
            debug_log(f"[{summary.get('name', summary.get('id'))}] skipped: workflow-bound conversation")
            continue
        try:
            poll_conversation(summary)
        except Exception as e:
            log(f"[{summary.get('name', summary.get('id'))}] poll failed: {e}")
    try:
        run_due_heartbeats(heartbeats_list)
    except Exception as e:
        log(f"heartbeat pass failed: {e}")
