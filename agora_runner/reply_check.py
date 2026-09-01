"""Which finished cycles never said anything to the owner.

The invariant is one line and it is `tools.reply_health`'s: **a finished
cycle thread contains at least one message that is not narration.**
Narration -- the tool chips and the prose a cycle streams while it works --
is marked `partial` by `nova_conversations`; the reply is the message that
is not. A thread whose every message is `partial` is a cycle that talked to
itself for an hour and then stopped. Since Cycle 780 that narration arrives
as one `partial` row carrying `steps` rather than as a row per passage, so
`replied` is unchanged and `last_narration` reads the steps.

**This module holds the judgement and nothing else, because it now has two
callers that reach the data by different routes.** `tools.reply_health` asks
nova-site over HTTP from the bridge pod, which is right for a check a cycle
runs by hand. `agora_runner.reply_notice` runs *inside* nova-site and calls
`nova_conversations.conversation_list` / `.thread` directly -- no socket, and
in particular no HTTP request from the site process into its own server. The
rule they share is this file; a second copy of "every message is partial"
living in the notifier is exactly the duplication `prompt.md` step 2 says to
delete rather than to guard.

The two gates are the design and they are both deliberate:

- A thread younger than `grace_minutes` is not judged. A cycle in flight has
  no reply yet *by construction*, and both callers run while one is in
  flight -- without the gate every run would report itself.
- A thread older than `window_hours` is not judged. A missed reply is
  permanent; there is no fix to ship, only a relay to make. An unbounded
  window would hand every future caller the same names forever.
"""

from datetime import datetime, timedelta, timezone

# A turn is killed at 45 minutes and the cadence is 30, so an hour is past
# the longest a live cycle can still be owing a reply.
GRACE_MINUTES = 60
WINDOW_HOURS = 24


def parse_stamp(stamp):
    """Agora's ISO stamps, as an aware UTC datetime, or None."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def cycle_threads(listing):
    """The cycle threads in a conversation listing.

    `cycleThread` is the site's own flag for a thread a heartbeat opened,
    so this never has to parse a name. A thread the owner started is
    not owed a reply by anybody and is not judged here.
    """
    conversations = (listing or {}).get("conversations") or []
    return [c for c in conversations if c.get("cycleThread")]


def replied(thread):
    """True when the thread holds a message that is not narration."""
    for message in (thread or {}).get("messages") or []:
        if not message.get("partial"):
            return True
    return False


def last_narration(thread):
    """The last thing the cycle said before it stopped, for the relay.

    **The words moved and this had to follow them.** Cycle 780 collapsed a
    turn's passages into `steps` on the row they belong to
    (`nova_conversations.visible_rows`), so the trailing row of a cycle that
    narrated and never replied now carries `text: ""` and its prose one level
    down. Reading `text` alone returned the empty string for exactly the
    case this function exists to describe -- a silent cycle -- and the push
    to his phone dropped its "the last thing it said was" line without
    saying anything was missing. Caught by my reviewer, not by me.

    So: the row's own text when it has any, and otherwise the last thought in
    its steps. A tool call is not something the cycle *said* and is skipped;
    "it ran `kubectl get pods`" is not the sentence he is owed."""
    for message in reversed((thread or {}).get("messages") or []):
        text = (message.get("text") or "").strip()
        if text:
            return text
        for step in reversed(message.get("steps") or []):
            if step.get("kind") == "thought" and (step.get("text") or "").strip():
                return step["text"].strip()
    return ""


def judge(conversation, now, grace_minutes, window_hours):
    """`"live"`, `"old"`, `"judge"` or `"unreadable"` for one thread.

    Split out from the sweep because the two gates are the whole design and
    a reader should be able to see them without the I/O around them.
    """
    updated = parse_stamp(conversation.get("updatedAt"))
    if updated is None:
        return "unreadable"
    if now - updated < timedelta(minutes=grace_minutes):
        return "live"
    if now - updated > timedelta(hours=window_hours):
        return "old"
    return "judge"


class Silence:
    """One cycle that finished without answering the owner."""

    def __init__(self, conversation, narration):
        self.conversation = conversation
        self.narration = narration

    @property
    def id(self):
        return self.conversation.get("id") or ""

    @property
    def name(self):
        return self.conversation.get("name") or ""

    @property
    def updated_at(self):
        return self.conversation.get("updatedAt") or ""


class Silences:
    """The verdict over a whole listing: who was silent, and what was skipped.

    The counts are not decoration. `unreadable` is what stops a failed fetch
    reading as a clean sweep, and `judged` is what stops an empty window
    reading as "every cycle spoke" -- both callers need that distinction and
    neither should re-derive it.
    """

    def __init__(self):
        self.silent = []
        self.judged = 0
        self.live = 0
        self.old = 0
        self.unreadable = 0
        self.notes = []


def find_silences(listing, fetch_thread, now=None,
                  grace_minutes=GRACE_MINUTES, window_hours=WINDOW_HOURS):
    """Judge every cycle thread in `listing`, fetching only the ones in window.

    `fetch_thread(conversation_id)` returns a thread payload or raises; the
    caller decides whether that is an HTTP call or an in-process one. An
    exception is counted as unreadable rather than allowed out, because one
    thread that will not load must not silence the verdict on the rest.
    """
    now = now or datetime.now(timezone.utc)
    result = Silences()
    for conversation in cycle_threads(listing):
        verdict = judge(conversation, now, grace_minutes, window_hours)
        if verdict == "unreadable":
            result.unreadable += 1
            result.notes.append(
                f"{conversation.get('name')} carries no timestamp.")
            continue
        if verdict == "live":
            result.live += 1
            continue
        if verdict == "old":
            result.old += 1
            continue
        try:
            thread = fetch_thread(conversation.get("id"))
        except Exception as error:  # noqa: BLE001 -- see docstring
            result.unreadable += 1
            result.notes.append(f"{conversation.get('name')} ({error}).")
            continue
        result.judged += 1
        if not replied(thread):
            result.silent.append(Silence(conversation, last_narration(thread)))
    return result
