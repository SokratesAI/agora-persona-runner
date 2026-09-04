"""`/api/galaxy`'s payload -- live claims, shaped for the canvas.

The one thing worth pinning harder than the field names: an unreadable
ledger and an empty one must not answer the same. On a canvas they are
the same picture -- no bodies -- and they mean opposite things, so the
page needs `readable` to be able to say which it got.
"""

import json
from datetime import datetime, timedelta, timezone

from agora_runner.nova_galaxy import galaxy_payload

NOW = datetime(2026, 9, 4, 11, 0, tzinfo=timezone.utc)


def ledger(*rows):
    return json.dumps({"claims": list(rows)})


def at(minutes):
    return (NOW - timedelta(minutes=minutes)).isoformat()


def test_an_open_claim_is_an_active_body_carrying_its_note():
    payload = galaxy_payload(ledger(
        {"item": "idea-63", "cycle": 900, "state": "open", "at": at(6),
         "note": "drawing the galaxy"},
    ), NOW)
    assert payload["readable"] is True
    assert payload["recent"] == []
    body, = payload["active"]
    assert body["cycle"] == 900
    assert body["note"] == "drawing the galaxy"
    assert body["state"] == "active"
    assert body["heldMinutes"] == 6.0


def test_a_stale_open_claim_is_not_drawn_as_working():
    """A killed cycle's row stays `open` forever; it is not a live session."""
    payload = galaxy_payload(ledger(
        {"item": "idea-63", "cycle": 900, "state": "open", "at": at(600)},
    ), NOW)
    assert payload["active"] == []
    assert payload["recent"] == []


def test_a_released_claim_cools_rather_than_vanishing():
    payload = galaxy_payload(ledger(
        {"item": "idea-63", "cycle": 899, "state": "done", "at": at(70),
         "outcome": "merged runner#712"},
    ), NOW)
    assert payload["active"] == []
    cooled, = payload["recent"]
    assert cooled["state"] == "done"
    assert cooled["outcome"] == "merged runner#712"


def test_an_unreadable_ledger_is_not_an_empty_galaxy():
    payload = galaxy_payload("{ not json", NOW)
    assert payload["readable"] is False
    assert payload["active"] == []
    assert payload["recent"] == []


def test_an_absent_ledger_reads_as_empty_and_readable():
    """`vault_read_path` answers `""` for a document that is not there, and
    `nova_claims.load` calls that an empty ledger rather than an error."""
    payload = galaxy_payload("", NOW)
    assert payload["readable"] is True
    assert payload["active"] == []


def test_bodies_are_newest_first():
    payload = galaxy_payload(ledger(
        {"item": "a", "cycle": 1, "state": "open", "at": at(30)},
        {"item": "b", "cycle": 2, "state": "open", "at": at(2)},
    ), NOW)
    assert [b["item"] for b in payload["active"]] == ["b", "a"]


def test_a_row_with_an_unreadable_timestamp_still_draws():
    """A hand-edited `at` is the ordinary case for a vault document, and a
    body I cannot age is still a real claim -- it sorts last and carries a
    `None` age rather than being dropped or given an invented one."""
    payload = galaxy_payload(ledger(
        {"item": "b", "cycle": 2, "state": "done", "at": at(4)},
        {"item": "a", "cycle": 1, "state": "done", "at": "not a date"},
    ), NOW)
    assert [b["item"] for b in payload["recent"]] == ["b", "a"]
    assert payload["recent"][1]["heldMinutes"] is None


def test_recent_is_capped_so_the_canvas_stays_a_report():
    rows = [{"item": f"i{n}", "cycle": n, "state": "done", "at": at(n + 1)}
            for n in range(40)]
    payload = galaxy_payload(ledger(*rows), NOW, recent_limit=12)
    assert len(payload["recent"]) == 12
    # Newest kept, oldest dropped -- the cap is a drawing limit, so it has
    # to cut from the end nobody is looking at.
    assert payload["recent"][0]["item"] == "i0"
