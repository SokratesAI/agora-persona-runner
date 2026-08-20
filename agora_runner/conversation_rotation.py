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

# How many older conversations one rotation will file into the heartbeat's
# folder. 100 covers the whole retained window several times over, so the
# switcher is right after a single rotation; the long archived tail drains
# over the next few. See `_backfill_folder`.
BACKFILL_PER_ROTATION = 100


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

        # Filing goes in its OWN patch, deliberately, even though bundling it
        # with the one above would save a round trip. Agora refuses the whole
        # request if `folderId` names a folder that has gone -- and it can go
        # between _ensure_folder returning its id and this patch landing, if
        # Edvard deletes it. Bundled, that 400 would take the `tags` with it,
        # and the tag is what every later cycle uses to find this
        # conversation: pruning, numbering, and the walk-back for a message
        # of his nobody answered. An unfiled conversation is cosmetic; an
        # untagged one is invisible.
        folder_id = _ensure_folder(heartbeat["name"])
        if folder_id:
            file_status, _ = agora_internal("PATCH", f"/conversations/{new_id}", {"folderId": folder_id})
            if file_status not in (200, 201):
                log(f"rotate_cycle_conversation: could not file into folder (HTTP {file_status}), continuing unfiled")

        point_status, _ = agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}", {
            "conversationId": new_id,
        })
        if point_status not in (200, 201):
            log(f"rotate_cycle_conversation: failed to point heartbeat at new conversation (HTTP {point_status})")
            return heartbeat["conversationId"]

        _prune_old_cycles(existing, heartbeat.get("conversationRetention") or DEFAULT_RETENTION)
        if folder_id:
            _backfill_folder(existing, folder_id)
        log(f"rotate_cycle_conversation: {heartbeat['name']} now on {new_id} (Cycle {cycle_n})")
        return new_id
    except Exception as e:
        log(f"rotate_cycle_conversation failed, keeping existing conversation: {e}")
        return heartbeat["conversationId"]


def _ensure_folder(name):
    """The switcher folder this heartbeat's cycle conversations are filed
    into (Edvard, ideas.md #5: "Heartbeat generated conversations should be
    auto created in the same folder by default"). Named after the heartbeat,
    so Nova's 30 retained cycles collapse into one row instead of being the
    list.

    POST /folders is find-or-create by name, so this is safe to call every
    cycle without persisting an id anywhere. Returns None on any failure --
    an unfiled conversation is a cosmetic problem and a cycle that does not
    run is not, so this never raises into the rotation."""
    try:
        status, body = agora_internal("POST", "/folders", {"name": name})
        if status not in (200, 201):
            log(f"_ensure_folder: HTTP {status} for {name!r}, leaving the conversation unfiled")
            return None
        return (body.get("folder") or {}).get("id")
    except Exception as e:
        log(f"_ensure_folder failed for {name!r}, leaving the conversation unfiled: {e}")
        return None


def _backfill_batch(unfiled):
    """Which unfiled conversations this rotation files, and how many are left.

    Two things decide the order, and both are reviewer findings on #264 that
    held up when I went and measured them.

    **Sorted by `createdAt`, not by `lastMessageAt`.** `_prune_old_cycles`
    sorts by `createdAt` and it is the one that decides what stays out of the
    archive -- so it decides what is in the switcher at all. Ordering the
    backfill by recent *activity* instead meant the two functions disagreed
    about which conversations matter, which is precisely the justification
    `_backfill_folder`'s docstring gives for going newest-first. On the live
    listing the two orders diverge from the fifth conversation on, so this
    was a real disagreement rather than a tidy-up.

    **Whole lineage groups, never split.** The switcher buckets a
    conversation by its own `folderId` and only groups roots with their forks
    *inside* a bucket, so a root left at the top level while its fork sits in
    the folder loses its `↳` and reads as an unrelated conversation --
    `conversation-store.fork()` copies `folderId` at fork time for exactly
    this reason, and that guard does nothing for conversations filed after
    the fact. Filing by lineage group and cutting only at a group boundary
    means the cap can never introduce the split. There are no forks among the
    tagged conversations today; this costs a few lines and stops the cap
    quietly creating the problem the first time there is one.
    """
    groups = {}
    for conversation in unfiled:
        groups.setdefault(conversation.get("rootId") or conversation["id"], []).append(conversation)
    ordered = sorted(
        groups.values(),
        key=lambda g: max(c.get("createdAt") or "" for c in g),
        reverse=True,
    )
    batch = []
    for group in ordered:
        if batch and len(batch) + len(group) > BACKFILL_PER_ROTATION:
            break
        batch.extend(sorted(group, key=lambda c: c.get("createdAt") or "", reverse=True))
    return batch, len(unfiled) - len(batch)


def _backfill_folder(existing, folder_id):
    """File this heartbeat's *older* conversations into the folder too.

    `_ensure_folder`'s docstring says the folder makes "Nova's 30 retained
    cycles collapse into one row instead of being the list". That was the
    intent and it was not what the code did: filing only ever touched the
    conversation this rotation had just created, so the folder started with
    one conversation in it and every older one stayed at the top level.
    Measured on the live service 2026-08-20, an hour after the feature went
    out: 296 conversations, 1 filed. Edvard's switcher was exactly as long
    as before, plus a folder holding a single row -- the feature looked
    broken rather than absent, which is worse.

    Newest first, because the 30 that survive pruning are the only ones in
    the switcher and they are what he judges this by; the archived tail is
    invisible until he unarchives one, and filing it now is what stops that
    row coming back loose. Capped per rotation so a first run against a long
    history cannot sit in the startup path of a cycle indefinitely -- what is
    left over is filed by the next rotation, and the count is logged so a
    backlog that never drains is visible rather than silent.
    `_backfill_batch` picks the order and the cut.

    A non-2xx gets its own log line rather than only being absent from the
    total: if Edvard deletes the folder midway through a batch, every
    remaining conversation fails identically, and "filed 3 of 100" alone does
    not say why.

    Never raises, for the same reason `_ensure_folder` doesn't: this runs
    after the heartbeat has already been pointed at the new conversation, so
    an exception escaping here would reach the caller's `except` and return
    the *old* conversation id for a cycle that is now running in the new one.
    """
    unfiled = [c for c in existing if c.get("id") and not c.get("folderId")]
    if not unfiled:
        return
    batch, remaining = _backfill_batch(unfiled)
    filed = 0
    for conversation in batch:
        try:
            status, _ = agora_internal(
                "PATCH", f"/conversations/{conversation['id']}", {"folderId": folder_id}
            )
            if status in (200, 201):
                filed += 1
            else:
                log(f"_backfill_folder: HTTP {status} for {conversation['id']}, leaving it unfiled")
        except Exception as e:
            log(f"_backfill_folder: {conversation['id']} failed, leaving it unfiled: {e}")
    log(
        f"_backfill_folder: filed {filed} of {len(batch)} older conversation(s), "
        f"{remaining} left for the next rotation"
    )


def _prune_old_cycles(existing_before_this_one, retention):
    """`existing_before_this_one` doesn't include the conversation just
    created -- so keeping `retention - 1` of them (newest first) plus
    the new one totals `retention` active conversations."""
    keep = max(0, retention - 1)
    by_age = sorted(existing_before_this_one, key=lambda c: c.get("createdAt", ""), reverse=True)
    for stale in by_age[keep:]:
        agora_internal("PATCH", f"/conversations/{stale['id']}", {"archived": True})
