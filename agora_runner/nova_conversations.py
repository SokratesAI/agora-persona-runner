"""The Conversations page: every Agora thread, readable and writable from Nova.

His capture, `ideas.md` 2026-08-25: *"Maybe replecate and build in the
conversation functionality into Nova ... Make a page for heartbeats and also
maybe make a page for conversations ... its basicly a chat app with multiple
conversations history and i can start new ones etc."*

Cycle 439 answered the bigger half of that capture -- don't merge the two
apps, make Nova the only front end and leave Agora underneath as the engine
-- and this is the first half of what that answer costs. Agora keeps every
conversation; the only reason he still opens its app is that nothing here
renders them.

**Nothing new happens on the answering side, exactly as `nova_ask` says of
itself.** `poll_once` walks every conversation each tick and `decide_turn`
makes the curator speak whenever the last visible message came from him, so
a message posted here is answered by machinery that is already running. This
module is a reader plus one `notify` call.

The difference from `nova_ask` is only that the conversation id is a
parameter rather than a constant found by tag. That is what "multiple
conversations history" means, and it is why `thread` here takes an id and
`nova_ask.thread` does not.

**`send` posts as the owner** for `nova_ask.ask`'s reason and not as a
convention: `decide_turn` speaks only when the last visible message came
from him, so any other sender writes a message nothing ever answers.
"""

from agora_runner.http_util import agora_get, agora_internal
from agora_runner.log import log


# Newest N messages rendered when a thread is opened. `nova_ask.MAX_THREAD`
# is 40 and this is the same number for the same reason -- it is what stands
# between his phone and a year of transcript.
MAX_THREAD = 40

MAX_MESSAGE_CHARS = 4000
MAX_NAME_CHARS = 200

# Threads the app runs for its own machinery rather than for him. Hiding
# them is deliberately *not* done here: he asked for the conversation
# history, and a list that silently drops rows is the same failure as the
# 400-chip cap. The tag is passed through so the page can label them.
NOVA_ASK_TAG = "nova-ask"


def _visible(messages):
    """Drop narration of the machinery, keep the conversation.

    Same filter as `nova_ask.thread` and `turns.build_history`: activity,
    thinking, forgotten and system messages are how the loop talks to
    itself, not what he said or what was said back.
    """
    return [
        {
            "id": m.get("id"),
            "sender": m.get("sender") or "",
            "text": m.get("text") or "",
            # Agora's `/messages` calls this `ts`; `createdAt` is accepted
            # too so a caller holding a message from `/conversations` (which
            # does use `createdAt`) is not silently undated. Measured
            # against the live store, Cycle 441.
            "createdAt": m.get("ts") or m.get("createdAt") or "",
        }
        for m in messages
        if not (m.get("forgotten") or m.get("system")
                or m.get("activity") or m.get("thinking"))
    ]


def conversations():
    """Every conversation Agora holds, newest activity first.

    Raises rather than returning an empty list on a failed fetch: an empty
    list and an unreachable store render identically as "no conversations",
    and those mean opposite things. The route turns this into a 502 he can
    read.
    """
    status, body = agora_get("/conversations")
    if status != 200:
        raise RuntimeError(f"conversation listing returned {status}")
    rows = []
    for c in body.get("conversations", []):
        cid = c.get("id")
        if not cid:
            continue
        if c.get("archived"):
            continue
        curator = ""
        for p in c.get("personas") or []:
            if p.get("role") == "curator" or not curator:
                curator = p.get("name") or ""
        rows.append({
            "id": cid,
            "name": c.get("name") or "(unnamed)",
            # A conversation carries a `personas` list, not a `personaId`.
            # The first version of this read `personaId` and `personaName`
            # off the row and got "" for every conversation in the store --
            # the page would have rendered 454 threads with nobody in them.
            # Measured against the live listing, Cycle 441.
            "personaName": curator,
            "model": c.get("model") or "",
            "tags": c.get("tags") or [],
            "updatedAt": c.get("lastMessageAt") or c.get("createdAt") or "",
            "cycleThread": any(
                str(t).startswith("evolve-cycle:") for t in (c.get("tags") or [])),
        })
    # An ISO-8601 string from Agora, so a plain reverse sort is
    # chronological. A row with no timestamp sorts last rather than first:
    # an undated conversation is not fresh news.
    rows.sort(key=lambda r: r["updatedAt"] or "", reverse=True)
    return {"conversations": rows}


def thread(conversation_id, limit=MAX_THREAD):
    """The visible tail of one conversation.

    `waiting` is `nova_ask.thread`'s flag and means the same thing: the last
    visible message is his, so a reply is expected and the page should keep
    polling. Deriving it here rather than in the page keeps one copy of a
    rule that really lives in `decide_turn`.
    """
    if not conversation_id:
        return {"conversationId": None, "messages": [], "waiting": False}
    status, detail = agora_get(
        f"/conversations/{conversation_id}/messages?limit={int(limit)}")
    if status != 200:
        raise RuntimeError(f"conversation fetch returned {status}")
    messages = _visible(detail.get("messages", []))
    waiting = bool(messages) and messages[-1]["sender"] == "Edvard"
    return {
        "conversationId": conversation_id,
        "messages": messages[-int(limit):],
        "waiting": waiting,
    }


def send(conversation_id, text):
    """(ok, message). `message` is for his screen on failure."""
    if not conversation_id:
        return False, "which conversation?"
    if not isinstance(text, str) or not text.strip():
        return False, "a message needs some text"
    text = text.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        return False, f"that is longer than {MAX_MESSAGE_CHARS} characters"
    status, body = agora_internal(
        "POST", f"/conversations/{conversation_id}/notify",
        {"text": text, "sender": "Edvard", "system": False, "push": False},
    )
    # `push: False` for the reason Cycle 439 built presence for -- he is
    # looking at the thread he just typed into, and Agora cannot see this
    # tab. Notifying him of his own message would be the wrapper problem
    # again.
    message_id = (body.get("message") or {}).get("id")
    if status not in (200, 201) or not message_id:
        log(f"nova_conversations: notify failed HTTP {status}")
        return False, "could not post the message"
    return True, message_id


def create(name, persona_id):
    """(ok, id-or-message). Starting a new thread from the page.

    No tag is written. `nova_ask` tags its one conversation because it has
    to find that same thread again next time; a conversation he starts here
    is found by being in the list, and inventing a tag for it would put a
    second name on something Agora already identifies by id.
    """
    if not isinstance(name, str) or not name.strip():
        return False, "a conversation needs a name"
    name = name.strip()
    if len(name) > MAX_NAME_CHARS:
        return False, f"that name is longer than {MAX_NAME_CHARS} characters"
    if not isinstance(persona_id, str) or not persona_id.strip():
        return False, "pick who you are talking to"
    status, created = agora_internal("POST", "/conversations", {
        "name": name,
        "personaId": persona_id.strip(),
    })
    if status not in (200, 201):
        log(f"nova_conversations: create failed HTTP {status}")
        return False, "could not start the conversation"
    new_id = (created.get("conversation") or {}).get("id")
    if not new_id:
        log("nova_conversations: create response carried no conversation id")
        return False, "the conversation store answered without an id"
    return True, new_id


def personas():
    """Who he can start a conversation with.

    The provider string is passed through so the page can show it. A
    metered `anthropic:` persona is a real thing in this store and
    `identity.md` rule 9 forbids *defaulting* onto it, not seeing it -- so
    it is labelled rather than hidden, which is what lets him make the
    choice knowingly instead of having it made for him silently.
    """
    status, body = agora_get("/personas")
    if status != 200:
        raise RuntimeError(f"persona listing returned {status}")
    rows = [
        {
            "id": p.get("id"),
            "name": p.get("name") or "(unnamed)",
            "model": p.get("model") or "",
            "metered": str(p.get("model") or "").startswith("anthropic:"),
        }
        for p in body.get("personas", [])
        if p.get("id")
    ]
    rows.sort(key=lambda r: (r["metered"], r["name"].lower()))
    return {"personas": rows}


def watching(conversation_id):
    """Tell Agora this conversation is on his screen here, so it holds the buzz.

    `nova_ask.watching` does the same thing for the questions thread and has
    since Cycle 439. It resolves its conversation by tag, so it can only ever
    vouch for that one thread -- and the dock stopped being a single-thread
    panel when it grew a conversation switcher (runner#408). Since then the
    poll tick has vouched only while the ask thread was open, on purpose,
    because vouching for the wrong conversation drops a notification he wanted.
    The consequence is that his original complaint is still live everywhere
    else: read a heartbeat's thread in the dock and Agora buzzes the phone
    about a message already on the screen.

    Agora's `mark()` was always keyed by conversation id, so nothing over
    there has to change -- this is the caller finally naming which one.

    (ok, reason). A refused ping means no suppression, so failing here costs
    at most a notification he was going to get anyway. The expensive direction
    is vouching when he is not there, and nothing on this path can do that:
    the id comes from the thread the dock is painting, and Agora refuses an id
    that is not a conversation rather than marking blind.
    """
    if not conversation_id:
        return False, "which conversation?"
    status, _body = agora_internal(
        "POST", f"/conversations/{conversation_id}/presence", {})
    if status not in (200, 201):
        log(f"nova_conversations: presence ping failed HTTP {status}")
        return False, "could not reach the conversation store"
    return True, "watching"
