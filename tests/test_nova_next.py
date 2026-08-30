"""What a cycle would take next, as a payload his phone can render.

Idea #38. The ranking these tests exercise is not new -- it moved out of
`tools/top_board_rows.py`, which the site could not import because
`tools/` is not in its image -- so `tests/test_top_board_rows.py` still
passes against the same functions through the CLI and is the proof the
move changed no behaviour. What is new is `next_payload`, and every test
here is about the composition: the order of the three lists, what an
unreadable ledger does, and what a project cell means.
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from agora_runner.nova_boards import PRIORITY_LABELS, STATUS_LABELS
from agora_runner.nova_claims import slug_for_row
from agora_runner.nova_next import next_payload

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 8, 30, 17, 0, tzinfo=OSLO)

IMMEDIATE = PRIORITY_LABELS["immediate"]
HIGH = PRIORITY_LABELS["high"]
LOW = PRIORITY_LABELS["low"]
BACKLOG = STATUS_LABELS["backlog"]


def board(*rows, captures=(), project=False):
    """A board file, optionally with the owner's `Project` column and captures."""
    head = []
    for bullet in captures:
        head.append("- " + bullet)
    if captures:
        head.append("")
    cols = "| # | Item | Status | Updated | Priority |"
    rule = "|---|---|---|---|---|"
    if project:
        cols = "| # | Item | Status | Updated | Priority | Project |"
        rule = "|---|---|---|---|---|---|"
    head += ["## Board", "", cols, rule]
    for row in rows:
        number, title, status, updated, priority = row[:5]
        line = (f"| [[#{number} — {title}\\|{number}]] | {title} "
                f"| {status} | {updated} | {priority} |")
        if project:
            line += f" {row[5]} |"
        head.append(line)
    head += ["", "## Done", "", "| # | Item | Updated | Where |", "|---|---|---|---|"]
    return "\n".join(head) + "\n"


def ledger(*claims):
    return json.dumps({"claims": list(claims)})


def test_the_ranked_list_is_the_order_a_cycle_would_take_them():
    issues = board((10, "a high issue", BACKLOG, "08-01", HIGH))
    ideas = board((64, "the immediate idea", BACKLOG, "08-12", IMMEDIATE))
    payload = next_payload(issues, ideas, ledger(), NOW)
    assert [(r["board"], r["number"]) for r in payload["next"]] == [
        ("idea", 64), ("issue", 10)]


def test_his_unfiled_captures_come_back_separately_from_the_board():
    """They outrank every row, so they cannot be mixed into the ranking."""
    issues = board((10, "a row", BACKLOG, "08-01", HIGH),
                   captures=["fix the thing on the NAS"])
    payload = next_payload(issues, board(), ledger(), NOW)
    assert [c["text"] for c in payload["captures"]] == ["fix the thing on the NAS"]
    assert [r["number"] for r in payload["next"]] == [10]


def test_a_live_claim_says_who_is_on_it_and_names_the_row():
    issues = board((10, "a row somebody took", BACKLOG, "08-01", HIGH))
    held = ledger({"item": slug_for_row("issue", 10), "cycle": 668,
                   "at": "2026-08-30T16:58:00+02:00", "state": "open"})
    payload = next_payload(issues, board(), held, NOW)
    assert payload["active"] == [{"item": slug_for_row("issue", 10), "cycle": 668,
                                  "title": "a row somebody took",
                                  "board": "issue", "number": 10}]
    assert payload["next"][0]["heldBy"] == 668


def test_a_claim_on_something_that_is_not_a_board_row_carries_no_title():
    """Handoff and capture slugs hash their text; a title here would be invented."""
    held = ledger({"item": "capture-12a1f8d8c624", "cycle": 667,
                   "at": "2026-08-30T16:58:00+02:00", "state": "open"})
    payload = next_payload(board(), board(), held, NOW)
    assert payload["active"][0]["title"] == ""
    assert payload["active"][0]["number"] is None


def test_a_stale_claim_is_not_live_work():
    """45 minutes is the turn cap, so an older claim is a killed cycle."""
    held = ledger({"item": slug_for_row("issue", 10), "cycle": 600,
                   "at": "2026-08-30T15:00:00+02:00", "state": "open"})
    payload = next_payload(board((10, "a row", BACKLOG, "08-01", HIGH)),
                           board(), held, NOW)
    assert payload["active"] == []
    assert payload["next"][0]["heldBy"] is None


def test_an_unreadable_ledger_is_not_an_empty_one():
    payload = next_payload(board((10, "a row", BACKLOG, "08-01", HIGH)),
                           board(), "{not json", NOW)
    assert payload["claimsReadable"] is False
    assert payload["active"] == []
    assert [r["number"] for r in payload["next"]] == [10]


def test_projects_are_ordered_by_their_best_row_not_by_count():
    issues = board((10, "one urgent thing", BACKLOG, "08-01", IMMEDIATE, "NAS"),
                   (11, "a small thing", BACKLOG, "08-02", LOW, "Site"),
                   (12, "another small thing", BACKLOG, "08-03", LOW, "Site"),
                   project=True)
    payload = next_payload(issues, board(), ledger(), NOW)
    assert [(p["name"], p["open"]) for p in payload["projects"]] == [("NAS", 1), ("Site", 2)]
    assert payload["projects"][0]["top"] == "one urgent thing"


def test_an_empty_project_cell_is_nova_because_that_is_the_board_default():
    """`nova_boards.DEFAULT_PROJECT`, not a second opinion decided here."""
    issues = board((10, "unfiled", BACKLOG, "08-01", HIGH, ""), project=True)
    payload = next_payload(issues, board(), ledger(), NOW)
    assert [p["name"] for p in payload["projects"]] == ["Nova"]


def test_a_board_with_no_project_column_still_files_every_row():
    """His boards grew the column; a board without one is not project-less."""
    payload = next_payload(board((10, "a row", BACKLOG, "08-01", HIGH)),
                           board(), ledger(), NOW)
    assert [(p["name"], p["open"]) for p in payload["projects"]] == [("Nova", 1)]


def test_the_ranked_list_is_cut_and_the_blocked_rows_are_not_in_it():
    blocked = STATUS_LABELS["blocked-on-edvard"]
    issues = board((10, "waiting on him", blocked, "08-01", IMMEDIATE),
                   (11, "actionable", BACKLOG, "08-02", LOW))
    payload = next_payload(issues, board(), ledger(), NOW, top=1)
    assert [r["number"] for r in payload["next"]] == [11]
    assert [r["number"] for r in payload["waiting"]] == [10]
