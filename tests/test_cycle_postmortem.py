"""Why an entryless cycle ended (idea #159).

The whole tool rests on reading one free-text line Agora writes, so most
of what is pinned here is that the two outcomes it distinguishes really
are distinguished, and that a line it does not recognise is a finding
rather than a shrug.

Every fixture string below is a verbatim closing line taken off a real
conversation on 2026-08-29, not one invented to match the regex.
"""

import pytest

from tools.cycle_postmortem import (
    conversations_by_cycle,
    format_report,
    judge,
    read_outcome,
)


def message(text):
    return {"text": text}


# --- reading Agora's closing line -------------------------------------

def test_a_run_that_replied_is_lost_work_and_not_a_failure():
    """Cycle 580, verbatim. It ran half an hour and told the owner 2,458
    characters about it, and the journal has nothing."""
    verdict, detail = read_outcome(
        "heartbeat: Nova finished in 30m 13s — replied 2458 chars")
    assert verdict == "lost"
    assert "30m 13s" in detail and "2458" in detail


def test_a_run_that_failed_quotes_the_reason_rather_than_bucketing_it():
    """The reason is the thing with the fix attached.

    `agentic_health` had to unlearn merging causes one layer down: three
    reds in a row is one number and can be three unrelated problems.
    """
    verdict, detail = read_outcome(
        "heartbeat: Nova finished in 0s — failed: "
        "<urlopen error [Errno 111] Connection refused>")
    assert verdict == "failed"
    assert "Connection refused" in detail
    assert "0s" in detail


def test_the_timeout_and_the_refusal_are_not_the_same_verdict_detail():
    """Cycle 379 hung for 46 minutes; cycle 385 never reached the bridge.

    Both are `failed`, and the idea is explicitly about telling them
    apart, so the detail has to carry the difference even though the
    verdict does not.
    """
    _, hung = read_outcome("heartbeat: Nova finished in 46m — failed: timed out")
    _, refused = read_outcome(
        "heartbeat: Nova finished in 0s — failed: "
        "<urlopen error [Errno 111] Connection refused>")
    assert hung != refused
    assert "timed out" in hung
    assert "Connection refused" in refused


def test_a_line_that_is_not_a_closing_line_reads_as_no_outcome():
    """Cycle 265's last message is the dead-man alarm, not Agora's own.

    Returning `None` rather than guessing is what lets `judge` call it
    `cut off` instead of filing an alarm as a cycle's outcome.
    """
    assert read_outcome("Nova has stopped writing. The last journal entry is "
                        "Cycle 264's") is None
    assert read_outcome("") is None
    assert read_outcome(None) is None


def test_a_closing_line_in_an_unknown_shape_is_not_read_as_success():
    verdict, _ = read_outcome("heartbeat: Nova finished in 4m — something new")
    assert verdict == "unjudged"


# --- one cycle's verdict ----------------------------------------------

def test_no_conversation_and_no_message_are_different_verdicts():
    """Cycle 475 has no conversation; cycles 8 and 360 have one and it is
    empty. The number was handed out either way and the two say different
    things about what happened next."""
    assert judge(475, None, [])["verdict"] == "absent"
    assert judge(360, {"id": "x"}, [])["verdict"] == "silent"


def test_a_run_with_no_closing_line_is_cut_off_and_quotes_what_it_did_say():
    row = judge(265, {"id": "x"}, [message("Nova has stopped writing.")])
    assert row["verdict"] == "cut off"
    assert "Nova has stopped writing" in row["detail"]


def test_only_the_last_message_decides_the_verdict():
    """A cycle says thousands of things; Agora's closing line is the last
    of them, and an earlier `API Error` in the transcript is not the
    outcome. Cycle 358 is the live case: it carries a 529 mid-run and
    still finished and replied."""
    row = judge(358, {"id": "x"}, [
        message("API Error: 529 Overloaded."),
        message("heartbeat: Nova finished in 3m 26s — replied 147 chars"),
    ])
    assert row["verdict"] == "lost"


# --- picking the heartbeat's own conversations -------------------------

def test_only_this_heartbeat_s_conversations_are_counted():
    payload = {"conversations": [
        {"id": "a", "name": "Nova — Cycle 12", "tags": ["evolve-cycle:hb"]},
        {"id": "b", "name": "Nova — Cycle 13", "tags": ["evolve-cycle:other"]},
    ]}
    assert set(conversations_by_cycle(payload, heartbeat="hb")) == {12}


def test_a_name_that_carries_no_number_is_skipped_not_guessed_at():
    """`Agora Evolve`, the very first conversation, predates the naming
    convention. Counting the list instead of parsing it would hand a
    fresh cycle a number an older one already used."""
    payload = {"conversations": [
        {"id": "a", "name": "Agora Evolve", "tags": ["evolve-cycle:hb"]},
        {"id": "b", "name": "Nova — Cycle 9", "tags": ["evolve-cycle:hb"]},
    ]}
    assert set(conversations_by_cycle(payload, heartbeat="hb")) == {9}


# --- the exit contract -------------------------------------------------

def row(number, verdict, recent=True):
    return {"number": number, "verdict": verdict, "detail": "d",
            "messages": 1, "recent": recent}


def test_a_recent_lost_cycle_raises_and_an_old_one_does_not():
    """The window is a reporting scope, not a judgement. Ten cycles have
    been `lost` since 08-10 and a check that goes red on all of them
    forever is a check nobody reads."""
    _, recent = format_report([row(580, "lost")], 607, None)
    _, old = format_report([row(87, "lost", recent=False)], 607, None)
    assert recent == 2
    assert old == 0


def test_a_recorded_failure_never_raises_however_recent():
    """Nothing to recover and no pull request that fixes a run that is
    over -- the call `security_alerts` makes on an already-fixed alert."""
    _, status = format_report([row(506, "failed")], 607, None)
    assert status == 0


def test_all_raises_on_a_lost_cycle_outside_the_window():
    _, status = format_report([row(87, "lost", recent=False)], 607, None,
                              raise_all=True)
    assert status == 2


def test_a_conversation_whose_messages_would_not_answer_is_never_clean():
    _, status = format_report([row(600, "unreadable")], 607, None)
    assert status == 1


def test_a_failed_listing_is_exit_one_and_says_it_is_no_instrument():
    """An empty journal has no gaps in it, so the read failing has to be
    louder than the read coming back clean -- this pod's `agora_runner.
    vault` really does answer 401 and return nothing."""
    report, status = format_report([], None, "could not list the journal")
    assert status == 1
    assert "no instrument" in report


def test_no_gaps_at_all_is_a_clean_zero_that_says_what_it_swept():
    report, status = format_report([], 607, None)
    assert status == 0
    assert "607" in report


def test_the_report_counts_every_verdict_even_the_ones_that_do_not_raise():
    """Keep the data whole and fix it with the exit status, not by
    dropping rows -- the 400-chip rule in `personality.md`."""
    report, _ = format_report(
        [row(580, "lost"), row(506, "failed"), row(360, "silent", recent=False)],
        607, None)
    assert "1 failed" in report and "1 lost" in report and "1 silent" in report
    assert "Cycle 360" in report


# --- the tail, which is where the freshest failure lives ---------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from tools.cycle_postmortem import MESSAGE_LIMIT, entryless  # noqa: E402


def paths(*cycles):
    return [f"{i:03d}-cycle-{n}.md" for i, n in enumerate(cycles, start=1)]


def test_a_loop_that_stopped_writing_an_hour_ago_is_found():
    """The failure my reviewer found, and the one that costs most.

    `cycle_health.missing_cycles` returns interior gaps only, so a run of
    cycles that all died *after* the newest entry leaves no gap at all --
    a live outage read as "nothing to act on" and exited 0.
    """
    assert entryless(paths(596, 597, 598), newest=605) == [599, 600, 601, 602, 603, 604]


def test_the_newest_cycle_is_never_reported_because_it_is_me_asking():
    assert entryless(paths(600), newest=601) == []


def test_an_interior_gap_and_a_tail_gap_are_both_reported():
    assert entryless(paths(10, 12), newest=15) == [11, 13, 14]


def test_a_journal_with_nothing_in_it_reports_no_gaps_rather_than_all_of_them():
    """An empty listing is `journal_paths` returning `None` -> exit 1.
    Reaching here with an empty list must not invent 605 findings."""
    assert entryless([], newest=605) == []


# --- a cycle that is still going is not a cycle that died --------------

def now_utc():
    return datetime(2026, 8, 29, 4, 0, tzinfo=timezone.utc)


def conversation_last_spoke(minutes_ago):
    stamp = (now_utc() - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z")
    return {"id": "x", "lastMessageAt": stamp}


def test_a_tail_cycle_with_no_outcome_that_just_spoke_is_still_running():
    """Three cycles overlap, so the newest few legitimately have no
    closing line. Calling those `cut off` reports the loop working as a
    failure, every single run."""
    row = judge(606, conversation_last_spoke(2), [message("Bash: ...")],
                now=now_utc())
    assert row["verdict"] == "still running"


def test_a_tail_cycle_that_went_quiet_long_ago_is_cut_off_not_still_running():
    row = judge(606, conversation_last_spoke(300), [message("Bash: ...")],
                now=now_utc())
    assert row["verdict"] == "cut off"


def test_an_unparseable_last_message_time_reads_as_stopped_not_as_running():
    row = judge(606, {"id": "x", "lastMessageAt": "whenever"},
                [message("Bash: ...")], now=now_utc())
    assert row["verdict"] == "cut off"


def test_a_conversation_at_the_read_limit_is_unreadable_not_cut_off():
    """Agora answers with the *oldest* N, so a longer conversation loses
    the closing line the whole measurement rests on -- and a truncated
    read is indistinguishable from a real one by the last message alone."""
    row = judge(600, {"id": "x"}, [message("Bash: ...")] * MESSAGE_LIMIT,
                now=now_utc())
    assert row["verdict"] == "unreadable"


# --- what "explained" means -------------------------------------------

def test_an_unrecognised_closing_line_raises_rather_than_reading_as_clean():
    """The contract is that 0 means every gap in the window is explained,
    and `unjudged` is by its own name the opposite. The day Agora grows a
    third outcome word must not be a silent one."""
    _, status = format_report([row(600, "unjudged")], 607, None)
    assert status == 2


def test_a_run_that_stopped_with_no_outcome_raises():
    _, status = format_report([row(600, "cut off")], 607, None)
    assert status == 2


def test_a_cycle_that_is_still_running_never_raises():
    _, status = format_report([row(606, "still running")], 607, None)
    assert status == 0
