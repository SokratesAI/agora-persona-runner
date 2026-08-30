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


def test_a_create_with_no_name_never_reaches_agora():
    for bad in ["", "  ", None]:
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


def test_a_passage_the_persona_wrote_mid_turn_is_kept_and_marked_partial():
    """Issue #129. The reply is written in passages with tool calls between
    them and each one is pushed into the conversation as it is written --
    so the answer is already arriving here while the turn runs. Dropping
    those with the tool chips is what made a four-minute turn look like
    nothing followed by one block of text.

    The text comes off `activity.detail`, not off the message's own `text`:
    Agora prefixes that with the capability name for its own search, so
    rendering it would put "assistant_text: " in front of every paragraph.
    """
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "how many pods?"},
        {"id": "b", "sender": "Nova", "text": "Bash: kubectl get pods",
         "activity": {"capability": "Bash", "detail": "kubectl get pods"}},
        {"id": "c", "sender": "Nova", "text": "assistant_text: Counting them now.",
         "activity": {"capability": "assistant_text", "detail": "Counting them now."}},
    ])
    assert [m["id"] for m in payload["messages"]] == ["a", "c"]
    assert payload["messages"][1]["text"] == "Counting them now."
    assert payload["messages"][1]["partial"] is True
    assert payload["messages"][0]["partial"] is False


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


def test_a_finished_turns_passages_do_not_survive_its_reply():
    """The half of issue #129 that would have made the page worse. One Nova
    cycle writes hundreds of passages and `MAX_THREAD` is 40 -- keeping the
    old ones would push every sentence he actually wrote off the page, and
    they are the same words as the reply that landed under them anyway."""
    (payload, _calls) = _run(lambda: convs.thread("c-1"), messages=[
        {"id": "a", "sender": "Edvard", "text": "how many pods?"},
        {"id": "b", "sender": "Nova", "text": "assistant_text: counting",
         "activity": {"capability": "assistant_text", "detail": "counting"}},
        {"id": "c", "sender": "Nova", "text": "Seven."},
        {"id": "d", "sender": "Edvard", "text": "and now?"},
        {"id": "e", "sender": "Nova", "text": "assistant_text: counting again",
         "activity": {"capability": "assistant_text", "detail": "counting again"}},
    ])
    # "b" belonged to a turn that finished; "e" is the turn running now.
    assert [m["id"] for m in payload["messages"]] == ["a", "c", "d", "e"]
    assert payload["waiting"] is True
