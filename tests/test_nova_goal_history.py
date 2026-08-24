"""The weekly goal-snapshot ledger (idea #38).

What these pin is the two ways a chart of the owner's own goals could lie to
him: a point drawn from something that is not a measurement, and a series
that quietly restarts when he rewrites a goal's wording.
"""

import json

import pytest

from agora_runner.nova_goal_history import (
    GoalHistoryError,
    append,
    goal_key,
    load,
    series,
)
from agora_runner.nova_plan import plan_payload
from tools.append_goal_snapshot import main as snapshot_main
from tools.append_goal_snapshot import snapshot


LEDGER = json.dumps(
    [
        {"date": "2026-08-16", "cycle": 229, "values": {"G1": 2.8, "G3": 4}},
        {"date": "2026-08-17", "cycle": 257, "values": {"G1": 2.5, "G3": 3}},
    ]
)

GOALS_MD = """# Goals

## The slate

**G1 — Working on what you asked for.**

```goal
name: G1 — Working on what you asked for
measure: Merged pull requests per board row closed
now: 2.5
target: 2.0
unit: PRs per closed row
direction: down
```

**G4 — More than one tenant.**

```goal
name: G4 — More than one tenant
measure: Personas doing real work on a schedule
now: not counted yet
target: 2
direction: up
```
"""


def test_goal_key_survives_edvard_rewriting_the_sentence():
    # The half after the dash is explicitly his to rewrite -- the whole
    # slate is a proposal until he edits it. Keying on the full name
    # would restart the series the first time he did, and it would look
    # like a new goal rather than a lost one.
    assert goal_key("G1 — Working on what you asked for") == "G1"
    assert goal_key("G1 — Shipping what Edvard actually asked for") == "G1"
    assert goal_key("G12: costs what it is worth") == "G12"
    assert goal_key("g5") == "G5"


def test_a_goal_with_no_id_keys_on_its_own_name():
    assert goal_key("Ship  the   thing") == "Ship the thing"


def test_load_sorts_and_an_absent_ledger_is_empty_not_broken():
    assert load("") == []
    assert load("   \n") == []
    rows = load(json.dumps(list(reversed(json.loads(LEDGER)))))
    assert [row["date"] for row in rows] == ["2026-08-16", "2026-08-17"]


@pytest.mark.parametrize(
    "row",
    [
        {"date": "16-08-2026", "cycle": 1, "values": {"G1": 1}},
        {"date": "2026-08-18", "cycle": 0, "values": {"G1": 1}},
        {"date": "2026-08-18", "cycle": 1, "values": {}},
        {"date": "2026-08-18", "cycle": 1, "values": {"G1": "2.8"}},
        {"date": "2026-08-18", "cycle": 1, "values": {"G1": True}},
        {"date": "2026-08-18", "cycle": 1, "values": {"G1": 1}, "note": "extra"},
    ],
)
def test_a_row_that_would_draw_a_fake_point_is_refused(row):
    with pytest.raises(GoalHistoryError):
        append(LEDGER, row)


def test_a_date_is_written_once():
    with pytest.raises(GoalHistoryError, match="already in the ledger"):
        append(LEDGER, {"date": "2026-08-17", "cycle": 300, "values": {"G1": 9}})


def test_append_keeps_every_earlier_week():
    updated = load(append(LEDGER, {"date": "2026-08-24", "cycle": 400, "values": {"G1": 2.0}}))
    assert [row["date"] for row in updated] == ["2026-08-16", "2026-08-17", "2026-08-24"]
    assert updated[0]["values"] == {"G1": 2.8, "G3": 4}


def test_series_only_lists_weeks_a_goal_was_actually_measured():
    got = series(json.dumps(json.loads(LEDGER) + [
        {"date": "2026-08-24", "cycle": 400, "values": {"G1": 2.0}},
    ]))
    assert [point["value"] for point in got["G1"]] == [2.8, 2.5, 2.0]
    # G3 was not measured on the 24th, and no point is invented for it --
    # a flat week nobody measured is the lie this avoids.
    assert [point["date"] for point in got["G3"]] == ["2026-08-16", "2026-08-17"]


def test_snapshot_reads_the_fences_and_skips_a_goal_with_no_number():
    assert snapshot(GOALS_MD) == {"G1": 2.5}


def test_the_payload_hangs_each_series_on_its_own_goal():
    payload = plan_payload({"goals": GOALS_MD}, LEDGER)
    goals = next(doc for doc in payload["documents"] if doc["key"] == "goals")
    g1, g4 = goals["scoreboard"]
    assert [point["value"] for point in g1["history"]] == [2.8, 2.5]
    # G4 has never been measured, so it gets an empty list rather than a
    # missing field: the renderer has one branch, not two.
    assert g4["history"] == []


def test_an_unreadable_ledger_costs_the_lines_and_not_the_page():
    payload = plan_payload({"goals": GOALS_MD}, "{not json")
    goals = next(doc for doc in payload["documents"] if doc["key"] == "goals")
    assert goals["scoreboard"][0]["history"] == []
    assert goals["scoreboard"][0]["now"] == "2.5"
    assert goals["sections"], "the prose is still there"


def test_the_tool_refuses_a_goals_file_that_did_not_fetch(tmp_path, capsys):
    goals = tmp_path / "goals.md"
    goals.write_text("[not found: projects/sokrates/projects/nova/goals.md]")
    history = tmp_path / "history.json"
    history.write_text(LEDGER)

    code = snapshot_main(
        ["--goals", str(goals), "--history", str(history), "--date", "2026-08-24", "--cycle", "1"]
    )

    assert code == 2
    assert "numeric 'now:'" in capsys.readouterr().err
    assert history.read_text() == LEDGER, "a refusal must not touch the ledger"


def test_the_tool_writes_a_week_and_then_refuses_the_same_week(tmp_path):
    goals = tmp_path / "goals.md"
    goals.write_text(GOALS_MD)
    history = tmp_path / "history.json"
    history.write_text(LEDGER)

    args = ["--goals", str(goals), "--history", str(history), "--date", "2026-08-24", "--cycle", "314"]
    assert snapshot_main(args) == 0
    assert load(history.read_text())[-1] == {
        "date": "2026-08-24",
        "cycle": 314,
        "values": {"G1": 2.5},
    }
    # Running it twice on one Monday is the ordinary accident, and being
    # told is worth more than a silent no-op.
    assert snapshot_main(args) == 2


def test_the_first_snapshot_starts_from_an_absent_ledger(tmp_path):
    goals = tmp_path / "goals.md"
    goals.write_text(GOALS_MD)
    history = tmp_path / "history.json"
    history.write_text("[not found: projects/sokrates/projects/agora/nova/resources/goal-history.json]\n")

    code = snapshot_main(
        ["--goals", str(goals), "--history", str(history), "--date", "2026-08-24", "--cycle", "314"]
    )

    assert code == 0
    assert len(load(history.read_text())) == 1


def test_two_goals_with_the_same_short_id_refuse_rather_than_overwrite():
    # The reviewer's finding on runner#287. `goal_key` is what keeps a
    # series attached to its goal across a rename, so a collision is not
    # a tie to break -- it is one goal's whole week disappearing with no
    # error and no gap visible on the chart.
    collided = GOALS_MD.replace("name: G4 — More than one tenant", "name: G1 — a copy-paste")
    collided = collided.replace("now: not counted yet", "now: 9.9")
    with pytest.raises(GoalHistoryError, match="keyed 'G1'"):
        snapshot(collided)
    with pytest.raises(GoalHistoryError, match="keyed 'G1'"):
        append("", {"date": "2026-08-24", "cycle": 1,
                    "values": {"G1 — one": 1, "G1 — two": 2}})


def test_the_tool_refuses_a_colliding_file_without_touching_the_ledger(tmp_path, capsys):
    goals = tmp_path / "goals.md"
    goals.write_text(GOALS_MD.replace("name: G4 — More than one tenant", "name: G1 — a copy-paste")
                     .replace("now: not counted yet", "now: 9.9"))
    history = tmp_path / "history.json"
    history.write_text(LEDGER)

    code = snapshot_main(
        ["--goals", str(goals), "--history", str(history), "--date", "2026-08-24", "--cycle", "1"]
    )

    assert code == 2
    assert "keyed 'G1'" in capsys.readouterr().err
    assert history.read_text() == LEDGER
