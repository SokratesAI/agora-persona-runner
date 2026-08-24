"""A dead cycle leaves a marker on the feed, and the marker does not disarm the alarm.

Sokrates' proposal on Edvard's `issues.md`, 2026-08-24: a run that fails
before it can write leaves nothing on the journal feed, so three dead cycles
looked exactly like three quiet hours. The half these tests care most about
is the trap in it -- a marker is a journal entry, and `stall_notice` dedupes
on the newest journal entry's write time, so a marker that counted as the
loop writing would silence the one message that reaches Edvard's phone.
"""

from datetime import datetime
from unittest.mock import patch

import pytest

import agora_runner
from agora_runner import cycle_stub
from agora_runner.config import OSLO
from agora_runner.nova_journal import (SILENCE_TITLE, build_status, parse_heading,
                                       parse_journal)


@pytest.fixture
def runner():
    return agora_runner


def test_a_marker_declares_itself_and_carries_no_cycle_number():
    body = cycle_stub.stub_markdown("failed: Could not resolve host: github.com")
    heading = body.splitlines()[0]
    parsed = parse_heading(heading.lstrip("# "))
    assert parsed["kind"] == "silence"
    assert parsed["cycle"] is None
    # The runner's own words reach the card rather than a paraphrase of them.
    assert "Could not resolve host: github.com" in body
    assert SILENCE_TITLE in body


def test_a_report_is_still_a_report_and_an_ordinary_entry_still_a_cycle():
    assert parse_heading("2026-08-24 14:20 (Oslo) — Cycle 365")["kind"] == "cycle"
    assert parse_heading("Report · Cycles 350–357")["kind"] == "report"


def _corpus(newest, older):
    return f"{newest}\n{older}\n"


def test_a_marker_does_not_move_the_stamp_the_stall_notice_dedupes_on():
    """The whole point. Without this, a loop failing every cycle goes unreported."""
    entry = ("### 2026-08-24 12:00 (Oslo) — Cycle 364\n\nDid a thing.\n\n"
             "---\nPR: none | Outcome: shipped\n")
    marker = cycle_stub.stub_markdown(
        "failed: boom", when=datetime(2026, 8, 24, 14, 20, tzinfo=OSLO))
    with_marker = parse_journal(_corpus(marker, entry))
    without = parse_journal(_corpus(entry, ""))
    marked, plain = build_status(with_marker), build_status(without)
    assert marked["lastWrittenAt"] == plain["lastWrittenAt"]
    # And it is Cycle 364's stamp, not the marker's 14:20.
    assert marked["lastWrittenAt"].startswith("2026-08-24T12:00")
    # The header still describes the last real cycle rather than the marker.
    assert marked["cycle"] == 364
    assert marked["lastOutcome"] == "shipped"
    # But the marker is on the feed, which is the thing that was missing.
    assert any(e["kind"] == "silence" for e in with_marker)


def test_next_seq_takes_the_number_after_the_highest_in_the_folder():
    assert cycle_stub.next_seq(["a/070-cycle-65.md", "a/418-cycle-364.md"]) == 419
    assert cycle_stub.next_seq([]) == 1


def test_a_sequence_conflict_walks_up_instead_of_giving_up():
    """Two runners racing for one number is what `if_rev=None` is for."""
    attempts = []

    def write(path, body, if_rev=None):
        attempts.append(path)
        assert if_rev is None, "a marker must never overwrite an existing entry"
        return "written" if len(attempts) == 3 else "FAILED(409 conflict: taken)"

    path = cycle_stub.write_stub("failed: boom",
                                 list_paths=lambda: ["j/418-cycle-364.md"],
                                 write=write)
    assert [p.rsplit("/", 1)[-1] for p in attempts] == [
        "419-silence.md", "420-silence.md", "421-silence.md"]
    assert path.endswith("421-silence.md")


def test_a_refusal_that_is_not_a_conflict_is_not_retried():
    calls = []

    def write(path, body, if_rev=None):
        calls.append(path)
        return "FAILED(500)"

    assert cycle_stub.write_stub("failed: boom", list_paths=list, write=write) is None
    assert len(calls) == 1


def test_a_marker_never_raises_out_of_the_failure_path():
    def boom(*_args, **_kwargs):
        raise RuntimeError("couch is down")

    assert cycle_stub.write_stub("failed: boom", list_paths=boom, write=boom) is None
    assert cycle_stub.write_stub("failed: boom", list_paths=list, write=boom) is None


def test_write_stub_gives_up_rather_than_spinning_on_endless_conflicts():
    calls = []

    def write(path, body, if_rev=None):
        calls.append(path)
        return "FAILED(409 conflict: taken)"

    assert cycle_stub.write_stub("failed: boom", list_paths=list, write=write,
                                 attempts=3) is None
    assert len(calls) == 3


# ---------------------------------------------------------------------------
# The wiring. A marker is only worth anything if the failure path reaches it.


def _failing_heartbeat(runner, heartbeat, persona):
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply",
                      side_effect=RuntimeError("Could not resolve host: github.com")), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})), \
         patch.object(cycle_stub, "write_stub") as stub:
        runner.run_heartbeat(heartbeat)
    return stub


def test_a_failed_nova_cycle_writes_a_marker(runner):
    from agora_runner.config import NOVA_PERSONA_ID

    heartbeat = {"id": "hb1", "personaId": NOVA_PERSONA_ID, "conversationId": "conv-1",
                 "schedule": "every@72m", "name": "Nova cycle", "enabled": True}
    persona = {"id": NOVA_PERSONA_ID, "name": "Nova", "model": "claude-cli:opus",
               "capabilities": dict(runner.NO_CAPS)}
    stub = _failing_heartbeat(runner, heartbeat, persona)
    assert stub.called
    assert "Could not resolve host" in stub.call_args.args[0]


def test_a_failed_monitoring_heartbeat_writes_nothing(runner):
    """It writes no entry when it succeeds either, so a card would be a lie."""
    heartbeat = {"id": "hb2", "personaId": "someone-else", "conversationId": "conv-2",
                 "schedule": "every@10m", "name": "Watchdog", "enabled": True}
    persona = {"id": "someone-else", "name": "Watchdog", "model": "claude-cli:haiku",
               "capabilities": dict(runner.NO_CAPS)}
    assert not _failing_heartbeat(runner, heartbeat, persona).called
