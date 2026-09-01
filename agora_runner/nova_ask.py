"""The Questions page: The owner types a question, a Sonnet persona answers it.

The owner's capture, `ideas.md` 2026-08-19: *"Make a questions page in Nova
where i can ask questions in a box and a Claude sonnet model answers me."*

**Almost nothing new happens on the answering side, and that is the whole
design.** `poll_once` already walks every conversation each tick, and
`decide_turn` already makes a persona speak whenever the last visible
message came from the owner. So a question posted into a conversation whose
curator is a Sonnet persona gets answered by the machinery that is already
running -- no model call in this process, no second answer path to keep in
step with `conversations.speak`, and nothing here to go stale when that
path changes.

Two consequences worth knowing before touching this:

- **The site pod cannot call a model even if a later cycle wants it to.**
  `nova-site` has `AGORA_TOKEN` and the two CouchDB secrets and nothing
  else -- no `CLAUDE_BRIDGE_URL`, no `CLAUDE_BRIDGE_TOKEN` (checked on the
  live deployment, Cycle 283). Answering here directly would need a config
  change in another repo. Going through Agora needs no new secret at all.
- **The answer is therefore not synchronous**, and the page polls for it.
  One poll tick plus one CLI turn is the latency, not one HTTP round trip.

`ANSWER_PERSONA_ID` is `claude-cli:claude-sonnet-5` on purpose. The
`claude-cli:` lane runs on the Claude Code subscription; `anthropic:` spends
the prepaid API balance on every question typed into the box. Do not
"simplify" this by pointing it at an `anthropic:` persona.

One conversation, not one per question. Follow-ups are the common case for
a question box ("why?", "show me the file"), and the bridge holds a
persistent CLI session per conversation id, so a single thread is what makes
"why?" mean anything. `MAX_THREAD` bounds what the page renders; the model's
own context is bounded by `conversations.FETCH_LIMIT` exactly as it is for
every other conversation.
"""

from agora_runner.nova_conversations import (
    ANSWER_PERSONA_ID, clamp_thread_limit, visible_rows)
from agora_runner.http_util import agora_get, agora_internal
from agora_runner.log import log


# Persona display name is "Nova", not "Nova Answers" -- the owner,
# 2026-08-30:
# talking to three differently-branded, differently-permissioned "Novas"
# (the cycle, the comment-reply turn, this one) read as three different
# people. The persona record's `name` and `personality` were updated live
# via the Agora API (not from this repo -- see the persona-store, not git,
# for the source of truth) to answer as one unified Nova with real write
# tools instead of a read-only companion. This constant just needs to
# stay in sync for anyone grepping the code for what the persona is called.
ANSWER_PERSONA_NAME = "Nova"
ASK_CONVERSATION_NAME = "Nova — Questions"

# Found by tag rather than by name, so renaming the conversation in the
# Agora UI does not silently start a second one underneath it.
ASK_TAG = "nova-ask"

MAX_QUESTION_CHARS = 4000

# Newest N rendered on the page. The thread is one long-lived conversation,
# so this is the only thing standing between the owner's phone and a year of
# transcript -- the same "what does this look like after 100 items" question
# that the Needs Edvard wall failed.  (not-prose: quoting a literal)
MAX_THREAD = 40


def _find_conversation():
    status, body = agora_get("/conversations")
    if status != 200:
        return None
    for c in body.get("conversations", []):
        if ASK_TAG in (c.get("tags") or []):
            return c.get("id")
    return None


def _create_conversation():
    status, created = agora_internal("POST", "/conversations", {
        "name": ASK_CONVERSATION_NAME,
        "personaId": ANSWER_PERSONA_ID,
    })
    if status not in (200, 201):
        log(f"nova_ask: create conversation failed HTTP {status}")
        return None
    new_id = (created.get("conversation") or {}).get("id")
    if not new_id:
        log("nova_ask: create response carried no conversation id")
        return None
    # The tag is what `_find_conversation` keys on next time, so a create
    # that lands and a tag that does not would make a fresh conversation
    # every question. Losing the id here is the one failure this cannot
    # repair later, so it is loud.
    status, _ = agora_internal("PATCH", f"/conversations/{new_id}", {"tags": [ASK_TAG]})
    if status not in (200, 201):
        log(f"nova_ask: could not tag conversation {new_id} (HTTP {status}) -- "
            "it will not be found again and a duplicate will be created")
    return new_id


def conversation_id(create=False):
    """The questions thread's id, or None. `create=True` only on the write
    path: a page that has never been used should render an empty thread,
    not manufacture a conversation for a reader who asked nothing."""
    found = _find_conversation()
    if found or not create:
        return found
    return _create_conversation()


def ask(text):
    """(ok, message). `message` is for the owner's screen on failure."""
    if not isinstance(text, str) or not text.strip():
        return False, "a question needs some text"
    text = text.strip()
    if len(text) > MAX_QUESTION_CHARS:
        return False, f"that is longer than {MAX_QUESTION_CHARS} characters"
    cid = conversation_id(create=True)
    if not cid:
        return False, "could not reach the conversation store"
    # sender="Edvard" is not decoration: `decide_turn` speaks only when the  (not-prose: quoting a literal)
    # last visible message came from him, so any other sender posts a
    # question that nothing ever answers.
    # `agora_internal` answers `(status, body)`, and the first version of
    # this unpacked the body straight into `message_id`. Nothing broke
    # visibly -- the page ignores this value on success -- but the guard
    # below then read "did Agora answer with a body at all", which is
    # always true, instead of "was a message actually appended". Caught by
    # running the live route rather than by a test: the smoke test's reply
    # carried the whole `{"status": "recorded", ...}` envelope where an id
    # should have been.
    status, body = agora_internal(
        "POST", f"/conversations/{cid}/notify",
        {"text": text, "sender": "Edvard", "system": False, "push": False},
    )
    message_id = (body.get("message") or {}).get("id")
    if status not in (200, 201) or not message_id:
        log(f"nova_ask: notify failed HTTP {status}")
        return False, "could not post the question"
    return True, message_id


def watching():
    """Tell Agora this thread is on his screen here, so it holds the buzz.

    His capture, `ideas.md` 2026-08-25: *"now when i use the new chat i get
    alerted by agora whenever a new message arrives. This is not a huge
    problem, but its not high quality of a product."* Agora's service worker
    already refuses to notify while its own app is visible; it cannot see a
    tab on this origin, so the page says so instead and `notify` withholds the
    push for `WATCHING_TTL_MS` (30s at the time of writing).

    `create=False` on purpose: a reader who has never asked anything has no
    thread to be present in, and manufacturing a conversation for a presence
    ping would be the same mistake `thread()` avoids below.

    (ok, reason). A refused ping means no suppression, so failing here can only
    ever cost a notification he was going to get anyway and the caller logs and
    moves on. The expensive direction is the other one -- vouching when he is
    not there -- and nothing on this path can do that.

    Costs a `GET /conversations` per call, since `conversation_id` resolves by
    tag. That is one extra listing per four-second poll tick and it is filed
    rather than cached: a module-level cache here would be a second place the
    thread's id lives, and the id is the one thing this module already goes to
    some trouble not to duplicate.
    """
    cid = conversation_id()
    if not cid:
        return False, "no questions thread yet"
    status, _body = agora_internal("POST", f"/conversations/{cid}/presence", {})
    if status not in (200, 201):
        log(f"nova_ask: presence ping failed HTTP {status}")
        return False, "could not reach the conversation store"
    return True, "watching"


def _progress(messages):
    """What the turn is doing right now, for the pending bubble, or None.

    His capture, `issues.md` 2026-08-30 12:56: *"I asked Nova for a status
    report, but it just says thinking for a long time. I need feedback. What
    is it doing? Did it even recieve my messages? What tools does it use? We
    have some of this in Agora, but not in Nova."*

    Three questions and this answers all three: `askedAt` is what the clock
    counts from, `latest` is the newest tool call, and `steps` is how many
    there have been since he spoke.

    **Read off the steps `visible_rows` already collected**, rather than from a
    second walk of the raw messages. That second walk is what this module
    used to hold -- its own copy of "which activity messages matter",
    beside `nova_conversations.visible_rows`'s -- and the two had to agree about
    narration for the page to render. One reader, one rule.
    """
    asked_at = ""
    steps = 0
    latest = None
    for row in messages:
        if row.get("sender") == "Edvard":
            # A new question resets the count, so a follow-up does not
            # inherit the previous turn's steps.
            asked_at = row.get("createdAt") or ""
            steps = 0
            latest = None
        for step in row.get("steps") or []:
            if step.get("kind") != "tool":
                continue
            steps += 1
            latest = {"capability": step.get("capability") or "",
                      "detail": step.get("input") or ""}
    return {"askedAt": asked_at, "steps": steps, "latest": latest}


def thread(limit=MAX_THREAD):
    """What the page renders: the visible tail of the questions thread.

    `nova_conversations.visible_rows` is the whole of the filtering, and it
    is imported rather than repeated: thinking, forgotten and system
    messages are dropped, and every tool call and mid-turn passage is folded
    into `steps` on the message it happened under, which the page draws as
    one collapsed line.
    """
    cid = conversation_id()
    if not cid:
        return {"conversationId": None, "messages": [], "waiting": False,
                "hasMore": False}
    # `limit + 1` and `hasMore` mean exactly what they mean in
    # `nova_conversations.thread`, and the clamp is imported from there
    # rather than re-derived: this is the same dock asking the same
    # question of a thread that happens to be found by tag instead of by id.
    limit = clamp_thread_limit(limit)
    status, detail = agora_get(f"/conversations/{cid}/messages?limit={limit + 1}")
    if status != 200:
        raise RuntimeError(f"conversation fetch returned {status}")
    has_more = len(detail.get("messages", [])) > limit
    messages = visible_rows(detail.get("messages", []))
    # The page needs to know whether to keep polling, and it cannot work
    # that out from the sender alone without re-deriving `decide_turn`.
    #
    # Deliberately blind to the steps-only row above: narration arriving
    # mid-turn would otherwise read as "the persona spoke last", and the page
    # would stop polling before the actual reply lands. It is evidence the
    # turn is still going, never that it finished.
    settled = [m for m in messages if not m["partial"]]
    waiting = bool(settled) and settled[-1]["sender"] == "Edvard"
    payload = {"conversationId": cid, "messages": messages[-limit:],
               "waiting": waiting, "hasMore": has_more}
    if waiting:
        # Only while the turn is running: a progress block on a finished
        # thread is a stale clock the page would keep counting up.
        payload["progress"] = _progress(messages)
    return payload
