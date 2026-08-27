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
