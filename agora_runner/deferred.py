"""The "seen, queued" chip for the conversations poll_once deliberately skips.

runner#45 stopped a message typed into a cycle transcript from firing a
whole PR-opening Claude Code run, which was right -- but it replaced an
expensive answer with no answer at all. Edvard's message IS carried into
the next scheduled run (heartbeats.pending_across_cycles), and from a
phone that is indistinguishable from being ignored for up to six hours.
This is the missing third option: acknowledge in one HTTP call, do the
work on the schedule that was going to happen anyway.

It has to be an audit chip rather than a persona message. merge_history
and decide_turn both exclude `activity` messages, and a real persona
message here would read as "somebody already replied" -- which would
drop his message from the very lookback this chip is promising him.
Not `ephemeral` either: those live on an eviction budget meant for the
hundreds of tool chips one cycle emits, and this one has to still be
there on the next tick to stop us posting it twice.
"""

from agora_runner.log import debug_log
from agora_runner.http_util import agora_get
from agora_runner.audit import audit

# The chip's capability, which is also its dedupe key: "have I already
# acknowledged this?" is answered by looking for one of these stamped
# after Edvard's newest message, with no local state at all. An
# in-process memo would re-post the chip after every pod restart.
QUEUED_CAPABILITY = "Queued"

# How far back the dedupe scan looks. This fetch runs on a 5-second tick
# for as long as a cycle is emitting chips, so its size is the whole cost
# of the feature. Measured 2026-08-05 against a live cycle transcript:
# ~22 KB at limit=10 against ~206 KB at limit=40 (the general FETCH_LIMIT),
# because a tail of tool chips carries their output verbatim.
#
# Ten is safe rather than merely cheap, and the reason is worth stating:
# the chip is always NEWER than the message it acknowledges, so any window
# that still contains his message also contains the acknowledgment -- the
# two can never scroll apart and produce a duplicate. Missing the window
# entirely (a burst of >10 chips inside one tick) costs us the chip, not
# correctness; the same live transcript averaged ~0.3 messages/second, so
# ten is about six ticks of headroom.
ACK_TAIL_LIMIT = 10

# conversation id -> the `lastMessageAt` we last fetched at. Purely a
# fetch-avoidance cache: without it every skipped conversation would be
# re-read every 5 seconds forever, and there are CYCLE_LOOKBACK-many of
# them per rotating heartbeat. Losing it on restart costs one extra fetch
# each, never a duplicate chip -- the dedupe above is what guarantees that.
_last_message_at = {}


def _speaker_name(summary):
    participants = summary.get("personas") or []
    curator = next((p.get("name") for p in participants if p.get("role") == "curator"), None)
    return curator or next((p.get("name") for p in participants if p.get("name")), None) \
        or summary.get("name") or "Agora"


def acknowledge_deferred(summary):
    """Post one chip if Edvard has written here since we last said so.

    `summary` is poll_once's existing listing entry, so the common case
    (nothing new) costs nothing but a dict lookup.
    """
    conversation_id = summary.get("id")
    if not conversation_id:
        return
    last_message_at = summary.get("lastMessageAt") or ""
    if _last_message_at.get(conversation_id) == last_message_at:
        return
    _last_message_at[conversation_id] = last_message_at

    status, detail = agora_get(
        f"/conversations/{conversation_id}/messages?limit={ACK_TAIL_LIMIT}")
    if status != 200:
        # Forget the mark so the next tick retries rather than treating a
        # transient blip as "already handled" until he writes again.
        _last_message_at.pop(conversation_id, None)
        return
    messages = detail.get("messages") or []

    newest_from_edvard = max(
        (str(message.get("ts") or "") for message in messages
         if message.get("sender") == "Edvard" and not message.get("forgotten")),
        default="",
    )
    if not newest_from_edvard:
        return
    for message in messages:
        activity = message.get("activity")
        if (isinstance(activity, dict)
                and activity.get("capability") == QUEUED_CAPABILITY
                and str(message.get("ts") or "") > newest_from_edvard):
            return

    # No push: /audit appends the chip without notifying, unlike /notify.
    # That is the behaviour we want -- he is already looking at the app if
    # he just typed here, and a run that fires four times a day should not
    # ring his phone to say it has not started yet.
    audit(_speaker_name(summary), conversation_id, QUEUED_CAPABILITY,
          "Noted — carried into the next run. The answer arrives in that "
          "run's own conversation, not here.")
    debug_log(f"[{summary.get('name', conversation_id)}] acknowledged a deferred message")
