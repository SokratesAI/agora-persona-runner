"""A score of 2 or below forces a diagnosis, and a repeat is surfaced not re-run.

Milestone M5 of `task-prioritization-redesign.md`, the half of his
satisfaction field that does something. The spec: *"Score ≤2
auto-generates a skip-to-top task: 'diagnose low satisfaction on
[project]'"*, and *"if satisfaction is still ≤2 after a diagnosis
already ran once, surface that persistence visibly rather than silently
re-triggering an identical diagnostic loop."*

The first assertion here is the one the whole mechanism turns on: **unrated
is not low.** `parse_project_satisfaction_cell` answers `0` for a project he
has never scored, and `0 <= 2`, so a reader that takes the number without
that guard forces a diagnosis on every project on the board the day the
column appears -- which would be worse than not building this at all.

What the rest pin: the ordering, the claim slug, that a diagnosed project
stops producing a task and starts producing a persistence line, that an
unreadable log says so rather than reading as never-diagnosed, that the
recorder replaces rather than appends, and that it refuses to record a
score no diagnosis was ever forced by.
"""
import json

import pytest

from agora_runner.nova_boards import parse_project_meta
from agora_runner.nova_next import (
    LOW_SATISFACTION_AT, diagnosis_slug, load_diagnoses, low_satisfaction,
)
from tools import satisfaction_diagnosis, top_board_rows


def projects(*rows):
    """`projects.md` with the live six-column shape, one row per tuple."""
    out = ["| Project | Priority | Updated | Order | TRL | Satisfaction |",
           "|---|---|---|---|---|---|"]
    for name, score in rows:
        out.append(f"| {name} | \U0001f7e0 High | 09-06 |  | Functional | {score} |")
    return "\n".join(out) + "\n"


def test_unrated_is_not_low():
    """A project he has never scored must never force a diagnosis.

    The cell parser answers 0 for unrated and 0 is below 2, so this is the
    one failure mode that would fire on every project at once.
    """
    meta = parse_project_meta(projects(("Marcus", ""), ("Nova", "")))
    assert meta["marcus"]["satisfaction"] == 0
    assert low_satisfaction(meta) == []


def test_the_band_is_one_and_two_only():
    meta = parse_project_meta(
        projects(("A", 1), ("B", 2), ("C", 3), ("D", 4), ("E", 5)))
    assert [d["project"] for d in low_satisfaction(meta)] == ["A", "B"]
    assert LOW_SATISFACTION_AT == 2


def test_worst_first_then_by_name():
    meta = parse_project_meta(
        projects(("Zeta", 2), ("Alpha", 2), ("Demos", 1)))
    assert [d["project"] for d in low_satisfaction(meta)] == \
        ["Demos", "Alpha", "Zeta"]


def test_slug_is_its_own_namespace():
    """`diagnose-` prefixed, so it cannot collide with a board row's slug."""
    assert diagnosis_slug("Sokrates Post") == "diagnose-sokrates-post"
    assert diagnosis_slug("WhatsApp bridge") == "diagnose-whatsapp-bridge"


def test_a_diagnosed_project_carries_its_log_row():
    meta = parse_project_meta(projects(("Marcus", 2)))
    log = load_diagnoses(json.dumps({"diagnoses": [
        {"project": "marcus", "score": 2, "cycle": 1098, "at": "2026-09-06 22:10"}]}))
    assert low_satisfaction(meta, log)[0]["diagnosed"]["cycle"] == 1098


def test_unparseable_log_reads_as_nothing_diagnosed():
    """A log I cannot parse must not suppress the task it is meant to close.

    Forcing a diagnosis twice costs an hour; suppressing one costs a
    project he has told me is bad, so the failure direction is chosen.
    """
    meta = parse_project_meta(projects(("Marcus", 1)))
    assert low_satisfaction(meta, load_diagnoses("{not json"))[0]["diagnosed"] is None


def test_undiagnosed_renders_the_forced_task():
    block = "\n".join(top_board_rows._low_satisfaction_block(
        low_satisfaction(parse_project_meta(projects(("Marcus", 2))))))
    assert "FORCED — LOW SATISFACTION (1)" in block
    assert "diagnose low satisfaction on Marcus" in block
    assert "[claim: diagnose-marcus]" in block
    # The spec's own guard rail, on the page rather than only in the note.
    assert "TRL or lifecycle" in block
    assert "STILL LOW" not in block


def test_diagnosed_renders_persistence_and_no_second_task():
    log = load_diagnoses(json.dumps({"diagnoses": [
        {"project": "Marcus", "score": 1, "cycle": 1098, "at": "2026-09-06 22:10",
         "found": "the plan editor never shipped"}]}))
    block = "\n".join(top_board_rows._low_satisfaction_block(
        low_satisfaction(parse_project_meta(projects(("Marcus", 2))), log)))
    assert "STILL LOW — Marcus is 2 of 5" in block
    assert "cycle 1098 already diagnosed it at 1" in block
    assert "the plan editor never shipped" in block
    # The whole point: no second forced task for the same project.
    assert "FORCED" not in block
    assert "[claim: diagnose-marcus]" not in block


def test_nothing_low_renders_nothing():
    assert top_board_rows._low_satisfaction_block([]) == []


def test_unreadable_log_is_said_out_loud():
    """Absent and unreadable are opposite answers, as with the claim ledger."""
    block = "\n".join(top_board_rows._low_satisfaction_block([], readable=False))
    assert "DIAGNOSIS LOG UNREADABLE" in block


def test_the_page_prints_it_above_the_board(tmp_path, monkeypatch, capsys):
    """End to end through `main`, which is where the placement is decided."""
    projects_file = tmp_path / "projects.md"
    projects_file.write_text(projects(("Marcus", 2)), encoding="utf-8")
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr(top_board_rows, "fetch_diagnoses", lambda: ("", True))
    top_board_rows.main([
        "--issues", str(empty), "--ideas", str(empty), "--notes", str(empty),
        "--claims", str(empty), "--projects", str(projects_file)])
    printed = capsys.readouterr().out
    assert printed.index("FORCED — LOW SATISFACTION") < \
        printed.index("TOP OF EDVARD'S BOARD")


def test_recorder_replaces_rather_than_appends(tmp_path):
    log = tmp_path / "log.json"
    assert satisfaction_diagnosis.main([
        "record", "--log", str(log), "--project", "Marcus",
        "--score", "2", "--cycle", "1098"]) == 0
    assert satisfaction_diagnosis.main([
        "record", "--log", str(log), "--project", "Marcus",
        "--score", "1", "--cycle", "1099", "--found", "no plan editor"]) == 0
    rows = json.loads(log.read_text(encoding="utf-8"))["diagnoses"]
    assert len(rows) == 1
    assert rows[0]["cycle"] == 1099 and rows[0]["found"] == "no plan editor"


def test_recorder_refuses_a_score_no_diagnosis_was_forced_by(tmp_path, capsys):
    log = tmp_path / "log.json"
    assert satisfaction_diagnosis.main([
        "record", "--log", str(log), "--project", "Nova",
        "--score", "4", "--cycle", "1099"]) == 1
    assert "must be 1..2" in capsys.readouterr().err
    assert not log.exists()


def test_recorder_show_on_a_missing_log_is_not_an_error(tmp_path, capsys):
    assert satisfaction_diagnosis.main(
        ["show", "--log", str(tmp_path / "nope.json")]) == 0
    assert "no diagnosis recorded" in capsys.readouterr().out


def test_a_not_found_placeholder_reads_as_empty(tmp_path):
    """`vault_tool.py get` writes `[not found: ...]` into the file, exit 0."""
    log = tmp_path / "log.json"
    log.write_text("[not found: whatever]\n", encoding="utf-8")
    assert satisfaction_diagnosis.read(str(log))[1] == {}
