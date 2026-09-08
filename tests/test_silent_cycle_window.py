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

import json
import traceback
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


# --- What a `failed:` record actually says -------------------------------
#
# Moving the `try` up here (above) is only half of "the next silent cycle
# names its own bug". The other half is what the record then holds:
# `lastResult` was `f"failed: {e}"`, and for the exception types this window
# raises that identifies nothing. `str(KeyError("schedule"))` is `"'schedule'"`
# -- no type, no file, no line -- and the traceback that did hold the line
# was never printed anywhere but a pod log, which dies with the pod. That is
# why 1145, 1146, 1148, 1166 and 1181 are undiagnosable today.


def test_raising_frame_names_the_innermost_line(runner):
    """The line that raised, not the call path above it."""
    def inner():
        raise ValueError("boom")

    def outer():
        inner()

    try:
        outer()
    except ValueError as error:
        where = runner.heartbeats.raising_frame(error)

    assert where.startswith("test_silent_cycle_window.py:"), where
    # `inner` raised; `outer` only called it.
    assert where.endswith(" in inner"), where
    # Basename, not an absolute path -- `lastResult` has 200 characters and
    # the directory layout is the same on every pod.
    assert "/" not in where, where


def test_raising_frame_survives_an_exception_that_never_raised(runner):
    """It is called from a failure path, so it may not fail there itself."""
    assert runner.heartbeats.raising_frame(ValueError("never raised")) == "no traceback"


# --- A stdlib raiser names nothing of ours -------------------------------
#
# Cycles 1213 and 1215 both died 32s into their window on 2026-09-08 and
# both recorded `socket.py:720 in readinto: TimeoutError('timed out')`.
# That string is what EVERY http read timeout in this window produces --
# reproduced Cycle 1222 against a hanging socket, twelve frames, nine of
# them stdlib -- so the record named the one part of the stack that could
# not vary. The call path is the part that varies, and it exists only in a
# pod log that dies with the pod.
#
# These raise inside `json`'s decoder rather than inside a socket: same
# shape, foreign raiser under our own frames, and no listening port or
# background thread in the suite to get it there.


def _outer_call_of_ours(payload):
    return _inner_call_of_ours(payload)


def _inner_call_of_ours(payload):
    return json.loads(payload)


def test_own_call_path_names_our_frames_when_the_raiser_is_foreign(runner):
    """What 1213 and 1215 could not say, and now must."""
    try:
        _outer_call_of_ours("{")
    except ValueError as error:
        raiser = runner.heartbeats.raising_frame(error)
        path = runner.heartbeats.own_call_path(error)

    # The raiser is unchanged and still useless on its own.
    assert raiser.startswith("decoder.py:"), raiser
    # The path is the difference from what 1213 wrote.
    assert path, "a foreign raiser must carry our own frames"
    # Outermost first, so it reads as a call path down towards the raiser.
    assert path.index("in test_own_call_path_names_our_frames_when_the_raiser_is_foreign") \
        < path.index("in _outer_call_of_ours") < path.index("in _inner_call_of_ours"), path
    # Basenames only -- an absolute path spends 40 of the 200 characters on
    # a directory layout that is the same on every pod.
    assert "/" not in path, path
    # Nothing foreign in it: the raiser is already recorded separately, and
    # nine stdlib frames would fill the record on their own.
    assert "decoder.py" not in path, path


def test_own_call_path_is_empty_when_the_raiser_is_already_ours(runner):
    """The path is added because the raiser named nothing, not always."""
    try:
        raise ValueError("ours")
    except ValueError as error:
        assert runner.heartbeats.raising_frame(error).startswith(
            "test_silent_cycle_window.py:")
        assert runner.heartbeats.own_call_path(error) == ""


def test_own_call_path_is_empty_when_no_frame_is_ours(runner):
    """Nothing of ours to add leaves the raiser as the whole answer."""
    try:
        _inner_call_of_ours("{")
    except ValueError as caught:
        error = caught

    # Drop every frame of ours, leaving the all-foreign stack.
    tb = error.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    error.__traceback__ = tb

    assert runner.heartbeats.raising_frame(error).startswith("decoder.py:")
    assert runner.heartbeats.own_call_path(error) == ""


def test_a_synthetic_filename_is_not_one_of_our_frames(runner):
    """`<string>` and `<frozen importlib._bootstrap>` are nobody's file.

    The obvious spelling of `_is_ours` was `os.path.abspath(...).startswith(
    root)`, which resolves a relative or synthetic filename against the
    working directory -- and on the pod that directory IS the repo root, so
    all three of these came back as ours. The record would then have named a
    frame that does not exist, in the one field whose whole job is to be
    believed. Asserted on the predicate directly, because with a synthetic
    frame innermost `own_call_path` returns early and would pass either way.
    """
    fake = traceback.FrameSummary("<string>", 1, "<module>")
    assert runner.heartbeats._is_ours(fake) is False
    frozen = traceback.FrameSummary("<frozen importlib._bootstrap>", 1, "_call")
    assert runner.heartbeats._is_ours(frozen) is False
    relative = traceback.FrameSummary("agora_runner/heartbeats.py", 1, "run")
    assert runner.heartbeats._is_ours(relative) is False
    # The control: a real frame of ours, absolute, still counts. Without
    # this the three above pass on a predicate that returns False always.
    real = traceback.FrameSummary(runner.heartbeats.__file__, 1, "run_heartbeat")
    assert runner.heartbeats._is_ours(real) is True


def test_a_synthetic_frame_is_left_out_of_the_call_path(runner):
    """The same thing one level up, where a cycle would actually read it."""
    try:
        _outer_call_of_ours("{")
    except ValueError as caught:
        error = caught

    frames = traceback.extract_tb(error.__traceback__)
    with patch.object(runner.heartbeats.traceback, "extract_tb",
                      lambda _tb: [traceback.FrameSummary("<string>", 1, "<module>")]
                      + list(frames)):
        path = runner.heartbeats.own_call_path(error)
    assert path, "our real frames are still there"
    assert "<string>" not in path, path


def test_the_record_keeps_the_exception_type_when_the_path_is_long(runner):
    """The 200-character cut may not eat the half of the diagnosis that
    says what went wrong. A deep own call path is the case that would --
    the path is last for exactly this reason."""
    def deep(n):
        if n:
            return deep(n - 1)
        return json.loads("{")

    try:
        deep(40)
    except ValueError as error:
        record = (f"failed: {runner.heartbeats.raising_frame(error)}: {error!r}")
        path = runner.heartbeats.own_call_path(error)
        result = (f"{record} via {path}" if path else record)[:200]

    assert len(path) > 200, "this test needs a path longer than the record"
    assert len(result) == 200, result
    assert "JSONDecodeError" in result, result
    assert result.startswith("failed: decoder.py:"), result
