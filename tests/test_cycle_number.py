"""One counter for Agora and the journal -- Edvard's Immediately capture,
2026-08-20: "They need to be the same number."
"""
import pytest

from agora_runner import cycle_number
from agora_runner.conversation_rotation import cycle_tag

TAG = cycle_tag("hb-1")


def conv(name, tag=TAG, **extra):
    return dict({"name": name, "tags": [tag] if tag else []}, **extra)


def test_numbers_come_off_the_names_not_the_count():
    """The drift this module exists to remove: 277 runs happened and the
    newest is named Cycle 277, so the next one is 278 -- regardless of how
    many of those runs got as far as writing a journal entry."""
    conversations = [conv("Nova — Cycle %d" % n) for n in range(1, 278)]
    assert cycle_number.numbers_in(conversations, TAG)[-1] == 277
    assert cycle_number.next_number(conversations, TAG) == 278


def test_a_deleted_conversation_does_not_reissue_a_used_number():
    """The reason this parses instead of counting. `len + 1` on a list with
    a hole gives 3, which cycle 3 already owns."""
    conversations = [conv("Nova — Cycle 1"), conv("Nova — Cycle 3")]
    assert len(conversations) + 1 == 3
    assert cycle_number.next_number(conversations, TAG) == 4


def test_other_heartbeats_conversations_are_not_counted():
    conversations = [
        conv("Nova — Cycle 9"),
        conv("K3s Sentinel — Cycle 400", tag=cycle_tag("hb-other")),
        conv("some unrelated chat", tag=None),
    ]
    assert cycle_number.numbers_in(conversations, TAG) == [9]
    assert cycle_number.next_number(conversations, TAG) == 10


def test_unparseable_names_are_skipped_not_guessed():
    conversations = [conv("Nova — Cycle 5"), conv("Nova — renamed by hand")]
    assert cycle_number.numbers_in(conversations, TAG) == [5]
    assert cycle_number.next_number(conversations, TAG) == 6


def test_first_rotation_ever_falls_back_to_counting():
    assert cycle_number.next_number([], TAG) == 1
    assert cycle_number.next_number([conv("Nova — no number here")], TAG) == 2


def test_current_number_is_the_running_cycles_own(monkeypatch):
    """`rotate_cycle_conversation` creates this cycle's conversation before
    the cycle wakes, so the highest that exists is the caller's own."""
    monkeypatch.setattr(cycle_number, "agora_get", lambda path: (
        200, {"conversations": [conv("Nova — Cycle %d" % n) for n in (275, 276, 277)]}
    ))
    assert cycle_number.current_number("hb-1") == 277


@pytest.mark.parametrize("outcome", [
    lambda path: (503, {}),
    lambda path: (_ for _ in ()).throw(OSError("no route to agora")),
])
def test_unreadable_returns_none_rather_than_a_plausible_number(monkeypatch, outcome):
    """A guess here is exactly the bug: it silently reintroduces a second
    counter, which is what drifted in the first place."""
    monkeypatch.setattr(cycle_number, "agora_get", outcome)
    assert cycle_number.current_number("hb-1") is None


def test_rotation_asks_this_module_for_the_number(monkeypatch):
    """The two numbers cannot diverge because there is only one of them.
    Names the conversation `rotate_cycle_conversation` would actually POST."""
    from agora_runner import conversation_rotation

    existing = [conv("Nova — Cycle %d" % n) for n in (1, 2, 3, 9)]
    posted = {}

    monkeypatch.setattr(conversation_rotation, "agora_get",
                        lambda path: (200, {"conversations": existing}))

    def fake_internal(method, path, payload=None):
        if method == "POST" and path == "/conversations":
            posted.update(payload)
            return 201, {"conversation": {"id": "new-id"}}
        return 200, {}

    monkeypatch.setattr(conversation_rotation, "agora_internal", fake_internal)

    used = conversation_rotation.rotate_cycle_conversation(
        {"id": "hb-1", "name": "Nova", "conversationId": "old-id",
         "rotateConversationEachRun": True},
        [{"personaId": "p-1"}],
    )

    assert used == "new-id"
    # len(existing) + 1 would have been 5 -- a number Cycle 5 may already
    # hold and, more to the point, one the journal would never match.
    assert posted["name"] == "Nova — Cycle 10"
