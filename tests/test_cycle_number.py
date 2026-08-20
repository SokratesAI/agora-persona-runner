"""One counter for Agora and the journal -- Edvard's Immediately capture,
2026-08-20: "They need to be the same number."
"""
import json

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
    # The counter doc: empty (no prior claim), and the write succeeds --
    # see test_claim_next_number_* below for the doc's own behaviour.
    monkeypatch.setattr(cycle_number, "vault_read_path_rev", lambda path: (None, None))
    monkeypatch.setattr(cycle_number, "vault_write_path",
                        lambda path, content, if_rev=None, allow_shrink=False: "written")

    used = conversation_rotation.rotate_cycle_conversation(
        {"id": "hb-1", "name": "Nova", "conversationId": "old-id",
         "rotateConversationEachRun": True},
        [{"personaId": "p-1"}],
    )

    assert used == "new-id"
    # len(existing) + 1 would have been 5 -- a number Cycle 5 may already
    # hold and, more to the point, one the journal would never match.
    assert posted["name"] == "Nova — Cycle 10"


def test_claim_next_number_seeds_the_counter_from_the_scan_on_first_use():
    """Nothing has ever claimed hb-1's counter before -- the floor comes
    entirely from the conversation scan, same answer next_number gives."""
    conversations = [conv("Nova — Cycle %d" % n) for n in (1, 2, 3, 9)]
    written = {}

    def fake_write(path, content, if_rev=None, allow_shrink=False):
        written["path"], written["content"], written["if_rev"] = path, content, if_rev
        return "written"

    with pytest.MonkeyPatch.context() as m:
        m.setattr(cycle_number, "vault_read_path_rev", lambda path: (None, None))
        m.setattr(cycle_number, "vault_write_path", fake_write)
        claimed = cycle_number.claim_next_number("hb-1", conversations, TAG)

    assert claimed == 10
    assert written["if_rev"] is None  # conditional create, not an overwrite
    assert json.loads(written["content"]) == {"n": 10}
    assert "hb-1" in written["path"]


def test_claim_next_number_uses_the_stored_counter_once_seeded():
    """A later claim, with the counter doc already ahead of what the
    (possibly stale) conversation listing shows -- the counter wins."""
    conversations = [conv("Nova — Cycle %d" % n) for n in (1, 2, 3, 9)]

    with pytest.MonkeyPatch.context() as m:
        m.setattr(cycle_number, "vault_read_path_rev",
                  lambda path: (json.dumps({"n": 15}), "3-abc"))
        m.setattr(cycle_number, "vault_write_path", lambda path, content, if_rev=None, allow_shrink=False: "written")
        claimed = cycle_number.claim_next_number("hb-1", conversations, TAG)

    assert claimed == 16


def test_claim_next_number_retries_once_on_conflict():
    """Two concurrent rotations for the SAME heartbeat: this call loses the
    first race (its if_rev is now stale), re-reads, and claims the next
    number after the winner rather than colliding with it."""
    conversations = [conv("Nova — Cycle %d" % n) for n in (1, 2)]
    reads = [(None, None), (json.dumps({"n": 3}), "2-rev")]  # winner claimed 3 in between
    writes = []

    def fake_read(path):
        return reads.pop(0)

    def fake_write(path, content, if_rev=None, allow_shrink=False):
        writes.append((content, if_rev))
        if if_rev is None:
            return "FAILED(409 conflict: document already exists)"
        return "written"

    with pytest.MonkeyPatch.context() as m:
        m.setattr(cycle_number, "vault_read_path_rev", fake_read)
        m.setattr(cycle_number, "vault_write_path", fake_write)
        claimed = cycle_number.claim_next_number("hb-1", conversations, TAG)

    assert claimed == 4  # not 3 -- the winner already holds that
    assert len(writes) == 2
    assert json.loads(writes[-1][0]) == {"n": 4}


def test_claim_next_number_falls_back_to_scan_when_the_counter_is_unreachable():
    """A rotation bug -- or CouchDB being briefly down -- must never be the
    reason a real cycle doesn't run. Same answer next_number would give."""
    conversations = [conv("Nova — Cycle %d" % n) for n in (1, 2, 3, 9)]

    def blow_up(path):
        raise OSError("no route to couchdb")

    with pytest.MonkeyPatch.context() as m:
        m.setattr(cycle_number, "vault_read_path_rev", blow_up)
        claimed = cycle_number.claim_next_number("hb-1", conversations, TAG)

    assert claimed == 10 == cycle_number.next_number(conversations, TAG)


def test_claim_next_number_falls_back_when_writes_keep_failing_non_conflict():
    """A 500 from CouchDB is not a race to retry against -- retrying it
    the same way as a 409 would just spin. Fall back instead."""
    conversations = [conv("Nova — Cycle %d" % n) for n in (1, 2, 3, 9)]

    with pytest.MonkeyPatch.context() as m:
        m.setattr(cycle_number, "vault_read_path_rev", lambda path: (None, None))
        m.setattr(cycle_number, "vault_write_path",
                  lambda path, content, if_rev=None, allow_shrink=False: "FAILED(500: couchdb unwell)")
        claimed = cycle_number.claim_next_number("hb-1", conversations, TAG)

    assert claimed == 10


def test_claim_next_number_different_heartbeats_never_collide():
    """The counter is per-heartbeat -- a claim for hb-2 must never read or
    write hb-1's doc, so two different heartbeats claiming at the same
    moment never contend with each other."""
    paths_read = []

    def fake_read(path):
        paths_read.append(path)
        return (None, None)

    with pytest.MonkeyPatch.context() as m:
        m.setattr(cycle_number, "vault_read_path_rev", fake_read)
        m.setattr(cycle_number, "vault_write_path",
                  lambda path, content, if_rev=None, allow_shrink=False: "written")
        cycle_number.claim_next_number("hb-1", [], cycle_tag("hb-1"))
        cycle_number.claim_next_number("hb-2", [], cycle_tag("hb-2"))

    assert paths_read[0] != paths_read[1]
    assert "hb-1" in paths_read[0] and "hb-2" not in paths_read[0]
    assert "hb-2" in paths_read[1] and "hb-1" not in paths_read[1]
