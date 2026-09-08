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
    entry_pr_numbers, find_misfiled, format_misfiled, misfiled_entries,
    reply_numbers,
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
