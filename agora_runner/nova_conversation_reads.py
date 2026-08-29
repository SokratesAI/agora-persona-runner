"""Which conversations have an answer he has not looked at yet.

His capture, `ideas.md` 2026-08-29: *"The floating chat bubble in the Nova
app will then get highlighted whenever a response is in, and the
conversations that has unread answers are at the top of the list of
conversations, also highlighted. So the list of conversations is ordered by
newest message in them."*

The list is already ordered by newest message (`nova_conversations.conversations`
sorts on `lastMessageAt`). The half that did not exist is *unread*, and the
reason is that **Agora does not know**: measured Cycle 635 against the live
store, a row in `GET /conversations` carries `archived createdAt folderId id
lastMessageAt memory model name personality personas rootId status
stickyFallback tags thinking` and nothing about having been read. The presence
ping `nova_conversations.watching` sends does suppress a push, but it is
fire-and-forget and never comes back out of the listing. So the marker is
ours to keep, and this module is the whole of keeping it.

**A marker is the timestamp of the newest message he has seen in a thread,
not the moment he opened it.** That is what makes the write cheap enough to
put on the thread route: the dock polls a thread every few seconds, and
stamping "now" each time would be one vault write per poll forever, while
stamping the newest message only writes when a new message has actually
arrived. `mark_seen` returns `False` when nothing changed and does no I/O.

**A conversation with no marker at all is read, not unread**, and that
choice is the difference between a useful page and an unusable one: 653
conversations were in the store when this was built and he has opened a
handful of them here, so treating "never opened in Nova" as unread would
light up the entire list on the first load and mean nothing. The store
therefore also carries `since` -- stamped the first time it is written --
and an unmarked conversation counts as unread only if its newest message
landed *after* that. So the page starts quiet and every answer that arrives
from then on is real signal.

The boundary this leaves, written down rather than papered over: a message
he sends from **Agora's own app** does not stamp anything here, so his own
reply over there reads as an unread answer until he opens the thread in
Nova. Nothing in the listing says who spoke last, and asking per
conversation is 653 requests, so this is not a thing to fix by guessing.
"""

import json

from agora_runner.log import log
from agora_runner.vault import vault_read_path_rev, vault_write_path


READS_PATH = ("projects/sokrates/projects/agora/nova/resources/"
              "conversation-reads.json")

# Same retry count and the same reason as `nova_comments.add_reply`: a 409
# means somebody wrote in between, and re-reading is the whole fix.
WRITE_ATTEMPTS = 3


def parse_reads(raw):
    """`(since, {conversation_id: newest_seen_iso})` from the stored document.

    Anything unreadable is an empty store rather than an exception. A missing
    marker file and a corrupt one both mean "I know of nothing he has seen",
    which is the safe direction here -- it under-reports unread rather than
    lighting up every row.
    """
    if not raw:
        return "", {}
    try:
        doc = json.loads(raw)
    except ValueError:
        log("nova_conversation_reads: stored markers are not JSON")
        return "", {}
    if not isinstance(doc, dict):
        return "", {}
    since = doc.get("since") or ""
    seen = doc.get("seen")
    if not isinstance(seen, dict):
        seen = {}
    return (since if isinstance(since, str) else ""),  {
        k: v for k, v in seen.items() if isinstance(k, str) and isinstance(v, str)}


def render_reads(since, seen):
    """The document as it is stored. Sorted so a diff of it is readable."""
    return json.dumps(
        {"since": since, "seen": {k: seen[k] for k in sorted(seen)}},
        indent=2, ensure_ascii=False) + "\n"


def is_unread(conversation_id, last_message_at, since, seen):
    """Is there something in this thread he has not seen?

    ISO-8601 from Agora throughout, so string comparison is chronological --
    the same assumption `nova_conversations.conversations` already sorts on.
    An undated conversation is never unread: there is nothing to compare, and
    guessing would put a row with no news at the top of his list.
    """
    if not last_message_at:
        return False
    marker = seen.get(conversation_id)
    if marker:
        return last_message_at > marker
    return bool(since) and last_message_at > since


def load_reads():
    """`(since, seen)` from the vault, or an empty store if it cannot be read.

    **Never raises.** `nova_conversations.conversations` calls this on the
    page he opens most, and the listing itself is deliberately strict --
    it turns an unreachable Agora into a 502 rather than an empty list,
    because those mean opposite things. This is the opposite case and the
    opposite call: a marker document that will not load costs him some
    highlights, and refusing to render 653 conversations over that would
    be the tail wagging the dog.
    """
    try:
        raw, _rev = vault_read_path_rev(READS_PATH)
    except Exception as e:
        log(f"nova_conversation_reads: could not read markers: {e}")
        return "", {}
    return parse_reads(raw)


def mark_seen(conversation_id, newest_at, now_iso):
    """Record that he has seen everything up to `newest_at` in one thread.

    Returns `True` only when a write actually happened. No conversation id,
    no timestamp, or a marker that is already at least this new, all mean
    there is nothing to store -- and that no-op is what makes this safe to
    call from the polling thread route.

    `now_iso` seeds `since` the first time the store is written, so every
    conversation that has been quiet since before this feature existed stays
    quiet. It is a parameter rather than a `utcnow()` call so the test can
    say what "now" is.
    """
    if not conversation_id or not newest_at:
        return False
    for _ in range(WRITE_ATTEMPTS):
        raw, rev = vault_read_path_rev(READS_PATH)
        since, seen = parse_reads(raw)
        if seen.get(conversation_id, "") >= newest_at:
            return False
        seen[conversation_id] = newest_at
        result = vault_write_path(
            READS_PATH, render_reads(since or now_iso, seen), if_rev=rev)
        if result == "written":
            return True
        if "409" not in str(result):
            log(f"nova_conversation_reads: could not store marker: {result}")
            return False
    log("nova_conversation_reads: lost the write race three times")
    return False
