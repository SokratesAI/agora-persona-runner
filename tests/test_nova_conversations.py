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


MODEL_CATALOG = [
    {"id": "claude-cli:claude-sonnet-5", "label": "Claude Sonnet 5 (CLI)", "metered": False},
    {"id": "anthropic:claude-opus-5", "label": "Claude Opus 5", "metered": True},
]


def _fakes(conversations=None, messages=None, personas=None, create_id="c-new",
           list_status=200, models=None, model_status=200,
           heartbeats=None, heartbeat_status=200):
    calls = []

    def fake_get(path):
        calls.append(("GET", path, None))
        if path == "/conversations":
            return list_status, {"conversations": conversations or []}
        if path == "/personas":
            return 200, {"personas": personas or []}
        if path == "/heartbeats":
            return heartbeat_status, {"heartbeats": heartbeats or []}
        if path == "/models":
            return model_status, {"models": models if models is not None else MODEL_CATALOG}
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


def test_nova_itself_is_not_written_onto_the_row():
    """Idea #95, slice 4. 687 of the 700 conversations in the live store
    carry this one persona, so "Nova" was on the meta line of nearly every
    row in an app that is Nova-only. The 13 answered by somebody else are
    the only ones where the name says anything."""
    mine = dict(LIVE_ROW, personas=[
        {"personaId": convs.NOVA_PERSONA_ID, "role": "curator", "name": "Nova"}])
    (payload, _calls) = _run(convs.conversations, conversations=[mine])
    row = payload["conversations"][0]
    assert row["personaName"] == ""
    # The model still reaches the row: this blanks a name, it does not blank
    # the meta line, and `app.js` joins what is left with `.filter(Boolean)`.
    assert row["model"] == "claude-cli:claude-sonnet-5"


def test_a_second_persona_also_called_nova_keeps_its_name():
    """Matched on the id, not the string. Two distinct personas in the live
    store are both named "Nova" -- 08ffac94 on 687 conversations and
    8972a54d on 2 -- and the second one genuinely is not me."""
    other = dict(LIVE_ROW, personas=[
        {"personaId": "8972a54d-cafa-4f07-a527-d8686cea51ca",
         "role": "curator", "name": "Nova"}])
    (payload, _calls) = _run(convs.conversations, conversations=[other])
    assert payload["conversations"][0]["personaName"] == "Nova"


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
    assert none_id == {"conversationId": None, "messages": [], "waiting": False,
                       "hasMore": False}
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


def test_a_new_conversation_always_goes_to_nova_and_answers_its_id():
    """No persona argument any more -- his issues.md #119, 2026-08-29:
    *"drop the Agora multi-persona chat picker from the Nova app entirely,
    the app should be Nova only"*. The id Agora is sent is the one persona
    the app talks to, not anything the page chose."""
    (result, calls) = _run(lambda: convs.create("Roofing"))
    assert result == (True, "c-new")
    created = [c for c in calls if c[0] == "POST" and c[1] == "/conversations"]
    assert created[0][2] == {"name": "Roofing",
                             "personaId": convs.ANSWER_PERSONA_ID}


def test_the_one_persona_is_the_same_id_nova_ask_answers_on():
    """Two modules name this persona and only one may own the literal. A
    second copy is a thread that opens against a persona nothing answers."""
    from agora_runner import nova_ask
    assert nova_ask.ANSWER_PERSONA_ID == convs.ANSWER_PERSONA_ID


def test_a_create_with_a_name_that_is_not_text_never_reaches_agora():
    """`""`, `"  "` and `None` used to be refused here too. They are legal
    now -- `issues.md` #139, he does not always know what a thread is about
    before he starts it -- and the pin that replaces that one is next door
    in `test_a_blank_name_starts_a_thread_called_new_chat`. What still has
    to refuse is a name that is not text at all, because `strip()` on it
    would raise inside the route rather than answer him."""
    for bad in [17, [], {"name": "x"}]:
        (result, calls) = _run(lambda b=bad: convs.create(b))
        assert result[0] is False
        assert calls == []


def test_a_create_that_answers_without_an_id_is_a_failure_not_a_success():
    """The id is the one thing this cannot repair later: the page opens the
    new thread by it, so a silent "" would land him in an empty page with
    no way back to the conversation he just made."""
    def internal(method, path, payload=None):
        return 201, {"conversation": {}}
    with patch.object(convs, "agora_internal", side_effect=internal):
        ok, message = convs.create("Roofing")
    assert ok is False and "id" in message


# `watching(id)` -- 2026-08-26. The switcher half of his 2026-08-25 capture:
# the vouch next door in `nova_ask` resolves the tagged thread itself, so it
# could only ever speak for that one and every other thread in the dock went
# on buzzing his phone.

def test_a_watched_conversation_is_marked_by_its_own_id():
    (ok, reason), calls = _run(lambda: convs.watching("c-7"))
    assert (ok, reason) == (True, "watching")
    assert ("POST", "/conversations/c-7/presence", {}) in calls


def test_no_conversation_id_never_marks_a_thread_blind():
    """The dangerous direction is vouching for a thread he is not reading --
    that drops a notification he wanted. An empty id must refuse rather than
    fall back to some default conversation."""
    (ok, _reason), calls = _run(lambda: convs.watching(""))
    assert ok is False
    assert not [c for c in calls if "presence" in c[1]]


def test_a_store_that_refuses_the_ping_is_reported_as_not_watching():
    """Agora validates the id rather than marking blind, so a refusal is how
    a wrong id surfaces at all. Reading it as success would suppress nothing
    while claiming it had."""
    def refuse(method, path, payload=None):
        return 404, {}

    with patch.object(convs, "agora_internal", side_effect=refuse):
        ok, _reason = convs.watching("c-gone")
    assert ok is False


# --- Managing a conversation from the dock -------------------------------
#
# His capture, `issues.md` 2026-08-27, rated 🔴 Immediately: *"I need the
# chat bubble to be able to start ned conversations, delete them, change
# name, organize like move to a folder."*
#
# What is worth pinning here is which *app* each write goes to and what a
# refusal means, because both are invisible from the page:
#
# - `DELETE /conversations/:id` is registered on Agora's public app only, so
#   a delete sent over the internal one answers 404 -- which this module
#   would report as "that conversation is already gone", the exact opposite
#   of what happened, and he would believe it;
# - moving to the top level is `folderId: None`, not `folderId: ""`, because
#   Agora treats the empty string as "not a string I recognise" and ignores
#   the key, so a conversation could never be taken *out* of a folder;
# - a failed folder listing must not take the conversation list down with
#   it, since losing the groups is much cheaper than losing every thread.


def _manage_fakes(status=200, body=None, folders=None, folder_status=200):
    calls = []

    def fake_get(path):
        calls.append(("GET", path, None))
        if path == "/folders":
            return folder_status, {"folders": folders or []}
        if path == "/conversations":
            return 200, {"conversations": []}
        if path == "/models":
            return 200, {"models": MODEL_CATALOG}
        return 404, {}

    def fake_internal(method, path, payload=None):
        calls.append(("INTERNAL", method, path, payload))
        return status, body or {}

    def fake_public(method, path, payload=None):
        calls.append(("PUBLIC", method, path, payload))
        return status, body or {}

    return fake_get, fake_internal, fake_public, calls


def test_rename_patches_the_name_and_returns_it():
    _get, internal, public, calls = _manage_fakes()
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, message = convs.rename("c-1", "  Holiday plans  ")
    assert (ok, message) == (True, "Holiday plans")
    assert calls == [("INTERNAL", "PATCH", "/conversations/c-1",
                      {"name": "Holiday plans"})]


@pytest.mark.parametrize("name", ["", "   ", None, 5])
def test_rename_refuses_a_nameless_conversation_without_calling_agora(name):
    _get, internal, public, calls = _manage_fakes()
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, _message = convs.rename("c-1", name)
    assert ok is False
    assert calls == []


def test_rename_refuses_a_name_longer_than_create_would_accept():
    _get, internal, public, calls = _manage_fakes()
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, _message = convs.rename("c-1", "x" * (convs.MAX_NAME_CHARS + 1))
    assert ok is False
    assert calls == []


def test_rename_reports_a_missing_conversation_as_gone():
    _get, internal, public, _calls = _manage_fakes(status=404)
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, message = convs.rename("c-1", "Anything")
    assert (ok, message) == (False, "that conversation is gone")


def test_move_sends_the_folder_id():
    _get, internal, public, calls = _manage_fakes()
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, _message = convs.move("c-1", "f-9")
    assert ok is True
    assert calls == [("INTERNAL", "PATCH", "/conversations/c-1",
                      {"folderId": "f-9"})]


def test_move_to_the_top_level_sends_null_not_empty_string():
    """`""` is not a folder id and Agora ignores it -- see the note above."""
    _get, internal, public, calls = _manage_fakes()
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, _message = convs.move("c-1", "")
    assert ok is True
    assert calls == [("INTERNAL", "PATCH", "/conversations/c-1",
                      {"folderId": None})]


def test_move_reports_an_unknown_folder_as_such():
    _get, internal, public, _calls = _manage_fakes(status=400)
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, message = convs.move("c-1", "f-nope")
    assert (ok, message) == (False, "that folder does not exist")


def test_delete_goes_to_the_public_app_because_that_is_where_the_route_is():
    _get, internal, public, calls = _manage_fakes()
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, message = convs.remove("c-1")
    assert (ok, message) == (True, "deleted")
    assert calls == [("PUBLIC", "DELETE", "/conversations/c-1", None)]


def test_delete_without_an_id_calls_nothing():
    _get, internal, public, calls = _manage_fakes()
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, _message = convs.remove("")
    assert ok is False
    assert calls == []


def test_folder_create_accepts_the_200_that_means_it_already_existed():
    _get, internal, public, calls = _manage_fakes(
        status=200, body={"folder": {"id": "f-1"}})
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, message = convs.folder_create("  Work  ")
    assert (ok, message) == (True, "f-1")
    assert calls == [("PUBLIC", "POST", "/folders", {"name": "Work"})]


def test_folder_create_refuses_a_response_with_no_id():
    _get, internal, public, _calls = _manage_fakes(status=201, body={})
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, message = convs.folder_create("Work")
    assert ok is False
    assert "folder id" in message


def test_the_listing_carries_folders_and_each_row_s_folder_id():
    fake_get, internal, public, _calls = _manage_fakes(
        folders=[{"id": "f-2", "name": "Zebras"}, {"id": "f-1", "name": "apples"}])

    def listing(path):
        if path == "/conversations":
            return 200, {"conversations": [
                {"id": "c-1", "name": "One", "folderId": "f-1"},
                {"id": "c-2", "name": "Two"},
            ]}
        return fake_get(path)

    with patch.object(convs, "agora_get", listing):
        payload = convs.conversations()
    # Sorted case-insensitively, or "Zebras" would sort above "apples" and
    # his folders would come back in ASCII order rather than alphabetical.
    assert payload["folders"] == [
        {"id": "f-1", "name": "apples"}, {"id": "f-2", "name": "Zebras"}]
    assert [r["folderId"] for r in payload["conversations"]] == ["f-1", ""]


def test_an_unreadable_folder_list_still_returns_every_conversation():
    fake_get, _internal, _public, _calls = _manage_fakes(folder_status=500)

    def listing(path):
        if path == "/conversations":
            return 200, {"conversations": [{"id": "c-1", "name": "One"}]}
        return fake_get(path)

    with patch.object(convs, "agora_get", listing):
        payload = convs.conversations()
    assert payload["folders"] == []
    assert len(payload["conversations"]) == 1


# --- the model picker (idea #95, slice 1's last door) -----------------------
#
# Agora moved `model` off the persona and onto the conversation on 08-21 and
# nothing in Nova ever called that write, so from the app he opens the model
# was still unchangeable. What is worth pinning:
#
# - the catalog rides along with the conversation list, because a picker
#   fetched separately is a second round trip on the switcher he opens most;
# - `metered` is passed through from Agora rather than re-derived, since a
#   picker that mislabels which models bill per token spends real money;
# - an unreadable catalog must not take the conversation list down with it,
#   for `_folder_rows`' reason.


def test_the_conversation_list_carries_the_model_catalog():
    payload, calls = _run(convs.conversations)
    assert payload["models"] == [
        {"id": "claude-cli:claude-sonnet-5",
         "label": "Claude Sonnet 5 (CLI)", "metered": False},
        {"id": "anthropic:claude-opus-5", "label": "Claude Opus 5", "metered": True},
    ]
    assert ("GET", "/models", None) in calls


def test_an_unreadable_catalog_leaves_the_conversations_intact():
    payload, _calls = _run(convs.conversations, model_status=500,
                           conversations=[dict(LIVE_ROW)])
    assert payload["models"] == []
    assert len(payload["conversations"]) == 1


def test_a_model_without_an_id_is_dropped_rather_than_offered():
    payload, _calls = _run(convs.conversations,
                           models=[{"label": "Nameless"},
                                   {"id": "gemini:gemini-flash-latest"}])
    assert [m["id"] for m in payload["models"]] == ["gemini:gemini-flash-latest"]
    # No label in the catalog row, so the id stands in rather than "".
    assert payload["models"][0]["label"] == "gemini:gemini-flash-latest"


def test_set_model_patches_the_conversation_and_returns_the_model():
    _get, internal, public, calls = _manage_fakes()
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, message = convs.set_model("c-1", "  claude-cli:claude-sonnet-5  ")
    assert (ok, message) == (True, "claude-cli:claude-sonnet-5")
    assert calls == [("INTERNAL", "PATCH", "/conversations/c-1",
                      {"model": "claude-cli:claude-sonnet-5"})]


@pytest.mark.parametrize("model", ["", "   ", None, 5])
def test_set_model_refuses_a_blank_model_without_calling_agora(model):
    _get, internal, public, calls = _manage_fakes()
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, _message = convs.set_model("c-1", model)
    assert ok is False
    assert calls == []


def test_set_model_reports_a_model_agora_rejects_as_its_own_refusal():
    # Agora validates against VALID_MODEL_IDS and answers 400. This file
    # deliberately keeps no copy of that set, so the 400 is the only signal.
    _get, internal, public, _calls = _manage_fakes(status=400)
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, message = convs.set_model("c-1", "openai:gpt-9")
    assert (ok, message) == (False, "Agora does not have that model")


def test_set_model_reports_a_missing_conversation_as_gone():
    _get, internal, public, _calls = _manage_fakes(status=404)
    with patch.object(convs, "agora_internal", internal), \
         patch.object(convs, "agora_public", public):
        ok, message = convs.set_model("c-1", "claude-cli:claude-sonnet-5")
    assert (ok, message) == (False, "that conversation is gone")


def test_a_turn_in_flight_becomes_one_steps_row_not_a_bubble_each():
    """His capture, `issues.md` 2026-09-01: *"The streaming of the thoughts
    in a conversation show up as multiple bubbles and i do not like that."*

    Issue #129 put those passages on the page and was right to -- a
    four-minute turn used to look like nothing at all. What it got wrong is
    that each one became a message. A tool call and the passage after it are
    one turn working, so they are one row carrying two steps, which the page
    draws as one collapsed line.

    The thought's text comes off `activity.detail`, not off the message's own
    `text`: Agora prefixes that with the capability name for its own search,
    so rendering it would put "assistant_text: " in front of every paragraph.
    """
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "how many pods?"},
        {"id": "b", "sender": "Nova", "text": "Bash: kubectl get pods",
         "activity": {"capability": "Bash", "detail": "kubectl get pods",
                      "toolUseId": "t1"}},
        {"id": "c", "sender": "Nova", "text": "assistant_text: Counting them now.",
         "activity": {"capability": "assistant_text", "detail": "Counting them now."}},
    ])
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["id"] == "a"
    assert payload["messages"][0]["partial"] is False
    assert payload["messages"][0].get("steps") is None
    working = payload["messages"][1]
    assert working["stepsOnly"] is True
    assert working["partial"] is True
    assert working["text"] == ""
    assert working["steps"] == [
        {"kind": "tool", "capability": "Bash", "input": "kubectl get pods",
         "id": "t1", "status": "running"},
        {"kind": "thought", "text": "Counting them now."},
    ]


def test_a_mid_turn_passage_does_not_stop_the_page_polling():
    """The one way this change could do damage. `waiting` used to read the
    last visible sender, and a passage from the persona arriving mid-turn
    would read as "answered" -- the page would stop polling and never draw
    the real reply. A partial means the turn is still going."""
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "and?"},
        {"id": "b", "sender": "Nova", "text": "assistant_text: working on it",
         "activity": {"capability": "assistant_text", "detail": "working on it"}},
    ])
    assert payload["messages"][-1]["partial"] is True
    assert payload["waiting"] is True


def test_an_empty_passage_is_still_dropped_as_machinery():
    """`report_text` strips before sending, so a blank one should not exist
    -- but a whitespace-only detail must not become an empty bubble."""
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Nova", "text": "assistant_text:   ",
         "activity": {"capability": "assistant_text", "detail": "   "}},
        {"id": "b", "sender": "Nova", "text": "done"},
    ])
    assert [m["id"] for m in payload["messages"]] == ["b"]


def test_a_legacy_boolean_activity_flag_is_still_dropped():
    """Messages written before Agora carried the activity object at all --
    `activity: true` and nothing else. `narration_passage` must not read a
    capability off a bool and must not crash trying."""
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Nova", "text": "Bash: ls", "activity": True},
        {"id": "b", "sender": "Nova", "text": "done"},
    ])
    assert [m["id"] for m in payload["messages"]] == ["b"]


def test_a_finished_turns_steps_attach_to_the_reply_they_produced():
    """The half of issue #129 that used to have to be solved by throwing the
    passages away. One Nova cycle writes hundreds of them and `MAX_THREAD`
    is 40, so a bubble each would push every sentence he actually wrote off
    the page -- `keep_only_live_passages` existed to drop the finished ones.
    A collapsed line costs one row per turn instead of one per passage, so
    the old ones can stay, attached to the answer they produced.

    The steps of a turn still running have nothing to attach to, so they
    stand alone -- and that row is what `waiting` keys on."""
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "how many pods?"},
        {"id": "b", "sender": "Nova", "text": "assistant_text: counting",
         "activity": {"capability": "assistant_text", "detail": "counting"}},
        {"id": "c", "sender": "Nova", "text": "Seven."},
        {"id": "d", "sender": "Edvard", "text": "and now?"},
        {"id": "e", "sender": "Nova", "text": "assistant_text: counting again",
         "activity": {"capability": "assistant_text", "detail": "counting again"}},
    ])
    assert [m["id"] for m in payload["messages"]] == ["a", "c", "d", ""]
    # The finished turn's thought hangs off "Seven.", not off a row of its own.
    assert payload["messages"][1]["steps"] == [
        {"kind": "thought", "text": "counting"}]
    assert payload["messages"][1]["partial"] is False
    # ...and his own message never collects the persona's work.
    assert payload["messages"][2].get("steps") is None
    assert payload["messages"][3]["stepsOnly"] is True
    assert payload["waiting"] is True


# --- Starting a thread before you know what it is about ------------------
#
# His capture, `issues.md` #139: *"I have to type a conversation title
# before I can even start it, but I don't always know what it'll be about.
# Want it to work like the official Claude app: starts as 'New chat',
# auto-titles itself after the first message."*
#
# What is worth pinning is the pair of directions, because they fail
# silently in opposite ways: a blank name must *start* a thread rather than
# refuse one, and a thread he named himself must never be renamed under him
# by something he happened to say in it.


def test_a_blank_name_starts_a_thread_called_new_chat():
    (ok, new_id), calls = _run(lambda: convs.create(""))
    assert ok is True and new_id == "c-new"
    posted = [c for c in calls if c[1] == "/conversations"]
    assert posted and posted[0][2]["name"] == convs.UNTITLED_NAME


def test_a_name_he_typed_is_still_the_name():
    (ok, _), calls = _run(lambda: convs.create("  Roofing  "))
    assert ok is True
    assert [c for c in calls if c[1] == "/conversations"][0][2]["name"] == "Roofing"


def test_rename_still_refuses_a_blank_where_create_now_accepts_one():
    """The two are deliberately not the same rule. Starting without a name
    is him not knowing yet; emptying the name of a thread that has one
    leaves a row he cannot find again in a list of seven hundred."""
    ok, message = convs.rename("c-7", "   ")
    assert ok is False and "needs a name" in message


def test_the_first_message_becomes_the_title():
    (ok, message), calls = _run(
        lambda: convs.autotitle("c-7", convs.UNTITLED_NAME,
                                "Can you look at the NAS backup? It has been failing."))
    assert ok is True and message == "Can you look at the NAS backup?"
    assert ("PATCH", "/conversations/c-7",
            {"name": "Can you look at the NAS backup?"}) in calls


def test_a_thread_he_named_is_never_retitled():
    """The one check that carries the whole safety of the route. Without it
    every opening message would overwrite a title he chose, and he would
    have no way to tell that from the app losing his rename."""
    (ok, message), calls = _run(
        lambda: convs.autotitle("c-7", "Roofing", "Can you look at the NAS backup?"))
    assert ok is False and "already has a name" in message
    assert not [c for c in calls if c[0] == "PATCH"]


def test_a_message_with_no_words_in_it_leaves_the_placeholder():
    """`New chat` is honest. A title cut out of an emoji or an attachment
    line is worse than no title, and it cannot be undone by sending a
    better message afterwards -- the thread no longer looks untitled."""
    (ok, _message), calls = _run(lambda: convs.autotitle("c-7", convs.UNTITLED_NAME, "😀😀"))
    assert ok is False
    assert not [c for c in calls if c[0] == "PATCH"]


def test_an_attachment_line_is_the_pages_text_not_his():
    assert convs.title_from_message("![shot](/api/upload/a.png)") == ""


def test_a_long_opening_line_is_cut_on_a_word_boundary():
    """Pinned as "the source has a space where this stopped", not as "the
    title does not end in a space". The second is true of a mid-word cut as
    well, so it passes against the bug -- the first run of this test did."""
    source = "A very long opening line about the conversation title problem that goes on"
    title = convs.title_from_message(source)
    assert len(title) <= convs.TITLE_CHARS + 1
    assert title.endswith("\u2026")
    kept = title[:-1]
    assert source.startswith(kept)
    assert source[len(kept)] == " ", f"cut mid-word: {title!r}"


def test_a_long_run_with_no_spaces_is_cut_where_it_falls():
    """A URL has no word boundary to cut on, and refusing to shorten it
    would put a 200-character row in the switcher."""
    url = "https://example.com/" + "a" * 120
    title = convs.title_from_message(url)
    assert len(title) == convs.TITLE_CHARS + 1


def test_only_the_first_line_is_the_title():
    assert convs.title_from_message("Roofing\nthe felt is lifting at the ridge") == "Roofing"


# --- The drawer: one collapsed line per turn ------------------------------
#
# His capture, `issues.md` 2026-09-01: *"The streaming of the thoughts in a
# conversation show up as multiple bubbles and i do not like that. It would
# be better to replecate Claude mobile app patter where the thoughts and
# tools are compressed to one clickable line that opens a modal drawer from
# the bottom that can be dragged upwards, but contains the thoughts as text,
# but if tools have been used it is showed as a list of clickable lines and
# when clicked, the "input" and "output" of the tool is shown."*

def test_the_two_halves_of_one_tool_call_are_one_step():
    """`tool_activity.report` narrates a call twice under one `toolUseId` --
    once with the arguments when it starts and once with the output when it
    returns -- because a `pytest` run takes minutes and he asked to see it
    start. Two rows in the drawer for one `ls` would be that implementation
    detail on his screen."""
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "how many pods?"},
        {"id": "b", "sender": "Nova", "text": "Bash: kubectl get pods",
         "activity": {"capability": "Bash", "detail": "kubectl get pods",
                      "toolUseId": "t1"}},
        {"id": "c", "sender": "Nova", "text": "Bash",
         "activity": {"capability": "Bash", "output": "seven pods",
                      "toolUseId": "t1"}},
        {"id": "d", "sender": "Nova", "text": "Seven."},
    ])
    assert payload["messages"][1]["steps"] == [
        {"kind": "tool", "capability": "Bash", "input": "kubectl get pods",
         "id": "t1", "status": "done"},
    ]


def test_a_call_that_has_not_returned_yet_reads_as_running():
    """The drawer is readable while the turn is in flight, so "no output has
    arrived" has to be a state rather than a blank. Only the *presence* of an
    output message says the call came back."""
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "run the suite"},
        {"id": "b", "sender": "Nova", "text": "Bash: pytest",
         "activity": {"capability": "Bash", "detail": "pytest",
                      "toolUseId": "t1"}},
    ])
    assert payload["messages"][1]["steps"][0]["status"] == "running"


def test_a_failed_call_says_so_rather_than_reading_as_finished():
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "read it"},
        {"id": "b", "sender": "Nova", "text": "Read: /nope",
         "activity": {"capability": "Read", "detail": "/nope", "toolUseId": "t1"}},
        {"id": "c", "sender": "Nova", "text": "Read",
         "activity": {"capability": "Read", "output": "no such file",
                      "isError": True, "toolUseId": "t1"}},
    ])
    assert payload["messages"][1]["steps"][0]["status"] == "failed"


def test_calls_with_no_tool_use_id_are_not_merged_into_one_step():
    """Agora leaves `toolUseId` off some rows. Folding those together on ""
    would collapse every anonymous call in a turn into one step, which reads
    as the persona having done a quarter of the work it did."""
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "go"},
        {"id": "b", "sender": "Nova", "text": "Bash: one",
         "activity": {"capability": "Bash", "detail": "one"}},
        {"id": "c", "sender": "Nova", "text": "Bash: two",
         "activity": {"capability": "Bash", "detail": "two"}},
        {"id": "d", "sender": "Nova", "text": "done"},
    ])
    assert [s["input"] for s in payload["messages"][1]["steps"]] == ["one", "two"]


def test_no_tool_output_is_carried_in_the_thread():
    """The measured reason there is a second route at all. Agora truncates a
    tool output at 20,000 characters and there is one per call, so a window
    of forty calls would put up to 800KB on his phone for a drawer he may
    never open -- against 1-9KB of thought text and a `detail` that
    `audit.DETAIL_CHARS_MAX` already bounds at 500 (three live threads,
    Cycle 780).

    Asserted as "the output string appears nowhere in the payload" rather
    than as "the step has no `output` key": a later cycle adding it under
    another name would pass the second and fail this."""
    import json
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "go"},
        {"id": "b", "sender": "Nova", "text": "Bash: ls",
         "activity": {"capability": "Bash", "detail": "ls", "toolUseId": "t1"}},
        {"id": "c", "sender": "Nova", "text": "Bash",
         "activity": {"capability": "Bash", "toolUseId": "t1",
                      "output": "ENORMOUS-OUTPUT-BODY"}},
        {"id": "d", "sender": "Nova", "text": "done"},
    ])
    assert "ENORMOUS-OUTPUT-BODY" not in json.dumps(payload)
    # ...and the call itself is still there, so this is a test of what was
    # withheld rather than of the whole step having been dropped.
    assert payload["messages"][1]["steps"][0]["input"] == "ls"


def test_step_output_returns_both_halves_of_one_call():
    (found, _calls) = _run(lambda: convs.step_output("c-1", "t1"), messages=[
        {"id": "b", "sender": "Nova", "text": "Bash: ls",
         "activity": {"capability": "Bash", "detail": "ls", "toolUseId": "t1"}},
        {"id": "c", "sender": "Nova", "text": "Bash",
         "activity": {"capability": "Bash", "output": "a\nb", "toolUseId": "t1"}},
    ])
    assert found == {"capability": "Bash", "input": "ls", "output": "a\nb",
                     "status": "done"}


def test_step_output_does_not_let_the_start_message_reset_the_status():
    """The two halves arrive in order, but nothing guarantees Agora returns
    them in it. A start message carries no output, so reading the status off
    every row unconditionally would put a settled call back to `running`."""
    (found, _calls) = _run(lambda: convs.step_output("c-1", "t1"), messages=[
        {"id": "c", "sender": "Nova", "text": "Bash",
         "activity": {"capability": "Bash", "output": "a", "toolUseId": "t1"}},
        {"id": "b", "sender": "Nova", "text": "Bash: ls",
         "activity": {"capability": "Bash", "detail": "ls", "toolUseId": "t1"}},
    ])
    assert found["status"] == "done"
    assert found["output"] == "a"


def test_step_output_is_none_for_a_call_this_thread_does_not_hold():
    """A call that has scrolled out of Agora's retention, or an id off a
    stale page. `None` and an empty output are different answers and the
    route turns this one into a 404."""
    (found, _calls) = _run(lambda: convs.step_output("c-1", "gone"), messages=[
        {"id": "b", "sender": "Nova", "text": "Bash: ls",
         "activity": {"capability": "Bash", "detail": "ls", "toolUseId": "t1"}},
    ])
    assert found is None


def test_step_output_refuses_without_both_a_thread_and_a_call():
    assert convs.step_output("", "t1") is None
    assert convs.step_output("c-1", "") is None


def test_work_that_runs_into_his_next_message_does_not_land_on_it():
    """A block with his own message under it is the persona's turn ending,
    not the start of his -- so it stands alone rather than attaching. Without
    this the collapsed line would appear on the question he typed, saying he
    had run a tool.

    This is the case the fixture above cannot reach: there, every block has a
    reply under it. Here the turn narrated and stopped, and he asked again."""
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "how many pods?"},
        {"id": "b", "sender": "Nova", "text": "Bash: kubectl get pods",
         "activity": {"capability": "Bash", "detail": "kubectl get pods",
                      "toolUseId": "t1"}},
        {"id": "c", "sender": "Edvard", "text": "well?"},
    ])
    assert [m["id"] for m in payload["messages"]] == ["a", "", "c"]
    assert payload["messages"][1]["stepsOnly"] is True
    assert payload["messages"][1]["steps"][0]["capability"] == "Bash"
    assert payload["messages"][2].get("steps") is None
    assert payload["messages"][2]["sender"] == "Edvard"


def test_the_thread_says_which_window_its_rows_came_from():
    """The drawer asks for a step's output inside the same window the rows
    were built from, and it learns that window from here. A payload that
    said `0` would send the client back to the default and answer 404 for a
    call he can see, once he has paged back through a long thread -- which is
    the bug this field exists to close, not a hypothetical."""
    (payload, _calls) = _run(lambda: convs.thread("c-1", 160), messages=[
        {"id": "a", "sender": "Edvard", "text": "hi"},
    ])
    assert payload["limit"] == 160
    # And it is the clamped window rather than the string off the wire, so a
    # `?limit=junk` cannot come back out as a query parameter.
    (payload, _calls) = _run(lambda: convs.thread("c-1", "junk"), messages=[
        {"id": "a", "sender": "Edvard", "text": "hi"},
    ])
    assert payload["limit"] == convs.MAX_THREAD


# --- model_choice: what the visible picker in the chat reads (his issue #143)

def test_model_choice_reads_this_threads_model_out_of_the_listing():
    """Agora has no `GET /conversations/<id>` -- it answers 404, measured
    Cycle 805 -- so the model on one thread can only come out of the list of
    all of them. Pinned because a future cycle reaching for the cheaper
    single-conversation fetch would get a 404 and an empty picker."""
    other = dict(LIVE_ROW, id="c-2", model="anthropic:claude-opus-5", tags=[])
    (payload, _calls) = _run(
        lambda: convs.model_choice("c-1"), conversations=[other, LIVE_ROW])
    assert payload["model"] == "claude-cli:claude-sonnet-5"
    assert payload["found"] is True
    assert [m["id"] for m in payload["models"]] == [
        "claude-cli:claude-sonnet-5", "anthropic:claude-opus-5"]


def test_model_choice_separates_a_thread_with_no_model_from_a_thread_that_is_gone():
    """Both leave `model` empty and they mean opposite things: the first is a
    real conversation the picker should offer to point somewhere, the second
    is one Agora no longer holds. Without `found` the page draws
    "Model (unset)" over an answer it does not have."""
    unset = dict(LIVE_ROW, model="")
    (has_no_model, _c) = _run(
        lambda: convs.model_choice("c-1"), conversations=[unset])
    (is_gone, _c2) = _run(
        lambda: convs.model_choice("c-404"), conversations=[unset])
    assert (has_no_model["model"], has_no_model["found"]) == ("", True)
    assert (is_gone["model"], is_gone["found"]) == ("", False)


def test_model_choice_raises_when_the_listing_cannot_be_read():
    """`conversations()`' rule, for its reason: an unreachable store and a
    thread with no model render the same and mean opposite things. The
    picker hides itself on a failed fetch; it must not hide itself on a
    successful one that says nothing."""
    with pytest.raises(RuntimeError):
        _run(lambda: convs.model_choice("c-1"), list_status=502)


def test_model_choice_asks_agora_nothing_without_a_conversation():
    """The dock's Ask row has no id until its thread payload arrives, so this
    is called with an empty one on the way there. A listing fetch for a
    thread that does not exist yet is 826 rows spent on nothing."""
    (payload, calls) = _run(lambda: convs.model_choice(""))
    assert payload == {"model": "", "models": [], "found": False}
    assert calls == []
