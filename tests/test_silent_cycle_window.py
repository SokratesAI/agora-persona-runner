"""A cycle that dies before its opening chip still says so.

The owner's idea #267, 2026-09-08: *"Eighteen silent cycles in one report,
eleven of them in four days -- read three transcripts before theorising."*
The transcripts hold nothing, and that is the finding rather than a dead
end: `rotate_cycle_conversation` creates, numbers and tags the cycle's
conversation, and the first thing a run ever posts into it is the opening
chip -- so everything in between ran on a bare thread with no enclosing
handler. Anything raising there killed the thread with no chip, no closing
line, no `lastResult` PATCH and no stub marker, which is `cycle_postmortem`'s
`silent` verdict: 16 of them, 1029/1038/1082/1133/1145/1146/1148/1166/1181
among the recent ones.

These tests pin the invariant the fix is: **from the moment the conversation
exists, every exit path writes a closing chip and a `lastResult`.**
"""

from unittest.mock import patch

import pytest

import agora_runner
from agora_runner import cycle_stub
from agora_runner.config import NOVA_PERSONA_ID


@pytest.fixture
def runner():
    return agora_runner


#: What `rotate_cycle_conversation` hands back -- deliberately different from
#: the heartbeat's own `conversationId`, so a check on it cannot pass by both
#: sides being the same string.
ROTATED_INTO = "conv-cycle-1203"


def _nova_heartbeat():
    return {"id": "hb1", "personaId": NOVA_PERSONA_ID, "conversationId": "conv-1",
            "schedule": "every@18m", "name": "Nova cycle", "enabled": True}


def _nova_persona(runner):
    return {"id": NOVA_PERSONA_ID, "name": "Nova", "model": "claude-cli:opus",
            "capabilities": dict(runner.NO_CAPS)}


def _run_with_window_failure(runner, exploder="nova_health_note"):
    """Run a heartbeat whose *pre-chip* window raises, and report what it left.

    `nova_health_note` and `fetch_vault_context` both sit between the
    rotation and the opening chip. Either is a real caller; the point is the
    window, not the callee.
    """
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    chips, patches = [], []

    def fake_audit(_name, conversation_id, kind, text):
        chips.append((conversation_id, kind, text))
        return 200, "chip"

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == "/heartbeats/hb1":
            patches.append(payload)
        return 200, {}

    with patch.object(runner.heartbeats, "fetch_persona",
                      return_value=_nova_persona(runner)), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "rotate_cycle_conversation",
                      return_value=ROTATED_INTO), \
         patch.object(runner.heartbeats, exploder,
                      side_effect=RuntimeError("the window exploded")), \
         patch.object(runner.heartbeats, "generate_reply",
                      return_value="should never be reached"), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.heartbeats, "audit", side_effect=fake_audit), \
         patch.object(runner.heartbeats, "agora_internal",
                      side_effect=fake_agora_internal), \
         patch.object(cycle_stub, "write_stub") as stub:
        runner.run_heartbeat(_nova_heartbeat())
    return chips, patches, stub


def test_a_failure_before_the_opening_chip_still_closes_the_run(runner):
    """The whole point: no chip is not the same as no record."""
    chips, patches, _stub = _run_with_window_failure(runner)

    # It never got as far as the opening chip -- that is the precondition,
    # and without asserting it this test would pass on a run that spoke.
    assert not any("every@18m" in text for _c, _k, text in chips)
    # ...and it still closed the run in the conversation the owner reads.
    assert len(chips) == 1
    conversation_id, kind, text = chips[0]
    # Into THIS cycle's conversation, not the one it rotated away from.
    # `cycle_postmortem` reads the new one, so a closing line posted into the
    # previous cycle's transcript would leave this cycle looking silent and
    # would put a second closing line under a cycle that already has one.
    assert conversation_id == ROTATED_INTO
    assert kind == "heartbeat"
    assert "finished in" in text
    # The runner's own words, not a bucket.
    assert "the window exploded" in text

    # And the heartbeat is not wedged on `running`: the closing PATCH landed.
    assert patches, "no PATCH reached the heartbeat at all"
    assert patches[-1]["lastResult"].startswith("failed:")
    assert "the window exploded" in patches[-1]["lastResult"]
    assert patches[-1]["forceRun"] is False


def test_the_window_failure_reaches_the_journal_marker_too(runner):
    """A dead cycle's marker is written from the same handler, so it covers
    this window now as well -- that is what makes the hole visible on the
    feed rather than only in a pod log that dies with the pod."""
    _chips, _patches, stub = _run_with_window_failure(runner)
    assert stub.called
    assert "the window exploded" in stub.call_args.args[0]


def test_the_vault_fetch_is_inside_the_guard_as_well(runner):
    """Two different callers in the same window, so the test is about the
    window rather than about `nova_health_note`."""
    heartbeat = dict(_nova_heartbeat(), vaultPaths=["some/path.md"])
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    patches = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == "/heartbeats/hb1":
            patches.append(payload)
        return 200, {}

    with patch.object(runner.heartbeats, "fetch_persona",
                      return_value=_nova_persona(runner)), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "fetch_vault_context",
                      side_effect=RuntimeError("vault unreachable")), \
         patch.object(runner.heartbeats, "generate_reply", return_value="unreached"), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal",
                      side_effect=fake_agora_internal), \
         patch.object(cycle_stub, "write_stub"):
        runner.run_heartbeat(heartbeat)

    assert patches[-1]["lastResult"].startswith("failed:")
    assert "vault unreachable" in patches[-1]["lastResult"]


def test_a_healthy_run_still_posts_the_opening_chip_before_the_model_call(runner):
    """The guard must not swallow the chip, and the chip must still come
    FIRST -- the owner asked for it up front in 2026-08-03 precisely so a
    45-minute cycle is visible while it runs."""
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    order = []

    def fake_audit(_name, _conversation_id, _kind, text):
        order.append(("chip", text))
        return 200, "chip"

    def fake_generate_reply(*_args, **_kwargs):
        order.append(("model", ""))
        return "a real reply"

    with patch.object(runner.heartbeats, "fetch_persona",
                      return_value=_nova_persona(runner)), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply",
                      side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.heartbeats, "audit", side_effect=fake_audit), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(_nova_heartbeat())

    kinds = [k for k, _ in order]
    assert kinds == ["chip", "model", "chip"], order
    assert "every@18m" in order[0][1]
    assert "replied" in order[-1][1]
