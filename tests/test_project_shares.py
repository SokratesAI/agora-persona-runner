"""Share vs actual on the projects page -- the last third of issue #214.

His issue: *"Replace the project stack with shares of cycles ... Show
share vs actual on the projects page."* The picking half shipped on
2026-09-12 and the display did not, so the arithmetic that decides which
project a cycle takes was printed to a cycle by `tools.top_board_rows`
and to nobody else.

`agora_runner.nova_shares` already owns the arithmetic and
`tests/test_nova_shares.py` holds it to its behaviour. What is tested
here is the wiring, which is where this could go wrong quietly: the
payload must count a cycle against the project of the row it claimed, and
it must tell the three cases apart -- a ledger that would not read, a
ledger where nothing resolves to a project, and a real measurement.
Collapsing any two of those shows him a confident 0% that means "I have
no idea".
"""

import json

import pytest

from agora_runner import nova_site


def _row(number, project, status_key="backlog"):
    return {
        "number": number,
        "title": f"Row {number}",
        "status": "⚪ Backlog",
        "statusKey": status_key,
        "updated": "09-12",
        "where": "",
        "priority": "🟠 High",
        "priorityKey": "high",
        "project": project,
        "done": status_key == "done",
    }


ISSUES = [_row(1, "Nova"), _row(2, "Marcus"), _row(7, "Infra")]
IDEAS = [_row(3, "Marcus"), _row(7, "Nova")]

PROJECTS = """
| Project | Priority | Updated | Order | TRL | Satisfaction | Lifecycle | Proposed |
|---|---|---|---|---|---|---|---|
| Marcus | 🔴 Immediately | 09-05 | 3 |  |  | Active |  |
| Nova | 🟠 High | 09-01 | 1 |  |  | Active |  |
| Infra |  |  | 9 |  |  | Active |  |
| NAS | 🔵 Medium | 09-01 | 4 |  |  | Paused |  |
"""


def _ledger(*claims):
    return json.dumps({"claims": list(claims)})


def _claim(item, cycle, at="2026-09-13T10:00:00+02:00"):
    return {"item": item, "cycle": cycle, "state": "open", "at": at}


@pytest.fixture(autouse=True)
def _site(monkeypatch):
    payloads = {"issues": {"items": ISSUES}, "ideas": {"items": IDEAS}}
    monkeypatch.setattr(nova_site, "board_payload", lambda name: payloads[name])
    monkeypatch.setattr(
        nova_site, "cached_payload",
        lambda name, build: (build(), b"", "etag"),
    )
    monkeypatch.setattr(nova_site, "comments_markdown", lambda: "")
    monkeypatch.setattr(nova_site, "plans_payload", lambda: {"documents": []})
    monkeypatch.setattr(nova_site, "milestone_pins_markdown", lambda: "")
    monkeypatch.setattr(nova_site, "milestone_seats_markdown", lambda: "")
    # The real `projects.md` parse, not a stub: the shares on the page have
    # to be the shares the picker computes off the same table, and a fake
    # meta dict here would let the two drift.
    from agora_runner.nova_boards import parse_project_meta
    monkeypatch.setattr(
        nova_site, "project_priorities", lambda: parse_project_meta(PROJECTS))


def _shares(monkeypatch, ledger_text):
    monkeypatch.setattr(
        nova_site, "vault_read_path_rev", lambda path: (ledger_text, "1-a"))
    return nova_site.project_payload()["projectShares"]




def test_share_and_actual_are_both_carried(monkeypatch):
    shares = _shares(monkeypatch, _ledger(
        _claim("issue-1", 1500), _claim("idea-3", 1501)))
    assert shares["counted"] == 2
    nova = shares["projects"]["nova"]
    marcus = shares["projects"]["marcus"]
    assert nova["cycles"] == 1 and nova["actual"] == pytest.approx(50.0)
    assert marcus["cycles"] == 1 and marcus["actual"] == pytest.approx(50.0)
    # Owed is his seed, taken is the ledger, and the two are different
    # numbers -- a payload that sent one of them twice would pass every
    # assertion above.
    assert nova["share"] != pytest.approx(nova["actual"])
    assert marcus["share"] > nova["share"]
    assert nova["deficit"] == pytest.approx(nova["share"] - nova["actual"], abs=0.1)


def test_the_two_boards_are_numbered_separately(monkeypatch):
    """`issue #7` is Infra and `idea #7` is Nova. A slug that dropped the
    board would attribute one of these cycles to the wrong project."""
    shares = _shares(monkeypatch, _ledger(_claim("issue-7", 1500)))
    assert shares["projects"]["infra"]["cycles"] == 1
    assert shares["projects"]["nova"]["cycles"] == 0
    shares = _shares(monkeypatch, _ledger(_claim("idea-7", 1500)))
    assert shares["projects"]["nova"]["cycles"] == 1
    assert shares["projects"]["infra"]["cycles"] == 0


def test_one_cycle_on_two_rows_of_one_project_is_one_cycle(monkeypatch):
    shares = _shares(monkeypatch, _ledger(
        _claim("issue-1", 1500), _claim("idea-7", 1500)))
    assert shares["counted"] == 1
    assert shares["projects"]["nova"]["cycles"] == 1
    assert shares["projects"]["nova"]["actual"] == pytest.approx(100.0)


def test_a_slug_that_names_no_row_is_not_in_the_denominator(monkeypatch):
    """Roughly half the slugs in the live ledger are free text. Counting
    them would deflate every project's actual share against a denominator
    nothing could ever be attributed to."""
    shares = _shares(monkeypatch, _ledger(
        _claim("issue-1", 1500),
        _claim("health-line-reviewer-findings", 1501),
        _claim("journal-seq-1502", 1502)))
    assert shares["counted"] == 1
    assert shares["projects"]["nova"]["actual"] == pytest.approx(100.0)


def test_a_ledger_that_will_not_read_answers_none(monkeypatch):
    """Not zeroes. Every project at 0% taken is also what a completely idle
    loop looks like, and the page must not draw the wrong one of those."""
    def refuse(path):
        raise RuntimeError("couchdb said no")

    monkeypatch.setattr(nova_site, "vault_read_path_rev", refuse)
    assert nova_site.project_payload()["projectShares"] is None


def test_a_ledger_that_will_not_parse_answers_none(monkeypatch):
    assert _shares(monkeypatch, "{not json") is None


def test_nothing_attributable_is_a_zero_window_not_a_zero_share(monkeypatch):
    """`counted == 0` is the state the page has to say out loud: the shares
    are still owed, there is just no denominator to compare them against."""
    shares = _shares(monkeypatch, _ledger(_claim("journal-seq-1500", 1500)))
    assert shares["counted"] == 0
    assert shares["projects"]["nova"]["share"] > 0
    assert shares["projects"]["nova"]["actual"] == 0.0


def test_a_paused_project_is_listed_at_zero_rather_than_dropped(monkeypatch):
    shares = _shares(monkeypatch, _ledger(_claim("issue-1", 1500)))
    assert shares["projects"]["nas"]["share"] == 0.0


def test_the_floor_does_not_fire_on_a_ledger_too_young_to_measure_it(monkeypatch):
    """`prune` keeps the ledger a day old, so every project reads as
    untouched for 14 days whatever the loop actually did. The page says the
    check did not run rather than marking the whole board starved."""
    shares = _shares(monkeypatch, _ledger(_claim("issue-1", 1500)))
    assert shares["floorMeasurable"] is False
    assert not any(p["starved"] for p in shares["projects"].values())


def test_the_floor_fires_once_the_ledger_reaches_back_far_enough(monkeypatch):
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    shares = _shares(monkeypatch, _ledger(
        _claim("issue-1", 1400, at=old),
        _claim("issue-1", 1500, at=recent)))
    assert shares["floorMeasurable"] is True
    # Nova was claimed today; Marcus has a share and no claim at all.
    assert shares["projects"]["nova"]["starved"] is False
    assert shares["projects"]["marcus"]["starved"] is True
    # A paused project is never rescued by the floor -- it is owed nothing.
    assert shares["projects"]["nas"]["starved"] is False


def test_the_window_and_the_floor_length_are_carried(monkeypatch):
    """The page prints "1 of the last 6" and "14-day floor"; both numbers
    live in `nova_shares` and neither is retyped in the browser."""
    from agora_runner.nova_shares import FLOOR_DAYS, WINDOW
    shares = _shares(monkeypatch, _ledger(_claim("issue-1", 1500)))
    assert shares["window"] == WINDOW
    assert shares["floorDays"] == FLOOR_DAYS


def test_a_named_project_does_not_pay_for_the_ledger(monkeypatch):
    """The block draws on the index cards only, and the drawer fetches
    `/api/project?name=` on every tap. A vault read per press buys a number
    that press does not show."""
    def refuse(path):
        raise AssertionError("the ledger was read for a single project")

    monkeypatch.setattr(nova_site, "vault_read_path_rev", refuse)
    payload = nova_site.project_payload("Nova")
    assert "projectShares" not in payload
