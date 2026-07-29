"""poll_once -- one tick of the main loop: every conversation, then due heartbeats."""

from agora_runner.log import log, debug_log
from agora_runner.http_util import agora_get
from agora_runner.agora_api import clear_persona_cache
from agora_runner.conversations import poll_conversation
from agora_runner.heartbeats import run_due_heartbeats


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
    debug_log(f"poll_once: {len(conversations)} conversations fetched")
    for summary in conversations:
        try:
            poll_conversation(summary)
        except Exception as e:
            log(f"[{summary.get('name', summary.get('id'))}] poll failed: {e}")
    try:
        run_due_heartbeats()
    except Exception as e:
        log(f"heartbeat pass failed: {e}")
