"""Does the dropped-firing count survive a grid that is not the fixture's?"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tools import heartbeat_gaps as hg


NOW = datetime(2026, 9, 6, 2, 30, tzinfo=timezone.utc)


def _runs(*offsets_minutes):
    return [NOW - timedelta(minutes=m) for m in offsets_minutes]


def test_no_gap_when_every_slot_fired():
    runs = _runs(0, 15, 30, 45, 60)
    missed = hg.missed_slots(runs, 900, NOW - timedelta(hours=1), NOW)
    assert missed == []


def test_names_the_slot_that_produced_no_run():
    # Runs at 02:30, 02:15, 01:45 and 01:30: 02:00 is the absent slot.
    runs = _runs(0, 15, 45, 60)
    missed = hg.missed_slots(runs, 900, NOW - timedelta(hours=1), NOW)
    assert [m.strftime("%H:%M") for m in missed] == ["02:00"]


def test_a_run_is_claimed_by_one_slot_only():
    # A run 7 minutes late still belongs to its own slot and does not also
    # satisfy the next one -- otherwise one late run hides a real gap.
    runs = [NOW, NOW - timedelta(minutes=15), NOW - timedelta(minutes=37)]
    missed = hg.missed_slots(runs, 900, NOW - timedelta(minutes=50), NOW)
    assert [m.strftime("%H:%M") for m in missed] == ["01:45"]


def test_window_start_bounds_the_grid():
    runs = _runs(0)
    hour = hg.missed_slots(runs, 900, NOW - timedelta(hours=1), NOW)
    two = hg.missed_slots(runs, 900, NOW - timedelta(hours=2), NOW)
    assert len(hour) == 4 and len(two) == 8


def test_no_runs_at_all_is_not_a_grid():
    # With nothing to anchor on, this reports nothing rather than inventing
    # a full window of gaps -- heartbeat_health owns the "stopped" verdict.
    assert hg.missed_slots([], 900, NOW - timedelta(hours=1), NOW) == []


def _heartbeat(**over):
    row = {
        "id": "hb", "name": "Nova", "schedule": "every@15m@16:00",
        "enabled": True, "rotateConversationEachRun": True,
        "conversationId": "c0",
    }
    row.update(over)
    return row


def _conversations(offsets_minutes, folder="f1"):
    rows = []
    for i, m in enumerate(offsets_minutes):
        rows.append({
            "id": "c0" if i == 0 else f"c{i}",
            "folderId": folder,
            "createdAt": (NOW - timedelta(minutes=m)).isoformat().replace("+00:00", "Z"),
        })
    return rows


def test_judge_counts_runs_in_the_heartbeats_own_folder():
    convs = _conversations([0, 15, 45]) + [
        {"id": "other", "folderId": "f2",
         "createdAt": (NOW - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")},
    ]
    row = hg.judge(_heartbeat(), convs, NOW, 1)
    assert row["verdict"] == "judged"
    assert row["runs"] == 3
    # The 02:00 conversation is in another folder, so it is another
    # heartbeat's run and must not fill this one's slot.
    assert [m.strftime("%H:%M") for m in row["missed"]] == ["02:00", "01:30"]


def test_a_non_rotating_heartbeat_is_unjudged_not_clean():
    row = hg.judge(_heartbeat(rotateConversationEachRun=None), _conversations([0]), NOW, 1)
    assert row["verdict"] == "unjudged"
    assert "per-run trace" in row["detail"]


def test_an_unreadable_schedule_is_unjudged():
    row = hg.judge(_heartbeat(schedule="whenever"), _conversations([0]), NOW, 1)
    assert row["verdict"] == "unjudged"
    assert "not readable" in row["detail"]


def test_a_missing_current_conversation_is_unjudged():
    row = hg.judge(_heartbeat(conversationId="gone"), _conversations([0]), NOW, 1)
    assert row["verdict"] == "unjudged"
    assert "folder" in row["detail"]


def test_unreadable_is_exit_one_and_says_so():
    text, status = hg.format_report([], "could not read http://x/conversations: boom", 24, 0)
    assert status == 1
    assert "COULD NOT READ" in text
    assert "not a clean sweep" in text


def test_dropped_firings_a_rollout_accounts_for_do_not_raise():
    # This used to assert that NO dropped firing raises, on the premise that
    # the runner was `strategy: Recreate` and every drop was by design. The
    # premise expired on 2026-09-06; the exemption survives only for slots a
    # rollout actually landed in. Runs at 02:30, 02:15 and 01:45, so 02:00
    # and 01:30 are the missed slots -- one rollout inside each period.
    row = hg.judge(_heartbeat(), _conversations([0, 15, 45]), NOW, 1)
    rolled = [NOW - timedelta(minutes=35), NOW - timedelta(minutes=65)]
    text, status = hg.format_report([row], None, 1, 4, rolled, {"type": "Recreate"})
    assert status == 0, text
    assert "2 firing(s) produced no run" in text
    assert "01:30" in text
    assert "UNEXPLAINED" not in text


def test_only_unjudged_rows_is_exit_one():
    row = hg.judge(_heartbeat(rotateConversationEachRun=None), _conversations([0]), NOW, 1)
    text, status = hg.format_report([row], None, 24, 1)
    assert status == 1
    assert "NOT JUDGED" in text


def test_unjudged_rows_sharing_a_reason_collapse_to_one_line_but_keep_every_name():
    convs = _conversations([0])
    rows = [
        hg.judge(_heartbeat(name=n, rotateConversationEachRun=None), convs, NOW, 1)
        for n in ("Sentinel", "Retro", "Design")
    ]
    rows.append(hg.judge(_heartbeat(name="Off", enabled=False), convs, NOW, 1))
    text, _ = hg.format_report(rows, None, 24, 1)
    assert text.count("NOT JUDGED") == 2, text
    for name in ("Sentinel", "Retro", "Design", "Off"):
        assert name in text


def test_a_short_list_names_its_own_shortfall():
    # Asked for 24h, but Agora listed nothing older than 45 minutes.
    row = hg.judge(_heartbeat(), _conversations([0, 15, 30, 45]), NOW, 24)
    text, _ = hg.format_report([row], None, 24, 4)
    assert "shorter than the one asked for" in text


def test_a_full_window_does_not_claim_a_shortfall():
    row = hg.judge(_heartbeat(), _conversations([0, 15, 30, 45, 55]), NOW, 1)
    text, _ = hg.format_report([row], None, 1, 5)
    assert "shorter than the one asked for" not in text


def test_fetch_conversations_reports_an_error_rather_than_an_empty_list():
    def boom(*a, **k):
        raise OSError("refused")
    rows, error = hg.fetch_conversations(opener=boom)
    assert rows == []
    assert "could not read" in error


# --- attributing a missed slot to a rollout ------------------------------
#
# The check used to end on a fixed sentence asserting the runner is
# `strategy: Recreate` and that every dropped firing is therefore by
# design. It moved to RollingUpdate on 2026-09-06 and the sentence did not,
# so four lost cycles a day were being explained away by a premise that had
# stopped being true. These fix the shape of that: read it, attribute what
# a rollout accounts for, and charge what it does not.


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _fake_kubectl(by_resource):
    """A `subprocess.run` that answers per resource, and records the args."""
    def run(argv, capture_output=None, text=None):
        for key, proc in by_resource.items():
            if key in argv:
                return proc
        return _Proc(returncode=1, stderr=f"unexpected: {argv}")
    return run


def test_a_rollout_inside_the_slots_own_period_explains_it():
    slot = NOW
    rollout = NOW - timedelta(minutes=10)
    explained, unexplained = hg.attribute([slot], [rollout], 900)
    assert explained == [slot] and unexplained == []


def test_a_rollout_older_than_the_period_explains_nothing():
    # The boundary matters: a rollout exactly one period back belongs to the
    # PREVIOUS slot, and lending it forward is how every miss gets excused.
    slot = NOW
    explained, unexplained = hg.attribute([slot], [NOW - timedelta(seconds=900)], 900)
    assert explained == [] and unexplained == [slot]


def test_a_rollout_after_the_slot_explains_nothing():
    slot = NOW - timedelta(minutes=30)
    explained, unexplained = hg.attribute([slot], [NOW], 900)
    assert unexplained == [slot]


def test_an_unexplained_slot_raises_and_an_explained_one_does_not():
    # Runs at 02:30, 02:15, 01:45, 01:30 -- 02:00 is the missed slot.
    row = hg.judge(_heartbeat(), _conversations([0, 15, 45, 60]), NOW, 1)
    rolled = NOW - timedelta(minutes=35)   # inside 01:45..02:00
    text, status = hg.format_report([row], None, 1, 4, [rolled], {"type": "RollingUpdate"})
    assert status == 0, text
    assert "UNEXPLAINED" not in text

    text, status = hg.format_report([row], None, 1, 4, [], {"type": "RollingUpdate"})
    assert status == 2, text
    assert "UNEXPLAINED — 1 slot(s)" in text


def test_a_missing_slot_count_reaches_the_line_preflight_reads():
    row = hg.judge(_heartbeat(), _conversations([0, 15, 45, 60]), NOW, 1)
    text, _ = hg.format_report([row], None, 1, 4, [], {"type": "RollingUpdate"})
    assert "1 of them with no rollout to explain it" in text.splitlines()[-1]


def test_an_unreadable_rollout_shape_is_neither_clean_nor_an_excuse():
    row = hg.judge(_heartbeat(), _conversations([0, 15, 45, 60]), NOW, 1)
    text, status = hg.format_report(
        [row], None, 1, 4, [], None, "kubectl failed: Forbidden"
    )
    assert status == 1, text
    assert "COULD NOT READ the runner's rollout shape" in text
    # It must not charge the slot either -- an unattributable slot is not an
    # unexplained one.
    assert "UNEXPLAINED" not in text
    assert "NOT ATTRIBUTED" in text


def test_the_shape_is_read_off_the_live_deployment_not_asserted():
    doc = {
        "spec": {
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxUnavailable": 1, "maxSurge": 1},
            },
            "template": {"spec": {"terminationGracePeriodSeconds": 2880}},
        }
    }
    shape, error = hg.read_rollout_shape(
        _fake_kubectl({"deploy": _Proc(stdout=json.dumps(doc))})
    )
    assert error is None
    assert shape["type"] == "RollingUpdate"
    assert shape["maxUnavailable"] == 1 and shape["grace"] == 2880


def test_an_unreadable_deployment_is_an_error_not_a_default_shape():
    shape, error = hg.read_rollout_shape(
        _fake_kubectl({"deploy": _Proc(returncode=1, stderr="Forbidden")})
    )
    assert shape is None and "Forbidden" in error


def test_rollout_instants_come_from_replicaset_creation_times():
    doc = {"items": [
        {"metadata": {"creationTimestamp": "2026-09-07T21:32:38Z"}},
        {"metadata": {"creationTimestamp": "2026-09-07T17:42:06Z"}},
        {"metadata": {}},
    ]}
    times, error = hg.read_rollout_instants(
        _fake_kubectl({"rs": _Proc(stdout=json.dumps(doc))})
    )
    assert error is None
    assert [t.strftime("%H:%M") for t in times] == ["17:42", "21:32"]


def _conversations_with_ends(pairs, folder="f1"):
    """`[(start_offset, last_message_offset)]` in minutes before NOW."""
    rows = []
    for i, (start, end) in enumerate(pairs):
        rows.append({
            "id": "c0" if i == 0 else f"c{i}",
            "folderId": folder,
            "createdAt": (NOW - timedelta(minutes=start)).isoformat().replace("+00:00", "Z"),
            "lastMessageAt": (NOW - timedelta(minutes=end)).isoformat().replace("+00:00", "Z"),
        })
    return rows


def test_a_slot_inside_a_running_cycle_is_covered_not_idle():
    slot = NOW - timedelta(minutes=30)
    intervals = [(NOW - timedelta(minutes=45), NOW - timedelta(minutes=20))]
    covered, idle = hg.split_by_in_flight([slot], intervals)
    assert covered == [slot] and idle == []


def test_a_slot_after_the_last_word_of_every_run_is_idle():
    slot = NOW - timedelta(minutes=30)
    # The run ended five minutes BEFORE the slot came round, so nothing was
    # in flight and the poller had an idle loop when it dropped the tick.
    intervals = [(NOW - timedelta(minutes=60), NOW - timedelta(minutes=35))]
    covered, idle = hg.split_by_in_flight([slot], intervals)
    assert idle == [slot] and covered == []


def test_a_runs_own_start_does_not_cover_its_own_slot():
    # Otherwise every firing that DID produce a run would read as covered by
    # the run it produced, and the split would say "covered" for everything.
    slot = NOW - timedelta(minutes=45)
    intervals = [(slot, NOW - timedelta(minutes=20))]
    covered, idle = hg.split_by_in_flight([slot], intervals)
    assert idle == [slot] and covered == []


def test_a_run_that_has_said_nothing_covers_no_slot():
    # `lastMessageAt` absent must not become an open-ended interval that
    # swallows every later slot -- covering a slot needs evidence.
    convs = _conversations([0, 15, 45, 60])
    row = hg.judge(_heartbeat(), convs, NOW, 1)
    covered, idle = hg.split_by_in_flight(row["missed"], row["intervals"])
    assert covered == [] and idle == row["missed"]


def test_the_report_separates_a_covered_slot_from_an_idle_one():
    # Runs at 02:30, 02:15, 01:45, 01:30; 02:00 is missed, and the 01:45 run
    # was still talking at 02:05, so it was in flight when 02:00 came round.
    convs = _conversations_with_ends([(0, 0), (15, 10), (45, 25), (60, 50)])
    row = hg.judge(_heartbeat(), convs, NOW, 1)
    text, status = hg.format_report([row], None, 1, 4, [], {"type": "RollingUpdate"})
    assert status == 2, text
    assert "1 of them came round with an earlier run still going" in text
    assert "came round with nothing running at all" not in text


def test_a_covered_slot_is_still_charged():
    # A run in flight is not an excuse: the concurrency limit is 3, so a slot
    # dropped with one run going is a tick the poller had room to take.
    convs = _conversations_with_ends([(0, 0), (15, 10), (45, 25), (60, 50)])
    row = hg.judge(_heartbeat(), convs, NOW, 1)
    text, status = hg.format_report([row], None, 1, 4, [], {"type": "RollingUpdate"})
    assert status == 2
    assert "1 of them with no rollout to explain it" in text.splitlines()[-1]


def test_a_slot_on_a_runs_last_word_is_still_covered():
    # The boundary is closed at the end on purpose: a run whose newest
    # message lands exactly on the slot was demonstrably alive at it.
    slot = NOW - timedelta(minutes=30)
    intervals = [(NOW - timedelta(minutes=45), slot)]
    covered, idle = hg.split_by_in_flight([slot], intervals)
    assert covered == [slot] and idle == []


# --- the poller's own reason for a slot it declined (idea #267) --------------
#
# The reason a due tick was dropped used to exist only in the runner's stdout,
# which is collected with the Pod, so a slot lost yesterday could be counted
# and never explained. `agora_runner.dropped_ticks` writes it to the vault;
# these hold the half that reads it back.


def _drop(at, reason="3 run(s) in flight, limit 3", hb="hb"):
    return {"at": at.isoformat(), "heartbeatId": hb, "heartbeat": "Nova",
            "reason": reason, "dropsSinceLastStart": 1}


def test_a_drop_record_is_matched_to_the_slot_it_was_written_in():
    slot = NOW - timedelta(minutes=30)
    found = hg.reasons_for([slot], [_drop(slot + timedelta(minutes=5))], 900)
    assert found[slot] == ["3 run(s) in flight, limit 3"]


def test_a_drop_record_from_the_next_slot_is_not_lent_backwards():
    # Same boundary `attribute` is tested at, for the same reason: a reason
    # printed against the wrong slot is worse than no reason at all.
    slot = NOW - timedelta(minutes=30)
    found = hg.reasons_for([slot], [_drop(slot + timedelta(seconds=900))], 900)
    assert found[slot] == []


def test_a_drop_record_from_another_heartbeat_is_not_borrowed():
    slot = NOW - timedelta(minutes=30)
    records = [_drop(slot + timedelta(minutes=1), hb="somebody-else")]
    assert hg.reasons_for([slot], records, 900, "hb")[slot] == []


def test_a_drop_record_with_no_heartbeat_id_is_still_used():
    # Records written before the field existed must not be silently dropped:
    # the history is the whole reason this ledger is kept.
    slot = NOW - timedelta(minutes=30)
    records = [{"at": (slot + timedelta(minutes=1)).isoformat(), "reason": "old"}]
    assert hg.reasons_for([slot], records, 900, "hb")[slot] == ["old"]


def test_a_record_with_an_unreadable_timestamp_is_skipped_not_guessed():
    slot = NOW - timedelta(minutes=30)
    records = [{"at": "not a time", "heartbeatId": "hb", "reason": "why"}]
    assert hg.reasons_for([slot], records, 900, "hb")[slot] == []


def test_the_reason_reaches_the_report_under_the_slot_it_explains():
    row = hg.judge(_heartbeat(), _conversations([0, 15, 45, 60]), NOW, 1)
    missed = NOW - timedelta(minutes=30)
    text, status = hg.format_report(
        [row], None, 1, 4, [], {"type": "RollingUpdate"}, None,
        [_drop(missed + timedelta(minutes=2), "claim for lastRunAt=X not visible yet")],
        None,
    )
    assert status == 2, text
    assert "the poller said: claim for lastRunAt=X not visible yet" in text


def test_a_slot_with_no_recorded_reason_says_nothing_rather_than_guessing():
    row = hg.judge(_heartbeat(), _conversations([0, 15, 45, 60]), NOW, 1)
    text, _ = hg.format_report(
        [row], None, 1, 4, [], {"type": "RollingUpdate"}, None, [], None)
    assert "the poller said" not in text


def test_an_unreadable_ledger_says_so_rather_than_reading_as_no_reasons():
    row = hg.judge(_heartbeat(), _conversations([0, 15, 45, 60]), NOW, 1)
    text, _ = hg.format_report(
        [row], None, 1, 4, [], {"type": "RollingUpdate"}, None, [], "ledger is not JSON")
    assert "NO REASONS READ — ledger is not JSON" in text


def test_a_ledger_that_does_not_exist_yet_is_an_empty_measurement():
    # Nothing has been dropped since this shipped. That is not an error, and
    # reporting it as one would make every clean sweep carry a scary line.
    def run(argv, capture_output=None, text=None, timeout=None):
        return _Proc(returncode=0, stdout=f"[not found: {hg.DROP_LEDGER}]\n")

    assert hg.read_drop_records(runner=run) == ([], None)


def test_a_ledger_holding_something_other_than_json_is_an_error():
    def run(argv, capture_output=None, text=None, timeout=None):
        return _Proc(returncode=0, stdout="{not json")

    records, error = hg.read_drop_records(runner=run)
    assert records == [] and "not JSON" in error


def test_a_ledger_holding_a_list_is_returned_verbatim():
    stored = [_drop(NOW)]

    def run(argv, capture_output=None, text=None, timeout=None):
        return _Proc(returncode=0, stdout=json.dumps(stored) + "\n")

    assert hg.read_drop_records(runner=run) == (stored, None)


# --- the scheduler-lag ledger --------------------------------------------
# `read_drop_records` above explains a slot the poller DECLINED. These cover
# the other loss: the poller never looked, which is what `note_pass` records.

def _lag(at, gap, pass_seconds=1.0, interval=5.0):
    return {"at": at, "gapSeconds": gap, "passSeconds": pass_seconds,
            "intervalSeconds": interval, "lateSinceHealthy": 1}


def test_lag_summary_is_silent_when_the_scheduler_was_never_late():
    now = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    assert hg.lag_summary([], now, 24) is None


def test_lag_summary_drops_records_older_than_the_window():
    now = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    old = _lag("2026-09-05T19:00:00+00:00", 900.0)
    assert hg.lag_summary([old], now, 24) is None


def test_lag_summary_names_the_worst_gap_and_its_pass():
    now = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    records = [_lag("2026-09-08T10:00:00+00:00", 30.0, pass_seconds=1.0),
               _lag("2026-09-08T11:00:00+00:00", 612.5, pass_seconds=611.0)]
    line = hg.lag_summary(records, now, 24)
    assert "2 recorded pass(es)" in line
    assert "612.5s" in line
    assert "611.0s" in line


def test_a_record_with_no_usable_stamp_is_still_counted():
    """Dropping it would understate an outage, which is the wrong direction."""
    now = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    records = [_lag("not a date", 44.0)]
    line = hg.lag_summary(records, now, 24)
    assert line is not None
    assert "1 recorded pass(es)" in line


def test_an_unreadable_lag_ledger_says_so_rather_than_reading_as_clean():
    def runner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="{not json", stderr="")

    records, error = hg.read_lag_records(runner=runner)
    assert records == []
    assert error and "scheduler-lag.json" in error


def test_a_missing_lag_ledger_is_an_empty_measurement_not_an_error():
    def runner(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0, stdout="[not found: whatever]", stderr="")

    assert hg.read_lag_records(runner=runner) == ([], None)


# --- declined, or never evaluated? (idea #267) -------------------------------
#
# A covered slot -- one with an earlier run still going -- was reported for six
# hours as "the poller declined a tick it had room for". That is a cause the
# check never measured: the same evidence fits the poller never reaching the
# tick at all (`_skipped_occurrences`, runner#916), which is a different bug.
# The drop-record ledger is the only thing that separates them, and an EMPTY
# ledger separates nothing -- these hold that line.


def test_ledger_start_is_the_oldest_record_it_holds():
    oldest = NOW - timedelta(hours=3)
    records = [_drop(NOW - timedelta(minutes=5)), _drop(oldest),
               _drop(NOW - timedelta(hours=1))]
    assert hg.ledger_start(records) == oldest


def test_an_empty_ledger_speaks_for_no_slot_at_all():
    assert hg.ledger_start([]) is None


def test_a_ledger_of_unreadable_stamps_speaks_for_no_slot_either():
    # Not "now", and not the epoch: a record whose time cannot be read says
    # nothing about how far back the ledger reaches.
    assert hg.ledger_start([_drop_unparseable(), {"nope": 1}, "junk"]) is None


def _drop_unparseable():
    return {"at": "not a time", "heartbeatId": "hb", "reason": "why"}


def test_a_slot_the_poller_named_is_declined():
    slot = NOW - timedelta(minutes=30)
    named = {slot: ["3 run(s) in flight, limit 3"]}
    declined, unevaluated, unattributed = hg.split_by_drop_record(
        [slot], named, NOW - timedelta(hours=3))
    assert (declined, unevaluated, unattributed) == ([slot], [], [])


def test_a_slot_the_covering_ledger_is_silent_about_was_never_evaluated():
    slot = NOW - timedelta(minutes=30)
    declined, unevaluated, unattributed = hg.split_by_drop_record(
        [slot], {}, NOW - timedelta(hours=3))
    assert (declined, unevaluated, unattributed) == ([], [slot], [])


def test_a_slot_older_than_the_ledger_is_neither():
    # The recorder was not writing yet. Absent is not empty, and calling this
    # "never evaluated" would be inventing the cause all over again.
    slot = NOW - timedelta(hours=5)
    declined, unevaluated, unattributed = hg.split_by_drop_record(
        [slot], {}, NOW - timedelta(hours=3))
    assert (declined, unevaluated, unattributed) == ([], [], [slot])


def test_a_slot_exactly_at_the_ledger_start_is_covered_by_it():
    start = NOW - timedelta(minutes=30)
    declined, unevaluated, unattributed = hg.split_by_drop_record(
        [start], {}, start)
    assert unevaluated == [start] and unattributed == []


def test_with_no_ledger_at_all_every_covered_slot_is_unattributed():
    slot = NOW - timedelta(minutes=30)
    declined, unevaluated, unattributed = hg.split_by_drop_record(
        [slot], {}, None)
    assert (declined, unevaluated, unattributed) == ([], [], [slot])


def _covered_row():
    # 02:00 is missed and the 01:45 run was still talking at 02:05.
    convs = _conversations_with_ends([(0, 0), (15, 10), (45, 25), (60, 50)])
    return hg.judge(_heartbeat(), convs, NOW, 1)


def test_the_report_says_declined_only_when_the_poller_said_so():
    missed = NOW - timedelta(minutes=30)
    text, status = hg.format_report(
        [_covered_row()], None, 1, 4, [], {"type": "RollingUpdate"}, None,
        [_drop(missed + timedelta(minutes=2))], None,
    )
    assert status == 2, text
    assert "the poller recorded declining them" in text
    assert "never evaluated rather than declined" not in text
    assert "does not reach back that far" not in text


def test_the_report_calls_a_silent_covering_ledger_never_evaluated():
    # A record from an earlier slot: it puts the ledger's start before the
    # missed slot without explaining the missed slot itself.
    text, status = hg.format_report(
        [_covered_row()], None, 1, 4, [], {"type": "RollingUpdate"}, None,
        [_drop(NOW - timedelta(minutes=50))], None,
    )
    assert status == 2, text
    assert "the tick was never evaluated rather than declined" in text
    assert "the poller recorded declining them" not in text


def test_the_report_refuses_to_pick_a_cause_with_an_empty_ledger():
    text, status = hg.format_report(
        [_covered_row()], None, 1, 4, [], {"type": "RollingUpdate"}, None,
        [], None,
    )
    assert status == 2, text
    assert "does not reach back that far" in text
    assert "never evaluated rather than declined" not in text
    assert "the poller recorded declining them" not in text


def test_an_unreadable_ledger_never_reads_as_never_evaluated():
    # `[]` with an error is not `[]` measured. The cause stays open.
    text, _ = hg.format_report(
        [_covered_row()], None, 1, 4, [], {"type": "RollingUpdate"}, None,
        [], "dropped-ticks.json is not JSON",
    )
    assert "does not reach back that far" in text
    assert "never evaluated rather than declined" not in text


def test_no_report_still_claims_the_poller_declined_a_tick_it_had_room_for():
    # The exact sentence this replaced, in every ledger state.
    for records, error in (([], None), ([_drop(NOW - timedelta(minutes=50))], None),
                           ([], "unreadable")):
        text, _ = hg.format_report(
            [_covered_row()], None, 1, 4, [], {"type": "RollingUpdate"}, None,
            records, error,
        )
        assert "declined a tick it had room for" not in text


# --- a scheduler pass that raised ----------------------------------------


def _fail(at, error="RuntimeError: agora unreachable", n=1):
    return {"at": at.isoformat(), "error": error,
            "errorType": error.split(":")[0], "failedSinceHealthy": n}


def test_a_failure_record_is_matched_to_the_slot_it_was_written_in():
    slot = NOW - timedelta(minutes=30)
    found = hg.reasons_for([slot], [_fail(slot + timedelta(minutes=5))], 900,
                           field="error")
    assert found[slot] == ["RuntimeError: agora unreachable"]


def test_a_failure_record_from_the_next_slot_is_not_lent_backwards():
    slot = NOW - timedelta(minutes=30)
    found = hg.reasons_for([slot], [_fail(slot + timedelta(seconds=900))], 900,
                           field="error")
    assert found[slot] == []


def test_the_report_names_a_raised_pass_instead_of_calling_it_unevaluated():
    # The whole point: with no failure ledger this same slot reads as
    # `unevaluated`, which is the name of the poller sleeping through it.
    missed = NOW - timedelta(minutes=30)
    text, status = hg.format_report(
        [_covered_row()], None, 1, 4, [], {"type": "RollingUpdate"}, None,
        [_drop(NOW - timedelta(minutes=50))], None, [], None,
        [_fail(missed + timedelta(minutes=2))], None, NOW,
    )
    assert status == 2, text
    assert "had a scheduler pass raise inside their own period" in text
    assert "RuntimeError: agora unreachable" in text
    assert "never evaluated rather than declined" not in text


def test_without_the_failure_ledger_that_same_slot_still_reads_unevaluated():
    # The control for the test above: the fixture really does land in the
    # bucket the failure record moves it out of, so the assertion above is
    # measuring the change and not a slot that was never there.
    text, _ = hg.format_report(
        [_covered_row()], None, 1, 4, [], {"type": "RollingUpdate"}, None,
        [_drop(NOW - timedelta(minutes=50))], None, [], None,
        [], None, NOW,
    )
    assert "the tick was never evaluated rather than declined" in text
    assert "had a scheduler pass raise inside their own period" not in text


def test_a_failure_outside_the_slot_does_not_reclassify_it():
    missed = NOW - timedelta(minutes=30)
    text, _ = hg.format_report(
        [_covered_row()], None, 1, 4, [], {"type": "RollingUpdate"}, None,
        [_drop(NOW - timedelta(minutes=50))], None, [], None,
        [_fail(missed - timedelta(minutes=20))], None, NOW,
    )
    assert "had a scheduler pass raise inside their own period" not in text
    assert "the tick was never evaluated rather than declined" in text


def test_a_healthy_scheduler_prints_no_failure_line():
    summary = hg.failure_summary([], NOW, 24)
    assert summary is None


def test_the_failure_summary_quotes_the_newest_error():
    records = [_fail(NOW - timedelta(hours=2), "ValueError: old"),
               _fail(NOW - timedelta(minutes=5), "RuntimeError: new")]
    summary = hg.failure_summary(records, NOW, 24)
    assert "2 recorded" in summary
    assert "RuntimeError: new" in summary
    assert "ValueError: old" not in summary


def test_a_failure_older_than_the_window_is_not_counted():
    assert hg.failure_summary([_fail(NOW - timedelta(hours=30))], NOW, 24) is None


def test_an_undated_failure_is_counted_not_dropped():
    summary = hg.failure_summary([{"error": "boom"}], NOW, 24)
    assert "1 recorded" in summary
    assert "no readable timestamp" in summary


def test_an_unreadable_failure_ledger_is_named_not_treated_as_clean():
    text, _ = hg.format_report(
        [_covered_row()], None, 1, 4, [], {"type": "RollingUpdate"}, None,
        [], None, [], None, [], "scheduler-failures.json is not JSON", NOW,
    )
    assert "NO SCHEDULER FAILURES READ" in text
