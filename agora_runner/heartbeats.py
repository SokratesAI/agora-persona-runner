"""Heartbeat scheduling: due-check, vault-context injection, and the workflow-mode thread dispatch."""

import threading
import time
from datetime import datetime, timezone

from agora_runner.config import (
    FETCH_LIMIT,
    HEARTBEAT_NO_REPORT_SENTINEL,
    NO_CAPS,
    NOVA_PERSONA_ID,
    OSLO,
)
from agora_runner.log import log, debug_log
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.audit import audit
from agora_runner.agora_api import fetch_persona
from agora_runner.vault import fetch_vault_context
from agora_runner.turns import build_system, merge_history, pending_user_turn, schedule_due
from agora_runner.reply import generate_reply
from agora_runner.conversations import notify
from agora_runner.workflows import run_workflow_heartbeat
from agora_runner.conversation_rotation import cycle_tag, rotate_cycle_conversation
from agora_runner.deferred import ANSWERED_LIVE_CAPABILITY

# How many previous cycle-conversations the pending-message lookback may
# walk back through, and how much of Edvard's text it may carry into one
# trigger. Retention (conversation_rotation.DEFAULT_RETENTION) is 5, so 5
# is "everything still un-archived"; the char cap is Edvard's own
# constraint -- a long-lived channel he can write into freely must not
# quietly turn into megabytes of prompt every cycle.
CYCLE_LOOKBACK = 5
PENDING_CHARS_CAP = 4000


def _elapsed(seconds):
    """Wall-clock of one heartbeat run, as a chip label -- '9s', '2m 14s',
    '38m'. Rounded to the second on purpose: this is the answer to "is it
    still going", not a metric anyone measures against."""
    minutes, secs = divmod(int(seconds), 60)
    if minutes and secs:
        return f"{minutes}m {secs}s"
    return f"{minutes}m" if minutes else f"{secs}s"


def _unread_from_edvard(detail, since=None):
    """Everything Edvard wrote in one conversation that no run has read
    yet, oldest first and joined, or None.

    This replaced a rule that only carried his words when the thread
    ENDED on him (2026-08-05, later the same day). That rule dropped the
    single most likely case there is: he watches a cycle run, types
    something while it is working, and forty minutes later the cycle
    posts its own report underneath him. The thread now ends on a
    persona, so the next run saw nothing pending and his message was
    gone -- silently, permanently, and specifically when he had been
    paying the most attention.

    "Ends on him" was standing in for "nobody has answered him", and in
    a cycle transcript those are not the same thing at all: poll_once
    skips these conversations, so the only persona message that can ever
    land here is the cycle's own report, written from a trigger built
    before he spoke. Nothing in this thread is ever a reply to him.

    `since` is the previous run's `lastRunAt`, and it is the honest
    boundary instead: a run reads its trigger at its start, so anything
    older than that start was already offered to it, and anything newer
    was not. That also bounds re-carrying without any new state -- the
    conversation this run reads in full becomes, next run, an older one
    filtered by a `since` that has moved past all of it. It is left at
    None for the conversation we just rotated away from, because that
    conversation was created by the previous run: everything in it
    arrived after that run had already built its trigger.

    2026-08-19: the paragraph above says "Nothing in this thread is ever
    a reply to him", and that stopped being true the moment poll_once
    started answering the live cycle conversation in real time. A
    persona message after his text there is now ambiguous -- the live
    answer, or the running cycle's report landing underneath him -- so
    the marker is deferred.ANSWERED_LIVE_CAPABILITY, stamped by exactly
    one of those two. Anything at or before the newest such chip has
    been answered where he wrote it and is not carried again.

    Note the asymmetry with `since`, and it is on purpose: `since`
    filters on a boundary the run owns, while this filters on a chip the
    conversation carries, so a missing chip degrades to the old
    behaviour (carry it, answer twice) rather than to silence."""
    answered_through = ""
    for message in detail.get("messages") or []:
        activity = message.get("activity")
        if (isinstance(activity, dict)
                and activity.get("capability") == ANSWERED_LIVE_CAPABILITY):
            ts = str(message.get("ts") or "")
            if ts > answered_through:
                answered_through = ts
    texts = []
    for message in detail.get("messages") or []:
        if message.get("sender") != "Edvard" or message.get("forgotten"):
            continue
        ts = str(message.get("ts") or "")
        if since and ts <= since:
            continue
        if answered_through and ts <= answered_through:
            continue
        text = (message.get("text") or "").strip()
        if text:
            texts.append(text)
    return "\n\n".join(texts) or None


def pending_across_cycles(heartbeat, previous_detail, current_id=None, since=None):
    """Edvard's unanswered messages, walking back from the conversation
    we just rotated away from through older cycle-conversations. Returns
    [(source_label, text)], oldest first.

    2026-08-02: #28 made the rotating heartbeat look back exactly ONE
    conversation. But a cycle that dies before replying (which has now
    happened twice in one day -- merging into this repo rolls the pod
    running the cycle) leaves an EMPTY conversation behind, so one step
    back lands on nothing and anything Edvard typed two cycles ago is
    dropped silently and permanently. That is his own top complaint
    ("even if i write something in an older conversation, it is never
    read"), and the whole reason he currently has to talk to this loop
    through vault files instead of the app he built for it.

    2026-08-05: the walk used to STOP at the first conversation where a
    persona had replied, using that reply as the boundary of "already
    seen". That boundary was in the wrong place. A healthy loop replies
    in every cycle conversation, so the walk stopped after one step
    essentially always, and anything Edvard wrote in an older thread was
    never reached. The system covered for it by letting ordinary
    turn-taking answer those threads instead (see
    cycle_bound_conversation_ids) -- which meant one sentence typed into
    a week-old transcript fired a full, PR-opening Claude Code cycle.
    That happened on 2026-08-05 to a note that read "if you ever need to
    create a secret, use the sealed secrets in platform-config", and it
    is the second time this shape of expensive-run has bitten him.

    So the walk now covers every conversation in the lookback, and
    `since` (the PREVIOUS run's lastRunAt) is the boundary instead --
    see _unread_from_edvard for why a timestamp is the honest marker
    here and a persona reply is not. That makes the set of conversations
    walked a superset of the set poll_once skips, which is the invariant
    the two functions have to keep between them.

    2026-08-05, later still: what a conversation contributes is now
    every unread thing he wrote in it, not only a trailing one. Until
    then, typing into a cycle transcript *while that cycle was running*
    was dropped outright -- the run's own report landed underneath his
    message and made the thread stop ending on him. poll_once now posts
    him a chip promising this walk will reach it (deferred.py), which is
    only worth posting if it is true.

    The cost is that the older conversations are now always fetched
    (one listing plus up to CYCLE_LOOKBACK message fetches) rather than
    only when the previous cycle died without replying. That is ~6
    in-cluster requests, four times a day, to stop firing whole cycles
    by accident."""
    collected = []
    tail = _unread_from_edvard(previous_detail)
    if tail:
        collected.append(("the previous cycle's conversation", tail))
    for detail, label in _older_cycle_conversations(heartbeat, current_id):
        tail = _unread_from_edvard(detail, since)
        if tail:
            collected.append((label, tail))
    collected.reverse()  # oldest first -- read in the order he wrote them
    while len(collected) > 1 and sum(len(t) for _s, t in collected) > PENDING_CHARS_CAP:
        collected.pop(0)  # drop the oldest rather than truncate mid-sentence
    return collected


def _older_cycle_conversations(heartbeat, current_id):
    """Details of this heartbeat's earlier cycle-conversations, newest
    first, excluding both the one already walked (the heartbeat's
    pre-rotation `conversationId`) and the empty one rotation just
    created for this cycle. Still a generator, but since 2026-08-05 the
    caller drains it every run rather than stopping at the first reply,
    so expect all of them to be fetched."""
    status, listing = agora_get("/conversations")
    if status != 200:
        return
    tag = cycle_tag(heartbeat["id"])
    seen_ids = {heartbeat.get("conversationId"), current_id}
    candidates = [
        c for c in listing.get("conversations", [])
        if tag in (c.get("tags") or []) and not c.get("archived")
        and c.get("id") not in seen_ids
    ]
    candidates.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
    for conversation in candidates[:CYCLE_LOOKBACK]:
        detail_status, detail = agora_get(
            f"/conversations/{conversation['id']}/messages?limit={FETCH_LIMIT}")
        if detail_status != 200:
            continue
        yield detail, f'the conversation "{conversation.get("name") or conversation["id"]}"'


def _parse_run_at(stamp):
    """An Agora `lastRunAt` as an aware datetime, or `None`.

    `None` on anything unparseable rather than a raised error or a guessed
    time: it feeds `gaps_since`, where `None` means "no boundary, report
    everything once". Erring toward one noisy run beats silently adopting
    a wrong boundary and swallowing a real failure.
    """
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def nova_health_note(persona, previous_run_at, schedule=None):
    """The journal self-check, as a line for Nova's own heartbeat, or `""`.

    Edvard, `issues.md` 2026-08-12: *"Cycle 134 failed. If you do not
    already have a self check that your previous cycles worked correctly,
    you should make yourself do this and self repair automatically."*
    `cycle_health` answered the first half the same day and then sat there
    with nothing calling it for five cycles -- issue #70, and the reason it
    stalled is that a cycle cannot run it itself. `agora_runner` is not in
    the bridge image, and the `COUCHDB_*` names it needs are set in *this*
    pod and not that one, so the check reads an empty journal and certifies
    a healthy loop from a blind instrument. Here is the only place that has
    both the credentials and a cycle's attention: the run that dispatches
    the cycle, one line ahead of it in the same turn.

    It reports `heartbeat_findings` rather than `findings` -- only the gaps
    this run is the first to see, so a cycle is told about a dead
    predecessor once instead of reading the same six historical holes every
    hour until it stops looking.

    Gated on the persona because the finding is about Nova's journal and
    would be meaningless in front of anyone else's heartbeat, and wrapped
    because a self-check is never worth losing a cycle over -- if the vault
    read fails, the cycle should still run, and step 1 will notice.

    **The unit the stall is measured in is how often an entry gets
    written, not how often this heartbeat runs.** "No entry for 2
    heartbeat intervals" is only a true sentence if the interval is the
    one entries actually arrive at. `cycle_health.nova_cadence_minutes`
    answers that, and it is the same call `nova_site` makes for the badge
    -- the shortest of every enabled, non-workflow heartbeat pointed at
    Nova, because any of them dispatching writes an entry.

    #166 asked the narrower question instead, using `schedule`, this
    heartbeat's own. With one heartbeat the two agree, and Nova has one
    today; with two they diverge, and this side would be measuring in the
    interval of whichever one happened to fire. `schedule` stays as the
    middle fallback because it is already in hand and costs nothing: it
    is right whenever Agora cannot be reached but this heartbeat is
    running, which is exactly the case a network failure here produces.
    `HEARTBEAT_MINUTES` is the last resort under both -- a `cron@` or
    `daily@` heartbeat has no single interval, and no such heartbeat is
    Nova's today.
    """
    if (persona or {}).get("id") != NOVA_PERSONA_ID:
        return ""
    try:
        from agora_runner.cycle_health import (
            HEARTBEAT_MINUTES, describe, heartbeat_findings,
            nova_cadence_minutes,
        )
        from agora_runner.nova_journal import JOURNAL_DIR
        from agora_runner.turns import schedule_minutes
        from agora_runner.vault import vault_bulk_list

        try:
            cadence = nova_cadence_minutes()
        except Exception as e:
            # Deliberately not inside the outer `except`. Agora being
            # unreachable *raises* rather than returning a status --
            # `http_json` catches `HTTPError`, not `URLError` -- and the
            # agora pod rolling is a real window, four `Connection
            # refused` lines over 20 seconds on 2026-08-14. The vault read
            # below is what this note exists for; letting a failed lookup
            # of the *unit* discard the whole measurement would be trading
            # the answer for its label, when `schedule` is already in hand
            # and cannot fail.
            log(f"cadence lookup failed, measuring in this heartbeat's own: {e}")
            cadence = None
        files, mtimes = vault_bulk_list(JOURNAL_DIR)
        line = describe(heartbeat_findings(
            list(files), mtimes, datetime.now(OSLO),
            _parse_run_at(previous_run_at),
            cadence or schedule_minutes(schedule) or HEARTBEAT_MINUTES,
            unreadable=getattr(files, "unreadable", ()),
        ))
    except Exception as e:
        # Never re-raised -- a self-check is not worth a cycle's hour. But
        # not silent either, and the reason is specific to reporting each
        # gap once: the boundary this filters on is Agora's `lastRunAt`,
        # which advances whether or not this check succeeded. So a gap
        # whose bracketing entry lands during a failed hour is not delayed,
        # it is lost -- the next successful run sees a bracket older than
        # its own boundary and treats it as already told. Saying so is the
        # only thing standing between that and the exact failure this whole
        # module exists to prevent: an all-clear from an instrument that
        # never ran.
        log(f"cycle health check failed, dispatching anyway: {e}")
        return (
            "## Your own last hours\n"
            f"The automatic check of your journal folder failed to run: {e}. "
            "It reports each dead cycle exactly once and its boundary moves on "
            "regardless, so a cycle that died in the last hour may now never be "
            "reported. Run `python -m agora_runner.cycle_health` in the runner "
            "pod (terminal_exec) if you want the full history."
        )
    if not line:
        return ""
    return (
        "## Your own last hours\n"
        f"An automatic check of your journal folder, run just now: {line}. "
        "You are the first cycle to be told this. Anything a dead cycle left "
        "behind is still in `/data/workspace` -- prompt.md step 1c sweeps it -- "
        "and picking that up beats starting something new."
    )


def run_heartbeat(heartbeat):
    # Read BEFORE the claim PATCH below overwrites it: this is the
    # previous run's timestamp, and it is the boundary
    # pending_across_cycles uses to tell "Edvard wrote this since I last
    # ran" from "already offered to an earlier run". Taken from the local
    # snapshot on purpose — the server-side value is gone one line later.
    previous_run_at = heartbeat.get("lastRunAt")
    # Claim the run BEFORE running it (2026-08-02) — same claim, same
    # reasons, as run_workflow_heartbeat's (see its comment for the
    # measurements). #25 added it there and only there; a regular
    # heartbeat had no duplicate protection of any kind, and since v2
    # the Evolve loop runs on a regular heartbeat — so the unguarded
    # path was the one doing the long, PR-opening runs.
    #
    # Confirmed live twice on 2026-08-02: a merge into this repo rolls
    # the pod hosting the in-flight cycle, the process is killed before
    # the final PATCH below, so `forceRun` is still set when the
    # replacement pod reads it — and it starts the same cycle over. A
    # kill isn't an exception, so the `except Exception` below can never
    # clean this up; only a claim written up front survives it.
    claim_status, _ = agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                                     {"forceRun": False,
                                      "lastRunAt": datetime.now(timezone.utc).isoformat(),
                                      "lastResult": "running"})
    if claim_status not in (200, 201):
        # Not fatal — a transient Agora blip shouldn't block a real
        # cycle — but never silent: this line is the evidence if
        # duplicate runs ever reappear.
        log(f"heartbeat {heartbeat['name']}: claim PATCH failed (HTTP {claim_status}), "
            "run is unclaimed and may be duplicated by a restart or another replica")
    persona = fetch_persona(heartbeat["personaId"])
    if persona is None:
        agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                       {"forceRun": False, "lastRunAt": datetime.now(timezone.utc).isoformat(),
                        "lastResult": "failed: persona not found"})
        return
    status, detail = agora_get(
        f"/conversations/{heartbeat['conversationId']}/messages?limit={FETCH_LIMIT}"
    )
    if status != 200:
        agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                       {"forceRun": False, "lastRunAt": datetime.now(timezone.utc).isoformat(),
                        "lastResult": f"failed: conversation fetch {status}"})
        return

    # Per-cycle conversation rotation (2026-08-02, same mechanism
    # workflows.py's run_workflow_heartbeat already uses) -- no-op unless
    # heartbeat["rotateConversationEachRun"] is set. `detail` is stale
    # (the OLD conversation's) when it does rotate, so re-fetch it.
    previous_detail = detail
    conversation_id = rotate_cycle_conversation(heartbeat, detail.get("personas") or [])
    rotated = conversation_id != heartbeat["conversationId"]
    if rotated:
        _status, detail = agora_get(f"/conversations/{conversation_id}/messages?limit={FETCH_LIMIT}")

    # 2026-08-05, Edvard: "The times when you start will not always be exactly
    # 6 hours as I often manually trigger you to start when i see that we have
    # a lot of token quota left. Maybe a good idea to you is to add to the
    # agora manual trigger to let you know that you where triggered manually."
    #
    # `forceRun` is exactly that signal -- POST /heartbeats/:id/run sets it,
    # and the claim PATCH above clears it server-side, so this local snapshot
    # (fetched by run_due_heartbeats before the claim) is the last place it can
    # be read at all. Without it a cycle silently assumes its own schedule
    # elapsed since the previous run, which is how one of them ends up
    # reasoning about "six hours of vault changes" over a twelve-minute gap.
    manual = bool(heartbeat.get("forceRun"))
    origin = ("a manual trigger — Edvard started this run himself just now "
              f"rather than waiting for the {heartbeat['schedule']} schedule"
              if manual else
              f"an automatic scheduled turn ({heartbeat['schedule']})")
    extra_parts = [
        "## Heartbeat turn",
        f"This message is {origin}. It is not a direct reply to Edvard — "
        "write to Edvard proactively.",
    ]
    if heartbeat.get("task"):
        extra_parts.append(f"Task for this turn: {heartbeat['task']}")
    # Before the vault context and after the task, so a cycle reads what it
    # was asked to do and then what went wrong last hour -- the second only
    # ever changes how it does the first.
    health = nova_health_note(persona, previous_run_at, heartbeat.get("schedule"))
    if health:
        extra_parts.append(health)
    if heartbeat.get("vaultPaths"):
        context = fetch_vault_context(heartbeat["vaultPaths"])
        if context:
            extra_parts.append(
                "## Reference material from Edvard's vault\n"
                "Already fetched for you — answer from it directly rather than "
                "browsing the vault with tools, unless something essential is "
                f"missing.\n\n{context}"
            )
    heartbeat_extra = "\n\n".join(extra_parts)

    caps = persona.get("capabilities") or dict(NO_CAPS)
    participants = detail.get("personas") or []
    system = build_system(persona, detail, participants, heartbeat_extra)
    history = merge_history(detail.get("messages", []), persona["name"],
                            len(participants) > 1)
    # A heartbeat may fire into an empty/assistant-ended thread — providers
    # need a user turn, so the trigger itself becomes a synthetic one.
    #
    # 2026-08-02: claude-cli personas only ever see this LAST history entry
    # (bridge/cli.py's generate_reply forwards history[-1], not the full
    # thread) -- so if Edvard's real last message was just sitting in
    # `history` unaddressed, a claude-cli persona would never actually see
    # it, only this synthetic trigger. Folding his real content into the
    # trigger when it's genuinely his turn (last message role is "user")
    # fixes that without changing anything for Anthropic/Gemini, which
    # already see the full thread regardless.
    #
    # 2026-08-02, later: rotation (above) replaces `detail` with a
    # brand-new EMPTY conversation, so on a rotating heartbeat `history`
    # is always empty and the fold-in below could never fire -- the two
    # halves of the fix cancelled each other out. Anything Edvard typed
    # between cycles lived only in the conversation we just rotated away
    # from, and was dropped silently, forever. So when we rotated, fall
    # back to the pre-rotation thread for his pending message.
    #
    # 2026-08-02, later still: one step back isn't enough either -- a
    # cycle that dies before replying leaves an empty conversation, and
    # the message from the cycle before it was still lost. See
    # pending_across_cycles.
    # Said twice on purpose: claude-cli personas only ever see this last
    # history entry (the comment above), so the system prompt's `origin`
    # alone would not reach them.
    trigger = ("[Manual heartbeat trigger — Edvard started this run himself. "
               "Address Edvard directly.]" if manual else
               "[Automatic heartbeat trigger — address Edvard directly.]")
    pending = pending_user_turn(history)
    carried = [("this conversation", pending)] if pending else []
    if not carried and rotated:
        carried = pending_across_cycles(heartbeat, previous_detail,
                                        conversation_id, since=previous_run_at)
    if len(carried) == 1:
        source, text = carried[0]
        trigger += f" Edvard's most recent message in {source}: {text}"
    elif carried:
        lines = "\n".join(f"- in {source}: {text}" for source, text in carried)
        trigger += ("\n\nEdvard's messages since your last reply, none of them "
                    f"answered yet, oldest first:\n{lines}")
    history.append({"role": "user", "content": trigger})

    # 2026-08-03 (Edvard's ask): the "Ran heartbeat" chip is meant to show
    # that something is *processing*, but it was posted after notify() at
    # the very end of the run -- so it rendered BELOW the reply and only
    # appeared once there was nothing left to wait for. On a claude-cli
    # cycle that is up to 45 minutes late: "they serve no purpose other
    # than hindsight logging. I want to see them immediately when they are
    # triggered." So post it up front instead.
    #
    # Not for monitoring-style heartbeats, though. Those opt into
    # HEARTBEAT_NO_REPORT_SENTINEL (config.py) precisely so a clean run
    # leaves the chat untouched, and a chip every 10 minutes saying "Ran
    # heartbeat" is exactly the noise that sentinel exists to prevent.
    # Whether a run will go silent isn't knowable until the reply is in
    # hand -- but opting in means *instructing the model*, and the only
    # channel for that is the system prompt, so that is what we test.
    # Those heartbeats keep the old end-of-run chip, unchanged.
    may_go_silent = HEARTBEAT_NO_REPORT_SENTINEL in system
    # The opening chip says which of the two started this run, so the thread
    # itself records what Edvard did rather than only what the clock did.
    started_chip = f"{heartbeat['name']} ({'manual trigger' if manual else heartbeat['schedule']})"
    if not may_go_silent:
        audit(persona["name"], conversation_id, "heartbeat", started_chip)

    result = ""
    silent = False
    started_at = time.monotonic()
    try:
        # 2026-07-24: heartbeats always run non-sticky regardless of the
        # bound conversation's own stickyFallback setting -- a scheduled
        # proactive message shouldn't permanently downgrade a persona that
        # other conversations may also use via the same Gemini model.
        # 2026-07-25: deliberately NOT streamed (no on_text) -- unlike a
        # live chat turn, a monitoring heartbeat's prompt may ask for a
        # silent HEARTBEAT_NO_REPORT_SENTINEL reply when there's nothing
        # worth Edvard's attention, and that decision can only be made
        # once the full reply is in hand, before anything is posted.
        reply = generate_reply(persona, caps, system, history, conversation_id, sticky=False,
                                unattended=True)
        if reply.strip().upper().startswith(HEARTBEAT_NO_REPORT_SENTINEL):
            result = "checked, nothing to report (not posted to chat)"
            silent = True
        else:
            # 2026-08-14, Edvard: "Did you fix the notification for agora
            # heartbeats? So i can turn them off?" -- pushNotifications:false
            # on the heartbeat posts the reply without the phone buzz. The
            # message still lands in the conversation, same as quiet hours:
            # withholding the message instead would throw the cycle's reply
            # away, which is not what turning a notification off means.
            # Absent is true, so every heartbeat created before this field
            # keeps notifying, and only a literal false mutes -- matching the
            # `push === false` check the notify route already does.
            push = heartbeat.get("pushNotifications") is not False
            notify(conversation_id, reply, persona["name"], push=push)
            result = f"replied {len(reply)} chars"
            if may_go_silent:
                # Chip was withheld up front because this run might have
                # ended in silence. It didn't, so post it now — exactly
                # the old behaviour, for exactly the old reason.
                audit(persona["name"], conversation_id, "heartbeat", started_chip)
    except Exception as e:
        result = f"failed: {e}"[:200]
        log(f"heartbeat {heartbeat['name']} failed: {e}")

    # 2026-08-05, Edvard: "it is hard for me to know when you are done. I just
    # assume you are done when you post the final response and the Journal."
    # He was assuming correctly and had no way to confirm it. A cycle runs up
    # to ~45 minutes, and until now the thread's last entry during all of it
    # was the opening chip -- so "still working", "finished" and "died twenty
    # minutes ago" were indistinguishable from his phone. This closes the run
    # explicitly, and closes it on failure too, which is the case where he
    # would otherwise wait forever for a reply that is never coming.
    #
    # Silent monitoring runs stay silent: HEARTBEAT_NO_REPORT_SENTINEL exists
    # so a clean check leaves the chat untouched, and a "finished" chip every
    # 10 minutes is precisely the noise it is there to prevent. A silent run
    # posted no opening chip either, so there is nothing left dangling.
    if not silent:
        audit(persona["name"], conversation_id, "heartbeat",
              f"{heartbeat['name']} finished in {_elapsed(time.monotonic() - started_at)} — {result}")

    agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                   {"forceRun": False,
                    "lastRunAt": datetime.now(timezone.utc).isoformat(),
                    "lastResult": result})
    log(f"heartbeat {heartbeat['name']}: {result}")


# Decisions/0009 — heartbeat id -> Thread, module-level so it survives
# across ticks. The poll loop (poll_once/main) is otherwise fully
# sequential and blocking (one urllib call after another, no asyncio,
# no thread pool); a run that takes minutes must not stall every other
# conversation's turn-taking and every other heartbeat's schedule for
# that whole time.
#
# 2026-08-08: this now holds EVERY heartbeat's thread, not just
# workflow-mode ones. It was workflow-only because a workflow was the
# only thing expected to outlive a tick — but the Nova cycle is an
# ordinary heartbeat that runs the Claude CLI, and seven measured runs
# took 9m29s–21m44s (mean ~15m) each, all of it on the main thread with
# every other conversation frozen behind it. At the 6-hourly schedule
# that was ~4% of the day and nobody noticed. Edvard moved to a plan
# with 5x the limits and asked for the cycle rate to match, so the
# schedule went to every@72m@22:00 — 20 runs a day, which is ~21% of
# the day blocked, and up to 22 minutes of silence for anyone chatting
# with any other persona. Same fix as the workflow one, same guard,
# now applied to both paths.
#
# Those numbers are the 2026-08-09 cadence, kept because they are what
# motivated the fix. They are not today's: Edvard has changed the
# schedule four times since and it is every@60m@19:00 as of 2026-08-14.
# Nothing here reads the cadence — `schedule_minutes` is the one place
# that does — so this is a note, not a constant going stale.
_heartbeat_threads = {}


def join_running_heartbeats():
    """Block until every in-flight heartbeat run has finished.

    The drain in main.py protected the reply a cycle was in the middle
    of producing — but only while runs were synchronous, because "the
    tick in flight" and "the run in flight" were the same thing. Now
    that a run has its own thread, the tick returns immediately and the
    process would exit out from under a cycle that is minutes from
    posting. That is exactly the regression the drain was built for
    (issue #15, Cycles 3/20/21/22/23 each lost their reply to it), so
    the drain has to follow the work onto the thread.

    No timeout on purpose. The one real bound on a drain is
    terminationGracePeriodSeconds (2880s, agora-persona-runner-config),
    which k8s enforces with SIGKILL whatever we do here; a second,
    shorter bound invented in this file could only ever kill a cycle
    the platform was still willing to wait for. The synchronous drain
    this replaces had no timeout either, for the same reason."""
    for hb_id, thread in list(_heartbeat_threads.items()):
        if thread.is_alive():
            log(f"draining: waiting for heartbeat {hb_id} to finish")
            thread.join()


def workflow_bound_conversation_ids(heartbeats_list):
    """Conversation ids driven by an enabled, workflow-mode heartbeat.
    poll_once (2026-07-30) skips ordinary curator/@mention turn-taking
    for these entirely: a workflow step's own personaIds already decides
    who acts and when, so decide_turn's @mention-chain logic has nothing
    legitimate to do there -- and worse, can crash outright. Found live:
    a workflow persona's reply naturally included "@OtherPersona", the
    ordinary poll loop picked that up as a real mention and tried to
    continue the exchange via speak(), but a workflow-only conversation
    may never have a real Edvard message to anchor on (unlike one Edvard
    started himself) -- merge_history pops every leading non-user turn,
    so the history came back empty, speak() raised, and three such
    crashes auto-paused the conversation via what is now FAILURE_BACKOFF_CAP. The
    workflow engine's own turns are unaffected either way (run_workflow_steps
    already appends its own synthetic user turn every round)."""
    return {
        hb["conversationId"] for hb in heartbeats_list
        if hb.get("enabled") and hb.get("workflowId") and hb.get("conversationId")
    }


def current_cycle_conversation_ids(heartbeats_list):
    """Just the conversation each enabled rotating heartbeat is pointed
    at right now -- the live one, not its retired predecessors.

    Edvard, 2026-08-19: "since he now files ideas/issues/notes through
    the Nova app's own capture flow ... he no longer uses a heartbeat
    conversation transcript for routine notes. He now writes directly
    into a cycle conversation specifically when he wants to talk to the
    agent that had that session, and getting back only a 'Noted --
    carried into the next run' chip ... defeats that."

    That is him reversing the 2026-08-03 preference that
    cycle_bound_conversation_ids was built for, deliberately and with a
    reason: the accidental-trigger problem was that a routine note fired
    a full cycle, and routine notes do not land here any more. He scoped
    the revert himself -- "restore real-time replies for ONLY the single
    conversation a rotating heartbeat is CURRENTLY pointed at (the
    live/most-recent one). Every older/retired cycle conversation should
    keep today's defer + Noted-chip behavior untouched -- that part of
    PR#45 is still exactly the right fix, a message in a months-old
    thread should never fire a surprise full cycle."

    So this is the subtrahend, not a replacement: poll_once skips
    cycle_bound_conversation_ids() MINUS this set. Deliberately does not
    take `conversations` -- the whole point is that it never reaches
    back through the tag lookback the way its sibling does.

    **A heartbeat whose run is in flight is excluded, and that exclusion
    is the whole reason this is safe.** Runs have had their own thread
    since 2026-08-08, so `poll_once` keeps ticking every five seconds
    while a cycle executes -- and `rotate_cycle_conversation` PATCHes
    `conversationId` to the new transcript at the *start* of the run.
    Without this check, Edvard typing into the transcript of a cycle
    that is still working would have ordinary turn-taking call the
    bridge with the same `conversation_id` the running cycle is already
    resumed on, within five seconds, on the main thread. Two concurrent
    `--resume` calls against one CLI session, every tick until the
    backoff cap, with the real cycle's own turn in the middle of it.

    The first draft of this function had no such check and its docstring
    asserted the opposite -- "poll_once is blocked for the length of the
    run" -- which was true only before runs were threaded. Caught in
    review before merge; the fix is one `is_alive()`.

    While a run is in flight the conversation therefore stays in the
    deferred set: he gets the Noted chip, and pending_across_cycles
    carries his message into the next run exactly as it did before. That
    is the old behaviour, unchanged, for precisely the window where the
    old behaviour is the only correct one.
    """
    return {
        hb["conversationId"] for hb in heartbeats_list
        if hb.get("enabled") and hb.get("rotateConversationEachRun")
        and hb.get("conversationId") and not _run_in_flight(hb.get("id"))
    }


def _run_in_flight(heartbeat_id):
    """True while this heartbeat's own run thread is still going.

    `run_due_heartbeats` already consults `_heartbeat_threads` the same
    way to avoid starting a second run of the same heartbeat, so this
    reuses that registry rather than inventing a second notion of "busy"
    that could disagree with it.
    """
    if not heartbeat_id:
        return False
    thread = _heartbeat_threads.get(heartbeat_id)
    return thread is not None and thread.is_alive()


def cycle_bound_conversation_ids(heartbeats_list, conversations=()):
    """Conversation ids that are a cycle-transcript of an enabled,
    rotating heartbeat -- the one it currently points at, plus its
    still-un-archived older ones. poll_once skips ordinary turn-taking
    for all of these (2026-08-03), for a different reason than the
    workflow case above.

    Edvard's own report: "I just replied in the Agora conversation 5...
    that triggered a normal conversation run... that makes me not going
    to write messages again in the conversation." Typing one sentence
    into the live Evolve transcript fired an immediate, full,
    PR-opening Claude Code cycle -- so the app he built to talk to this
    loop is the one channel he'd stopped using, and he fell back to
    vault files.

    Skipping is only safe where his message is still guaranteed to be
    read, and for a rotating heartbeat it is: the next scheduled run
    rotates away from this conversation and run_heartbeat feeds its
    trailing unanswered text into the trigger via pending_across_cycles
    (#28/#30). So the message isn't dropped, it's deferred to the run
    that was going to happen anyway.

    Deliberately keyed on `rotateConversationEachRun`, not merely on
    being heartbeat-bound. Rotation is the explicit signal that a
    conversation is a per-cycle machine transcript rather than a
    durable channel; a non-rotating heartbeat's conversation (K3s
    Sentinel) is one Edvard may legitimately chat in and expect an
    ordinary answer from, and silencing that was not asked for.

    Older cycle-conversations used to keep ordinary turn-taking on
    purpose, because pending_across_cycles stopped its lookback at the
    first conversation where a persona had replied and so could not
    promise to reach them. An ordinary reply was the only thing that
    answered him there -- and for this loop an "ordinary reply" is a
    full Claude Code cycle. On 2026-08-05 he typed a one-line note about
    sealed secrets into a transcript from an earlier cycle and got
    exactly that: the whole machine, nine seconds later, spending real
    quota to answer something he had not asked to have answered now.

    pending_across_cycles now walks the full lookback, so the promise it
    could not make before it can make now, and these are skipped too.
    The invariant between the two functions: **every id skipped here
    must be one that walk reaches.** Both are bounded by
    CYCLE_LOOKBACK-many un-archived conversations per heartbeat, which
    is also conversation_rotation's retention, so the sets line up --
    but if either bound moves, move it in both places or his messages
    start vanishing again.

    2026-08-19: poll_once no longer skips ALL of these. It subtracts
    current_cycle_conversation_ids() and defers only the rest -- see that
    function for Edvard's ask. This function is unchanged and still
    returns the full set, because the invariant above is about the set
    the *walk* must reach, and the walk must still reach every one of
    them: while a run is in flight its own conversation drops back out
    of the live set (`_run_in_flight`), so a message typed mid-cycle is
    deferred and the current conversation still needs carrying.

    `conversations` is poll_once's existing listing, passed in rather
    than re-fetched; without it this degrades to the current-id-only
    behaviour."""
    ids = set()
    for hb in heartbeats_list:
        if not (hb.get("enabled") and hb.get("rotateConversationEachRun")):
            continue
        if hb.get("conversationId"):
            ids.add(hb["conversationId"])
        if not hb.get("id"):
            continue
        tag = cycle_tag(hb["id"])
        older = [
            c for c in conversations
            if c.get("id") and tag in (c.get("tags") or []) and not c.get("archived")
        ]
        older.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
        ids.update(c["id"] for c in older[:CYCLE_LOOKBACK])
    return ids


def run_due_heartbeats(heartbeats_list=None):
    if heartbeats_list is None:
        status, body = agora_internal("GET", "/heartbeats")
        if status != 200:
            return  # old Agora — feature not there yet
        heartbeats_list = body.get("heartbeats", [])
    now = datetime.now(timezone.utc)
    for heartbeat in heartbeats_list:
        try:
            # forceRun (POST /heartbeats/:id/run) must bypass enabled --
            # otherwise "run now" silently no-ops on a disabled heartbeat.
            due = heartbeat.get("forceRun") or (
                heartbeat.get("enabled") and schedule_due(
                    heartbeat.get("schedule", ""), heartbeat.get("lastRunAt"),
                    heartbeat.get("createdAt", now.isoformat()), now,
                )
            )
            if not due:
                continue
            # Runs off the main thread — see _heartbeat_threads' comment
            # above. In-flight guard: skip re-spawning if a prior run of
            # the SAME heartbeat hasn't finished yet. A run can
            # legitimately outlive its own schedule interval (a 5-minute
            # "every@1m" workflow; a Nova cycle that overruns its 72
            # minutes), and without this the claim PATCH is no defence —
            # it moves lastRunAt to the run's START, so an anchored
            # schedule's next slot reads as due while the run is still
            # going and would spawn a second one on top of it.
            hb_id = heartbeat["id"]
            existing = _heartbeat_threads.get(hb_id)
            if existing is not None and existing.is_alive():
                debug_log(f"heartbeat {hb_id} still running, skipping this tick")
                continue
            target = run_workflow_heartbeat if heartbeat.get("workflowId") else run_heartbeat
            thread = threading.Thread(target=target, args=(heartbeat,), daemon=True)
            _heartbeat_threads[hb_id] = thread
            thread.start()
        except Exception as e:
            log(f"heartbeat {heartbeat.get('name')} scheduling error: {e}")
