"""notify/speak/poll_conversation -- one conversation's turn each tick."""

import time
from urllib.parse import quote

from agora_runner.config import (
    FAILURE_BACKOFF_CAP, FAILURE_BACKOFF_MAX_SECONDS, FAILURE_BACKOFF_SECONDS,
    FETCH_LIMIT, NO_CAPS, POLL_INTERVAL_SECONDS,
)
from agora_runner.log import log, debug_log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.agora_api import fetch_persona
from agora_runner.turns import build_system, decide_turn, merge_history, PAUSE_SENTINEL
from agora_runner.reply import generate_reply


# conversation id -> consecutive speak() failure count. Persists across
# poll_once() calls (per-conversation, not per-tick) so repeated failures
# actually accumulate. Cleared on any successful reply.
_conversation_failures = {}

# conversation id -> monotonic time before which we don't retry it. Set
# from _conversation_failures once a conversation crosses FAILURE_BACKOFF_CAP.
# This replaced auto-pause on 2026-08-05 at Edvard's ask -- see config.py.
_conversation_backoff = {}

# conversation id -> (last_message_id, rev, messages). The poll loop re-read
# the whole FETCH_LIMIT window for every conversation every tick, and almost
# every tick it had not changed. Measured against the live pod 2026-08-10:
# five polled conversations, 247,890 bytes per tick at POLL_INTERVAL_SECONDS=5,
# i.e. 4.28 GB/day to learn nothing. The server has answered ?after+?rev since
# agora#51 (built for the drawer) and this is the busier consumer of the two.
#
# We keep the messages, not just the fingerprint, because decide_turn and
# merge_history both want the window rather than the delta -- so an
# incremental answer is appended to what we already hold and re-trimmed to
# FETCH_LIMIT, which leaves the window this function passes on byte-identical
# to what a full fetch would have returned.
#
# Correctness rests entirely on the server's `incremental` flag, never on us
# guessing: `rev` fingerprints id+forgotten+text of every message up to
# `after`, so an edit, a delete or a forget anywhere in the prefix comes back
# incremental=False with the full window, and we replace instead of appending.
# A server that has never heard of ?after (or any response without a rev)
# lands on the same path -- we send no after/rev, and get the window.
_message_window_cache = {}


def prune_message_window_cache(known_ids):
    """Drop cached windows for conversations that no longer exist. Called
    once per tick from poll_once with the ids in that tick's listing, so
    the cache is bounded by the conversation list rather than by process
    uptime -- the heartbeat rotates Nova into a new conversation every
    cycle, so without this it would grow ~24 windows a day forever."""
    for conversation_id in set(_message_window_cache) - set(known_ids):
        del _message_window_cache[conversation_id]


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


def back_off(conversation_id, conversation_name, failures, reason, last_message_id=None):
    """Stop retrying a persistently failing conversation for a while, without
    touching its status. Replaced auto_pause() on 2026-08-05 -- Edvard:
    *"Please turn off the auto pause of conversations as they are just
    blocking now."* A paused conversation needs a manual resume from the
    menu, so a transient outage left him locked out of a thread until he
    noticed; backing off recovers on its own once the cause clears.

    The runaway it still has to prevent is real and measured (2026-07-23:
    two rate-limited conversations retried every poll tick for 8+ hours,
    each retry cascading the whole Gemini fallback chain, which is what
    actually exhausted every model's quota). Doubling the wait per failure
    bounds that at a handful of attempts per hour instead of hundreds."""
    delay = min(
        FAILURE_BACKOFF_SECONDS * 2 ** (failures - FAILURE_BACKOFF_CAP),
        FAILURE_BACKOFF_MAX_SECONDS,
    )
    _conversation_backoff[conversation_id] = (time.monotonic() + delay, last_message_id)
    # system=True (2026-07-24): found live that without this, a persona
    # asked an unrelated question shortly after answered about this
    # control-plane message instead -- merge_history was feeding it into
    # the model's own context as if it were a real previous reply. See
    # Message.system's docstring (agora repo).
    notify(
        conversation_id,
        f"⚠️ {failures} consecutive failed reply attempts. Last error: {reason}. "
        f"Still active — retrying in {round(delay / 60)} min, and on any message you send.",
        "Agora",
        system=True,
    )
    log(f"[{conversation_name}] backing off {delay}s after {failures} failures: {reason}")


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
    path = f"/conversations/{summary['id']}/messages?limit={FETCH_LIMIT}"
    cached = _message_window_cache.get(summary["id"])
    if cached:
        path += f"&after={quote(cached[0])}&rev={quote(cached[1])}"
    status, detail = agora_get(path)
    if status != 200:
        debug_log(f"[{name}] skipped: conversation fetch returned {status}")
        return
    thread = detail.get("messages", [])
    if cached and detail.get("incremental"):
        # Only what arrived since we last looked, so re-attach our copy of
        # the rest. Normally this delta is empty.
        thread = (cached[2] + thread)[-FETCH_LIMIT:]
        debug_log(f"[{name}] incremental: {len(detail.get('messages', []))} new message(s)")
    rev = detail.get("rev")
    last_id = thread[-1].get("id") if thread else None
    if rev and last_id:
        _message_window_cache[summary["id"]] = (last_id, rev, thread)
    else:
        _message_window_cache.pop(summary["id"], None)
    personas = detail.get("personas") or [
        {"name": detail.get("name", ""), "role": "curator", "personaId": None}
    ]
    speakers = decide_turn(thread, personas)
    if not speakers:
        debug_log(f"[{name}] no turn needed (last sender not Edvard, or no @mention match)")
        return
    if speakers == [PAUSE_SENTINEL]:
        # The chain stops here; the conversation stays active. Pausing it
        # was the old multi-persona architecture's backstop, and Edvard
        # asked for it gone (2026-08-05) -- the cap's actual job is to stop
        # personas @mentioning each other forever, and returning does that
        # on its own. His next message starts a fresh chain, no menu.
        debug_log(f"[{name}] AI turn cap reached: chain stopped, conversation left active")
        return
    debug_log(f"[{name}] turn decided: speakers={speakers}")

    visible = [m for m in thread if not m.get("forgotten")]
    override = None
    if visible and visible[-1].get("sender") == "Edvard":
        override = visible[-1].get("modelOverride")

    # Backoff is checked here rather than at the top of the function on
    # purpose: a message Edvard just sent clears it immediately, and we
    # can't know who sent last without the fetch above. The fetch is one
    # cheap GET -- the cost this guards is generate_reply() cascading the
    # whole fallback chain, which is below this point.
    state = _conversation_backoff.get(summary["id"])
    if state is not None:
        until, seen_last_id = state
        last_id = visible[-1].get("id") if visible else None
        # A *new* message from him clears it immediately -- but it has to be
        # new. The common retry storm is "Edvard asked something and every
        # reply attempt fails", where his message stays the last one in the
        # thread forever, so testing sender alone would clear the backoff on
        # every single tick and change nothing at all.
        if visible and visible[-1].get("sender") == "Edvard" and last_id != seen_last_id:
            _conversation_backoff.pop(summary["id"], None)
            _conversation_failures.pop(summary["id"], None)
        elif time.monotonic() < until:
            debug_log(f"[{name}] skipped: backing off for {round(until - time.monotonic())}s more")
            return
        else:
            _conversation_backoff.pop(summary["id"], None)

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
        # forever with zero backoff (see FAILURE_BACKOFF_CAP in config.py).
        count = _conversation_failures.get(summary["id"], 0) + 1
        _conversation_failures[summary["id"]] = count
        if count >= FAILURE_BACKOFF_CAP:
            # The old label ("rate limit or outage") was a guess -- the real
            # exception only ever reached stdout via poll.py's own
            # try/except, never the conversation Edvard actually reads.
            # Thread the real error into the visible message instead.
            detail_text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            back_off(summary["id"], detail.get("name"), count, detail_text,
                     last_message_id=visible[-1].get("id") if visible else None)
        raise
    else:
        _conversation_failures.pop(summary["id"], None)
        _conversation_backoff.pop(summary["id"], None)
