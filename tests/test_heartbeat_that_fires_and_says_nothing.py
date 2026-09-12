"""A heartbeat can fire on schedule and produce nothing at all.

Cycle 1450, on idea #122. `K3s Sentinel` fired on all fourteen days from
2026-08-29 to 2026-09-11 and its last actual sentence is dated 2026-08-28:
every run since is three `kubectl_read` tool calls and then the turn ends.
`heartbeat_health` printed `ok` for fifteen days, correctly by the only
question it asked --- did it run --- and a watcher that fires and says
nothing is indistinguishable from a watcher whose cluster is healthy.

**And then I read the heartbeat and it was working.** Its `lastResult` says
`checked, nothing to report (not posted to chat)` and its task says *"If all
checks come back clean, reply with exactly: NO_ISSUES_FOUND --- nothing
else."* So the silence was deliberate, my finding was a false alarm, and the
field that separates the two cases is one nothing here had ever read. Both
halves are pinned below: the conversation can only raise a verdict the run
record does not already account for.
"""

from datetime import datetime, timedelta, timezone

import pytest

from agora_runner import heartbeat_liveness as hl
from tools import heartbeat_health as hh


NOW = datetime(2026, 9, 12, 9, 30, tzinfo=timezone.utc)


def _stamp(delta):
    return (NOW - delta).isoformat().replace("+00:00", "Z")


def _tool_call(delta, capability="kubectl_read"):
    return {
        "text": f"{capability}: get pods",
        "ts": _stamp(delta),
        "activity": {"capability": capability, "detail": "get pods"},
    }


def _reply(delta, text="Alt er stabilt!", activity=None):
    message = {"text": text, "ts": _stamp(delta)}
    if activity:
        message["activity"] = {"capability": activity}
    return message


def _ok_row(schedule="daily@12:00"):
    return {
        "name": "K3s Sentinel",
        "schedule": schedule,
        "verdict": "ok",
        "detail": "daily; last ran recently",
        "last": NOW - timedelta(hours=1),
        "note": "daily",
    }


# --- what counts as the persona actually saying something ----------------


def test_a_bare_message_is_a_reply_and_a_tool_call_is_not():
    # Agora's own personas post prose with no activity block at all.
    assert hl.is_reply({"text": "Hei Edvard, all clear."})
    # Every tool call is recorded as a message too, and is not output.
    assert not hl.is_reply(_tool_call(timedelta(0)))


def test_the_bridge_posts_its_prose_as_an_assistant_text_activity():
    """The half my first version missed, measured against my own loop.

    The hourly `Nova` conversation holds 103 messages and *not one* of them
    lacks an `activity` block --- the Claude bridge files a turn's prose as
    `assistant_text`. A rule that only looked for a missing `activity` would
    have called this loop, running at the time of measurement, mute.
    """
    assert hl.is_reply(_reply(timedelta(0), "I'll start by reading.", "assistant_text"))
    assert not hl.is_reply(_reply(timedelta(0), "Bash: ls", "Bash"))


def test_an_empty_message_is_not_a_reply():
    assert not hl.is_reply({"text": "   ", "ts": _stamp(timedelta(0))})
    assert not hl.is_reply(None)


def test_last_reply_at_takes_the_newest_and_ignores_tool_calls():
    conversation = {
        "messages": [
            _reply(timedelta(days=15)),
            _tool_call(timedelta(days=1)),
            _reply(timedelta(days=3)),
            _tool_call(timedelta(hours=2)),
        ]
    }
    assert hl.last_reply_at(conversation) == NOW - timedelta(days=3)


def test_a_conversation_of_nothing_but_tool_calls_has_no_reply():
    conversation = {"messages": [_tool_call(timedelta(days=n)) for n in range(14)]}
    assert hl.last_reply_at(conversation) is None


# --- the verdict ---------------------------------------------------------


def _fourteen_silent_days():
    return {
        "messages": [_reply(timedelta(days=15))]
        + [_tool_call(timedelta(days=n)) for n in range(1, 15)]
    }


def test_fourteen_silent_days_with_no_run_record_is_mute():
    verdict = hl.judge_mute(_ok_row(), _fourteen_silent_days(), NOW)
    assert verdict["verdict"] == "mute"
    assert "producing nothing" in verdict["detail"]
    assert "allowed 2d" in verdict["detail"]


def test_the_sentinel_as_it_actually_was_is_NOT_mute():
    """The correction, as the live values that produced it.

    This is the test that must fail if the run-record gate is ever removed:
    the same fourteen silent days, with the record Agora actually holds,
    are a watcher working exactly as its task tells it to.
    """
    verdict = hl.judge_mute(
        _ok_row(),
        _fourteen_silent_days(),
        NOW,
        "checked, nothing to report (not posted to chat)",
    )
    assert verdict["verdict"] == "ok"


def test_the_whole_measured_vocabulary_of_run_records_is_accounted_for():
    """Every `lastResult` across the ten live heartbeats, 2026-09-12."""
    for record in (
        "checked, nothing to report (not posted to chat)",
        "replied 3205 chars",
        "running",
        "workflow: 2 steps, 2 rounds, 2 replies posted",
    ):
        assert hl.run_accounted_for(record), record


def test_a_run_record_that_names_no_outcome_is_not_accounted_for():
    # The only case where a silent conversation is evidence of anything.
    assert not hl.run_accounted_for(None)
    assert not hl.run_accounted_for("")
    assert not hl.run_accounted_for("   ")
    assert not hl.run_accounted_for("error: tool call limit reached")


def test_a_heartbeat_that_replied_inside_its_own_window_stays_ok():
    conversation = {"messages": [_reply(timedelta(hours=2)), _tool_call(timedelta(hours=2))]}
    assert hl.judge_mute(_ok_row(), conversation, NOW)["verdict"] == "ok"


def test_the_window_is_the_schedules_own_and_not_a_flat_number():
    """The mutation that matters: a flat threshold kills the weekly cycles.

    Measured live 2026-09-12 --- the Sunday architecture cycle had last
    spoken 6.2 days earlier and is perfectly healthy, because its turn is a
    week. Any flat threshold tight enough to catch the Sentinel at 15 days
    against a *daily* schedule would call that one dead.
    """
    weekly = _ok_row(schedule="cron@0 6 * * 0")
    six_days = {"messages": [_reply(timedelta(days=6, hours=5))]}
    assert hl.judge_mute(weekly, six_days, NOW)["verdict"] == "ok"
    # The same silence against a daily schedule is mute.
    assert hl.judge_mute(_ok_row(), six_days, NOW)["verdict"] == "mute"


def test_a_deliberately_silent_watcher_still_prints_what_it_found():
    """Idea #122's actual ask: the Sentinel needs somewhere to report TO him.

    A run record of "nothing to report" is a finding, not an absence, and
    dropping it is how fifteen days of clean scans became invisible.
    """
    row = dict(_ok_row(), lastResult="checked, nothing to report (not posted to chat)")
    report, status = hh.format_report([row], None)
    assert status == 0
    assert "last run: checked, nothing to report (not posted to chat)" in report


def test_an_ok_row_with_no_run_record_prints_no_dangling_suffix():
    report, _ = hh.format_report([_ok_row()], None)
    assert "last run:" not in report
    assert "ok   K3s Sentinel — daily; last ran recently" in report


def test_an_off_or_overdue_row_is_never_relabelled_mute():
    """Two causes on one row is the merged-cause failure, again."""
    silent = {"messages": [_reply(timedelta(days=40))]}
    for verdict in ("off", "overdue", "off_marked", "unjudged"):
        row = dict(_ok_row(), verdict=verdict)
        assert hl.judge_mute(row, silent, NOW)["verdict"] == verdict


def test_a_brand_new_conversation_with_no_reply_yet_is_not_mute():
    """The hourly loop rotates its conversation every run.

    `Nova`'s `conversationId` points at the cycle running right now, so for
    the first minutes of every cycle it holds no reply at all. Measuring
    from the conversation's own creation is what stops that reading as
    silence --- the same call `judge` makes for a heartbeat that has never
    run.
    """
    fresh = {"createdAt": _stamp(timedelta(minutes=4)), "messages": [_tool_call(timedelta(minutes=1))]}
    row = _ok_row(schedule="every@40m@16:00")
    assert hl.judge_mute(row, fresh, NOW)["verdict"] == "ok"


def test_a_conversation_that_has_never_replied_and_is_old_is_mute():
    stale = {"createdAt": _stamp(timedelta(days=30)), "messages": [_tool_call(timedelta(hours=1))]}
    verdict = hl.judge_mute(_ok_row(), stale, NOW)
    assert verdict["verdict"] == "mute"
    assert "has never replied" in verdict["detail"]


def test_a_conversation_with_no_creation_stamp_is_left_alone():
    assert hl.judge_mute(_ok_row(), {"messages": []}, NOW)["verdict"] == "ok"


def test_an_unreadable_schedule_is_not_judged_for_muteness():
    row = dict(_ok_row(schedule="whenever I feel like it"))
    silent = {"messages": [_reply(timedelta(days=40))]}
    assert hl.judge_mute(row, silent, NOW)["verdict"] == "ok"


# --- the report and its exit code ----------------------------------------


def test_a_mute_row_raises_the_exit_status_and_names_itself():
    report, status = hh.format_report([dict(_ok_row(), verdict="mute", detail="said nothing")], None)
    assert status == 2
    assert "MUTE — K3s Sentinel: said nothing" in report
    assert "its own run record does not say what it produced" in report


def test_a_conversation_that_could_not_be_read_never_reads_as_clean():
    report, status = hh.format_report(
        [_ok_row()], None, [("K3s Sentinel", "could not read http://agora/...: timed out")]
    )
    assert status == 1
    assert "CANNOT JUDGE MUTENESS — K3s Sentinel" in report


def test_a_real_finding_outranks_an_unreadable_conversation():
    """An incomplete sweep must not hide a mute heartbeat behind exit 1."""
    rows = [dict(_ok_row(), verdict="mute", detail="said nothing")]
    _, status = hh.format_report(rows, None, [("Nova", "timed out")])
    assert status == 2


def test_every_heartbeat_replying_on_time_is_still_exit_zero():
    report, status = hh.format_report([_ok_row()], None)
    assert status == 0
    # The row, not the legend — the legend names MUTE on every report.
    assert "MUTE —" not in report


# --- the fetch -----------------------------------------------------------


def test_apply_mute_relabels_only_the_row_whose_conversation_is_silent(monkeypatch):
    rows = [
        {"name": "K3s Sentinel", "conversationId": "sentinel"},
        {"name": "Nova", "conversationId": "nova"},
    ]
    results = [_ok_row(), dict(_ok_row(), name="Nova", schedule="every@40m@16:00")]
    bodies = {
        "sentinel": {"messages": [_reply(timedelta(days=15))]},
        "nova": {"messages": [_reply(timedelta(minutes=3), activity="assistant_text")]},
    }
    monkeypatch.setattr(
        hh, "fetch_conversation", lambda cid, **kw: (bodies[cid], None)
    )
    out, unreadable = hh.apply_mute(rows, results, NOW)
    assert [r["verdict"] for r in out] == ["mute", "ok"]
    assert unreadable == []


def test_apply_mute_passes_the_run_record_through_and_it_spares_the_row(monkeypatch):
    """The gate has to survive the trip from the heartbeat to the verdict."""
    rows = [
        {
            "name": "K3s Sentinel",
            "conversationId": "sentinel",
            "lastResult": "checked, nothing to report (not posted to chat)",
        }
    ]
    monkeypatch.setattr(
        hh,
        "fetch_conversation",
        lambda cid, **kw: ({"messages": [_reply(timedelta(days=15))]}, None),
    )
    out, _ = hh.apply_mute(rows, [_ok_row()], NOW)
    assert out[0]["verdict"] == "ok"
    assert out[0]["lastResult"] == "checked, nothing to report (not posted to chat)"


def test_a_heartbeat_naming_no_conversation_is_reported_not_assumed_quiet():
    conversation, error = hh.fetch_conversation(None)
    assert conversation is None
    assert "names no conversation" in error


def test_a_conversation_fetch_that_raises_is_an_error_not_an_empty_one():
    def boom(*a, **kw):
        raise OSError("connection refused")

    conversation, error = hh.fetch_conversation("abc", opener=boom)
    assert conversation is None
    assert "could not read" in error and "connection refused" in error
