"""conversation_rotation.py -- per-cycle conversation rotation for
workflow-mode heartbeats (2026-08-02). See the module's own docstring
for why: a heartbeat bound forever to one conversation accumulates
every tool-call-heavy round of every cycle it has ever run, unbounded,
in a UI meant for human chat."""
from unittest.mock import patch

import agora_runner.conversation_rotation as rotation


PARTICIPANTS = [
    {"personaId": "coder-1", "name": "Evolve-Coder", "role": "curator"},
    {"personaId": "reviewer-1", "name": "Evolve-Reviewer", "role": "listener"},
]


def test_rotate_is_noop_when_flag_unset():
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old"}
    with patch.object(rotation, "agora_get") as mock_get, \
         patch.object(rotation, "agora_internal") as mock_internal:
        result = rotation.rotate_cycle_conversation(heartbeat, PARTICIPANTS)
    assert result == "c-old"
    mock_get.assert_not_called()
    mock_internal.assert_not_called()


def test_rotate_is_noop_when_flag_false():
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old",
                 "rotateConversationEachRun": False}
    with patch.object(rotation, "agora_get") as mock_get:
        result = rotation.rotate_cycle_conversation(heartbeat, PARTICIPANTS)
    assert result == "c-old"
    mock_get.assert_not_called()


def test_rotate_creates_conversation_carries_personas_and_points_heartbeat():
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old",
                 "rotateConversationEachRun": True}
    calls = []

    def fake_get(path):
        assert path == "/conversations"
        return 200, {"conversations": []}

    def fake_internal(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path == "/conversations":
            return 201, {"conversation": {"id": "c-new"}}
        return 200, {}

    with patch.object(rotation, "agora_get", side_effect=fake_get), \
         patch.object(rotation, "agora_internal", side_effect=fake_internal):
        result = rotation.rotate_cycle_conversation(heartbeat, PARTICIPANTS)

    assert result == "c-new"
    create_call = next(c for c in calls if c[0] == "POST" and c[1] == "/conversations")
    assert create_call[2]["name"] == "Agora Evolve v1 — Cycle 1"
    assert create_call[2]["personaId"] == "coder-1"

    persona_patch = next(c for c in calls if c[1] == "/conversations/c-new")
    assert persona_patch[2]["personas"] == PARTICIPANTS
    assert persona_patch[2]["tags"] == ["evolve-cycle:hb1"]

    heartbeat_patch = next(c for c in calls if c[1] == "/heartbeats/hb1")
    assert heartbeat_patch[2] == {"conversationId": "c-new"}


def test_rotate_numbers_the_cycle_from_existing_tagged_conversations():
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old",
                 "rotateConversationEachRun": True}
    # Every fixture carries the `name` a real conversation carries. Without
    # it this test passed on the fallback path -- `numbers_in` found nothing
    # to parse, `next_number` counted instead, and reverting the whole fix to
    # `len(existing) + 1` still produced "Cycle 3". Reviewer finding on #250:
    # a test named for the numbering rule, pinning the rule it replaced.
    existing = [
        {"id": "c1", "name": "Agora Evolve v1 — Cycle 4",
         "tags": ["evolve-cycle:hb1"], "createdAt": "2026-08-01T00:00:00Z"},
        {"id": "c2", "name": "Agora Evolve v1 — Cycle 5",
         "tags": ["evolve-cycle:hb1"], "createdAt": "2026-08-01T06:00:00Z"},
        {"id": "c3", "name": "K3s Sentinel — Cycle 99",
         "tags": ["some-other-tag"], "createdAt": "2026-08-01T12:00:00Z"},
    ]
    calls = []

    def fake_get(path):
        return 200, {"conversations": existing}

    def fake_internal(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path == "/conversations":
            return 201, {"conversation": {"id": "c-new"}}
        return 200, {}

    with patch.object(rotation, "agora_get", side_effect=fake_get), \
         patch.object(rotation, "agora_internal", side_effect=fake_internal):
        rotation.rotate_cycle_conversation(heartbeat, PARTICIPANTS)

    create_call = next(c for c in calls if c[0] == "POST" and c[1] == "/conversations")
    # The highest number named by a conversation tagged for THIS heartbeat
    # is 5, so this is cycle 6. Counting the two of them would say 3 -- a
    # number cycle 3 already holds -- which is why the numbers 4 and 5 are
    # not 1 and 2 here: a contiguous fixture cannot tell the two rules
    # apart. The live store is NOT in that state today and #250 said it was:
    # 277 tagged conversations, 276 of them carrying a parseable number, max
    # 277, and the one without a number is the very first, named
    # `Agora Evolve` from before the convention existed. Both rules answer
    # 278 there. Parsing is protection against a conversation being deleted,
    # which counting cannot survive -- not a live discrepancy.
    # c3 carries a higher number and the wrong tag, and must not be read.
    assert create_call[2]["name"] == "Agora Evolve v1 — Cycle 6"


def test_rotate_prunes_beyond_retention_keeping_the_newest():
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old",
                 "rotateConversationEachRun": True, "conversationRetention": 3}
    existing = [
        {"id": f"c{i}", "tags": ["evolve-cycle:hb1"], "createdAt": f"2026-08-0{i}T00:00:00Z"}
        for i in range(1, 6)  # c1 (oldest) .. c5 (newest of the pre-existing ones)
    ]
    calls = []

    def fake_get(path):
        return 200, {"conversations": existing}

    def fake_internal(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path == "/conversations":
            return 201, {"conversation": {"id": "c-new"}}
        return 200, {}

    with patch.object(rotation, "agora_get", side_effect=fake_get), \
         patch.object(rotation, "agora_internal", side_effect=fake_internal):
        rotation.rotate_cycle_conversation(heartbeat, PARTICIPANTS)

    archive_calls = [c for c in calls if c[2] == {"archived": True}]
    archived_ids = {c[1].rsplit("/", 1)[-1] for c in archive_calls}
    # retention=3 means 3 active total (the new one + 2 kept old ones) ->
    # keep c5, c4 (newest 2 of the pre-existing 5), archive c3, c2, c1.
    assert archived_ids == {"c1", "c2", "c3"}


def test_rotate_falls_back_to_existing_conversation_on_create_failure():
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old",
                 "rotateConversationEachRun": True}

    def fake_internal(method, path, payload=None):
        if method == "POST" and path == "/conversations":
            return 500, {"error": "boom"}
        return 200, {}

    with patch.object(rotation, "agora_get", return_value=(200, {"conversations": []})), \
         patch.object(rotation, "agora_internal", side_effect=fake_internal):
        result = rotation.rotate_cycle_conversation(heartbeat, PARTICIPANTS)

    assert result == "c-old"


def test_rotate_falls_back_when_no_participants():
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old",
                 "rotateConversationEachRun": True}
    with patch.object(rotation, "agora_get") as mock_get:
        result = rotation.rotate_cycle_conversation(heartbeat, [])
    assert result == "c-old"
    mock_get.assert_not_called()


def test_rotate_falls_back_safely_on_unexpected_exception():
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old",
                 "rotateConversationEachRun": True}
    with patch.object(rotation, "agora_get", side_effect=RuntimeError("network exploded")):
        result = rotation.rotate_cycle_conversation(heartbeat, PARTICIPANTS)
    assert result == "c-old"
