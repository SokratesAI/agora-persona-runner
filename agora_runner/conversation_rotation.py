"""Per-cycle conversation rotation for workflow-mode heartbeats
(2026-08-02) -- Edvard's own framing: "all the thoughts and outputs are
written there [the conversation]... that conversation is going to be
longer and longer, just like the Journal." A heartbeat bound forever to
one conversation accumulates every tool-call-heavy round of every cycle
it has ever run, unbounded, in a UI meant for human chat.

`rotate_cycle_conversation` creates a fresh, empty conversation for this
cycle (same persona list as before), points the heartbeat at it, and
archives older cycle-conversations beyond a retention count -- tagged so
only conversations THIS heartbeat created get touched, never an
unrelated one. The vault journal remains the actual cross-cycle MEMORY
(a curated summary, read fresh every cycle); this is purely about
keeping the raw, verbose, human-browsable transcript bounded.

Off by default (`Heartbeat.rotateConversationEachRun`) -- an ordinary
heartbeat (e.g. K3s Sentinel) wants its one conversation to keep
accumulating, same as before this existed.
"""
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.log import log

# Edvard, unboarded capture 2026-08-20, rated 🔴 Immediately: "I want Agora
# to keep the last 30 conversations for a heartbeat so that i'm able to talk
# to them." It was 5, which is about three hours of a 72-minute cadence --
# every cycle he had not read within that window was already archived and out
# of the switcher by the time he opened his phone. Archiving is not deletion,
# so the cost of 30 is a longer list, not more storage.
DEFAULT_RETENTION = 30


def cycle_tag(heartbeat_id):
    """The tag every conversation this heartbeat creates carries. Public
    because heartbeats.py needs it to find previous cycles' conversations
    when looking for a message from Edvard nobody has answered yet."""
    return f"evolve-cycle:{heartbeat_id}"


def rotate_cycle_conversation(heartbeat, participants):
    """Returns the conversation_id to actually use for this run --
    either a freshly created one (rotation enabled) or the heartbeat's
    existing `conversationId` unchanged (rotation disabled, or anything
    about the rotation attempt failed -- a rotation bug must never be
    the reason a real cycle doesn't run)."""
    if not heartbeat.get("rotateConversationEachRun"):
        return heartbeat["conversationId"]

    bootstrap_persona_id = participants[0].get("personaId") if participants else None
    if not bootstrap_persona_id:
        log("rotate_cycle_conversation: no participants to bootstrap a new conversation with, skipping rotation")
        return heartbeat["conversationId"]

    tag = cycle_tag(heartbeat["id"])
    try:
        status, listing = agora_get("/conversations")
        existing = [
            c for c in (listing.get("conversations", []) if status == 200 else [])
            if tag in (c.get("tags") or [])
        ]
        # Read the next number off the names already out there rather than
        # counting them, so this and `cycle_number.current_number` -- which
        # is what a live cycle asks for its own number -- can never answer
        # differently. Imported here because `cycle_number` imports
        # `cycle_tag` from this module.
        from agora_runner.cycle_number import next_number

        cycle_n = next_number(existing, tag)

        create_status, created = agora_internal("POST", "/conversations", {
            "name": f"{heartbeat['name']} — Cycle {cycle_n}",
            "personaId": bootstrap_persona_id,
        })
        if create_status not in (200, 201):
            log(f"rotate_cycle_conversation: create failed HTTP {create_status}, keeping existing conversation")
            return heartbeat["conversationId"]
        new_id = created.get("conversation", {}).get("id")
        if not new_id:
            log("rotate_cycle_conversation: create response had no conversation id, keeping existing conversation")
            return heartbeat["conversationId"]

        # Carry the full persona list forward (create only bootstraps
        # the one curator) and tag it so future rotations/pruning can
        # find it.
        agora_internal("PATCH", f"/conversations/{new_id}", {
            "personas": [dict(p) for p in participants],
            "tags": [tag],
        })

        point_status, _ = agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}", {
            "conversationId": new_id,
        })
        if point_status not in (200, 201):
            log(f"rotate_cycle_conversation: failed to point heartbeat at new conversation (HTTP {point_status})")
            return heartbeat["conversationId"]

        _prune_old_cycles(existing, heartbeat.get("conversationRetention") or DEFAULT_RETENTION)
        log(f"rotate_cycle_conversation: {heartbeat['name']} now on {new_id} (Cycle {cycle_n})")
        return new_id
    except Exception as e:
        log(f"rotate_cycle_conversation failed, keeping existing conversation: {e}")
        return heartbeat["conversationId"]


def _prune_old_cycles(existing_before_this_one, retention):
    """`existing_before_this_one` doesn't include the conversation just
    created -- so keeping `retention - 1` of them (newest first) plus
    the new one totals `retention` active conversations."""
    keep = max(0, retention - 1)
    by_age = sorted(existing_before_this_one, key=lambda c: c.get("createdAt", ""), reverse=True)
    for stale in by_age[keep:]:
        agora_internal("PATCH", f"/conversations/{stale['id']}", {"archived": True})
