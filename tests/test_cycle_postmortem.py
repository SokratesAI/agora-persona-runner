"""Why an entryless cycle ended (idea #159).

The whole tool rests on reading one free-text line Agora writes, so most
of what is pinned here is that the two outcomes it distinguishes really
are distinguished, and that a line it does not recognise is a finding
rather than a shrug.

Every fixture string below is a verbatim closing line taken off a real
conversation on 2026-08-29, not one invented to match the regex.
"""

import pytest

from tools import cycle_postmortem

from tools.cycle_postmortem import (
    apply_misfiled,
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
    outcome -- a cycle that hits a 529 mid-run and recovers replies
    normally afterwards, and that is lost work like any other.

    This used to name cycle 358 as the live case for that, on a fixture
    where the 529 is the only thing said before the closing line. I read
    that conversation on 2026-09-08 and the fixture is not what it holds:
    the 529 IS its last message and `replied 147 chars` is that error's
    own length. So the recovery is the invariant and 358 is not an example
    of it -- it is in `test_a_stalled_stream_is_the_same_verdict_as_a_500`'s
    bucket instead.
    """
    row = judge(358, {"id": "x"}, [
        message("API Error: 529 Overloaded."),
        message("Recovered, and here is what I did with the hour."),
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


# --- did a change fix it? (idea #170, --split-at) ----------------------

from collections import Counter  # noqa: E402

from tools.cycle_postmortem import (  # noqa: E402
    _created,
    format_rate_split,
    main as postmortem_main,
    rate_split,
)


def _conversations(pairs):
    """`{number: conversation}` from `(number, 'YYYY-MM-DDTHH:MM:SSZ')` pairs."""
    return {number: {"id": f"c{number}", "createdAt": stamp}
            for number, stamp in pairs}


def _hourly(first, count, start="2026-08-28T00:00:00Z"):
    """`count` conversations one hour apart, numbered from `first`."""
    begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
    return _conversations(
        (first + i, (begin + timedelta(hours=i)).isoformat().replace("+00:00", "Z"))
        for i in range(count))


def _at(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def test_the_before_window_is_the_same_length_as_the_after_window():
    """A count cannot answer idea #170 -- the cadence has changed four
    times, so more silent cycles per day can just mean more cycles per
    day. Both sides have to be rates over equal spans."""
    conversations = _hourly(1, 21)          # cycle N starts at hour N-1
    split = rate_split([], conversations, _at("2026-08-28T15:00:00Z"), newest=21)
    # after: cycles 16..20, 15:00 to 19:00 -- cycle 21 is the one asking
    assert split["hours"] == 4.0
    assert (split["after"]["lo"], split["after"]["hi"]) == (16, 20)
    # before: the matched 4 hours, 11:00 to 14:00
    assert split["before"]["cycles"] == 4
    assert (split["before"]["lo"], split["before"]["hi"]) == (12, 15)


def test_the_cycle_asking_the_question_is_in_neither_side():
    """It has not written its entry yet, so counting it would report the
    running cycle as a gap on whichever side it fell."""
    conversations = _hourly(1, 6)
    split = rate_split([], conversations, _at("2026-08-28T03:00:00Z"), newest=6)
    assert 6 not in split["after"]["numbers"]
    assert split["after"]["hi"] == 5


def test_the_verdicts_are_counted_apart_not_summed():
    """`failed: Connection refused` is the bridge being down and no CLI
    version changes it. One summed number merges that into the answer."""
    conversations = _hourly(1, 11)          # cycle N starts at hour N-1
    results = [row(5, "failed"), row(6, "failed"), row(8, "lost")]
    split = rate_split(results, conversations, _at("2026-08-28T06:00:00Z"), newest=11)
    assert split["before"]["verdicts"] == Counter({"failed": 2})
    assert split["after"]["verdicts"] == Counter({"lost": 1})
    assert "2 failed" in "\n".join(format_rate_split(split))


def test_a_conversation_with_no_readable_createdat_is_named_not_dropped():
    """It is in neither window, so a silent drop would shrink a
    denominator and move the rate without saying so."""
    conversations = _hourly(1, 6)
    conversations[3] = {"id": "c3", "createdAt": "not a date"}
    split = rate_split([], conversations, _at("2026-08-28T02:00:00Z"), newest=6)
    assert split["undated"] == 1
    assert "NOT COUNTED" in "\n".join(format_rate_split(split))


def test_no_cycle_after_the_split_says_so_rather_than_dividing_by_zero():
    conversations = _hourly(1, 4)
    assert rate_split([], conversations, _at("2026-09-30T00:00:00Z"), newest=4) is None
    assert "nothing on the after side" in "\n".join(format_rate_split(None))


def test_created_reads_agoras_z_stamp_and_refuses_anything_else():
    assert _created({"createdAt": "2026-08-28T23:14:42Z"}) == _at("2026-08-28T23:14:42Z")
    assert _created({"createdAt": ""}) is None
    assert _created({"createdAt": 17}) is None
    assert _created(None) is None


def test_split_at_never_moves_the_exit_status(monkeypatch, capsys):
    """A rate that has not moved is a report, not a fault -- and a rate
    that HAS moved is still not one. The exit code stays the postmortem's
    own, so `preflight` cannot be turned red by a measurement."""
    conversations = _hourly(600, 6)
    monkeypatch.setattr("tools.cycle_postmortem.collect",
                        lambda window=None: ([], 605, None, conversations, []))
    status = postmortem_main(["--split-at", "2026-08-28T03:00:00Z"])
    assert status == 0
    assert "ENTRYLESS RATE" in capsys.readouterr().out


def test_an_unparseable_split_at_refuses_rather_than_reporting_on_nothing(capsys):
    assert postmortem_main(["--split-at", "last tuesday"]) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


# --- Agora's own notices are not the run's record (idea #170) ---------

#: Verbatim, off cycle 1035's conversation on 2026-09-06. Agora posts one
#: of these into whichever cycle conversation is open when it notices an
#: *earlier* cycle never replied, so it lands after that run's closing
#: line and is about a different run entirely.
NOTICE = {
    "sender": "Agora",
    "system": True,
    "text": ("Nova — Cycle 1029 finished without ever replying to you.\n\n"
             "It ran, and the thread it left you is all narration — no answer "
             "at the end. Its journal entry is the record of what it actually "
             "did, and the next cycle will relay it.\n\n"
             "One message per cycle. You will not get this one again."),
}

#: Verbatim, same conversation, the line this check exists to read.
CLOSING_1035 = message(
    "heartbeat: Nova finished in 0s — failed: "
    "<urlopen error [Errno 111] Connection refused>")


def test_a_notice_landing_after_the_closing_line_does_not_hide_it():
    """Cycle 1035, verbatim and in order. Agora recorded why the run
    failed and then wrote two notices about other cycles on top of it.
    Judging the last message called that `cut off`, which raises, when
    Agora had already said `failed`, which does not."""
    row = judge(1035, {"id": "x"},
                [message("heartbeat: Nova (every@15m@16:00)"),
                 CLOSING_1035, dict(NOTICE), dict(NOTICE)])
    assert row["verdict"] == "failed"
    assert "Connection refused" in row["detail"]


def test_a_conversation_holding_only_notices_never_spoke():
    """Cycle 1041, verbatim shape: three notices and no heartbeat line at
    all. The run never started, and counting Agora's own messages as the
    run speaking filed it as one that stopped part-way."""
    row = judge(1041, {"id": "x"}, [dict(NOTICE) for _ in range(3)])
    assert row["verdict"] == "silent"
    assert "3 message(s)" in row["detail"]
    assert "Agora's own notices" in row["detail"]


def test_a_run_that_really_said_nothing_still_says_so_plainly():
    """The pre-existing shape has to keep its own wording -- an empty
    conversation and one holding three notices are different findings and
    the report is the only place that difference is visible."""
    row = judge(1029, {"id": "x"}, [])
    assert row["verdict"] == "silent"
    assert row["detail"] == (
        "the conversation was created and nothing ever spoke in it")


def test_truncation_is_judged_on_what_agora_returned_not_what_survives():
    """A conversation at the read ceiling is unreadable however many of
    its rows are notices -- the closing line is off the end either way,
    and counting the filtered rows would let it slip under the ceiling
    and be judged off a message that is not the last one."""
    row = judge(600, {"id": "x"},
                [dict(NOTICE)] + [message("Bash: ...")] * (MESSAGE_LIMIT - 1))
    assert row["verdict"] == "unreadable"
    assert row["messages"] == MESSAGE_LIMIT


# --- an entry filed under the wrong cycle number (idea #267) ----------

from tools.cycle_postmortem import (  # noqa: E402
    entry_pr_numbers, find_misfiled, format_misfiled, keep_still_lost,
    merge_misfiled, misfiled_entries, reply_numbers,
)


def _said(*numbers):
    return frozenset(numbers)


def test_the_entry_one_number_up_is_attributed_to_the_cycle_that_announced_it():
    """Measured 2026-09-08: cycle 1183 replied announcing runner#876 and left
    no entry; `1238-cycle-1184.md` carries `PR: agora-persona-runner#876` and
    cycle 1184's own reply announced #877. `judge` calls 1183 lost and 1184
    fine, and 1184 is holding 1183's entry."""
    pairs = misfiled_entries(
        [1183],
        {1184: _said(876), 1185: _said(877)},
        {1183: _said(876), 1184: _said(877), 1185: _said(878)},
    )
    assert (1183, 1184) in pairs


def test_the_shift_is_followed_forward_because_it_cascades():
    """Every cycle that overruns the heartbeat interval and asks without its
    conversation id gets the same wrong answer, so the entries shift as a
    run rather than singly -- 1183 through 1187 on 2026-09-08."""
    pairs = misfiled_entries(
        [1183],
        {1184: _said(876), 1185: _said(877), 1186: _said(878), 1187: _said(879)},
        {1183: _said(876), 1184: _said(877), 1185: _said(878), 1186: _said(879),
         1187: _said(880)},
    )
    assert pairs == [(1183, 1184), (1184, 1185), (1185, 1186), (1186, 1187)]


def test_a_cycle_that_announced_its_own_entrys_pr_is_left_alone():
    """The second condition, and it is what keeps a coincidence out: if the
    run the entry is filed under named that pull request too, the pair is
    ambiguous and this says nothing rather than picking one."""
    assert misfiled_entries(
        [1183],
        {1184: _said(876)},
        {1183: _said(876), 1184: _said(876, 877)},
    ) == []


def test_an_entry_with_no_pull_request_is_never_attributed():
    """`PR: none` is the honest footer for a cycle that shipped nothing, and
    an empty set is a subset of everything -- so without this the tool would
    hand every no-op entry to whichever cycle ran before it."""
    assert misfiled_entries([1183], {1184: frozenset()},
                            {1183: _said(876), 1184: _said(877)}) == []


def test_an_unreadable_run_stops_the_chain_rather_than_extending_it():
    """`None` means the conversation would not answer. Reading that as "it
    named nothing" would satisfy the second condition for free, which is a
    positive result guaranteed in advance."""
    assert misfiled_entries([1183], {1184: _said(876)},
                            {1183: _said(876), 1184: None}) == []


def test_a_lost_cycle_that_said_nothing_readable_claims_no_entry():
    assert misfiled_entries([1183], {1184: _said(876)}, {1184: _said(877)}) == []


def test_the_footer_board_number_is_not_read_as_a_pull_request():
    """`PR: marcus#82 | Board: idea #187 | Outcome: merged` -- only the field
    before the first pipe is the pull request."""
    assert entry_pr_numbers(
        "PR: marcus#82 | Board: idea #187 | Outcome: merged") == frozenset({82})


def test_agoras_own_notices_are_not_the_runs_own_words():
    """Same reason `judge` filters them: a notice is about another cycle, so
    a pull request number inside one is not something this run announced."""
    assert reply_numbers([
        {"text": "merged #876"},
        {"text": "cycle 900 never replied, see #999", "system": True},
    ]) == frozenset({876})


def test_nothing_is_read_at_all_when_no_cycle_came_back_lost():
    """`lost` is the only verdict that can mean the entry is somewhere else,
    so a healthy sweep must not spend a vault read per gap."""
    def refuse(_):
        raise AssertionError("read anyway")
    assert find_misfiled([{"number": 1, "verdict": "silent"}], {}, ["x"],
                         read_entry=refuse, fetch=refuse) == []


def test_find_misfiled_reads_the_entry_and_the_reply_and_names_the_pair():
    paths = ["projects/x/journal/1238-cycle-1184.md",
             "projects/x/journal/1239-cycle-1185.md"]
    entries = {paths[0]: "### Cycle 1184\n\n---\nPR: agora-persona-runner#876 | Outcome: merged\n",
               paths[1]: "### Cycle 1185\n\n---\nPR: agora-persona-runner#877 | Outcome: merged\n"}
    replies = {"c1183": [{"text": "merged runner#876"}],
               "c1184": [{"text": "merged runner#877"}],
               "c1185": [{"text": "merged runner#878"}]}
    pairs = find_misfiled(
        [{"number": 1183, "verdict": "lost"}],
        {1183: {"id": "c1183"}, 1184: {"id": "c1184"}, 1185: {"id": "c1185"}},
        paths,
        read_entry=entries.get,
        fetch=lambda cid: replies[cid],
    )
    assert pairs == [(1183, 1184), (1184, 1185)]
    block = format_misfiled(pairs)
    assert any("Cycle 1183's work is in the entry filed as cycle 1184" in line
               for line in block)
    assert format_misfiled([]) == []


def test_only_the_reply_counts_not_the_whole_transcript():
    """The first version of `reply_numbers` read every message and could not
    work: a cycle reads the digest, so its transcript names the pull request
    the cycle before it shipped, which makes `misfiled_entries`' first
    condition true for free and its second false for free. Measured against
    cycle 1183: 300-odd numbers off the transcript, one off the reply."""
    assert reply_numbers([
        {"text": "the digest says cycle 1183 merged #876"},
        {"text": "Merged and live. The banner works now (#877)."},
        {"text": "heartbeat: Nova finished in 28m 22s — replied 2053 chars"},
    ]) == frozenset({877})


# --- a located entry is not a lost one ---------------------------------

def test_a_lost_cycle_whose_entry_was_located_stops_raising():
    """`lost` raises because the gap is unexplained. Once `find_misfiled`
    has named where the entry is, the gap is explained in the same report
    -- and on 2026-09-08 the live run printed exactly that explanation
    twelve lines under an exit 2."""
    results = [row(1183, "lost")]
    apply_misfiled(results, [(1183, 1184)])
    text, status = format_report(results, 1230, None)
    assert status == 0
    assert "filed as cycle 1184" in text


def test_a_lost_cycle_nothing_located_still_raises():
    """The downgrade is not a blanket amnesty on `lost` -- without a pair
    naming it, the row is untouched and still red. Without this the first
    test above passes on a `format_report` that never raises at all."""
    results = [row(580, "lost")]
    apply_misfiled(results, [(1183, 1184)])
    assert results[0]["verdict"] == "lost"
    assert format_report(results, 1230, None)[1] == 2


def test_only_a_lost_row_is_downgraded():
    """A `failed` cycle can share a number with nothing, but the guard is
    cheap and the wrong one would erase a recorded reason."""
    results = [row(1035, "failed")]
    apply_misfiled(results, [(1035, 1036)])
    assert results[0]["verdict"] == "failed"


def test_the_downgrade_keeps_what_the_run_actually_did():
    """The detail is appended to, not replaced: "ran 26m 45s and replied
    1715 chars" is the evidence that the work happened at all."""
    results = [{"number": 1183, "verdict": "lost", "recent": True, "messages": 186,
                "detail": "ran 26m 45s and replied 1715 chars"}]
    apply_misfiled(results, [(1183, 1184)])
    assert results[0]["detail"] == (
        "ran 26m 45s and replied 1715 chars; the entry is filed as cycle 1184")


def test_main_searches_before_it_grades_not_after(monkeypatch, capsys):
    """The bug this closes lives in the ORDER, not in `apply_misfiled`.

    `format_report` decides the exit status off the verdicts it is handed,
    so a search run after it prints the explanation under a red status --
    which is what the 2026-09-08 run did with cycle 1183. Call the search
    late again and this test goes red while every unit above it stays
    green.
    """
    results = [row(1183, "lost")]
    monkeypatch.setattr("tools.cycle_postmortem.collect",
                        lambda window=None: (results, 1230, None, {}, []))
    monkeypatch.setattr("tools.cycle_postmortem.find_misfiled",
                        lambda *a, **k: [(1183, 1184)])
    status = postmortem_main([])
    out = capsys.readouterr().out
    assert status == 0
    assert "FILED UNDER THE WRONG NUMBER" in out
    assert "filed as cycle 1184" in out


def test_all_does_not_raise_on_a_misfiled_cycle():
    """`--all` widens the window, it does not change what a verdict means.

    `misfiled` is non-raising only by being absent from `RAISING_VERDICTS`,
    so nothing else pins it -- a special case under `raise_all` would go
    uncaught. My reviewer found this hole."""
    results = [row(366, "misfiled", recent=False)]
    assert format_report(results, 1230, None, raise_all=True)[1] == 0


def test_the_rate_split_counts_a_misfiled_cycle_as_a_gap_and_names_the_cause():
    """`apply_misfiled` mutates the list `--split-at` then reads, so the
    idea #170 rate is downstream of the downgrade. The gap COUNT must not
    move -- a misfiled cycle still has no entry under its own number, and
    that is what the rate measures -- while the verdict breakdown must,
    because that docstring keeps the causes apart on purpose and `lost`
    and `misfiled` are different causes. My reviewer found this."""
    results = [row(1183, "lost")]
    conversations = {1183: {"createdAt": "2026-09-08T09:00:00Z"},
                     1184: {"createdAt": "2026-09-08T10:00:00Z"}}
    split = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    before = rate_split(results, conversations, split, 1230)
    apply_misfiled(results, [(1183, 1184)])
    after = rate_split(results, conversations, split, 1230)
    assert before["after"]["gaps"] == after["after"]["gaps"] == 1
    assert before["after"]["verdicts"]["lost"] == 1
    assert after["after"]["verdicts"] == {"misfiled": 1}


# --- a model-call error is not a reply --------------------------------

CLOSING_87 = "heartbeat: Nova finished in 7m 19s — replied 2146 chars"
CLOSING_839 = "heartbeat: Nova finished in 11m 30s — replied 158 chars"
ERROR_839 = ("API Error: 500 Internal server error. This is a server-side issue, "
             "usually temporary — try again in a moment. If it persists, check "
             "https://status.claude.com.")
ERROR_301 = ("API Error: Response stalled mid-stream. The response above may be "
             "incomplete.")


def test_a_run_whose_last_message_is_the_models_error_is_not_lost_work():
    """Cycle 839, verbatim off Agora on 2026-09-08.

    Agora's closing line says `replied 158 chars` and those 158 characters
    are the CLI's own transport error, posted into the conversation as an
    ordinary message. Read as a reply it made four dead runs look like four
    recoverable ones, and three cycles in a row called the whole bucket
    undiagnosable because of it.
    """
    row = judge(839, {"id": "c"},
                [message("working"), message(ERROR_839), message(CLOSING_839)])
    assert row["verdict"] == "api error"
    assert "11m 30s" in row["detail"]
    assert "500 Internal server error" in row["detail"]


def test_a_stalled_stream_is_the_same_verdict_as_a_500():
    """Cycle 301, verbatim. No status code in it at all, so a matcher
    keyed on a number would file this one back under `lost`."""
    row = judge(301, {"id": "c"},
                [message("working"), message(ERROR_301),
                 message("heartbeat: Nova finished in 8m 35s — replied 77 chars")])
    assert row["verdict"] == "api error"
    assert "stalled mid-stream" in row["detail"]


def test_a_real_reply_that_talks_about_an_api_error_is_still_lost_work():
    """The precondition the matcher rests on: it is anchored at the start
    of the reply, because a cycle that spent its hour on this very bug
    writes the phrase into the middle of a perfectly good reply."""
    reply = ("Done. Four of the eleven silent cycles ended on an "
             "API Error: 500 rather than a reply, so they are not recoverable.")
    row = judge(87, {"id": "c"},
                [message("working"), message(reply), message(CLOSING_87)])
    assert row["verdict"] == "lost"


def test_the_two_causes_get_separate_headings_and_both_still_raise():
    """Unmerging the cause must not quieten it: both are a cycle's work
    that the journal does not have, and neither has been explained."""
    report, status = format_report(
        [row(87, "lost"), row(839, "api error")], 1243, None)
    assert "RAN AND LEFT NO RECORD" in report
    assert "DIED ON A MODEL-CALL ERROR" in report
    assert status == 2
    # And on its own, so that dropping `api error` from RAISING_VERDICTS
    # shows up here -- `lost` raises by itself and would carry this for free.
    _, alone = format_report([row(839, "api error")], 1243, None)
    assert alone == 2


def test_the_report_prints_the_lost_cycles_own_account():
    """Four cycles running called this bucket undiagnosable while every one
    of those replies sat in Agora, because the report named the recovery
    instead of performing it. Naming it is what did not work."""
    reply = "Merged runner#421.\nThe cache is warm and the page is 4x faster."
    lost = dict(row(87, "lost"), reply=reply)
    report, _ = format_report([lost], 1243, None)
    assert "Merged runner#421." in report
    # Whole, both lines of it -- a summary of a lost cycle's only surviving
    # account is the same loss one step further along.
    assert "The cache is warm and the page is 4x faster." in report


def test_a_lost_row_whose_reply_cannot_be_read_says_so():
    """The heading promises the reply is in the conversation. A row that
    printed nothing would make that promise false with nothing on the page
    to tell a reader it had."""
    report, _ = format_report([dict(row(87, "lost"), reply=None)], 1243, None)
    assert "no reply in the conversation to recover" in report
    assert "--- end of recovered reply ---" not in report


def test_only_a_lost_row_carries_a_recovered_reply():
    """An `api error` run's last message IS the error, already quoted in its
    own detail line; printing it again under a recovery banner would read as
    work to get back."""
    report, _ = format_report([row(839, "api error"), row(506, "failed")],
                              1243, None)
    assert "recovered from Agora" not in report
    assert "no reply in the conversation to recover" not in report


def test_judge_carries_the_reply_out_with_the_lost_row():
    """`collect` has already paid for these messages. A reader who has to go
    back to Agora for them is the state this replaces, so the text has to
    leave `judge` attached to the row rather than be re-fetched."""
    reply = "Done -- the journal page renders in 300ms now."
    lost = judge(87, {"id": "c"},
                 [message("working"), message(reply), message(CLOSING_87)])
    assert lost["verdict"] == "lost"
    assert lost["reply"] == reply


def test_a_heading_with_no_note_prints_no_blank_indent():
    """Most verdicts carry no note and must not grow an empty line."""
    report, _ = format_report([row(506, "failed")], 1243, None)
    assert "\n    \n" not in report
    assert "    None" not in report


# --- a lost cycle whose record is in the folder under another name ---------
#
# Two of the seven `lost` cycles on 2026-09-09 had written an eight-cycle
# report and no entry. `entryless` reads `file_cycle`, which only parses
# `-cycle-M`, so a document named anything else is invisible to it and the
# run reads as having left no record at all.

_REPORT_265 = "### 2026-08-17 14:07 (Oslo) — Report · Cycles 256–263\n\nbody\n"
_REPORT_276 = "### 2026-08-20 06:53 (Oslo) — Report · Cycles 268–274\n\nbody\n"

_JOURNAL = [
    "j/318-cycle-264.md",
    "j/319-report-256-263.md",
    "j/320-cycle-266.md",
    "j/329-cycle-274.md",
    "j/330-report-268-274.md",
    "j/331-cycle-277.md",
]

# The real windows, straight off Agora. 275 closed an hour before the
# document in its own gap was written; 276 was still running.
_CONVERSATIONS = {
    265: {"createdAt": "2026-08-17T12:00:01.016Z",
          "lastMessageAt": "2026-08-19T17:54:23.731Z"},
    275: {"createdAt": "2026-08-20T03:27:01.476Z",
          "lastMessageAt": "2026-08-20T03:44:54.751Z"},
    276: {"createdAt": "2026-08-20T04:39:01.568Z",
          "lastMessageAt": "2026-08-20T05:04:38.220Z"},
}


def _lost(*numbers):
    return [{"number": n, "verdict": "lost", "detail": f"ran and replied", "messages": 1}
            for n in numbers]


def _read(path):
    return {"j/319-report-256-263.md": _REPORT_265,
            "j/330-report-268-274.md": _REPORT_276}.get(path)


def test_document_written_at_converts_the_oslo_stamp_to_utc():
    at = cycle_postmortem.document_written_at(_REPORT_265)
    assert at is not None
    assert at.isoformat() == "2026-08-17T12:07:00+00:00"


def test_document_with_no_time_in_its_heading_is_not_guessed_at():
    """A date alone cannot be joined to a run window that is minutes wide,
    and 707 of the 1,334 documents in the folder are exactly that. `None`
    is the honest answer for them; a midnight default would be a guess."""
    assert cycle_postmortem.document_written_at("no heading here\n") is None
    assert cycle_postmortem.document_written_at("### 2026-08-02 — Cycle 4") is None


def test_the_stamp_is_read_in_either_heading_order():
    """The rule this used to carry required the literal `(Oslo)` and the
    date before the cycle number, and half my entries are written the
    other way round -- `### Cycle 579 — 2026-08-28 14:25 — ...`. Measured
    across the whole folder 2026-09-09: 272 documents readable under the
    old rule, 620 under `nova_journal.parse_heading`, which is the parser
    the site has rendered both orders with since Cycle 3. The failure was
    silent in the direction that hides -- a join with nothing to find and
    a join that cannot read look identical from outside."""
    cycle_first = cycle_postmortem.document_written_at(
        "### Cycle 579 — 2026-08-28 14:25 — The comments page\n\nbody\n")
    assert cycle_first is not None
    assert cycle_first.isoformat() == "2026-08-28T12:25:00+00:00"
    # No `(Oslo)` anywhere, and still a stamp: rule 7 says every time I
    # write for him is Oslo, so the marker was never what made it one.
    assert cycle_postmortem.document_written_at(
        "### 2026-08-17 14:07 — Report").isoformat() == "2026-08-17T12:07:00+00:00"


def test_candidates_are_the_documents_in_a_lost_cycle_s_sequence_gap():
    found = cycle_postmortem.unnumbered_candidates(_JOURNAL, [265, 275, 276])
    assert found[265] == ["j/319-report-256-263.md"]
    # One gap, two lost cycles, one document -- offered to both on purpose.
    assert found[275] == ["j/330-report-268-274.md"]
    assert found[276] == ["j/330-report-268-274.md"]


def test_candidates_exclude_documents_outside_the_gap():
    found = cycle_postmortem.unnumbered_candidates(_JOURNAL, [265])
    assert "j/330-report-268-274.md" not in found[265]


def test_the_clock_decides_which_of_two_lost_cycles_wrote_the_document():
    pairs = cycle_postmortem.find_unnumbered(
        _lost(265, 275, 276), _CONVERSATIONS, _JOURNAL, read_entry=_read)
    assert pairs == [(265, "j/319-report-256-263.md"),
                     (276, "j/330-report-268-274.md")]
    # 275 is left `lost`: its run closed at 03:44:54Z and the only document
    # in its gap was written at 04:53Z.
    assert 275 not in dict((n, p) for n, p in pairs)


def test_a_document_two_cycles_could_both_claim_is_dropped():
    both = dict(_CONVERSATIONS)
    both[275] = {"createdAt": "2026-08-20T03:27:01.476Z",
                 "lastMessageAt": "2026-08-20T05:10:00.000Z"}
    pairs = cycle_postmortem.find_unnumbered(
        _lost(275, 276), both, _JOURNAL, read_entry=_read)
    assert pairs == []


def test_nothing_is_read_when_no_cycle_came_back_lost():
    reads = []

    def counting(path):
        reads.append(path)
        return _REPORT_265

    rows = [{"number": 265, "verdict": "misfiled", "detail": "x", "messages": 1}]
    assert cycle_postmortem.find_unnumbered(
        rows, _CONVERSATIONS, _JOURNAL, read_entry=counting) == []
    assert reads == []


def test_apply_unnumbered_downgrades_the_row_and_names_the_document():
    rows = _lost(265, 275)
    cycle_postmortem.apply_unnumbered(rows, [(265, "j/319-report-256-263.md")])
    assert rows[0]["verdict"] == "unnumbered"
    assert "319-report-256-263.md" in rows[0]["detail"]
    assert rows[1]["verdict"] == "lost"


def test_unnumbered_does_not_raise_the_exit_status():
    assert "unnumbered" not in cycle_postmortem.RAISING_VERDICTS


def test_unnumbered_has_a_heading_so_the_rows_are_printed():
    assert "unnumbered" in dict((h[0], h[1]) for h in cycle_postmortem._HEADINGS)


def test_format_unnumbered_names_the_cycle_and_the_file():
    lines = cycle_postmortem.format_unnumbered([(276, "j/330-report-268-274.md")])
    text = "\n".join(lines)
    assert "Cycle 276 wrote `330-report-268-274.md`" in text
    assert "never renamed" in text


def test_format_unnumbered_is_silent_with_nothing_to_say():
    assert cycle_postmortem.format_unnumbered([]) == []


def test_a_document_written_before_the_run_opened_is_not_claimed():
    # The gap is positional and a gap reaches backwards as well as
    # forwards: the entry below `319-report-256-263.md` is cycle 264's, so
    # a document 264 wrote sits in 265's shortlist too. Only the window's
    # near edge separates them.
    late = {265: {"createdAt": "2026-08-17T13:00:00.000Z",
                  "lastMessageAt": "2026-08-17T18:00:00.000Z"}}
    assert cycle_postmortem.find_unnumbered(
        _lost(265), late, _JOURNAL, read_entry=_read) == []


def test_a_document_that_does_not_stamp_itself_is_not_claimed():
    paths = _JOURNAL + ["j/319b-loose-note.md"]

    def read(path):
        if path == "j/319b-loose-note.md":
            return "a note somebody dropped in the folder with no heading\n"
        return _read(path)

    pairs = cycle_postmortem.find_unnumbered(
        _lost(265), _CONVERSATIONS, paths, read_entry=read)
    assert pairs == [(265, "j/319-report-256-263.md")]


# --- a lost cycle's entry filed under a number somebody else also used ----
#
# The live case, measured 2026-09-09: `516-cycle-454.md` and
# `517-cycle-454.md` both sit in the folder, cycle 455 has no entry, and
# 455's reply to the owner announces #396 -- the pull request in 517's
# footer, which 454's reply (#531, #535) never mentions.

_DOUBLED_JOURNAL = ["j/515-cycle-453.md", "j/516-cycle-454.md",
                    "j/517-cycle-454.md", "j/518-cycle-456.md"]

_DOUBLED_ENTRIES = {"j/516-cycle-454.md": frozenset({531, 535}),
                    "j/517-cycle-454.md": frozenset({396})}

_DOUBLED_REPLIES = {454: frozenset({531, 535}), 455: frozenset({396})}


def _doubled_paths():
    return {454: ["j/516-cycle-454.md", "j/517-cycle-454.md"]}


def test_the_footer_decides_which_of_two_entries_the_lost_cycle_wrote():
    assert cycle_postmortem.doubled_entries(
        [455], _DOUBLED_ENTRIES, _DOUBLED_REPLIES, _doubled_paths()
    ) == [(455, "j/517-cycle-454.md")]


def test_a_number_with_only_one_entry_is_never_read_as_doubled():
    assert cycle_postmortem.doubled_entries(
        [455], _DOUBLED_ENTRIES, _DOUBLED_REPLIES,
        {454: ["j/517-cycle-454.md"]}) == []


def test_an_entry_the_named_cycle_also_announced_is_left_alone():
    # The second condition: 454 named #396 too, so which of the two ran
    # that work is a coin toss and this says nothing rather than picking.
    replies = {**_DOUBLED_REPLIES, 454: frozenset({396, 531})}
    assert cycle_postmortem.doubled_entries(
        [455], _DOUBLED_ENTRIES, replies, _doubled_paths()) == []


def test_a_lost_cycle_that_could_claim_both_documents_claims_neither():
    # A contradiction rather than a finding -- 454 would then have written
    # neither of the two entries carrying its own number.
    replies = {**_DOUBLED_REPLIES, 455: frozenset({396, 531, 535}),
               454: frozenset()}
    assert cycle_postmortem.doubled_entries(
        [455], _DOUBLED_ENTRIES, replies, _doubled_paths()) == []


def test_a_document_two_lost_cycles_could_both_claim_is_dropped():
    replies = {**_DOUBLED_REPLIES, 453: frozenset({396})}
    paths = dict(_doubled_paths())
    assert cycle_postmortem.doubled_entries(
        [453, 455], _DOUBLED_ENTRIES, replies, paths) == []


def test_an_entry_with_no_pull_request_in_its_footer_is_not_attributed():
    entries = {**_DOUBLED_ENTRIES, "j/517-cycle-454.md": frozenset()}
    assert cycle_postmortem.doubled_entries(
        [455], entries, _DOUBLED_REPLIES, _doubled_paths()) == []


def test_a_neighbour_whose_reply_could_not_be_read_is_skipped():
    # `None` is "cannot say", not "named nothing" -- the same call
    # `misfiled_entries` makes, and the difference is a wrong attribution.
    replies = {**_DOUBLED_REPLIES, 454: None}
    assert cycle_postmortem.doubled_entries(
        [455], _DOUBLED_ENTRIES, replies, _doubled_paths()) == []


def test_find_doubled_shortlists_on_the_filenames_before_reading_anything():
    reads = []

    def counting(path):
        reads.append(path)
        return "PR: #396 | Outcome: merged"

    # 456 carries one entry here, so nothing beside it is ever fetched.
    cycle_postmortem.find_doubled(
        _lost(457), {}, _DOUBLED_JOURNAL, read_entry=counting,
        fetch=lambda _id: [])
    assert reads == []


def test_find_doubled_reads_both_documents_and_joins_them_to_the_run():
    texts = {"j/516-cycle-454.md": "PR: #531, #535 | Outcome: merged",
             "j/517-cycle-454.md": "PR: #396 | Outcome: merged"}
    conversations = {454: {"id": "c454"}, 455: {"id": "c455"}}
    messages = {"c454": [{"text": "shipped #531 and #535"}],
                "c455": [{"text": "shipped #396"}]}
    pairs = cycle_postmortem.find_doubled(
        _lost(455), conversations, _DOUBLED_JOURNAL,
        read_entry=texts.get, fetch=lambda cid: messages[cid])
    assert pairs == [(455, "j/517-cycle-454.md")]


def test_find_doubled_reads_nothing_when_no_cycle_came_back_lost():
    reads = []
    rows = [{"number": 455, "verdict": "misfiled", "detail": "x", "messages": 1}]
    assert cycle_postmortem.find_doubled(
        rows, {}, _DOUBLED_JOURNAL, read_entry=reads.append,
        fetch=lambda _id: []) == []
    assert reads == []


def test_apply_doubled_downgrades_the_row_and_names_the_file():
    rows = _lost(455, 580)
    cycle_postmortem.apply_doubled(rows, [(455, "j/517-cycle-454.md")])
    assert rows[0]["verdict"] == "doubled"
    assert "517-cycle-454.md" in rows[0]["detail"]
    assert rows[1]["verdict"] == "lost"


def test_doubled_does_not_raise_the_exit_status():
    assert "doubled" not in cycle_postmortem.RAISING_VERDICTS


def test_doubled_has_a_heading_so_the_rows_are_printed():
    assert "doubled" in dict((h[0], h[1]) for h in cycle_postmortem._HEADINGS)


def test_format_doubled_names_the_cycle_and_the_file():
    text = "\n".join(cycle_postmortem.format_doubled(
        [(455, "j/517-cycle-454.md")]))
    assert "Cycle 455's work is in `517-cycle-454.md`" in text
    assert "never renamed" in text


def test_format_doubled_is_silent_with_nothing_to_say():
    assert cycle_postmortem.format_doubled([]) == []


def test_a_footer_the_lost_run_never_announced_is_not_attributed():
    # The first condition on its own. The second cannot cover this: a pull
    # request neither run named passes `footer & own` for free, so without
    # `footer <= said` the lost cycle claims a document that is nobody's.
    entries = {**_DOUBLED_ENTRIES, "j/516-cycle-454.md": frozenset({999})}
    replies = {**_DOUBLED_REPLIES, 454: frozenset({531, 535})}
    assert cycle_postmortem.doubled_entries(
        [455], entries, replies, _doubled_paths()) == [(455, "j/517-cycle-454.md")]


def test_an_entry_filed_one_number_down_is_found_with_the_same_two_conditions():
    """Measured live 2026-09-09: cycle 87's reply announces `#75`, and `#75`
    is the footer of `093-cycle-86.md`. Reading only `+1` left that cycle in
    RAN AND LEFT NO RECORD for four months with the entry in the folder."""
    assert misfiled_entries([87], {86: _said(75)},
                            {87: _said(75), 86: _said(74)}, step=-1) == [(87, 86)]


def test_the_downward_walk_follows_the_chain_it_finds():
    """The same shift that displaces one entry displaces the one before it:
    the live chain runs 87 -> 86 -> 85 -> 84 -> 83 -> 82."""
    assert misfiled_entries(
        [87],
        {86: _said(75), 85: _said(74), 84: _said(73)},
        {87: _said(75), 86: _said(74), 85: _said(73), 84: _said(72)},
        step=-1,
    ) == [(87, 86), (86, 85), (85, 84)]


def test_the_downward_walk_keeps_the_second_condition():
    """A neighbour that announced the entry's own pull request is ambiguous
    in this direction too, and the pair is dropped rather than guessed."""
    assert misfiled_entries([87], {86: _said(75)},
                            {87: _said(75), 86: _said(75, 74)}, step=-1) == []


def test_the_default_direction_is_still_up():
    """`step` defaults to `1`, so every existing caller is unchanged and a
    downward pair is invisible to a caller that did not ask for it."""
    assert misfiled_entries([87], {86: _said(75)},
                            {87: _said(75), 86: _said(74)}) == []


def test_a_pair_the_two_directions_disagree_about_is_dropped():
    """Two lost cycles either side of one entry is a guess between two
    answers, and `doubled_entries` makes the same call for the same reason."""
    assert merge_misfiled([(85, 86)], [(87, 86)]) == []
    assert merge_misfiled([(85, 86)], [(87, 88)]) == [(85, 86), (87, 88)]


def test_one_lost_cycle_handed_two_entries_is_dropped():
    """It cannot have written both, so neither claim is trustworthy."""
    assert merge_misfiled([(87, 88)], [(87, 86)]) == []


def test_the_downward_search_yields_to_a_block_that_named_a_document():
    """`find_misfiled` keeps one path per cycle number, so on a number that
    carries two documents it answers with an arbitrary one of them. Cycle
    455 is that case live -- `doubled` names `517-cycle-454.md` and this
    would only have said "filed as cycle 454" -- so the head of a downward
    chain that another block already explained is dropped."""
    results = [{"number": 455, "verdict": "doubled"},
               {"number": 580, "verdict": "lost"}]
    assert keep_still_lost(results, [(455, 454), (580, 579)]) == [(580, 579)]


def test_a_chain_link_below_the_head_is_not_in_results_and_survives():
    """Only the head of a chain is ever `lost`; every later link has an entry
    under its own number and never reaches `results` at all."""
    assert keep_still_lost([{"number": 87, "verdict": "lost"}],
                           [(87, 86), (86, 85)]) == [(87, 86), (86, 85)]


def test_the_block_says_which_way_the_entry_moved():
    """A reader has to know whether to look at the cycle before or the cycle
    after, and the pair alone does not say it out loud."""
    up = format_misfiled([(1183, 1184)])
    down = format_misfiled([(87, 86)])
    assert any("filed as cycle 1184 (one number up)" in line for line in up)
    assert any("filed as cycle 86 (one number down)" in line for line in down)


# --- the clock join, for a shift neither run named a pull request on ------

_LOST_WINDOW = (
    datetime(2026, 8, 28, 12, 0, 4, tzinfo=timezone.utc),
    datetime(2026, 8, 28, 12, 32, 41, tzinfo=timezone.utc),
)
#: 579's own run, which had closed before its entry was written.
_NEIGHBOUR_WINDOW = (
    datetime(2026, 8, 28, 11, 30, 4, tzinfo=timezone.utc),
    datetime(2026, 8, 28, 11, 53, 25, tzinfo=timezone.utc),
)
#: `646-cycle-579.md` stamps itself 14:25 Oslo.
_ENTRY_STAMP = datetime(2026, 8, 28, 12, 25, tzinfo=timezone.utc)


def test_clock_join_finds_the_shift_the_pr_join_cannot():
    """Cycle 580's reply names no `#number` at all, so `misfiled_entries`
    has nothing to join on and stops at its first condition. The entry
    filed as 579 stamps itself inside 580's window and outside 579's."""
    found = cycle_postmortem.clock_shifted(
        [580], {579: _ENTRY_STAMP},
        {580: _LOST_WINDOW, 579: _NEIGHBOUR_WINDOW}, step=-1)
    assert found == [(580, 579)]


def test_an_entry_stamped_inside_its_own_run_is_never_taken_off_it():
    """The guard, and the whole reason this join is two-sided. A cycle
    that files its own entry stamps it inside its own window, so an
    overlapping neighbour must not be able to claim it -- one-sided
    evidence here is a coincidence, not a measurement."""
    overlapping = (datetime(2026, 8, 28, 12, 20, tzinfo=timezone.utc),
                   datetime(2026, 8, 28, 12, 40, tzinfo=timezone.utc))
    assert cycle_postmortem.clock_shifted(
        [580], {579: _ENTRY_STAMP},
        {580: _LOST_WINDOW, 579: overlapping}, step=-1) == []


def test_a_neighbour_whose_window_cannot_be_read_is_refused_not_assumed():
    """With no window for the run the entry is filed under there is
    nothing to exclude it with, and this function exists to not accept
    one-sided evidence."""
    assert cycle_postmortem.clock_shifted(
        [580], {579: _ENTRY_STAMP}, {580: _LOST_WINDOW}, step=-1) == []


def test_a_stamp_outside_the_lost_run_is_not_claimed():
    """Cycle 275, live. `329-cycle-274.md` stamps itself 06:53 Oslo
    (04:53Z), which is outside 275's window (03:27..03:44Z) *and* outside
    274's own (02:15..02:32Z) -- so the neighbour guard cannot reject it
    and only "inside the lost run" can. That asymmetry is why this fixture
    is the real one and not the tidier neighbour on the other side:
    dropping the condition entirely left every other test in this file
    green, measured Cycle 1251.

    275 therefore stays `lost`, which is the honest answer. Its reply says
    it typed its own number as 274, and the document filed under 274 was
    written two hours after 275 stopped speaking, so it is somebody
    else's."""
    stamp_274 = datetime(2026, 8, 20, 4, 53, tzinfo=timezone.utc)
    window_275 = (datetime(2026, 8, 20, 3, 27, 1, tzinfo=timezone.utc),
                  datetime(2026, 8, 20, 3, 44, 54, tzinfo=timezone.utc))
    window_274 = (datetime(2026, 8, 20, 2, 15, 0, tzinfo=timezone.utc),
                  datetime(2026, 8, 20, 2, 32, 45, tzinfo=timezone.utc))
    assert cycle_postmortem.clock_shifted(
        [275], {274: stamp_274},
        {275: window_275, 274: window_274}, step=-1) == []


def test_the_neighbour_on_the_other_side_is_left_alone():
    """`647-cycle-581.md` is stamped 14:45 Oslo, thirteen minutes after
    580's run closed and inside 581's own."""
    later = datetime(2026, 8, 28, 12, 45, tzinfo=timezone.utc)
    assert cycle_postmortem.clock_shifted(
        [580], {581: later},
        {580: _LOST_WINDOW,
         581: (datetime(2026, 8, 28, 12, 30, 1, tzinfo=timezone.utc),
               datetime(2026, 8, 28, 12, 46, 50, tzinfo=timezone.utc))},
        step=1) == []


def test_the_clock_block_says_it_is_the_weaker_join():
    """It reads when a document says it was written, not what it says it
    did, and a reader quoting it needs to know which of the two it is."""
    lines = cycle_postmortem.format_clock_shifted([(580, 579)])
    assert any("filed as cycle 579 (one number down)" in line for line in lines)
    assert any("weaker join" in line for line in lines)
    assert cycle_postmortem.format_clock_shifted([]) == []


def test_a_utc_heading_is_not_read_as_oslo():
    """`parse_heading` answers with a bare date and time and says nothing
    about the zone, and one of the four live heading shapes states UTC:
    `### 2026-08-03 03:19Z — Cycle 6`. Taking that as Oslo moves it two
    hours in summer -- a wrong instant rather than a refusal, which is the
    same silent failure this function was just fixed for, pointed the
    other way. The zone is read off the raw heading."""
    at = cycle_postmortem.document_written_at(
        "### 2026-08-03 03:19Z — Cycle 6, closing status\n\nbody\n")
    assert at is not None
    assert at.isoformat() == "2026-08-03T03:19:00+00:00"
    # And the Oslo form of the same clock is still two hours behind it.
    assert cycle_postmortem.document_written_at(
        "### 2026-08-03 03:19 (Oslo) — Cycle 6").isoformat() == (
            "2026-08-03T01:19:00+00:00")


def test_an_unpadded_hour_is_still_a_stamp():
    """`854-retrospective.md` is a real document headed `### 2026-09-02
    7:09 (Oslo) — Retrospective`, and `nova_journal._TIME_RE` accepts a
    one-digit hour on purpose. Requiring two here would re-create the
    blindness this function was fixed for, inside the fix for it."""
    at = cycle_postmortem.document_written_at(
        "### 2026-09-02 7:09 (Oslo) — Retrospective\n\nWhat happened.\n")
    assert at is not None
    assert at.isoformat() == "2026-09-02T05:09:00+00:00"


# --- Two numbers down ------------------------------------------------------
#
# Cycle 275's real numbers. It listed the journal folder at 05:43 Oslo on
# 2026-08-20 and got `327-cycle-272.md` as the newest, because 273 had not
# written and 274 would not write for another hour; it named its own entry
# from that and filed as `328-cycle-273.md`. Footer `#246, #247`, which is
# what 275's reply announced and what 273's reply (`#7, #62`) does not.

def test_an_entry_filed_two_numbers_down_is_found():
    from tools.cycle_postmortem import misfiled_entries
    assert misfiled_entries(
        [275],
        {273: frozenset({246, 247})},
        {275: frozenset({7, 94, 246, 247}), 273: frozenset({7, 62})},
        step=-2,
    ) == [(275, 273)]


def test_two_numbers_down_still_refuses_when_the_filed_cycle_named_it_too():
    """The second condition is what keeps a coincidence out, and widening
    the search widens what could coincide. If 273 had announced `#246` as
    well, the pair is ambiguous and this says nothing rather than picking
    one."""
    from tools.cycle_postmortem import misfiled_entries
    assert misfiled_entries(
        [275],
        {273: frozenset({246, 247})},
        {275: frozenset({246, 247}), 273: frozenset({246})},
        step=-2,
    ) == []


def test_one_number_down_does_not_reach_cycle_275():
    """The pass that already existed cannot find this one: 274's entry
    footer is `#248`, which 275 never announced. Without this assertion a
    `-2` search that quietly behaved like `-1` would still pass the test
    above, since `misfiled_entries` is called with the step under test."""
    from tools.cycle_postmortem import misfiled_entries
    assert misfiled_entries(
        [275],
        {274: frozenset({248}), 273: frozenset({246, 247})},
        {275: frozenset({7, 94, 246, 247}), 274: frozenset({7, 63}),
         273: frozenset({7, 62})},
        step=-1,
    ) == []


def test_the_report_names_the_real_distance():
    """It said "one number down" for every negative distance, which was
    true only while the search was one number wide."""
    from tools.cycle_postmortem import describe_shift, format_misfiled
    assert describe_shift(-2) == "two numbers down"
    assert describe_shift(-1) == "one number down"
    assert describe_shift(1) == "one number up"
    block = "\n".join(format_misfiled([(275, 273)]))
    assert "filed as cycle 273 (two numbers down)" in block
    assert "one number down)" not in block


def test_the_down_search_reaches_two_numbers_and_the_report_says_so():
    """Cycle 275's real shape, through the wiring rather than through
    `misfiled_entries` alone: 274's entry (footer `#248`) is not 275's and
    the one-number pass correctly refuses it, so a search that stops at one
    leaves 275 `lost` with `328-cycle-273.md` sitting in the folder."""
    from tools.cycle_postmortem import find_shifted_down
    paths = ["projects/x/journal/328-cycle-273.md",
             "projects/x/journal/329-cycle-274.md"]
    entries = {paths[0]: "### Cycle 273\n\n---\nPR: #246, #247 | Outcome: merged\n",
               paths[1]: "### Cycle 274\n\n---\nPR: #248 | Outcome: merged\n"}
    replies = {"c273": [{"text": "merged #7 and #62"}],
               "c274": [{"text": "merged #7 and #63"}],
               "c275": [{"text": "That's runner#246, merged. And runner#247, merged. issue #7 #94"}]}
    results = [{"number": 275, "verdict": "lost", "detail": "ran 16m 45s"}]
    pairs = find_shifted_down(
        results,
        {273: {"id": "c273"}, 274: {"id": "c274"}, 275: {"id": "c275"}},
        paths,
        read_entry=entries.get,
        fetch=lambda cid: replies[cid],
    )
    assert pairs == [(275, 273)]
    assert results[0]["verdict"] == "misfiled"
    assert "filed as cycle 273" in results[0]["detail"]


def test_the_nearer_join_wins_when_both_could_claim_a_row():
    """Nearest first is not cosmetic: with the same footer one and two
    numbers down, the row must go to the nearer entry rather than to
    whichever pass happened to run last."""
    from tools.cycle_postmortem import find_shifted_down
    paths = ["projects/x/journal/010-cycle-8.md", "projects/x/journal/011-cycle-9.md"]
    entries = {paths[0]: "### Cycle 8\n\n---\nPR: #500 | Outcome: merged\n",
               paths[1]: "### Cycle 9\n\n---\nPR: #500 | Outcome: merged\n"}
    replies = {"c8": [{"text": "merged #1"}], "c9": [{"text": "merged #2"}],
               "c10": [{"text": "merged #500"}]}
    results = [{"number": 10, "verdict": "lost", "detail": "ran 9m"}]
    assert find_shifted_down(
        results, {8: {"id": "c8"}, 9: {"id": "c9"}, 10: {"id": "c10"}}, paths,
        read_entry=entries.get, fetch=lambda cid: replies[cid],
    ) == [(10, 9)]


# --- a `lost` row's branch, and whether its content reached main -------------
#
# Cycle 359 replied that its work was "committed on
# `nova/status-word-back-on-the-card`" and then wrote no entry, so every
# reader met it under a heading claiming the work was gone. Most of it was
# not: it merged as #316. These pin the reading, not the conclusion.


def test_branches_named_takes_a_backticked_branch_and_not_a_filename():
    reply = ("The work is committed on `nova/status-word-back-on-the-card`. "
             "One `git push` finishes it; see `tools/mutate.py` and `agora_runner/x.py`.")
    assert cycle_postmortem.branches_named(reply) == [
        "nova/status-word-back-on-the-card"]


def test_branches_named_dedupes_and_keeps_reply_order():
    reply = "on `b/two` then `a/one` then `b/two` again"
    assert cycle_postmortem.branches_named(reply) == ["b/two", "a/one"]


def test_branches_named_reads_nothing_out_of_an_empty_reply():
    assert cycle_postmortem.branches_named(None) == []
    assert cycle_postmortem.branches_named("no branch here, just `push`") == []


class _Done:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _runner(refs):
    """A fake git: `refs` maps a branch name to the oid origin has for it."""
    calls = []

    def run(root, clone, *args):
        calls.append(args)
        if args[0] == "fetch":
            return _Done(0, "")
        name = args[-1].replace("origin/", "").replace("^{commit}", "")
        if name in refs:
            return _Done(0, refs[name] + "\n")
        return _Done(1, "")
    run.calls = calls
    return run


def test_branch_landing_fetches_before_it_reads_the_ref():
    # The oid guard inside `_content_landed` is only honest against a ref
    # that was just fetched; a stale ref makes the guard pass on the wrong
    # commit. Deleting the fetch must fail here.
    run = _runner({"nova/x": "abc123"})
    seen = {}

    def landed(root, clone, base, branch, sha):
        seen["sha"] = sha
        return (5, 5)

    rows = cycle_postmortem.branch_landing(["nova/x"], "/r", "c",
                                           run=run, landed=landed)
    assert ("fetch", "--quiet", "origin", "nova/x") in run.calls
    assert run.calls[0][0] == "fetch"
    assert seen["sha"] == "abc123"
    assert rows == [{"branch": "nova/x", "verdict": "landed",
                     "landed": 5, "total": 5}]


def test_branch_landing_separates_gone_from_unmeasurable():
    run = _runner({"nova/there": "deadbeef"})
    rows = cycle_postmortem.branch_landing(
        ["nova/there", "nova/missing"], "/r", "c",
        run=run, landed=lambda *a: None)
    assert [row["verdict"] for row in rows] == ["unmeasurable", "gone"]


def test_branch_landing_calls_a_ref_gone_when_rev_parse_prints_nothing():
    # `rev-parse --verify --quiet` can exit 0 with empty stdout; reading that
    # as an oid hands `_content_landed` an empty sha, which its own guard
    # then compares against the ref and refuses -- an `unmeasurable` where
    # the honest answer is `gone`.
    def run(root, clone, *args):
        return _Done(0, "" if args[0] == "rev-parse" else "")
    rows = cycle_postmortem.branch_landing(["nova/x"], "/r", "c", run=run,
                                           landed=lambda *a: (1, 1))
    assert rows[0]["verdict"] == "gone"


def test_branch_landing_calls_a_short_match_partly():
    run = _runner({"nova/x": "abc"})
    rows = cycle_postmortem.branch_landing(["nova/x"], "/r", "c", run=run,
                                           landed=lambda *a: (103, 158))
    assert rows[0] == {"branch": "nova/x", "verdict": "partly",
                       "landed": 103, "total": 158}


def test_branch_landing_lines_say_so_when_a_reply_names_none():
    # "checked and it names none" and "nobody checked" are opposite findings.
    assert cycle_postmortem._branch_landing_lines(None) == []
    text = "\n".join(cycle_postmortem._branch_landing_lines([]))
    assert "names no branch" in text


def test_branch_landing_lines_do_not_call_a_partial_match_a_verdict():
    rows = [{"branch": "nova/x", "verdict": "partly", "landed": 103, "total": 158}]
    text = "\n".join(cycle_postmortem._branch_landing_lines(rows))
    assert "103 of its 158" in text
    assert "not a verdict" in text


def test_branch_landing_lines_name_the_whole_match():
    rows = [{"branch": "nova/x", "verdict": "landed", "landed": 5, "total": 5}]
    text = "\n".join(cycle_postmortem._branch_landing_lines(rows))
    assert "all 5 of its added lines" in text
    assert "the ENTRY is missing" in text


def test_apply_branch_landing_touches_only_lost_rows():
    results = [{"number": 359, "verdict": "lost", "reply": "on `nova/x`"},
               {"number": 360, "verdict": "misfiled", "reply": "on `nova/y`"}]
    seen = []

    def measure(names, root, clone, base="main"):
        seen.append(names)
        return [{"branch": n, "verdict": "landed", "landed": 1, "total": 1}
                for n in names]

    cycle_postmortem.apply_branch_landing(results, "/r", "c", measure=measure)
    assert seen == [["nova/x"]]
    assert results[0]["branch_landing"][0]["branch"] == "nova/x"
    assert "branch_landing" not in results[1]


def test_format_report_prints_the_branch_line_under_a_lost_row():
    results = [{"number": 359, "verdict": "lost", "detail": "ran 41m",
                "messages": 74, "recent": False, "reply": "on `nova/x`",
                "branch_landing": [{"branch": "nova/x", "verdict": "landed",
                                    "landed": 5, "total": 5}]}]
    text, status = cycle_postmortem.format_report(results, 1257, None)
    assert "all 5 of its added lines" in text
    # The branch line explains; it does not excuse. The entry really is
    # missing, so `lost` must still be a raising verdict.
    assert status == 2 or "lost" in cycle_postmortem.RAISING_VERDICTS
