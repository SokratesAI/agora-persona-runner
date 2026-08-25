"""nova_heartbeats.py -- the Heartbeats page's server half.

His capture, ideas.md 2026-08-25: *"Make a page for heartbeats ... Then
Agora can just be purely for heartbeats."*

What is worth pinning is what would silently break while the page still
renders something plausible:

- the writes go to Agora's **public** app on `AGORA_URL`, not the internal
  one. The internal API accepts `forceRun` but silently ignores `enabled`,
  so a toggle sent there answers 200 with the heartbeat unchanged and the
  page redraws exactly as it was -- a switch that does nothing and says
  nothing;
- an enabled heartbeat sorts above a disabled one, because the list exists
  to answer "is the loop running" and the retired rows outnumber the live
  ones;
- a heartbeat that has never run carries `lastRunAt == ""` rather than a
  fabricated stamp, because "never ran" and "ran at an unknown time" are
  different facts and the page prints different words for them;
- an unreachable store raises rather than returning an empty list, for
  `nova_conversations`' reason: "no heartbeats" and "cannot reach Agora"
  render identically and mean opposite things;
- a failing *persona* listing does not take the page down with it, because
  a row with a blank persona name is still the page he asked for.
"""
from unittest.mock import patch

import pytest

import agora_runner.nova_heartbeats as hb


ROW = {
    "id": "hb-1",
    "name": "Nova",
    "personaId": "p-1",
    "conversationId": "c-1",
    "schedule": "every@20m@16:20",
    "task": "Read and follow prompt.md exactly.",
    "enabled": True,
    "forceRun": False,
    "lastRunAt": "2026-08-25T20:40:06.089807+00:00",
    "lastResult": "running",
}

PERSONAS = [{"id": "p-1", "name": "Nova"}, {"id": "p-2", "name": "K3s Sentinel"}]


def _list(rows, list_status=200, persona_status=200):
    def fake_get(path):
        if path == "/heartbeats":
            return list_status, {"heartbeats": rows}
        if path == "/personas":
            return persona_status, {"personas": PERSONAS}
        return 404, {}

    with patch.object(hb, "agora_get", side_effect=fake_get):
        return hb.heartbeats()


def _write(fn, status=200, body=None):
    calls = []

    def fake_http(method, url, payload=None, headers=None, timeout=30):
        calls.append((method, url, payload))
        return status, body if body is not None else {}

    with patch.object(hb, "http_json", side_effect=fake_http):
        return fn(), calls


def test_a_row_carries_the_persona_name_rather_than_its_uuid():
    rows = _list([ROW])["heartbeats"]
    assert rows[0]["personaName"] == "Nova"
    assert rows[0]["personaId"] == "p-1"


def test_lastresult_running_is_what_says_a_cycle_is_in_flight():
    rows = _list([ROW])["heartbeats"]
    assert rows[0]["running"] is True
    assert _list([dict(ROW, lastResult="replied 900 chars")])["heartbeats"][0]["running"] is False


def test_an_enabled_heartbeat_sorts_above_a_disabled_one():
    older_but_on = dict(ROW, id="on", enabled=True, lastRunAt="2026-08-01T00:00:00Z")
    newer_but_off = dict(ROW, id="off", enabled=False, lastRunAt="2026-08-25T23:00:00Z")
    rows = _list([newer_but_off, older_but_on])["heartbeats"]
    assert [r["id"] for r in rows] == ["on", "off"]


def test_the_newest_run_sorts_first_within_the_enabled_group():
    old = dict(ROW, id="old", lastRunAt="2026-08-20T00:00:00Z")
    new = dict(ROW, id="new", lastRunAt="2026-08-25T00:00:00Z")
    rows = _list([old, new])["heartbeats"]
    assert [r["id"] for r in rows] == ["new", "old"]


def test_a_heartbeat_that_has_never_run_sorts_last_and_says_so_with_an_empty_stamp():
    never = dict(ROW, id="never", lastRunAt=None, lastResult=None)
    rows = _list([never, ROW])["heartbeats"]
    assert [r["id"] for r in rows] == ["hb-1", "never"]
    assert rows[1]["lastRunAt"] == ""


def test_a_row_with_no_id_is_dropped_rather_than_rendered_unaddressable():
    rows = _list([dict(ROW, id=None), ROW])["heartbeats"]
    assert [r["id"] for r in rows] == ["hb-1"]


def test_an_unreachable_store_raises_rather_than_reading_as_no_heartbeats():
    with pytest.raises(RuntimeError):
        _list([ROW], list_status=502)


def test_a_failing_persona_listing_leaves_the_names_blank_and_the_page_up():
    rows = _list([ROW], persona_status=500)["heartbeats"]
    assert rows[0]["personaName"] == ""
    assert rows[0]["name"] == "Nova"


def test_set_enabled_patches_the_public_app_because_the_internal_one_ignores_enabled():
    (ok, message), calls = _write(
        lambda: hb.set_enabled("hb-1", False),
        body={"heartbeat": {"enabled": False}})
    assert ok is True and message == "off"
    method, url, payload = calls[0]
    assert method == "PATCH"
    assert url.endswith("/heartbeats/hb-1")
    assert hb.AGORA_URL in url
    assert payload == {"enabled": False}


def test_run_now_posts_to_the_run_route_and_calls_it_queued():
    (ok, message), calls = _write(lambda: hb.run_now("hb-1"))
    assert ok is True and message == "queued"
    method, url, _ = calls[0]
    assert method == "POST"
    assert url.endswith("/heartbeats/hb-1/run")
    assert hb.AGORA_URL in url


def test_an_unknown_heartbeat_id_is_told_apart_from_a_broken_store():
    (ok, message), _ = _write(lambda: hb.set_enabled("nope", True), status=404)
    assert ok is False and message == "no heartbeat with that id"
    (ok, message), _ = _write(lambda: hb.set_enabled("hb-1", True), status=502)
    assert ok is False and message == "could not change the heartbeat"


def test_a_missing_id_or_a_non_boolean_flag_never_reaches_agora():
    for bad in (lambda: hb.set_enabled("", True),
                lambda: hb.set_enabled("hb-1", "yes"),
                lambda: hb.run_now("")):
        (ok, _), calls = _write(bad)
        assert ok is False
        assert calls == []


def test_two_runs_in_the_same_second_sort_by_the_microseconds():
    """`isoformat()` drops the microseconds when they are exactly zero, so
    two stamps in one second can be different lengths. The first sort key
    inverted the string per character and read the shorter one as newer."""
    whole = dict(ROW, id="whole", lastRunAt="2026-08-25T20:40:06+00:00")
    later = dict(ROW, id="later", lastRunAt="2026-08-25T20:40:06.089807+00:00")
    assert [r["id"] for r in _list([whole, later])["heartbeats"]] == ["later", "whole"]
