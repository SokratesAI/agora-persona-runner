"""nova_conversations.py -- the Conversations page's server half.

His capture, ideas.md 2026-08-25: *"its basicly a chat app with multiple
conversations history and i can start new ones etc."*

What is worth pinning is what would silently break while the page still
renders something plausible:

- a message is posted as `sender="Edvard"`, because `decide_turn` speaks   (not-prose: quoting a literal)
  only for a message from him and any other sender posts into silence --
  the thread would look fine and simply never answer;
- the curator's name comes off the `personas` list rather than a
  `personaId` field, because Agora's listing has no such field and reading
  it yields "" for every row in the store;
- a message's timestamp is `ts`, for the same reason;
- an unreachable store raises rather than returning an empty list, because
  "no conversations" and "cannot reach Agora" render identically and mean
  opposite things.
"""
from unittest.mock import patch

import pytest

import agora_runner.nova_conversations as convs


def _fakes(conversations=None, messages=None, personas=None, create_id="c-new",
           list_status=200):
    calls = []

    def fake_get(path):
        calls.append(("GET", path, None))
        if path == "/conversations":
            return list_status, {"conversations": conversations or []}
        if path == "/personas":
            return 200, {"personas": personas or []}
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


def _run(fn, **kw):
    get, internal, calls = _fakes(**kw)
    with patch.object(convs, "agora_get", side_effect=get), \
         patch.object(convs, "agora_internal", side_effect=internal):
        return fn(), calls


LIVE_ROW = {
    "id": "c-1",
    "name": "Nova — Questions",
    "personas": [{"personaId": "p-1", "role": "curator", "name": "Nova Answers",
                  "model": "claude-cli:claude-sonnet-5"}],
    "model": "claude-cli:claude-sonnet-5",
    "tags": ["nova-ask"],
    "lastMessageAt": "2026-08-20T13:24:25.848Z",
    "archived": False,
}


def test_the_curator_name_comes_off_the_personas_list():
    """Agora's listing has no `personaId` and no `personaName`. Reading
    those is what the first version did, and every one of the 454 rows in
    the live store would have rendered with nobody in it."""
    (payload, _calls) = _run(convs.conversations, conversations=[LIVE_ROW])
    row = payload["conversations"][0]
    assert row["personaName"] == "Nova Answers"
    assert row["model"] == "claude-cli:claude-sonnet-5"
    assert row["updatedAt"] == "2026-08-20T13:24:25.848Z"


def test_archived_conversations_are_left_out():
    """414 of the 454 threads in the live store are archived cycle
    conversations. A list that carries them is not a list he can use."""
    archived = dict(LIVE_ROW, id="c-old", archived=True)
    (payload, _calls) = _run(convs.conversations, conversations=[LIVE_ROW, archived])
    assert [r["id"] for r in payload["conversations"]] == ["c-1"]


def test_newest_activity_first_and_an_undated_row_sorts_last():
    undated = dict(LIVE_ROW, id="c-none", lastMessageAt=None, createdAt=None)
    newer = dict(LIVE_ROW, id="c-2", lastMessageAt="2026-08-25T20:00:00.000Z")
    (payload, _calls) = _run(
        convs.conversations, conversations=[LIVE_ROW, undated, newer])
    assert [r["id"] for r in payload["conversations"]] == ["c-2", "c-1", "c-none"]


def test_an_unreachable_store_raises_rather_than_reading_as_empty():
    with pytest.raises(RuntimeError):
        _run(convs.conversations, conversations=[LIVE_ROW], list_status=503)


def test_a_cycles_own_thread_is_flagged_not_dropped():
    """He asked for the history. Labelling a machine thread lets the page
    dim it; dropping it here would decide for him."""
    cycle = dict(LIVE_ROW, id="c-cyc", tags=["evolve-cycle:abc"])
    (payload, _calls) = _run(convs.conversations, conversations=[cycle, LIVE_ROW])
    flags = {r["id"]: r["cycleThread"] for r in payload["conversations"]}
    assert flags == {"c-cyc": True, "c-1": False}


def test_a_messages_timestamp_is_ts():
    """`/messages` answers `ts`; `nova_ask` reads `createdAt` and therefore
    renders every message undated. Measured against the live store."""
    (payload, _calls) = _run(
        lambda: convs.thread("c-1"),
        messages=[{"id": "m", "sender": "Edvard", "text": "hi",
                   "ts": "2026-08-25T20:00:00.000Z"}])
    assert payload["messages"][0]["createdAt"] == "2026-08-25T20:00:00.000Z"


def test_narration_is_dropped_from_the_thread():
    """Activity, thinking, forgotten and system messages are how the loop
    talks to itself. A cycle thread is 90% tool calls."""
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Nova", "text": "Bash: ls", "activity": True},
        {"id": "b", "sender": "Nova", "text": "done"},
    ])
    assert [m["id"] for m in payload["messages"]] == ["b"]


def test_waiting_is_true_only_when_his_message_is_last():
    (mine, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Nova", "text": "hi"},
        {"id": "b", "sender": "Edvard", "text": "and?"},
    ])
    assert mine["waiting"] is True
    (theirs, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "b", "sender": "Edvard", "text": "and?"},
        {"id": "c", "sender": "Nova", "text": "so"},
    ])
    assert theirs["waiting"] is False


def test_an_empty_id_reads_as_an_empty_thread_without_calling_agora():
    # The page before anything is open. It must not become a listing call,
    # and it must not become an Agora fetch for the id "".
    (none_id, calls) = _run(lambda: convs.thread(None),
                            messages=[{"id": "m", "sender": "x"}])
    assert none_id == {"conversationId": None, "messages": [], "waiting": False}
    assert calls == []
    (empty, calls) = _run(lambda: convs.thread(""), messages=[])
    assert empty["messages"] == [] and calls == []


def test_a_message_is_posted_as_edvard_and_without_a_push():
    """`decide_turn` speaks only for a message from him, so any other
    sender writes into silence. `push: False` because he is looking at the
    thread he just typed into -- notifying him of his own message is the
    wrapper problem Cycle 439 fixed."""
    (result, calls) = _run(lambda: convs.send("c-1", "hello"))
    assert result == (True, "m-1")
    posted = [c for c in calls if c[0] == "POST" and c[1].endswith("/notify")]
    assert len(posted) == 1
    assert posted[0][1] == "/conversations/c-1/notify"
    assert posted[0][2]["sender"] == "Edvard"
    assert posted[0][2]["push"] is False


def test_empty_text_and_a_missing_conversation_are_refused_before_the_call():
    for bad in [("c-1", "   "), ("c-1", None), ("", "hello")]:
        (result, calls) = _run(lambda b=bad: convs.send(*b))
        assert result[0] is False
        assert calls == []


def test_an_overlong_message_is_refused_before_the_call():
    (result, calls) = _run(
        lambda: convs.send("c-1", "x" * (convs.MAX_MESSAGE_CHARS + 1)))
    assert result[0] is False and calls == []


def test_a_new_conversation_carries_the_chosen_persona_and_answers_its_id():
    (result, calls) = _run(lambda: convs.create("Roofing", "p-9"))
    assert result == (True, "c-new")
    created = [c for c in calls if c[0] == "POST" and c[1] == "/conversations"]
    assert created[0][2] == {"name": "Roofing", "personaId": "p-9"}


def test_a_create_with_no_name_or_no_persona_never_reaches_agora():
    for bad in [("", "p-9"), ("  ", "p-9"), ("Roofing", ""), ("Roofing", None)]:
        (result, calls) = _run(lambda b=bad: convs.create(*b))
        assert result[0] is False
        assert calls == []


def test_a_create_that_answers_without_an_id_is_a_failure_not_a_success():
    """The id is the one thing this cannot repair later: the page opens the
    new thread by it, so a silent "" would land him in an empty page with
    no way back to the conversation he just made."""
    def internal(method, path, payload=None):
        return 201, {"conversation": {}}
    with patch.object(convs, "agora_internal", side_effect=internal):
        ok, message = convs.create("Roofing", "p-9")
    assert ok is False and "id" in message


def test_a_metered_persona_is_labelled_rather_than_hidden():
    """identity.md rule 9 forbids *defaulting* onto the prepaid API, not
    seeing it. Labelling is what lets him choose knowingly; hiding would
    make the choice for him silently, and sorting the metered ones last
    is what stops one being the option his thumb lands on."""
    (payload, _calls) = _run(convs.personas, personas=[
        {"id": "p-a", "name": "Zed", "model": "anthropic:claude-opus-5"},
        {"id": "p-b", "name": "Nova Answers", "model": "claude-cli:claude-sonnet-5"},
    ])
    rows = payload["personas"]
    assert [r["name"] for r in rows] == ["Nova Answers", "Zed"]
    assert [r["metered"] for r in rows] == [False, True]
