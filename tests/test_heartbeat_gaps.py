"""Does the dropped-firing count survive a grid that is not the fixture's?"""

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


def test_dropped_firings_do_not_raise():
    row = hg.judge(_heartbeat(), _conversations([0, 15, 45]), NOW, 1)
    text, status = hg.format_report([row], None, 1, 4)
    assert status == 0
    assert "2 firing(s) produced no run" in text
    assert "01:30" in text


def test_only_unjudged_rows_is_exit_one():
    row = hg.judge(_heartbeat(rotateConversationEachRun=None), _conversations([0]), NOW, 1)
    text, status = hg.format_report([row], None, 24, 1)
    assert status == 1
    assert "NOT JUDGED" in text


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
