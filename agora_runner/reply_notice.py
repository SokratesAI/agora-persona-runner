"""Tell the owner when a cycle ran the whole hour and never answered him.

His issue #105, in his own words: *"Tell me when cycles go missing, instead
of waiting for me to ask."* Half of that row was already built --
`stall_notice` pushes a message when the loop stops writing journal entries
altogether -- and the other half of it, a watcher that survives the node
dying, is #103's and is blocked on a machine that does not exist yet.

**This is the third failure, and it was the one with nothing watching it.**
A cycle that runs, merges, writes its journal entry and then dies eighteen
seconds before it replies is not stalled: `lastWrittenAt` moved, so
`stall_notice` is correctly quiet, `cycle_health` counts the entry,
`workload_health` sees a healthy Pod and `gh pr checks` is green. From his
phone it is a thread of tool chips that stops. `tools.reply_health` measures
it exactly -- and it reports to *me*, once an hour, in a preflight sweep he
never sees. So the only way he learns a cycle never reached him is by
opening the thread and working it out, which is the sentence his row opens
with.

Measured 2026-08-31 by `reply_health`: two of the thirty cycle threads
nova-site lists went silent inside 24 hours (721 and 696), and neither was
relayed until a later cycle happened to read the sweep.

**Everything structural here is `stall_notice`'s and is deliberately
borrowed rather than rebuilt** -- it lives in the site process because the
runner is the thing that dies, it rides the shutdown loop rather than taking
a thread, it reads the bound conversation off the live heartbeat so the
message lands where he is reading, it honours `pushNotifications: false`,
and it posts as `Agora` with `system=True` so a later Nova turn cannot read
its own obituary as something it said. Those calls were argued out once in
that module and importing them is the point.

Three things that are this module's own:

**Dedupe is a set of conversation ids, not one key.** `stall_notice` has a
single stall to announce at a time; here several cycles can be silent at
once and each is its own message. A conversation id is permanent and unique
to one cycle, so an id already announced can never legitimately need a
second message -- and the 24h window in `reply_check` bounds the set rather
than letting it grow for the life of the process.

**A process restart re-arms it, same as `stall_notice`, and for the same
reason.** Bounding it properly means persisting state; the cost of being
wrong in this direction is a duplicate message about a real failure, and in
the other direction a stale file that silences the alarm for good.

**It reads the site's own payload builders, not the site's own HTTP.**
`nova_site` runs on a `ThreadingHTTPServer`, so a self-request would not
deadlock -- but it would still be this process asking itself a question over
a socket, and `conversation_list()` and `thread()` are the functions that
route answers with. `reply_health` keeps the HTTP path because it runs from
the bridge pod, where that is the only route there is.
"""

import time
from datetime import datetime, timezone

from agora_runner.log import log
from agora_runner.reply_check import (
    GRACE_MINUTES,
    WINDOW_HOURS,
    find_silences,
)
from agora_runner.stall_notice import nova_conversation


# Longer than `stall_notice`'s five minutes on purpose. A silent cycle
# cannot be detected until it is an hour old anyway -- that is
# `reply_check`'s grace -- so checking every five minutes buys at most five
# minutes of notice, and unlike a stall check this one fetches a thread per
# in-window cycle. Half an hour costs at most half an hour of latency on a
# failure that is already permanent.
REPLY_CHECK_SECONDS = 1800

# How long after start-up the *first* check runs. It used to be one whole
# interval, on the reasoning that a silent cycle is an hour old before it is
# detectable so half an hour of extra latency costs nothing. That reasoning
# is right about latency and wrong about frequency: it assumes the process
# outlives an interval. Measured 2026-09-06 across nova-site's last eleven
# rollouts (18:46Z-05:42Z), 5 of the 10 pod lifetimes were under 30 minutes
# -- 16, 16, 17, 26 and 26 -- so half the pods armed the watch and died
# without ever reaching a check. `_checked_at` resets on every restart, so
# this is not a delay, it is a check that does not happen at all.
#
# The docstring's actual reason for not checking on the first tick is that it
# lands about a second after `start_nova_site()` kicks off the cache-warming
# thread. That wants seconds, not half an hour.
REPLY_WARM_UP_SECONDS = 60


def notice_text(silence):
    """What he reads on his phone. One cycle, named, with what it was doing.

    The narration is quoted because it is the only thing that says what the
    cycle was in the middle of, and it is what makes the message actionable
    rather than an alarm -- 721's last words were "Both images built green.
    Writing my reply now."
    """
    name = silence.name or "A cycle"
    lines = [
        f"{name} finished without ever replying to you.",
        "",
        "It ran, and the thread it left you is all narration — no answer at "
        "the end. Its journal entry is the record of what it actually did, "
        "and the next cycle will relay it.",
    ]
    narration = silence.narration
    if narration:
        lines += ["", f"The last thing it said was: {narration!r}"]
    lines += ["", "One message per cycle. You will not get this one again."]
    return "\n".join(lines)


def due(silences, announced):
    """The `(id, text)` pairs to post now, oldest thread first.

    A pure function of the verdict and the set of ids already announced, so
    the thing that rings his phone is testable without a clock or a socket.
    """
    pending = [s for s in silences if s.id and s.id not in announced]
    pending.sort(key=lambda s: s.updated_at)
    return [(s.id, notice_text(s)) for s in pending]


class ReplyWatch:
    """Checks for a silent cycle on a schedule, one message per cycle ever.

    The four collaborators are injected so a test can drive the whole path
    -- including the rate limiter and the dedupe -- without a network. The
    defaults are the production wiring and are imported lazily, because
    importing `nova_conversations` at module scope would make this module
    unusable from the runner process for no reason.
    """

    def __init__(self, listing=None, fetch_thread=None, heartbeats=None,
                 post=None, interval=REPLY_CHECK_SECONDS,
                 grace_minutes=GRACE_MINUTES, window_hours=WINDOW_HOURS,
                 clock=None, warm_up=REPLY_WARM_UP_SECONDS):
        # `clock` is wall time and is separate from `tick`'s `now`, which is
        # monotonic and only ever answers "is a check due". The two gates
        # are measured against Agora's stamps, so a test that fixes one and
        # not the other judges its own fixtures against the real date.
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._listing = listing or _live_listing
        self._fetch_thread = fetch_thread or _live_thread
        self._heartbeats = heartbeats or _live_heartbeats
        self._post = post or _live_post
        self._interval = interval
        self._warm_up = warm_up
        self._grace_minutes = grace_minutes
        self._window_hours = window_hours
        self._announced = set()
        self._checked_at = None
        # The wait before the next check: the warm-up until one has run,
        # the full interval afterwards.
        self._due_in = warm_up

    def tick(self, now=None):
        """Do a check if one is due. Returns the number of messages posted.

        **The first tick after construction checks nothing**, for
        `stall_notice.tick`'s reason: it lands about a second after
        `start_nova_site()` kicks off the cache-warming thread, and this
        check reaches Agora for a conversation listing. It waits
        `REPLY_WARM_UP_SECONDS` rather than a whole interval, because half
        of this pod's lifetimes are shorter than an interval -- see the
        measurement on that constant.

        Never raises. This runs inside the site's shutdown loop, and a
        transient failure reaching Agora must cost a check, not the process
        serving the owner's app.
        """
        now = time.monotonic() if now is None else now
        if self._checked_at is None:
            self._checked_at = now
            return 0
        if now - self._checked_at < self._due_in:
            return 0
        self._checked_at = now
        self._due_in = self._interval
        try:
            found = find_silences(
                self._listing(), self._fetch_thread, now=self._clock(),
                grace_minutes=self._grace_minutes,
                window_hours=self._window_hours)
            for note in found.notes:
                log(f"reply notice: could not read {note}")
            # One line per completed check, whether or not anything was
            # found. Without it a check that ran and saw nothing is
            # byte-identical in the log to a check that never ran -- which is
            # exactly how the `conversation_list` ImportError below stayed
            # invisible for six days, and that at least logged something.
            # The counts are `Silences`' own and are not decoration: `judged`
            # is what separates "every cycle spoke" from "the listing came
            # back empty", and an empty listing raises nothing at all, so it
            # is the next way this watch can go quiet without failing.
            log(f"reply notice: checked {found.judged} thread(s) in window, "
                f"{found.live} still inside the {self._grace_minutes}m grace, "
                f"{found.old} older than {self._window_hours}h, "
                f"{found.unreadable} unreadable, "
                f"{len(found.silent)} silent")
            pending = due(found.silent, self._announced)
            if not pending:
                return 0
            conversation_id, push = nova_conversation(self._heartbeats())
            if not conversation_id:
                log("reply notice: no conversation bound to Nova's heartbeat")
                return 0
            posted = 0
            for silent_id, text in pending:
                status = self._post(conversation_id, text, push)
                if status not in (200, 201):
                    # Deliberately not recorded as announced: a post that
                    # failed sent nothing, so the next check should try again
                    # rather than treat this cycle as reported.
                    log(f"reply notice: post returned {status}")
                    continue
                self._announced.add(silent_id)
                posted += 1
                log(f"reply notice posted for silent cycle {silent_id}")
            return posted
        except Exception as error:  # noqa: BLE001 -- see docstring
            log(f"reply notice check failed: {error!r}")
            return 0


def _live_listing():
    # `conversations`, not `conversation_list`. That name has never existed
    # in `nova_conversations`; `nova_site` imports this same function *as*
    # `conversation_list` for its route, and this module copied the alias
    # instead of the name. Every check raised ImportError into `tick`'s
    # blanket `except`, so the watch ran on schedule and reported nothing
    # from the day it shipped -- see the tests below, which call these
    # four functions instead of injecting past them.
    from agora_runner.nova_conversations import conversations

    return conversations()


def _live_thread(conversation_id):
    from agora_runner.nova_conversations import thread

    return thread(conversation_id)


def _live_heartbeats():
    from agora_runner.stall_notice import _live_heartbeats as heartbeats

    return heartbeats()


def _live_post(conversation_id, text, push):
    from agora_runner.stall_notice import _live_post as post

    return post(conversation_id, text, push)
