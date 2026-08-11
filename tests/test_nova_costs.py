"""The costs page's data (issues.md #57, page 2).

`nova_costs` does no I/O and parses no markdown -- it takes the raw
`cost-ledger.json` the bridge publishes after every cycle and cuts it to
what two charts and four tiles actually plot. So every test here is a
pure function against a literal document, and the shape assertions are
written as literals rather than derived from the input, because a
transform compared against itself survives its own mutation.

The one thing worth stating out loud: the compaction is the point. Rows
go out as arrays, not objects, and a test that only checked "the numbers
are all there" would pass just as happily against a payload three times
the size, which is the failure this endpoint is most likely to regress
into.
"""
import json

import pytest

from agora_runner import nova_costs, nova_site
from agora_runner.nova_costs import CYCLE_COLUMNS, QUOTA_COLUMNS, costs_payload

# A ledger with everything that varies in the real one: a cycle whose
# stamp will not parse, a quota reading from before `pace` existed, and a
# summary carrying keys the page has no use for.
LEDGER = {
    "generatedAt": "2026-08-11T12:19:41Z",
    "cycles": [
        {
            "session": "f2896a6f",
            "startedAt": "2026-08-03T11:33:26.980Z",
            "endedAt": "2026-08-03T11:45:04.251Z",
            "durationSeconds": 697.3,
            "turns": 64,
            "subagentTurns": 0,
            "toolCalls": 72,
            "weightedTokens": 849975.5,
            "models": ["claude-opus-5"],
        },
        {
            "session": "broken",
            "startedAt": "not a timestamp",
            "durationSeconds": 60,
            "turns": 1,
            "toolCalls": 1,
            "weightedTokens": 100,
        },
        {
            "session": "92e73b68",
            "startedAt": "2026-08-11T12:00:17.908Z",
            "durationSeconds": 1162.2,
            "turns": 103,
            "toolCalls": 118,
            "weightedTokens": 1911429.8,
        },
    ],
    "summary": {
        "cycles": 110,
        "other_sessions": 192,
        "first_cycle": "2026-08-03T11:33:26.980Z",
        "last_cycle": "2026-08-11T12:00:17.908Z",
        "totals": {"input_tokens": 568009, "output_tokens": 5575717},
        "totals_weighted": {"input_tokens": 568009.0},
        "total_weighted": 157495265.0,
        "mean_weighted": 1431775.1,
        "median_weighted": 1204348.4,
        "max_weighted": 3916724.1,
        "mean_duration_seconds": 1035.1,
        "median_duration_seconds": 973.9,
        "cost_share": {"output_tokens": 17.7, "cache_read_tokens": 60.2},
        "models": ["claude-opus-5"],
    },
    "quota": [
        {"at": 1786227966.684, "five_hour": 27.0, "seven_day": 2.0},
        {
            "at": 1786450678.872,
            "five_hour": 78.0,
            "five_hour_pace": 0.944,
            "seven_day": 51.0,
            "seven_day_pace": 0.615,
        },
    ],
    "weights": {"output_tokens": 5.0, "cache_read_tokens": 0.1},
}


@pytest.fixture
def payload():
    return costs_payload(json.dumps(LEDGER))


def test_a_cycle_row_is_the_five_columns_in_the_declared_order(payload):
    """The client indexes these by position, so the order is the contract.

    Written as one literal row rather than five separate assertions: the
    failure this catches is a column being inserted in the middle, which
    each field checked on its own would report as five unrelated breaks.
    """
    assert payload["cycleColumns"] == ["at", "minutes", "turns", "toolCalls", "weighted"]
    assert payload["cycles"][0] == [1785756806980, 11.6, 64, 72, 849976]


def test_a_quota_row_carries_both_windows_and_both_paces(payload):
    assert payload["quotaColumns"] == [
        "at", "fiveHour", "fiveHourPace", "sevenDay", "sevenDayPace"
    ]
    assert payload["quota"][-1] == [1786450678872, 78.0, 0.944, 51.0, 0.615]


def test_a_reading_from_before_pace_existed_is_a_hole_not_a_zero(payload):
    """`pace` was added partway through (bridge#24) and the older readings
    do not have it. Defaulted to 0 the chart would draw the line dropping
    to the floor and back -- a week of imaginary idleness. `None` is what
    makes the client's path break rather than dip."""
    first = payload["quota"][0]
    assert first[2] is None and first[4] is None
    assert first[1] == 27.0 and first[3] == 2.0


def test_a_cycle_with_an_unparseable_stamp_is_dropped_not_placed_at_zero(payload):
    """Both charts put time on the x-axis. A row that cannot say when it
    ran has no position, and 1970 is a position -- it would stretch the
    axis across fifty-six years and squash every real cycle into the last
    pixel."""
    assert len(payload["cycles"]) == 2
    assert [row[0] for row in payload["cycles"]] == [1785756806980, 1786449617908]


def test_the_summary_keeps_what_the_page_shows_and_drops_the_rest(payload):
    """`totals` and `totals_weighted` are ten raw token counts the page
    never plots; `cost_share` is the same information already divided
    through, which is the form the question is asked in."""
    assert payload["summary"]["median_weighted"] == 1204348.4
    assert payload["summary"]["cost_share"] == {"output_tokens": 17.7, "cache_read_tokens": 60.2}
    assert "totals" not in payload["summary"]
    assert "totals_weighted" not in payload["summary"]
    assert "models" not in payload["summary"]


def test_the_shaping_is_what_makes_this_endpoint_small():
    """The compaction, asserted as a ratio rather than described in a
    comment. Measured against the live ledger on 2026-08-11: 96,853 bytes
    in, 12,663 out.

    Asserted against a ledger grown to the live one's proportions rather
    than against the small fixture above, and that is not padding: at
    three cycles the fixed part -- the column names, the summary, the
    weights -- is most of the payload and the ratio is 0.59, so the same
    assertion would fail on a correct implementation. The saving is per
    row, so it only shows up once there are rows. A later cycle sending
    rows as objects would keep every test above green.
    """
    ledger = dict(
        LEDGER,
        cycles=[dict(LEDGER["cycles"][0], session=f"s{n}") for n in range(200)],
        quota=[dict(LEDGER["quota"][-1], at=1786450678.872 + n) for n in range(700)],
    )
    document = json.dumps(ledger)
    out = json.dumps(costs_payload(document))
    assert len(out) < len(document) / 2, f"{len(out)} bytes out of {len(document)}"


def test_a_vault_with_no_ledger_is_an_empty_page_not_an_error():
    """The publish lives in the other repo. A vault it has never run
    against should be a costs page with nothing on it, not a 502 on a nav
    tab."""
    empty = costs_payload("")
    assert empty["cycles"] == [] and empty["quota"] == []
    assert empty["summary"] == {} and empty["generatedAt"] is None
    # Still describes its own row format, so the client has one code path.
    assert empty["cycleColumns"] == list(CYCLE_COLUMNS)
    assert empty["quotaColumns"] == list(QUOTA_COLUMNS)


def test_a_ledger_that_will_not_parse_raises_rather_than_reading_empty():
    """The other half of the line above, and the reason it is a line at
    all: a truncated or half-written document must not render as "no
    cycles have ever run". That is a page that looks fine and lies."""
    with pytest.raises(ValueError):
        costs_payload('{"cycles": [')


def test_the_endpoint_is_the_fetch_and_the_shaping_and_nothing_else(monkeypatch):
    """`nova_site.costs_payload` holds no logic of its own, which is what
    keeps every test above a test of the endpoint and not just of a
    helper. Patched on `nova_site` rather than on `nova_sources` because
    the import binds the name here (tests/conftest.py's lesson: patch the
    reference the code under test actually calls)."""
    monkeypatch.setattr(nova_site, "cost_ledger_json", lambda: json.dumps(LEDGER))
    assert nova_site.costs_payload() == costs_payload(json.dumps(LEDGER))


def test_the_ledger_path_points_where_the_bridge_publishes():
    """Written out because nothing mechanical connects the two repos: the
    publish in `agora-claude-bridge` writes this exact path and this side
    only reads it. If one moves, this is the test that says so."""
    assert nova_costs.COST_LEDGER_PATH == (
        "projects/sokrates/projects/agora/nova/resources/cost-ledger.json"
    )
