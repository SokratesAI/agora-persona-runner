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


def test_rotate_default_retention_keeps_thirty_when_heartbeat_says_nothing():
    """Edvard's 🔴 Immediately capture, 2026-08-20: "keep the last 30
    conversations for a heartbeat so that i'm able to talk to them". A
    heartbeat that sets no `conversationRetention` must keep 30 active,
    not the 5 this defaulted to for the first eighteen days."""
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old",
                 "rotateConversationEachRun": True}
    # 40 pre-existing tagged conversations, c01 (oldest) .. c40 (newest).
    existing = [
        {"id": f"c{i:02d}", "tags": ["evolve-cycle:hb1"], "createdAt": f"2026-08-20T{i:02d}:00:00Z"}
        for i in range(1, 41)
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

    archived_ids = {c[1].rsplit("/", 1)[-1] for c in calls if c[2] == {"archived": True}}
    # 30 active total = the new one + the newest 29 pre-existing (c40..c12),
    # so exactly c01..c11 get archived.
    assert archived_ids == {f"c{i:02d}" for i in range(1, 12)}


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


def _rotation_calls(folder_response):
    """Runs one rotation with `folder_response` standing in for
    POST /folders, and hands back every internal call it made."""
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old",
                 "rotateConversationEachRun": True}
    calls = []

    def fake_get(path):
        return 200, {"conversations": []}

    def fake_internal(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path == "/conversations":
            return 201, {"conversation": {"id": "c-new"}}
        if method == "POST" and path == "/folders":
            if isinstance(folder_response, Exception):
                raise folder_response
            return folder_response
        return 200, {}

    with patch.object(rotation, "agora_get", side_effect=fake_get), \
         patch.object(rotation, "agora_internal", side_effect=fake_internal):
        result = rotation.rotate_cycle_conversation(heartbeat, PARTICIPANTS)
    return result, calls


def test_rotate_files_the_new_conversation_into_a_folder_named_after_the_heartbeat():
    """Edvard, ideas.md #5: "Heartbeat generated conversations should be
    auto created in the same folder by default"."""
    result, calls = _rotation_calls((201, {"folder": {"id": "f-nova", "name": "Agora Evolve v1"}}))
    assert result == "c-new"
    folder_call = next(c for c in calls if c[1] == "/folders")
    assert folder_call[2] == {"name": "Agora Evolve v1"}
    patches = [c[2] for c in calls if c[1] == "/conversations/c-new"]
    assert {"folderId": "f-nova"} in patches
    # Filing is its own patch, on purpose: bundled with the tag, a folder
    # deleted mid-rotation would 400 the whole request and lose the tag.
    tag_patch = next(p for p in patches if "tags" in p)
    assert "folderId" not in tag_patch
    assert tag_patch["tags"] == ["evolve-cycle:hb1"]


def test_rotate_reuses_the_existing_folder_rather_than_making_one_per_cycle():
    """POST /folders is find-or-create by name, so a second cycle gets a 200
    with the same folder — the rotation must not treat that as a failure."""
    _, calls = _rotation_calls((200, {"folder": {"id": "f-nova", "name": "Agora Evolve v1"}}))
    patches = [c[2] for c in calls if c[1] == "/conversations/c-new"]
    assert {"folderId": "f-nova"} in patches


def test_rotate_still_runs_the_cycle_when_the_folder_call_fails():
    """An unfiled conversation is cosmetic; a cycle that does not run is not."""
    for response in [(500, {}), (201, {}), ValueError("boom")]:
        result, calls = _rotation_calls(response)
        assert result == "c-new", response
        patches = [c[2] for c in calls if c[1] == "/conversations/c-new"]
        assert all("folderId" not in p for p in patches), response
        assert patches[0]["tags"] == ["evolve-cycle:hb1"], response
        heartbeat_patch = next(c for c in calls if c[1] == "/heartbeats/hb1")
        assert heartbeat_patch[2] == {"conversationId": "c-new"}, response


def test_rotate_keeps_the_cycle_tag_when_filing_is_refused():
    """Reviewer finding on agora#63: Agora refuses the whole PATCH if
    `folderId` names a folder that has gone, so bundling the filing with the
    tag would let a folder Edvard deleted mid-rotation take the tag with it.
    The tag is how every later cycle finds this conversation."""
    heartbeat = {"id": "hb1", "name": "Agora Evolve v1", "conversationId": "c-old",
                 "rotateConversationEachRun": True}
    calls = []

    def fake_internal(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path == "/conversations":
            return 201, {"conversation": {"id": "c-new"}}
        if method == "POST" and path == "/folders":
            return 201, {"folder": {"id": "f-gone"}}
        if path == "/conversations/c-new" and "folderId" in (payload or {}):
            return 400, {"error": "unknown folder"}
        return 200, {}

    with patch.object(rotation, "agora_get", side_effect=lambda p: (200, {"conversations": []})), \
         patch.object(rotation, "agora_internal", side_effect=fake_internal):
        result = rotation.rotate_cycle_conversation(heartbeat, PARTICIPANTS)

    assert result == "c-new"
    tag_patch = next(c[2] for c in calls if c[1] == "/conversations/c-new" and "tags" in c[2])
    assert tag_patch["tags"] == ["evolve-cycle:hb1"]
    assert tag_patch["personas"] == PARTICIPANTS
    heartbeat_patch = next(c for c in calls if c[1] == "/heartbeats/hb1")
    assert heartbeat_patch[2] == {"conversationId": "c-new"}
