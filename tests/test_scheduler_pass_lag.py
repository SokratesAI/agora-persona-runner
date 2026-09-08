"""A scheduler pass that started a whole beat late leaves a durable record.

`heartbeat_gaps` could say a slot produced no run and, since #915, why a due
tick was *declined*. It could not say anything at all about the loss that
actually costs an anchored slot: the scheduler not looking. `schedule_due`
asks only about the most recent occurrence, so a pass that lands after two
slot boundaries never asks about the older one -- nothing is declined and
nothing is logged. These tests hold the measurement that separates the two
causes underneath that: whether the pass itself was slow, or the thread was
starved between passes.
"""

import datetime
import json

import pytest

from agora_runner import dropped_ticks, heartbeat_pass


@pytest.fixture(autouse=True)
def _reset_counter():
    heartbeat_pass._late_since_healthy = 0
    yield
    heartbeat_pass._late_since_healthy = 0


def _recorder(calls):
    def record(gap, pass_seconds, interval, n):
        calls.append((gap, pass_seconds, interval, n))
        return "thread"
    return record


def test_a_pass_inside_two_intervals_is_not_late():
    calls = []
    assert heartbeat_pass.note_pass(9.0, 0.2, interval_seconds=5.0,
                                    record=_recorder(calls)) is None
    assert calls == []


def test_a_pass_past_two_intervals_is_recorded():
    calls = []
    heartbeat_pass.note_pass(600.0, 590.0, interval_seconds=5.0,
                             record=_recorder(calls))
    assert calls == [(600.0, 590.0, 5.0, 1)]


def test_the_very_first_pass_has_nothing_to_be_late_against():
    calls = []
    assert heartbeat_pass.note_pass(None, 0.0, interval_seconds=5.0,
                                    record=_recorder(calls)) is None
    assert calls == []


def test_a_run_of_late_passes_is_recorded_only_at_the_doublings():
    calls = []
    record = _recorder(calls)
    for _ in range(9):
        heartbeat_pass.note_pass(60.0, 1.0, interval_seconds=5.0, record=record)
    assert [n for _gap, _pass, _interval, n in calls] == [1, 2, 4, 8]


def test_a_healthy_pass_resets_the_counter_so_a_later_stall_is_loud_again():
    calls = []
    record = _recorder(calls)
    heartbeat_pass.note_pass(60.0, 1.0, interval_seconds=5.0, record=record)
    heartbeat_pass.note_pass(60.0, 1.0, interval_seconds=5.0, record=record)
    heartbeat_pass.note_pass(1.0, 1.0, interval_seconds=5.0, record=record)
    heartbeat_pass.note_pass(60.0, 1.0, interval_seconds=5.0, record=record)
    assert [n for _gap, _pass, _interval, n in calls] == [1, 2, 1]


def test_the_boundary_is_exclusive_so_exactly_two_intervals_passes():
    calls = []
    assert heartbeat_pass.note_pass(10.0, 0.1, interval_seconds=5.0,
                                    record=_recorder(calls)) is None
    assert calls == []


def test_the_record_carries_both_numbers_and_lands_in_its_own_document(monkeypatch):
    written = {}

    def fake_write(record, path=dropped_ticks.PATH, label="dropped-ticks"):
        written["record"] = record
        written["path"] = path
        written["label"] = label

    monkeypatch.setattr(dropped_ticks, "_write_quietly", fake_write)
    at = datetime.datetime(2026, 9, 8, 19, 30, tzinfo=datetime.timezone.utc)
    thread = dropped_ticks.record_pass_lag(612.5, 611.25, 5.0, 4, now=at)
    thread.join(timeout=5)

    assert written["path"] == dropped_ticks.LAG_PATH
    assert written["path"] != dropped_ticks.PATH
    assert written["record"] == {
        "at": at.isoformat(),
        "gapSeconds": 612.5,
        "passSeconds": 611.25,
        "intervalSeconds": 5.0,
        "lateSinceHealthy": 4,
    }


def test_a_lag_record_never_reaches_the_dropped_tick_ledger(monkeypatch):
    """The two documents are read for two different sentences.

    `heartbeat_gaps.reasons_for` files every record it is handed under a slot
    and prints it as "the poller said", so a lag record in that list would be
    reported as a reason a tick was declined -- which is the one thing it is
    not.
    """
    seen = []
    monkeypatch.setattr(dropped_ticks, "vault_read_path_rev",
                        lambda path: (json.dumps([]), "rev-1"))

    def fake_write_path(path, body, if_rev=None):
        seen.append((path, json.loads(body)))
        return "written"

    monkeypatch.setattr(dropped_ticks, "vault_write_path", fake_write_path)
    dropped_ticks.record_pass_lag(60.0, 1.0, 5.0, 1).join(timeout=5)
    dropped_ticks.record("hb-1", "Nova", "3 run(s) in flight, limit 3", 1).join(timeout=5)

    paths = [path for path, _body in seen]
    assert paths == [dropped_ticks.LAG_PATH, dropped_ticks.PATH]
    lag_body = dict(seen[0][1][0])
    assert "reason" not in lag_body


def test_an_interval_of_zero_has_no_beat_to_be_late_against():
    calls = []
    assert heartbeat_pass.note_pass(1.0, 0.0, interval_seconds=0.0,
                                    record=_recorder(calls)) is None
    assert calls == []
