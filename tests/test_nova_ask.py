"""nova_ask.py -- the Questions page's server half.

The owner's capture, ideas.md 2026-08-19: *"Make a questions page in Nova
where i can ask questions in a box and a Claude sonnet model answers me."*

What is worth pinning here is not "the HTTP calls happen" -- it is the
three things that would silently stop an answer ever arriving, each of
which looks fine from the outside:

- the question is posted as `sender="Edvard"`, because `decide_turn` speaks  (not-prose: quoting a literal)
  only for a message from him and any other sender posts into silence;
- the conversation is found by tag and created at most once, because a
  find that misses makes a fresh conversation per question and quietly
  throws away every follow-up's context;
- the answering persona is a `claude-cli:` one, because an `anthropic:`
  persona spends the prepaid API balance on every question typed.
"""
from unittest.mock import patch

import agora_runner.nova_ask as nova_ask


def _fakes(conversations, messages=None, create_id="c-new"):
    """(get, internal) doubles over the two Agora helpers, plus the call log."""
    calls = []

    def fake_get(path):
        calls.append(("GET", path, None))
        if path == "/conversations":
            return 200, {"conversations": conversations}
        if path.startswith("/conversations/"):
            return 200, {"messages": messages or []}
        return 404, {}

    def fake_internal(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path == "/conversations":
            return 201, {"conversation": {"id": create_id}}
        if method == "POST" and path.endswith("/notify"):
            return 201, {"message": {"id": "m-1"}}
        return 200, {}

    return fake_get, fake_internal, calls


def _run(fn, conversations, messages=None, create_id="c-new"):
    get, internal, calls = _fakes(conversations, messages, create_id)
    with patch.object(nova_ask, "agora_get", side_effect=get), \
         patch.object(nova_ask, "agora_internal", side_effect=internal):
        return fn(), calls


TAGGED = [{"id": "c-ask", "tags": [nova_ask.ASK_TAG]}]


def test_the_answering_persona_is_on_the_subscription_lane_not_the_metered_api():
    """The one fact in this module that costs real money if it drifts. A
    persona id cannot be checked from here, so this pins the intent beside
    it: the module names the CLI lane and says so."""
    assert "claude-cli" in nova_ask.__doc__
    assert "anthropic:" in nova_ask.__doc__


def test_a_question_is_posted_as_edvard():
    (ok, message_id), calls = _run(lambda: nova_ask.ask("why is the loop slow?"), TAGGED)
    assert ok
    # `agora_internal` answers `(status, body)`, and the first version of
    # ask() unpacked the body into what it called `message_id` -- so this
    # came back as the whole `{"status": "recorded", ...}` envelope and the
    # "was a message appended" guard below was really asking "did Agora
    # answer at all". Found by curling the live route, not by a test.
    assert message_id == "m-1"
    notify = [c for c in calls if c[1].endswith("/notify")]
    assert len(notify) == 1
    assert notify[0][1] == "/conversations/c-ask/notify"
    assert notify[0][2]["sender"] == "Edvard"
    assert notify[0][2]["text"] == "why is the loop slow?"


def test_an_existing_thread_is_reused_rather_than_replaced():
    _, calls = _run(lambda: nova_ask.ask("second question"), TAGGED)
    assert [c for c in calls if c[0] == "POST" and c[1] == "/conversations"] == []


def test_the_first_question_creates_the_thread_and_tags_it():
    (ok, _), calls = _run(lambda: nova_ask.ask("first question"), [])
    assert ok
    created = [c for c in calls if c[0] == "POST" and c[1] == "/conversations"]
    assert len(created) == 1
    assert created[0][2]["personaId"] == nova_ask.ANSWER_PERSONA_ID
    # Without this the next question finds nothing and creates a second
    # conversation, which loses every follow-up's context silently.
    tagged = [c for c in calls if c[0] == "PATCH"]
    assert tagged and tagged[0][2]["tags"] == [nova_ask.ASK_TAG]
    assert [c for c in calls if c[1].endswith("/notify")][0][1] == "/conversations/c-new/notify"


def test_a_conversation_carrying_another_tag_is_not_the_questions_thread():
    _, calls = _run(lambda: nova_ask.ask("hello"), [{"id": "c-cycle", "tags": ["cycle-nova"]}])
    assert [c for c in calls if c[0] == "POST" and c[1] == "/conversations"]


def test_empty_and_oversized_questions_are_refused_without_reaching_agora():
    for bad in ("", "   ", "\n"):
        (ok, message), calls = _run(lambda: nova_ask.ask(bad), TAGGED)
        assert not ok and "needs some text" in message
        assert calls == []
    long = "x" * (nova_ask.MAX_QUESTION_CHARS + 1)
    (ok, message), calls = _run(lambda: nova_ask.ask(long), TAGGED)
    assert not ok and "longer than" in message
    assert calls == []


def test_a_reader_who_asked_nothing_does_not_manufacture_a_conversation():
    payload, calls = _run(nova_ask.thread, [])
    assert payload == {"conversationId": None, "messages": [], "waiting": False}
    assert [c for c in calls if c[0] == "POST"] == []


def test_the_thread_drops_machinery_messages_and_keeps_the_conversation():
    messages = [
        {"id": "1", "sender": "Edvard", "text": "how many pods?", "createdAt": "t1"},
        {"id": "2", "sender": "Nova Answers", "text": "running a tool", "activity": True},
        {"id": "3", "sender": "Nova Answers", "text": "thinking out loud", "thinking": True},
        {"id": "4", "sender": "system", "text": "joined", "system": True},
        {"id": "5", "sender": "Edvard", "text": "typo", "forgotten": True},
        {"id": "6", "sender": "Nova Answers", "text": "Seven.", "createdAt": "t2"},
    ]
    payload, _ = _run(nova_ask.thread, TAGGED, messages)
    assert [m["id"] for m in payload["messages"]] == ["1", "6"]
    assert payload["messages"][1]["text"] == "Seven."


def test_waiting_is_true_only_while_an_answer_is_owed():
    asked = [{"id": "1", "sender": "Edvard", "text": "q", "createdAt": "t1"}]
    payload, _ = _run(nova_ask.thread, TAGGED, asked)
    assert payload["waiting"] is True

    answered = asked + [{"id": "2", "sender": "Nova Answers", "text": "a", "createdAt": "t2"}]
    payload, _ = _run(nova_ask.thread, TAGGED, answered)
    assert payload["waiting"] is False


def test_a_forgotten_question_at_the_tail_does_not_leave_the_page_waiting_forever():
    """`waiting` is read off the *visible* tail, not the raw one -- a
    deleted question would otherwise poll for four minutes for an answer
    nobody is going to write."""
    messages = [
        {"id": "1", "sender": "Edvard", "text": "q", "createdAt": "t1"},
        {"id": "2", "sender": "Nova Answers", "text": "a", "createdAt": "t2"},
        {"id": "3", "sender": "Edvard", "text": "oops", "forgotten": True},
    ]
    payload, _ = _run(nova_ask.thread, TAGGED, messages)
    assert payload["waiting"] is False


def test_the_rendered_thread_is_bounded():
    messages = [
        {"id": str(i), "sender": "Edvard", "text": "q", "createdAt": "t"}
        for i in range(nova_ask.MAX_THREAD * 3)
    ]
    payload, _ = _run(nova_ask.thread, TAGGED, messages)
    assert len(payload["messages"]) == nova_ask.MAX_THREAD


def test_a_notify_that_appends_nothing_is_a_failure_even_at_http_200():
    """The 200-with-no-message case the id-unpacking bug made unreachable."""
    def fake_get(path):
        return 200, {"conversations": [{"id": "c-ask", "tags": [nova_ask.ASK_TAG]}]}

    def fake_internal(method, path, payload=None):
        return 200, {"status": "recorded"}

    with patch.object(nova_ask, "agora_get", side_effect=fake_get), \
         patch.object(nova_ask, "agora_internal", side_effect=fake_internal):
        ok, message = nova_ask.ask("hello")
    assert not ok and "could not post" in message


def test_a_failed_create_is_reported_rather_than_posting_into_nowhere():
    def fake_get(path):
        return 200, {"conversations": []}

    def fake_internal(method, path, payload=None):
        if method == "POST" and path == "/conversations":
            return 500, {}
        raise AssertionError(f"nothing else should be called, got {method} {path}")

    with patch.object(nova_ask, "agora_get", side_effect=fake_get), \
         patch.object(nova_ask, "agora_internal", side_effect=fake_internal):
        ok, message = nova_ask.ask("hello")
    assert not ok and "could not reach" in message


# ---------------------------------------------------------------------------
# `watching()` -- 2026-08-25. His capture: *"now when i use the new chat i get
# alerted by agora whenever a new message arrives."* The push is Agora's to
# withhold; all this side does is say the thread is on his screen here.
# ---------------------------------------------------------------------------


def test_presence_is_posted_against_the_existing_questions_thread():
    (ok, reason), calls = _run(nova_ask.watching, TAGGED)
    assert ok and reason == "watching"
    assert ("POST", "/conversations/c-ask/presence", {}) in calls


def test_presence_never_creates_a_conversation():
    """A reader who has asked nothing has no thread to be present in, and a
    presence ping is the last thing that should manufacture one -- the dock
    is on every page, so this would fire for someone who never typed."""
    (ok, reason), calls = _run(nova_ask.watching, [])
    assert not ok and "no questions thread" in reason
    assert not [c for c in calls if c[0] == "POST"]


def test_a_refused_presence_ping_is_reported_rather_than_claimed():
    def fake_get(path):
        return 200, {"conversations": TAGGED}

    def fake_internal(method, path, payload=None):
        return 503, {}

    with patch.object(nova_ask, "agora_get", side_effect=fake_get), \
         patch.object(nova_ask, "agora_internal", side_effect=fake_internal):
        ok, reason = nova_ask.watching()
    assert not ok and "could not reach" in reason
