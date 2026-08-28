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

from agora_runner.http_util import agora_get, agora_internal, agora_public
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
            "folderId": c.get("folderId") or "",
            "cycleThread": any(
                str(t).startswith("evolve-cycle:") for t in (c.get("tags") or [])),
        })
    # An ISO-8601 string from Agora, so a plain reverse sort is
    # chronological. A row with no timestamp sorts last rather than first:
    # an undated conversation is not fresh news.
    rows.sort(key=lambda r: r["updatedAt"] or "", reverse=True)
    return {"conversations": rows, "folders": _folder_rows(),
            "models": _model_rows()}


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

    `scheduled` is the same move for a different confusion, and it is his,
    `issues.md` #119 on 2026-08-28: the app is called Nova and one of the
    personas inside it is also called Nova, so *"it is easy to lose track
    of which 'Nova' a sentence means: the product, or the one persona."*
    The honest separator is not the name -- it is that some of these wake
    up on their own and the rest only answer when he types. Capabilities
    cannot tell them apart (Claude and Opus hold every capability Nova
    holds, plus one), so it is read off Agora's heartbeat registry, which
    is the thing that actually does the waking. Today that marks Nova, the
    build loop, and K3s Sentinel, the nightly cluster scan.

    A heartbeat listing that fails degrades to no flag rather than to no
    picker: this is the only route he can start a conversation from, and
    `issues.md` #118 was that route being unusable for a day.
    """
    status, body = agora_get("/personas")
    if status != 200:
        raise RuntimeError(f"persona listing returned {status}")
    scheduled = _personas_with_a_live_heartbeat()
    rows = [
        {
            "id": p.get("id"),
            "name": p.get("name") or "(unnamed)",
            "model": p.get("model") or "",
            "metered": str(p.get("model") or "").startswith("anthropic:"),
            "scheduled": p.get("id") in scheduled,
        }
        for p in body.get("personas", [])
        if p.get("id")
    ]
    rows.sort(key=lambda r: (r["metered"], r["name"].lower()))
    return {"personas": rows}


def _personas_with_a_live_heartbeat():
    """Which personas wake on their own, by id.

    Only an *enabled* heartbeat counts. A disabled one is a schedule
    nobody is running -- the four `Workflow trial` personas each carry
    one -- and marking those as scheduled would say the opposite of what
    the label means.
    """
    try:
        status, body = agora_get("/heartbeats")
    except Exception as err:                        # pragma: no cover - network
        log(f"nova_conversations: heartbeat listing failed: {err}")
        return set()
    if status != 200:
        log(f"nova_conversations: heartbeat listing returned {status}")
        return set()
    return {
        hb.get("personaId")
        for hb in (body.get("heartbeats") or [])
        if hb.get("enabled") and hb.get("personaId")
    }


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


# --- Managing a conversation from the dock -------------------------------
#
# His capture, `issues.md` 2026-08-27, rated 🔴 Immediately: *"I need the
# chat bubble to be able to start ned conversations, delete them, change
# name, organize like move to a folder. Editing by pressing the
# conversation for 1 sec and it gives me edit options."*
#
# Every one of these already existed in Agora and none of them was reachable
# from Nova. `PATCH /conversations/:id` takes `name` and `folderId`, the
# `/folders` routes are registered on both apps, and `DELETE
# /conversations/:id` is on the public app only -- so the only thing missing
# was this file and the four routes over it. Nothing new is invented here;
# the whole diff is a front end for writes Agora has been accepting all
# along.


def _folder_rows():
    """The folders, newest name-sorted, or `[]` if the store cannot say.

    Unlike `conversations()` this swallows a failed fetch rather than
    raising. A conversation list that fails is a blank switcher and has to
    be a visible error; a *folder* list that fails is a switcher with no
    groups in it, which still shows him every thread. Losing the grouping
    is worth not losing the list.
    """
    status, body = agora_get("/folders")
    if status != 200:
        log(f"nova_conversations: folder listing returned {status}")
        return []
    rows = [
        {"id": f.get("id"), "name": f.get("name") or "(unnamed)"}
        for f in body.get("folders", [])
        if f.get("id")
    ]
    rows.sort(key=lambda r: r["name"].lower())
    return rows


def _model_rows():
    """The model catalog Agora will accept, or `[]` if it cannot say.

    Swallows a failed fetch for `_folder_rows`' reason: a switcher with no
    model picker still shows him every thread, and losing the picker is
    worth not losing the list.

    `metered` comes straight from Agora's catalog rather than being derived
    here. `nova_conversations.personas` derives it from an `anthropic:`
    prefix because a persona row carries no such flag; the model catalog
    does, and re-deriving it would be a second copy of the rule that pays
    for the whole prepaid balance if it drifts.
    """
    status, body = agora_get("/models")
    if status != 200:
        log(f"nova_conversations: model listing returned {status}")
        return []
    rows = []
    for m in body.get("models", []):
        mid = m.get("id")
        if not mid:
            continue
        rows.append({
            "id": mid,
            "label": m.get("label") or mid,
            "metered": bool(m.get("metered")),
        })
    return rows


def set_model(conversation_id, model):
    """(ok, message). Point one thread at a different model.

    This is the last door on idea #95 slice 1. Agora moved `model` off the
    persona and onto the conversation on 08-21 (agora#65/#66), which is the
    thing he actually complained about -- *"it is hard to change model for a
    conversation because that means changing the model for all other
    conversations that personas is in"* -- and the write has been accepted
    ever since. Nothing in Nova ever called it, so from the app he reads,
    the model was still unchangeable.

    The catalog is not re-checked here on purpose. Agora validates against
    its own `VALID_MODEL_IDS` and answers 400, so a copy of that set in this
    file would be a second list to drift; the 400 is mapped to a sentence he
    can read instead.
    """
    if not conversation_id:
        return False, "which conversation?"
    if not isinstance(model, str) or not model.strip():
        return False, "which model?"
    model = model.strip()
    status, _body = agora_internal(
        "PATCH", f"/conversations/{conversation_id}", {"model": model})
    if status != 200:
        log(f"nova_conversations: set_model failed HTTP {status}")
        if status == 404:
            return False, "that conversation is gone"
        if status == 400:
            return False, "Agora does not have that model"
        return False, "could not change the model"
    return True, model


def rename(conversation_id, name):
    """(ok, message). Change what a thread is called.

    The same `MAX_NAME_CHARS` and the same emptiness check as `create`,
    because it is the same field -- a rename that could write a name
    `create` refuses would let him produce a conversation he could not have
    started.
    """
    if not conversation_id:
        return False, "which conversation?"
    if not isinstance(name, str) or not name.strip():
        return False, "a conversation needs a name"
    name = name.strip()
    if len(name) > MAX_NAME_CHARS:
        return False, f"that name is longer than {MAX_NAME_CHARS} characters"
    status, _body = agora_internal(
        "PATCH", f"/conversations/{conversation_id}", {"name": name})
    if status != 200:
        log(f"nova_conversations: rename failed HTTP {status}")
        if status == 404:
            return False, "that conversation is gone"
        return False, "could not rename the conversation"
    return True, name


def move(conversation_id, folder_id):
    """(ok, message). File a thread under a folder, or `""` for the top level.

    Agora refuses an unknown folder id with a 400 rather than storing it,
    which is the behaviour this relies on: a typo cannot file a
    conversation into a folder that does not exist and thereby hide it from
    every group the switcher draws.
    """
    if not conversation_id:
        return False, "which conversation?"
    if folder_id is None:
        folder_id = ""
    if not isinstance(folder_id, str):
        return False, "which folder?"
    folder_id = folder_id.strip()
    status, _body = agora_internal(
        "PATCH", f"/conversations/{conversation_id}",
        {"folderId": folder_id or None})
    if status != 200:
        log(f"nova_conversations: move failed HTTP {status}")
        if status == 404:
            return False, "that conversation is gone"
        if status == 400:
            return False, "that folder does not exist"
        return False, "could not move the conversation"
    return True, folder_id


def folder_create(name):
    """(ok, id-or-message). A new folder, or the existing one of that name.

    `POST /folders` is `ensure`, not `create`: the same name twice answers
    200 with the folder that already exists instead of 201 with a second
    one. Both are success here -- he asked for a folder with that name and
    there is now a folder with that name.
    """
    if not isinstance(name, str) or not name.strip():
        return False, "a folder needs a name"
    name = name.strip()
    if len(name) > MAX_NAME_CHARS:
        return False, f"that name is longer than {MAX_NAME_CHARS} characters"
    status, body = agora_public("POST", "/folders", {"name": name})
    if status not in (200, 201):
        log(f"nova_conversations: folder create failed HTTP {status}")
        return False, "could not create the folder"
    new_id = (body.get("folder") or {}).get("id")
    if not new_id:
        log("nova_conversations: folder create answered without an id")
        return False, "the conversation store answered without a folder id"
    return True, new_id


def remove(conversation_id):
    """(ok, message). Delete a thread for good.

    **This is the one irreversible call on the page and it is deliberately
    not softened into an archive.** He asked to delete them; Agora already
    unbinds any heartbeat pointing at the conversation before it goes, so
    the destructive edge this has -- a beat firing into a 404 -- is handled
    on that side rather than guessed at here. The confirmation is the
    page's job, and it asks.

    `DELETE /conversations/:id` is registered on the public app only, which
    is why this is the one write in this module that does not go through
    `agora_internal`.
    """
    if not conversation_id:
        return False, "which conversation?"
    status, _body = agora_public("DELETE", f"/conversations/{conversation_id}")
    if status not in (200, 204):
        log(f"nova_conversations: delete failed HTTP {status}")
        if status == 404:
            return False, "that conversation is already gone"
        return False, "could not delete the conversation"
    return True, "deleted"
