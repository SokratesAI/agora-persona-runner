"""`agora_runner.heartbeat_liveness.liveness` --- the block `/api/health` serves.

Idea #117 asked for the heartbeat check to say so *on the health endpoint*.
The judging is `tools.heartbeat_health`'s and is tested there; what is new and
untested is the shape this hands to a JSON encoder and the question of which
verdicts are allowed to read as healthy.

The one that matters is `unjudged`. A schedule this cannot parse makes the CLI
tool exit 1 rather than 0, and an endpoint that called the same row ok would be
a green answer that was green regardless of the truth --- so it is asserted
here rather than left to read off the implementation.
"""

import json
from datetime import datetime, timezone

from agora_runner import heartbeat_liveness as hl

NOW = datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(payload):
    def open_(url, timeout=None):
        return _Response(payload)

    return open_


def _hb(**kw):
    row = {
        "name": "Nova",
        "schedule": "every@30m@16:00",
        "enabled": True,
        "lastRunAt": "2026-08-27T14:45:00Z",
    }
    row.update(kw)
    return row


def test_every_heartbeat_firing_reads_ok_and_is_json_serialisable():
    out = hl.liveness(opener=_opener([_hb()]), now=NOW)
    assert out["ok"] is True
    assert out["error"] is None
    assert out["heartbeats"][0]["verdict"] == "ok"
    assert out["heartbeats"][0]["lastRunAt"] == "2026-08-27T14:45:00+00:00"
    json.dumps(out)  # the endpoint encodes this; a datetime here would 502


def test_an_overdue_heartbeat_takes_ok_down_and_keeps_its_evidence():
    stale = _hb(lastRunAt="2026-08-27T12:00:00Z")
    out = hl.liveness(opener=_opener([stale]), now=NOW)
    assert out["ok"] is False
    assert out["heartbeats"][0]["verdict"] == "overdue"
    assert "allowed 1h" in out["heartbeats"][0]["detail"]


def test_off_on_purpose_is_not_a_failure_but_a_bare_off_is():
    marked = _hb(name="Workflow trial (disabled, manual only)", enabled=False)
    assert hl.liveness(opener=_opener([marked]), now=NOW)["ok"] is True

    bare = _hb(name="K3s Sentinel", enabled=False)
    out = hl.liveness(opener=_opener([bare]), now=NOW)
    assert out["ok"] is False
    assert out["heartbeats"][0]["verdict"] == "off"


def test_a_schedule_this_cannot_judge_never_reads_as_healthy():
    # The CLI tool exits 1 on this rather than 0. The endpoint has to make the
    # same call: a row nothing could judge is not evidence that it is firing.
    out = hl.liveness(opener=_opener([_hb(schedule="whenever")]), now=NOW)
    assert out["heartbeats"][0]["verdict"] == "unjudged"
    assert out["ok"] is False


def test_agora_not_answering_is_reported_rather_than_raised_or_called_healthy():
    def boom(url, timeout=None):
        raise OSError("connection refused")

    out = hl.liveness(opener=boom, now=NOW)
    assert out["ok"] is False
    assert "connection refused" in out["error"]
    assert out["heartbeats"] == []


def test_no_heartbeats_at_all_is_not_a_clean_sweep():
    # Every row gone is the largest version of what this watches for, and an
    # empty list trivially satisfies "no row is off".
    out = hl.liveness(opener=_opener([]), now=NOW)
    assert out["ok"] is False
    assert out["heartbeats"] == []


# --- a heartbeat that misses its own named slot -----------------------------
#
# The averaged rule below `judge` allows two of a schedule's own periods, so a
# weekly heartbeat that stops firing reads healthy for a fortnight --- which is
# the exact fourteen-day blindness this module was written to end, reproduced
# for anything slower than daily. `Nova — goals & reprioritise (Mon)` had
# `lastRunAt: null` since 2026-08-24 and read `ok ... allowed 14d` all of it.
# `last_due_slot` is the sharper instrument and these are its edges.

OSLO_MON_1301 = datetime(2026, 8, 31, 11, 1, tzinfo=timezone.utc)  # 13:01 Oslo


def _weekly(**kw):
    # Monday 07:00, read in Oslo, created the Monday before its first slot.
    row = {
        "name": "Nova — goals & reprioritise (Mon)",
        "schedule": "cron@0 7 * * 1",
        "enabled": True,
        "lastRunAt": None,
        "createdAt": "2026-08-24T21:05:21.253Z",
    }
    row.update(kw)
    return row


def test_a_weekly_heartbeat_that_misses_its_slot_is_overdue_the_same_day():
    out = hl.liveness(opener=_opener([_weekly()]), now=OSLO_MON_1301)
    row = out["heartbeats"][0]
    assert row["verdict"] == "overdue", row["detail"]
    # The slot, not the average, has to be what it says --- an evidence line
    # reading "allowed 14d" here would be the old rule wearing a new verdict.
    assert "2026-08-31 07:00 Oslo" in row["detail"]
    assert "has never run" in row["detail"]
    assert out["ok"] is False


def test_the_same_heartbeat_is_healthy_inside_the_grace():
    # 12:59 Oslo, one minute short of six hours late. A scheduler running a
    # few hours behind is not a stopped one, and the whole point of an
    # absolute grace is that it has to expire.
    out = hl.liveness(
        opener=_opener([_weekly()]),
        now=datetime(2026, 8, 31, 10, 59, tzinfo=timezone.utc),
    )
    assert out["heartbeats"][0]["verdict"] == "ok"


def test_a_slot_answered_on_time_is_not_reported():
    # Agora started the five schedules it was carrying 0m, 8m, 9m, 13m and 13m
    # after the minute they asked for, so a run at or after the slot is the
    # normal case and must stay silent.
    out = hl.liveness(
        opener=_opener([_weekly(lastRunAt="2026-08-31T05:09:00Z")]),
        now=OSLO_MON_1301,
    )
    assert out["heartbeats"][0]["verdict"] == "ok"


def test_a_slot_that_came_round_before_the_heartbeat_existed_is_not_a_miss():
    # Created Monday 23:05 Oslo, after that Monday's 07:00. It cannot have
    # missed a turn it did not exist for, and reporting one would make every
    # newly created weekly heartbeat red on the day it was made.
    out = hl.liveness(
        opener=_opener([_weekly()]),
        # Tuesday 2026-08-25, 13:01 Oslo: the only 07:00 Monday in range is
        # 08-24, and the row was created that evening.
        now=datetime(2026, 8, 25, 11, 1, tzinfo=timezone.utc),
    )
    assert out["heartbeats"][0]["verdict"] == "ok"


def test_an_every_schedule_names_no_slot_and_keeps_the_averaged_rule():
    # `every@24m@16:00` repeats through the day; its trailing field is an
    # anchor, not a slot, and treating it as one would report the loop's own
    # heartbeat as overdue every night.
    assert hl.last_due_slot("every@24m@16:00", OSLO_MON_1301) is None
    out = hl.liveness(
        opener=_opener([_hb(lastRunAt="2026-08-31T10:45:00Z")]), now=OSLO_MON_1301
    )
    assert out["heartbeats"][0]["verdict"] == "ok"


def test_the_slot_is_oslo_and_not_utc():
    # Agora reads cron in Europe/Oslo --- measured across all five schedules
    # on 2026-08-31, every one fired its stated hour in Oslo, two hours before
    # that hour in UTC. A slot built in UTC would be two hours wrong.
    slot = hl.last_due_slot("daily@12:00", datetime(2026, 8, 31, 1, 23, tzinfo=timezone.utc))
    assert slot.astimezone(timezone.utc).isoformat() == "2026-08-30T10:00:00+00:00"


def test_the_day_the_slot_is_looked_for_on_is_oslo_s_day_too():
    # 23:00 UTC on the 31st is 01:00 Oslo on the 1st, and the two dates
    # disagree for those two hours every night. A 00:30 slot has just gone by
    # in Oslo; walking back from UTC's date finds the *previous* day's 00:30
    # and reports a slot that is a full day stale, which for a daily schedule
    # is the difference between "ran" and "missed". The assertion above cannot
    # see this --- it is taken at 03:23 Oslo, where the two dates agree.
    slot = hl.last_due_slot("daily@00:30", datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc))
    assert slot.isoformat() == "2026-09-01T00:30:00+02:00"


def test_a_cron_this_cannot_read_as_a_slot_falls_back_rather_than_guessing():
    # A step or a range in the hour field is a shape `_slot_time` does not
    # model; inventing a slot for it would report a miss that never happened.
    assert hl.last_due_slot("cron@0 */4 * * 1", OSLO_MON_1301) is None
    assert hl.last_due_slot("cron@0 7 1 * 1", OSLO_MON_1301) is None
