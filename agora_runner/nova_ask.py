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

from agora_runner.http_util import agora_get, agora_internal
from agora_runner.log import log


ANSWER_PERSONA_ID = "8972a54d-cafa-4f07-a527-d8686cea51ca"
ANSWER_PERSONA_NAME = "Nova Answers"
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


def thread():
    """What the page renders: the visible tail of the questions thread.

    Activity, thinking, forgotten and system messages are dropped for the
    same reason `turns.build_history` drops them -- they are narration of
    the machinery, not the conversation. A cycle that wants to debug a turn
    has the Activity feed for that.
    """
    cid = conversation_id()
    if not cid:
        return {"conversationId": None, "messages": [], "waiting": False}
    status, detail = agora_get(f"/conversations/{cid}/messages?limit={MAX_THREAD}")
    if status != 200:
        raise RuntimeError(f"conversation fetch returned {status}")
    messages = [
        {
            "id": m.get("id"),
            "sender": m.get("sender") or "",
            "text": m.get("text") or "",
            "createdAt": m.get("createdAt") or "",
        }
        for m in detail.get("messages", [])
        if not (m.get("forgotten") or m.get("system")
                or m.get("activity") or m.get("thinking"))
    ]
    # The page needs to know whether to keep polling, and it cannot work
    # that out from the sender alone without re-deriving `decide_turn`.
    waiting = bool(messages) and messages[-1]["sender"] == "Edvard"
    return {"conversationId": cid, "messages": messages[-MAX_THREAD:], "waiting": waiting}
