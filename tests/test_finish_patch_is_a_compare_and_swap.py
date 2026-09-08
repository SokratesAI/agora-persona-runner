"""A run's ending may only overwrite its own claim, never a newer run's.

Idea #267. The claim PATCH at the top of `run_heartbeat` is a compare-and-swap
on `lastRunAt` (agora#89) so two pollers cannot both take one slot. The PATCH
that ends a run was unguarded, and with `HEARTBEAT_MAX_CONCURRENT` at 3
against a 24-minute cadence and a 45-minute turn cap, runs overlap by design:
an older run finishing would overwrite a newer run's `lastRunAt` with its own
FINISH time and its `lastResult` with its own result.

Both halves cost something real. The `lastResult` half destroys the record
#903 and #906 built so a run that dies in its startup window names itself. The
`lastRunAt` half is worse: the next tick reads the older run's finish time as
the slot the newer run took, so the newer run's own claim comes back 409 and
it returns without running -- a due firing that produces no conversation at
all, logged as "another poller claimed this run", which is not what happened.

These tests pin: **the ending carries `ifLastRunAt` set to the value this run
itself claimed, and a 409 on it writes nothing.**
"""

from unittest.mock import patch

import pytest

import agora_runner
from agora_runner.config import NOVA_PERSONA_ID


@pytest.fixture
def runner():
    return agora_runner


#: The heartbeat's `lastRunAt` as the poller read it. The claim swaps AGAINST
#: this value and writes a different one; the ending must guard on the value
#: written, not on this. Two distinct strings so a test cannot pass by both
#: sides happening to be equal.
PREVIOUS_RUN_AT = "2026-09-08T14:48:00+00:00"


def _nova_heartbeat():
    return {"id": "hb1", "personaId": NOVA_PERSONA_ID, "conversationId": "conv-1",
            "schedule": "every@24m@16:00", "name": "Nova", "enabled": True,
            "lastRunAt": PREVIOUS_RUN_AT}


def _run(runner, claim_status=200, finish_status=200):
    """Run a heartbeat to completion and report every PATCH it made.

    The reply is stubbed, so this exercises the ordinary success path -- the
    ending PATCH here is the one at the bottom of `run_heartbeat`, not an
    error path.
    """
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    patches = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == "/heartbeats/hb1":
            patches.append(payload)
            return (claim_status if len(patches) == 1 else finish_status), {}
        return 200, {}

    persona = {"id": NOVA_PERSONA_ID, "name": "Nova", "model": "claude-cli:opus",
               "capabilities": dict(runner.NO_CAPS)}
    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "rotate_cycle_conversation",
                      return_value="conv-cycle-1231"), \
         patch.object(runner.heartbeats, "nova_health_note", return_value=""), \
         patch.object(runner.heartbeats, "generate_reply", return_value="done"), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.heartbeats, "audit", return_value=(200, "chip")), \
         patch.object(runner.heartbeats, "agora_internal",
                      side_effect=fake_agora_internal):
        runner.run_heartbeat(_nova_heartbeat())
    return patches


def test_the_ending_guards_on_the_value_this_run_claimed(runner):
    claim, finish = _run(runner)[:2]
    # Precondition: the claim really did swap against the value the poller
    # read and really did write a different one. Without this the assertion
    # below could pass on a run that never claimed anything.
    assert claim["ifLastRunAt"] == PREVIOUS_RUN_AT
    assert claim["lastRunAt"] != PREVIOUS_RUN_AT
    assert finish["ifLastRunAt"] == claim["lastRunAt"]


def test_the_ending_still_clears_forcerun_and_moves_the_clock(runner):
    """The guard is added to the ending, it does not replace what it wrote."""
    claim, finish = _run(runner)[:2]
    assert finish["forceRun"] is False
    assert finish["lastResult"] and finish["lastResult"] != "running"
    assert finish["lastRunAt"] != claim["lastRunAt"]


def test_a_newer_run_owning_the_heartbeat_is_not_retried_unguarded(runner):
    """409 means a newer run owns all three fields. Leave them."""
    patches = _run(runner, finish_status=409)
    assert len(patches) == 2, "a refused ending must not be rewritten without the guard"


def test_an_unclaimed_run_ends_without_a_guard(runner):
    """No value of ours is in the field, so guarding on one 409s forever and
    `forceRun` is never cleared -- which re-fires this run for good."""
    patches = _run(runner, claim_status=500)
    claim, finish = patches[0], patches[1]
    assert claim["ifLastRunAt"] == PREVIOUS_RUN_AT  # precondition: it did try
    assert "ifLastRunAt" not in finish
    assert finish["forceRun"] is False


def test_an_early_return_ends_with_the_same_guard(runner):
    """`persona is None` returns before the run starts and PATCHes its own
    ending; it must not clobber a newer run either."""
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    patches = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == "/heartbeats/hb1":
            patches.append(payload)
        return 200, {}

    with patch.object(runner.heartbeats, "fetch_persona", return_value=None), \
         patch.object(runner.heartbeats, "agora_internal",
                      side_effect=fake_agora_internal):
        runner.run_heartbeat(_nova_heartbeat())
    assert len(patches) == 2
    assert patches[1]["lastResult"] == "failed: persona not found"
    assert patches[1]["ifLastRunAt"] == patches[0]["lastRunAt"]
