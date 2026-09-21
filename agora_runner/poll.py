"""poll_once -- one tick of the conversation loop: every active conversation."""

import threading

from agora_runner.log import log, debug_log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.agora_api import clear_persona_cache
from agora_runner.conversations import prepare_turn, prune_message_window_cache
from agora_runner.deferred import acknowledge_deferred, mark_answered_live
from agora_runner.heartbeats import (
    workflow_bound_conversation_ids,
    cycle_bound_conversation_ids,
    in_flight_cycle_conversation_ids,
)


# Replies run on their own thread, one per conversation, so a minutes-long
# claude-cli reply in one conversation never makes another wait. The owner's
# capture of 2026-09-21: Aristoteles sat two minutes behind a Nova reply in
# a different chat, starting 47 ms after it finished.
#
# Conversation id -> the thread writing its reply. A conversation in here is
# skipped by the tick outright -- not fetched, not decided -- because until
# the reply posts, the owner's message is still the last one in the thread
# and deciding again would start a second reply beside the first (two
# `--resume` calls against one CLI session). Only this module's main-thread
# code adds to it; each turn removes itself when done.
_turns = {}
_turns_lock = threading.Lock()

# At most this many replies generating at once. Not a comfort number: the
# failure `conversations.back_off` records is two conversations retrying at
# once and cascading the whole fallback chain until every model's quota was
# gone, and an unbounded fan-out lets every failing conversation do that in
# the same second. A conversation over the cap is left untouched this tick
# (nothing fetched, nothing cached) and is picked up by a later one.
MAX_PARALLEL_TURNS = 4


def _run_turn(summary, turn, live):
    try:
        spoke = turn()
        # Only for the live cycle conversation, and only when a reply
        # actually went out. The chip is what stops the next scheduled
        # run carrying this message in its trigger and answering it a
        # second time -- see heartbeats._unread_from_edvard.
        if spoke and live:
            try:
                mark_answered_live(summary)
            except Exception as e:
                log(f"[{summary.get('name', summary.get('id'))}] answered-live chip failed: {e}")
    except Exception as e:
        log(f"[{summary.get('name', summary.get('id'))}] poll failed: {e}")
    finally:
        with _turns_lock:
            _turns.pop(summary.get("id"), None)


def running_turn_ids():
    with _turns_lock:
        return set(_turns)


def join_running_turns():
    """Block until every reply in flight has posted. The drain calls this
    beside `join_running_heartbeats`, and for the same reason: when a reply
    ran inline the drain waited for it by construction, and on its own
    thread it would otherwise die with the process. No timeout, as there."""
    with _turns_lock:
        threads = list(_turns.values())
    for thread in threads:
        if thread.is_alive():
            log(f"draining: waiting for a chat reply ({thread.name}) to finish")
            thread.join()


def poll_once():
    """One tick: every conversation that owes somebody a turn.

    Due heartbeats used to be started from the bottom of this function and
    are not any more -- `agora_runner.heartbeat_pass` runs them on its own
    thread, because `speak` below generates a reply on the calling thread
    and a claude-cli reply takes minutes. The heartbeats listing is still
    fetched here: this loop needs it for the skip sets regardless.

    There is still no flag for calling this without doing the whole tick.
    One existed from 2026-08-31 for the draining process, which under
    `strategy: Recreate` was the only runner alive and would otherwise have
    answered nobody for the length of the drain. The strategy is
    `RollingUpdate` now, so the replacement pod is already polling and a
    draining one that also polled would double-answer -- so a caller that
    wants less than a full tick must not call this at all. See main.py's
    `_drain_and_exit`.
    """
    clear_persona_cache()
    # `?active=true` -- issue #30, fix 2. This tick runs every 11 seconds and
    # the store holds 1,052 conversations of which 999 are archived; measured
    # 2026-09-06 the full list is 1,836,578 bytes against 94,105 for the
    # active ones, and every archived row was dropped on the next line
    # anyway. Nothing below needs one: poll_conversation skips on the flag,
    # cycle_bound_conversation_ids already filters `not archived` when it
    # walks this listing, and acknowledge_deferred is guarded by it too.
    # An Agora that predates agora#86 ignores the parameter and answers with
    # everything, which is exactly what this asked for until today.
    status, body = agora_get("/conversations?active=true")
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
        with _turns_lock:
            if summary.get("id") in _turns:
                debug_log(f"[{summary.get('name', summary.get('id'))}] skipped: a reply is still being written")
                continue
            if len(_turns) >= MAX_PARALLEL_TURNS:
                debug_log(f"[{summary.get('name', summary.get('id'))}] skipped: "
                          f"{MAX_PARALLEL_TURNS} replies already in flight")
                continue
        try:
            turn = prepare_turn(summary)
        except Exception as e:
            log(f"[{summary.get('name', summary.get('id'))}] poll failed: {e}")
            continue
        if turn is None:
            continue
        thread = threading.Thread(
            target=_run_turn, args=(summary, turn, summary.get("id") in live_ids),
            name=f"turn-{summary.get('id')}", daemon=True,
        )
        with _turns_lock:
            _turns[summary.get("id")] = thread
        thread.start()
