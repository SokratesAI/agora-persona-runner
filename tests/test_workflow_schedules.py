"""One cron per workflow file, because a fast rung starves a slow one.

`nova-deadman.yaml` declared three cadences at once — every 30 minutes,
every 6 hours, once a day — and GitHub started it zero times in the 866
minutes they were live. This account's scheduler runs 36 to 574 minutes
late (median 92, measured across all 25 scheduled runs the org has ever
had, Cycle 555), and GitHub drops a scheduled occurrence that a newer
occurrence of the *same workflow* overtakes before the queue reaches it.
So the 30-minute rung did not merely fail: it superseded the two slower
rungs sharing its file, on every pass.

The ladder was a deliberate experiment and re-declaring one is a
reasonable thing for a future cycle to reach for. This is the note that
says the ladder has to be one file per rung, and it fails rather than
prints, because the symptom is silence.
"""
from __future__ import annotations

import pathlib

import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _triggers(doc: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True.
    if True in doc:
        return doc[True] or {}
    return doc.get("on") or {}


def test_no_workflow_declares_more_than_one_cron():
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            continue
        triggers = _triggers(doc)
        if not isinstance(triggers, dict):
            # `on: [push, pull_request]` is legal and carries no schedule.
            continue
        schedule = triggers.get("schedule") or []
        crons = [entry.get("cron") for entry in schedule if isinstance(entry, dict)]
        if len(crons) > 1:
            offenders.append(f"{path.name}: {crons}")
    assert not offenders, (
        "a workflow declares more than one cron: "
        + "; ".join(offenders)
        + ". Occurrences of one workflow supersede each other when GitHub's "
        "scheduler is behind, and this account's is 36-574 minutes behind, so "
        "the fastest rung silently starves every slower one. Give each cadence "
        "its own workflow file — see .github/workflows/nova-deadman-fast.yaml."
    )


def test_the_deadman_alarm_still_declares_a_schedule():
    # The guard above is satisfied by deleting every cron, which would remove
    # the alarm rather than fix it. Both rungs must still be scheduled.
    found = {}
    for name in ("nova-deadman.yaml", "nova-deadman-fast.yaml"):
        doc = yaml.safe_load((WORKFLOWS / name).read_text())
        schedule = _triggers(doc).get("schedule") or []
        found[name] = [entry.get("cron") for entry in schedule]
    assert found == {
        "nova-deadman.yaml": ["53 4 * * *"],
        "nova-deadman-fast.yaml": ["7,37 * * * *"],
    }, found
