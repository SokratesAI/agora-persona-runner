"""Does the dropped-firing count survive a grid that is not the fixture's?"""

import json
from datetime import datetime, timedelta, timezone

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
