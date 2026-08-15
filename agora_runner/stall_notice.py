"""Tell Edvard when the loop has stopped, because silence looks like a quiet hour.

The open half of his issue #70. Detection has been built for days --
`cycle_health.stalled_for` counts whole heartbeat intervals since the newest
journal entry, and `nova_site._with_silence` stamps `stalled` onto the status
payload on every request. What was missing is that nothing *tells* him. A
cycle that dies posts no reply, and no reply is exactly what a quiet hour
looks like from a phone; the badge only says so to somebody who has already
gone and looked at the page.

**This lives in the site process, not the runner, and that is the whole
point.** The runner is the thing that stalls. A notifier inside it is asleep
in precisely the case it exists for. `nova-site` is a separate Deployment on
the same image with its own lifecycle, it already carries `AGORA_TOKEN` and
`AGORA_INTERNAL_URL`, and it stays up through the 2880s drain the runner's
`Recreate` rollout sits in -- so it can speak when the loop cannot.

**Dedupe is keyed on the entry, never on the clock.** The failure to be
afraid of here is not a missed alarm, it is a phone that buzzes every time
some loop comes round -- which would make the one signal worth having the
first thing he mutes. So the key is `lastWrittenAt`, the write time of the
newest journal entry: while the loop is down that stamp does not move, so
every subsequent check finds the same key and posts nothing. It moves again
only when a cycle actually writes, which is the same event that ends the
stall. One message per stall falls out of that rather than being counted,
and it survives being checked once a second or once an hour.

A process restart re-arms the key, so a pod that is redeployed mid-stall can
send a second message. That is deliberate: bounding it properly means
persisting the key, and the cost of being wrong in this direction is one
extra message during an outage he is already being told about, against the
cost in the other direction of a state file that goes stale and silences the
alarm for good.
"""

import time

from agora_runner.log import log


# How long between checks. A stall is measured in heartbeat intervals -- so
# tens of minutes -- and the dedupe key means checking often costs nothing
# but a dict lookup. Five minutes is a compromise in favour of the message
# arriving close to when the grace window is actually crossed.
STALL_CHECK_SECONDS = 300


def nova_conversation(heartbeats):
    """`(conversation_id, push)` for Nova's cycle heartbeat, or `(None, True)`.

    Read from the live heartbeat rather than configured, because the
    heartbeat rotates Nova into a new conversation every cycle -- an id
    baked into a manifest would be correct for one hour and then point at
    a conversation Edvard has stopped reading.

    **`push` comes off the same heartbeat, and the first version of this
    threw it away.** `pushNotifications: false` is the switch Edvard asked
    for on 2026-08-14 and got the same day (#178): a muted heartbeat still
    posts, it just does not buzz. A notice that ignores it defeats the mute
    for the one case a mute cannot distinguish from nothing happening,
    which is silence -- the exact thing this module exists to report.
    Absent is `True`, matching `heartbeats.run_heartbeat`: only a literal
    `false` mutes, so every heartbeat created before that field keeps
    notifying.

    The heartbeat filter is `cycle_health.nova_cycle_heartbeats` rather
    than a third hand-written copy of the same three conditions.
    """
    from agora_runner.cycle_health import nova_cycle_heartbeats

    for heartbeat in nova_cycle_heartbeats(heartbeats):
        if heartbeat.get("conversationId"):
            return (heartbeat["conversationId"],
                    heartbeat.get("pushNotifications") is not False)
    return None, True


def notice_text(status):
    """What he reads on his phone. Plain, and it says what to do about it.

    `silentIntervals` is whole heartbeat intervals, which is the unit the
    stall is judged in and therefore the only one that cannot disagree with
    the badge on the page.
    """
    cycle = status.get("cycle")
    intervals = status.get("silentIntervals")
    when = status.get("lastWokeTime") or ""
    date = status.get("lastWokeDate") or ""
    stamped = " ".join(part for part in (date, when) if part)
    who = f"Cycle {cycle}" if cycle is not None else "the last cycle"
    plural = "interval" if intervals == 1 else "intervals"
    lines = [
        f"Nova has stopped writing. The last journal entry is {who}'s"
        + (f", written at {stamped}" if stamped else "")
        + f", and that is {intervals} heartbeat {plural} ago with nothing since.",
        "",
        "A dead cycle posts no reply, so silence looks the same as a quiet "
        "hour from your phone — this message is the difference. Worth a look "
        "at the agora-persona-runner pod.",
        "",
        "No more of these until a cycle writes again.",
    ]
    return "\n".join(lines)


def due(status, notified_key):
    """`(key, text)` if a stall notice should be posted now, else `None`.

    The whole decision, as a pure function of the status payload and the
    key of the last notice sent, so the thing that actually rings his phone
    is testable without a clock, a socket or a process.
    """
    if not status.get("stalled"):
        return None
    key = status.get("lastWrittenAt") or ""
    if not key:
        # No usable write time means nothing to judge and nothing to dedupe
        # on. `stalled` cannot be true without one today, and if that ever
        # changes, sending an undedupeable message every check is the one
        # outcome worth refusing outright.
        return None
    if key == notified_key:
        return None
    return key, notice_text(status)


class StallWatch:
    """Checks for a stall on a schedule and posts at most one message per stall.

    `check`, `heartbeats` and `post` are injected so a test can drive the
    whole path -- including the rate limiter -- without a network. The
    defaults are the production wiring and are imported lazily, because
    importing `nova_site` at module scope would make this module unusable
    from the runner process for no reason.
    """

    def __init__(self, check=None, heartbeats=None, post=None,
                 interval=STALL_CHECK_SECONDS):
        self._check = check or _live_status
        self._heartbeats = heartbeats or _live_heartbeats
        self._post = post or _live_post
        self._interval = interval
        self._notified = None
        self._checked_at = None

    def tick(self, now=None):
        """Do a check if one is due. Returns True if a message was posted.

        **The first tick after construction checks nothing**, and that is
        not laziness. `_live_status` asks `cached_payload("journal", ...)`,
        which on a cold cache is a 3-5s vault fetch and parse -- and the
        first tick lands about a second after `start_nova_site()` has kicked
        off the warming thread, so the two would contend on `_build_lock`
        and this one would block the loop that watches for SIGTERM for the
        whole window. Waiting one interval costs at most five minutes of
        notice on a stall that was already underway before the pod booted,
        against blocking the site's shutdown path on every single start.

        Never raises. This runs inside the site's shutdown loop, and a
        transient failure reaching Agora or the vault must cost a check,
        not the process serving Edvard's app.
        """
        now = time.monotonic() if now is None else now
        if self._checked_at is None:
            self._checked_at = now
            return False
        if now - self._checked_at < self._interval:
            return False
        self._checked_at = now
        try:
            verdict = due(self._check(), self._notified)
            if verdict is None:
                return False
            key, text = verdict
            conversation_id, push = nova_conversation(self._heartbeats())
            if not conversation_id:
                log("stall notice: no conversation bound to Nova's heartbeat")
                return False
            status = self._post(conversation_id, text, push)
            if status not in (200, 201):
                # Deliberately not recorded as notified: a post that failed
                # sent nothing, so the next check should try again rather
                # than treat this stall as announced.
                log(f"stall notice: post returned {status}")
                return False
            self._notified = key
            log(f"stall notice posted for entry written at {key}")
            return True
        except Exception as error:  # noqa: BLE001 -- see docstring
            log(f"stall notice check failed: {error!r}")
            return False


def _live_status():
    """The same status `/api/journal` publishes, age and all.

    The age is not optional here and this is the caller where it costs the
    most. `_with_silence` measures a live `now` against a cached
    `lastWrittenAt`, so a rebuild that keeps failing reads as a loop that
    stopped writing -- and unlike the badge, this consumer *pushes to
    Edvard's phone*. Without the age it would tell him the loop is dead
    because this process lost its own connection to the vault, which is
    the one false alarm that costs the notice its credibility.

    `stalled` is already false when the record is stale, so `_notice_for`
    needs no change: it returns early on exactly that flag.
    """
    from agora_runner.nova_site import _with_silence, cached_entry, journal_payload

    payload, _body, _etag, age = cached_entry("journal", journal_payload)
    return _with_silence(payload.get("status", {}), record_age=age)


def _live_heartbeats():
    from agora_runner.http_util import agora_internal

    status, body = agora_internal("GET", "/heartbeats")
    if status != 200:
        return []
    return body.get("heartbeats") or []


def _live_post(conversation_id, text, push):
    from agora_runner.conversations import notify

    # `system=True` keeps this out of every LLM context (turns.py), so a
    # later Nova turn cannot read "Nova has stopped writing" as its own
    # prior utterance. Sender is "Agora" rather than "Nova" for the same
    # reason `conversations.back_off` uses it: the loop is dead, so this is
    # the platform speaking about Nova, not Nova speaking.
    status, _message_id = notify(conversation_id, text, "Agora", system=True, push=push)
    return status
