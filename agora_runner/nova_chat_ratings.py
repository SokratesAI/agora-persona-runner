"""Thumbs up / thumbs down on an answer in Nova's chat -- issue #143's last
control.

His issue lists *"thumbs up/down feedback"* beside stop, edit, regenerate,
copy and the model picker. The others act on the thread; this one only has
to be kept somewhere a later cycle can read it, so it is a ledger in the
vault beside `app-opens.json`, not a field on Agora's message: Agora's
conversations are append-only and have no route that annotates a message.

**One row per message, and the newest tap wins.** Tapping the other thumb
changes the row, tapping the same one again clears it -- the client sends
`rating: null` for that -- so the document says what he thinks of each
answer now rather than every time he changed his mind. The first stretch of
the answer is kept with the row, so a reader of the ledger can see what was
rated without fetching the conversation.
"""
import json
from datetime import datetime, timezone

CHAT_RATINGS_PATH = "projects/sokrates/projects/agora/nova/resources/chat-ratings.json"

RATINGS = ("up", "down")

#: The longest id or answer excerpt kept. All three come from a request
#: body, so an unbounded one would be written into the vault verbatim.
MAX_TEXT = 300


def load(text):
    """The rows in a ledger document; `[]` for an empty or new one.

    A document that is present and not the expected shape raises, because
    reading it as empty would let the next `record` overwrite it."""
    if not (text or "").strip():
        return []
    rows = json.loads(text).get("ratings")
    if not isinstance(rows, list):
        raise ValueError(f"{CHAT_RATINGS_PATH} has no `ratings` list")
    return rows


def dumps(rows):
    return json.dumps({"ratings": rows}, indent=1) + "\n"


def parse(payload):
    """A request body -> `(conversation_id, message_id, rating, text)`, or
    None when it is not a rating. `rating` is "up", "down" or None (cleared)."""
    if not isinstance(payload, dict):
        return None
    cid, mid = payload.get("conversationId"), payload.get("messageId")
    if not isinstance(cid, str) or not cid.strip() or not isinstance(mid, str) or not mid.strip():
        return None
    rating = payload.get("rating")
    if rating is not None and rating not in RATINGS:
        return None
    text = payload.get("text")
    text = text[:MAX_TEXT] if isinstance(text, str) else ""
    return cid[:MAX_TEXT], mid[:MAX_TEXT], rating, text


def fold(rows, conversation_id, message_id, rating, text, now=None):
    """`rows` with this message's rating set, or removed when `rating` is None."""
    now = now or datetime.now(timezone.utc)
    kept = [r for r in rows
            if not (r.get("conversationId") == conversation_id and r.get("messageId") == message_id)]
    if rating is not None:
        kept.append({"conversationId": conversation_id, "messageId": message_id,
                     "rating": rating, "text": text, "at": now.isoformat(timespec="seconds")})
    return kept


def record(conversation_id, message_id, rating, text, read=None, write=None, now=None):
    """Set one rating. Read-modify-write on the revision it read, retried
    once: a lost race re-reads rather than overwriting the other writer."""
    if read is None or write is None:
        from agora_runner.vault import vault_read_path_rev, vault_write_path
        read = read or vault_read_path_rev
        write = write or vault_write_path
    for attempt in (1, 2):
        doc, rev = read(CHAT_RATINGS_PATH)
        rows = fold(load(doc), conversation_id, message_id, rating, text, now)
        try:
            write(CHAT_RATINGS_PATH, dumps(rows), if_rev=rev)
            return
        except Exception:
            if attempt == 2:
                raise
