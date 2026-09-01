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

from agora_runner.audit import fold_text_streams, narration_passage
from agora_runner.config import NOVA_PERSONA_ID
from agora_runner.http_util import agora_get, agora_internal, agora_public
from agora_runner.log import log
from agora_runner.nova_conversation_reads import is_unread, load_reads


# Newest N messages rendered when a thread is opened. `nova_ask.MAX_THREAD`
# is 40 and this is the same number for the same reason -- it is what stands
# between his phone and a year of transcript.
MAX_THREAD = 40

# The ceiling on a `limit` the page may ask for when he scrolls back through
# a thread. His capture, `issues.md` 2026-08-31: *"I can only see the latest
# messages in the chat. I can't scroll upwards and see the earlier
# messages."* `MAX_THREAD` above is what a thread *opens* on; without a way
# to ask for more it was also all he could ever reach, and 79 of the 720
# conversations in the store hold more than 40 messages (measured against
# the live store 2026-08-31, from the runner pod).
#
# 500 rather than a round number: the longest thread in the store that
# morning was 500 messages, so this admits every conversation that exists
# whole and still bounds a `?limit=` somebody typed by hand. It is a bound
# on one fetch, not on what he can read -- the page pages.
MAX_THREAD_CEILING = 500

# The one persona this app talks to. `nova_ask` re-exports it under its
# own name; it lives here because `nova_ask` already imports from this
# module and the other direction would be a cycle.
ANSWER_PERSONA_ID = "8972a54d-cafa-4f07-a527-d8686cea51ca"

MAX_MESSAGE_CHARS = 4000
MAX_NAME_CHARS = 200

# What a thread is called before it has been about anything. His capture,
# `issues.md` #139: *"I have to type a conversation title before I can even
# start it, but I don't always know what it'll be about. Want it to work like
# the official Claude app: starts as 'New chat', auto-titles itself after the
# first message."* This is the exact string `autotitle` will overwrite and
# nothing else -- a thread he named himself is never renamed under him.
UNTITLED_NAME = "New chat"

# How long a derived title may be. Far below `MAX_NAME_CHARS`, because this
# one is read in a switcher row on a 360px phone rather than typed by him.
TITLE_CHARS = 60

# Threads the app runs for its own machinery rather than for him. Hiding
# them is deliberately *not* done here: he asked for the conversation
# history, and a list that silently drops rows is the same failure as the
# 400-chip cap. The tag is passed through so the page can label them.
NOVA_ASK_TAG = "nova-ask"


def keep_only_live_passages(rows):
    """Drop the mid-turn passages of every turn except the one still running.

    A passage is worth seeing while it is the newest thing in the thread --
    that is the whole point, the answer arriving instead of four minutes of
    nothing. Once the turn's real reply lands underneath it, it is the same
    words a second time and it costs the thread its whole visible tail: one
    Nova cycle writes hundreds of them, and `MAX_THREAD` is 40, so a day-old
    cycle would push every actual sentence he wrote off the page.

    So partials survive only in the run at the very end of the thread, after
    the last settled message. `nova_ask.thread` imports this rather than
    keeping a second copy -- the rule is one rule, and the two pages
    disagreeing about it is the bug this function exists to not have.
    """
    last_settled = -1
    for i, row in enumerate(rows):
        if not row.get("partial"):
            last_settled = i
    return [row for i, row in enumerate(rows)
            if i > last_settled or not row.get("partial")]


def _visible(messages):
    """Drop narration of the machinery, keep the conversation.

    Same filter as `nova_ask.thread` and `turns.build_history`: activity,
    thinking, forgotten and system messages are how the loop talks to
    itself, not what he said or what was said back.

    One activity message is kept, and it is the reason this is a loop rather
    than the comprehension it used to be. A persona's reply is written in
    passages with tool calls between them, and each passage is pushed into
    the conversation the moment it is written -- so the answer is already
    arriving here, in pieces, while the turn runs. Dropping those passages
    with the tool chips is what made a four-minute turn look like nothing
    followed by one block of text (issue #129). `audit.narration_passage`
    is the test; `partial` marks them for the page, which draws them as the
    reply taking shape rather than as separate finished messages.
    """
    out = []
    for m in fold_text_streams(messages):
        if m.get("forgotten") or m.get("system") or m.get("thinking"):
            continue
        passage = narration_passage(m)
        if m.get("activity") and passage is None:
            continue
        out.append({
            "id": m.get("id"),
            "sender": m.get("sender") or "",
            "text": passage if passage is not None else (m.get("text") or ""),
            # Agora's `/messages` calls this `ts`; `createdAt` is accepted
            # too so a caller holding a message from `/conversations` (which
            # does use `createdAt`) is not silently undated. Measured
            # against the live store, Cycle 441.
            "createdAt": m.get("ts") or m.get("createdAt") or "",
            "partial": passage is not None,
        })
    return out


def visible_persona_name(persona_id, name):
    """The persona to write on a row, or "" when the persona is Nova itself.

    Idea #95, slice 4: *"We might revisor the personas, but more of a simple
    pre-written prompt that get injected and thats it."* One persona per
    conversation has been enforced since `agora#67`, and 687 of the 700
    conversations Agora holds carry this same persona -- so the word "Nova"
    was printed on the meta line of nearly every conversation row and nearly
    every heartbeat row in an app that is Nova-only. A label that is the same
    on every row tells the reader nothing; it is only the 13 rows answered by
    somebody else that carry information.

    Matched on the id, not on the name, and that is not pedantry: two
    distinct personas in the live store are both called "Nova"
    (`08ffac94...`, on 687 conversations, and `8972a54d...`, on 2). Matching
    the string would blank a persona that genuinely is not me.

    The three render sites in `app.js` join their meta line with
    `.filter(Boolean)`, so an empty name simply drops out and the model or
    the schedule stands alone. Nothing on the client side changes.
    """
    if (persona_id or "") == NOVA_PERSONA_ID:
        return ""
    return name or ""


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
        curator_id = ""
        for p in c.get("personas") or []:
            if p.get("role") == "curator" or not curator:
                curator = p.get("name") or ""
                curator_id = p.get("personaId") or ""
        rows.append({
            "id": cid,
            "name": c.get("name") or "(unnamed)",
            # A conversation carries a `personas` list, not a `personaId`.
            # The first version of this read `personaId` and `personaName`
            # off the row and got "" for every conversation in the store --
            # the page would have rendered 454 threads with nobody in them.
            # Measured against the live listing, Cycle 441.
            "personaName": visible_persona_name(curator_id, curator),
            "model": c.get("model") or "",
            "tags": c.get("tags") or [],
            "updatedAt": c.get("lastMessageAt") or c.get("createdAt") or "",
            "folderId": c.get("folderId") or "",
            "cycleThread": any(
                str(t).startswith("evolve-cycle:") for t in (c.get("tags") or [])),
        })
    # His capture, 2026-08-29: *"the conversations that has unread answers
    # are at the top of the list of conversations, also highlighted."* The
    # markers are ours, because Agora's listing carries no read state at all
    # -- see `nova_conversation_reads` for the measurement and for why an
    # unmarked conversation counts as read.
    since, seen = load_reads()
    for row in rows:
        row["unread"] = is_unread(row["id"], row["updatedAt"], since, seen)
    # An ISO-8601 string from Agora, so a plain reverse sort is
    # chronological. A row with no timestamp sorts last rather than first:
    # an undated conversation is not fresh news. Unread rows go above the
    # rest and stay newest-first among themselves, which is what he asked
    # for -- the ordering rule is not replaced, it gains a first key.
    rows.sort(key=lambda r: (r["unread"], r["updatedAt"] or ""), reverse=True)
    return {"conversations": rows, "folders": _folder_rows(),
            "models": _model_rows()}


def clamp_thread_limit(limit):
    """A `?limit=` off the wire, made into a number this app will fetch.

    Anything unreadable falls back to `MAX_THREAD` rather than raising: the
    caller is the page asking for a thread, and a typo in a query string
    should show him the thread, not an error.
    """
    try:
        wanted = int(limit)
    except (TypeError, ValueError):
        return MAX_THREAD
    if wanted < MAX_THREAD:
        return MAX_THREAD
    return min(wanted, MAX_THREAD_CEILING)


def thread(conversation_id, limit=MAX_THREAD):
    """The visible tail of one conversation.

    `waiting` is `nova_ask.thread`'s flag and means the same thing: the last
    visible message is his, so a reply is expected and the page should keep
    polling. Deriving it here rather than in the page keeps one copy of a
    rule that really lives in `decide_turn`.
    """
    if not conversation_id:
        return {"conversationId": None, "messages": [], "waiting": False,
                "hasMore": False}
    limit = clamp_thread_limit(limit)
    # One more than he asked for, and that extra row is the whole of
    # `hasMore`: Agora answers with the *newest* N, so being handed N+1 means
    # there is at least one message older than the page, and being handed
    # fewer means the page is the whole thread.
    status, detail = agora_get(
        f"/conversations/{conversation_id}/messages?limit={limit + 1}")
    if status != 200:
        raise RuntimeError(f"conversation fetch returned {status}")
    raw = detail.get("messages", [])
    # Counted on the raw rows rather than the visible ones on purpose.
    # `_visible` drops narration, and a page whose older half is all
    # narration would report "nothing older" while older messages exist.
    # The question this answers is "is another fetch worth making", which is
    # about what Agora holds, not about what survives the filter.
    has_more = len(raw) > limit
    messages = keep_only_live_passages(_visible(raw))
    # Blind to the partial passages `_visible` now keeps, for the reason
    # `nova_ask.thread` spells out: a passage arriving mid-turn is evidence
    # the turn is still running, and reading it as "answered" would stop the
    # page polling before the real reply lands.
    settled = [m for m in messages if not m.get("partial")]
    waiting = bool(settled) and settled[-1]["sender"] == "Edvard"
    return {
        "conversationId": conversation_id,
        "messages": messages[-limit:],
        "waiting": waiting,
        # Whether scrolling to the top of the thread should fetch again.
        "hasMore": has_more,
        # What the caller stamps as seen. Settled only, for `waiting`'s
        # reason one line up: a passage arriving mid-turn is the reply still
        # being written, and marking it seen would clear the highlight
        # before the answer exists.
        "newestAt": settled[-1]["createdAt"] if settled else "",
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


def starting_name(name):
    """What a thread he is starting is called before anyone has typed a title.

    One rule in one place: `create` writes this into the store and the route
    answers with it, so the name the page paints in the header is the name
    the store holds. Computing it twice is how a header ends up saying
    something the switcher does not.
    """
    return (name or "").strip() or UNTITLED_NAME


def title_from_message(text):
    """A conversation title derived from the first thing he said in it.

    Deliberately mechanical rather than a model call. Rule 9 in
    `identity.md` forbids production work on the metered API, and the
    subscription path costs a whole turn of a cycle's window to name a
    thread -- so this takes his own opening line, which is what he would
    have typed into the box anyway.

    `""` means "I could not make a title out of that", never a bad one: the
    caller leaves the thread on `UNTITLED_NAME`, which is honest, where a
    title cut out of an emoji or a bare URL would be worse than no title.
    """
    if not isinstance(text, str):
        return ""
    # The first sentence or the first line, whichever ends sooner. He opens
    # with the topic and then explains it; the explanation is not the title.
    head = text.strip().split("\n", 1)[0]
    for stop in (". ", "? ", "! "):
        cut = head.find(stop)
        if cut > 0:
            head = head[:cut + 1]
    head = " ".join(head.split())
    # A markdown attachment line is the page's own text, not his.
    if head.startswith("!["):
        return ""
    head = head.strip(" \t-*#>`\"'")
    if len(head) > TITLE_CHARS:
        # Cut on a word boundary when there is one anywhere near the end, so
        # a title never ends mid-word; a 60-character run with no space in it
        # is a URL or a token and is cut where it falls.
        clipped = head[:TITLE_CHARS]
        space = clipped.rfind(" ")
        head = (clipped[:space] if space >= TITLE_CHARS // 2 else clipped).rstrip()
        head += "\u2026"
    # A title has to be readable as words. One that is only punctuation or
    # emoji tells him nothing the placeholder did not.
    if not any(c.isalnum() for c in head):
        return ""
    return head


def autotitle(conversation_id, current_name, text):
    """(ok, message). Name an untitled thread after the first thing he said.

    `current_name` is what the page believes the thread is called, and this
    refuses unless it is exactly `UNTITLED_NAME`. That check is the whole
    safety of the route: a thread he named is never renamed under him. It is
    a claim from the page rather than a fact read from the store, and that is
    deliberate -- Agora publishes no `GET /conversations/{id}` (measured, it
    404s), so the only way to read one name is to list all 700, which is the
    single most expensive call this app makes. The page is not being trusted
    with any authority it did not already have: `/api/conversations/rename`
    lets it rename any thread to anything.
    """
    if not conversation_id:
        return False, "which conversation?"
    if current_name != UNTITLED_NAME:
        return False, "that conversation already has a name"
    title = title_from_message(text)
    if not title:
        return False, "there was no title in that message"
    return rename(conversation_id, title)


def create(name):
    """(ok, id-or-message). Starting a new thread from the page.

    Always with `ANSWER_PERSONA_ID`. There is no longer anyone else to
    start one with: `issues.md` #119, 2026-08-29 -- *"drop the Agora
    multi-persona chat picker from the Nova app entirely, the app should
    be Nova only, no Claude/Opus/Gemini/Haiku/Study buddy tabs inside
    it"*. The threads he already has with those personas are untouched
    and still open from the list; what is gone is starting a new one.

    No tag is written. `nova_ask` tags its one conversation because it has
    to find that same thread again next time; a conversation he starts here
    is found by being in the list, and inventing a tag for it would put a
    second name on something Agora already identifies by id.
    """
    if name is not None and not isinstance(name, str):
        return False, "a conversation needs a name"
    # A blank name is his answer to "what is it about?" when he does not know
    # yet, and it is the common case -- so it starts a thread rather than
    # refusing one. `rename` deliberately still refuses a blank: emptying the
    # name of a thread that has one leaves a row he cannot find again.
    name = starting_name(name)
    if len(name) > MAX_NAME_CHARS:
        return False, f"that name is longer than {MAX_NAME_CHARS} characters"
    status, created = agora_internal("POST", "/conversations", {
        "name": name,
        "personaId": ANSWER_PERSONA_ID,
    })
    if status not in (200, 201):
        log(f"nova_conversations: create failed HTTP {status}")
        return False, "could not start the conversation"
    new_id = (created.get("conversation") or {}).get("id")
    if not new_id:
        log("nova_conversations: create response carried no conversation id")
        return False, "the conversation store answered without an id"
    return True, new_id


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
    from an `anthropic:` prefix here: the catalog carries the flag, and
    re-deriving it would be a second copy of the rule that pays for the
    whole prepaid balance if it drifts.
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
