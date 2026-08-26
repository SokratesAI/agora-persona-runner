"""`tools.agentic_health` -- a dead scheduled workflow must not read as quiet.

The failure this guards is specific and already happened: `docs-sync` in
`SokratesAI/sokrates-docs` failed its last three scheduled runs over
nineteen days and nothing anywhere said so, because a gh-aw workflow that
finds nothing to do is *supposed* to produce no pull request. So the
assertions below are mostly about keeping "silent and fine" apart from
"silent and dead", and about refusing to let an unreadable repo print
what a clean sweep prints.
"""

import json

import pytest

from tools import agentic_health


def _runs(*pairs):
    """`(status, conclusion, createdAt)` triples as GitHub returns them."""
    return [
        {"status": s, "conclusion": c, "createdAt": t, "event": "schedule"}
        for s, c, t in pairs
    ]


def _fake_gh(workflows_by_repo, runs_by_workflow, failures=()):
    """A `_gh` stand-in driven by two dicts, so no test touches the network."""

    def run(args):
        if args[0] == "api":
            repo = args[1].split("/")[1] + "/" + args[1].split("/")[2]
            if repo in failures:
                return 1, "", "HTTP 404: Not Found"
            return 0, json.dumps({"workflows": workflows_by_repo.get(repo, [])}), ""
        if args[0] == "run":
            name = args[args.index("--workflow") + 1]
            return 0, json.dumps(runs_by_workflow.get(name, [])), ""
        raise AssertionError(f"unexpected gh call: {args}")

    return run


DOCS_SYNC = {"path": ".github/workflows/docs-sync.lock.yml", "name": "docs-sync",
             "state": "active"}
BUILD = {"path": ".github/workflows/build.yaml", "name": "build", "state": "active"}


def test_only_lock_files_count_as_agentic():
    """`build.yaml` is an ordinary workflow; the `.lock.yml` suffix is the marker."""
    run = _fake_gh({"o/r": [DOCS_SYNC, BUILD]}, {})
    found, err = agentic_health.agentic_workflows("o/r", run=run)
    assert err is None
    assert [w["path"] for w in found] == [".github/workflows/docs-sync.lock.yml"]


def test_a_run_of_reds_is_failing_with_the_streak_and_the_last_green():
    """The finding is the streak and the date, not that the newest run is red."""
    verdict = agentic_health.verdict_for(
        DOCS_SYNC,
        _runs(
            ("completed", "failure", "2026-08-21T05:43:07Z"),
            ("completed", "failure", "2026-08-14T06:23:17Z"),
            ("completed", "failure", "2026-08-07T18:19:26Z"),
            ("completed", "success", "2026-08-07T12:40:38Z"),
        ),
    )
    assert verdict["verdict"] == "failing"
    assert verdict["failures"] == 3
    assert verdict["last_good"] == "2026-08-07T12:40:38Z"


def test_an_in_progress_run_does_not_decide_the_verdict():
    """Otherwise the answer would depend on what minute the tool was called."""
    verdict = agentic_health.verdict_for(
        DOCS_SYNC,
        _runs(
            ("in_progress", None, "2026-08-26T06:00:00Z"),
            ("completed", "success", "2026-08-19T06:00:00Z"),
        ),
    )
    assert verdict["verdict"] == "healthy"


def test_nothing_but_in_progress_is_never_run_not_a_guess():
    verdict = agentic_health.verdict_for(
        DOCS_SYNC, _runs(("in_progress", None, "2026-08-26T06:00:00Z"))
    )
    assert verdict["verdict"] == "never-run"
    assert "in progress" in verdict["note"]


def test_never_succeeded_says_so_rather_than_naming_a_date():
    verdict = agentic_health.verdict_for(
        DOCS_SYNC, _runs(("completed", "failure", "2026-08-21T05:43:07Z"))
    )
    assert verdict["verdict"] == "failing"
    assert verdict["last_good"] is None
    assert "never succeeded" in verdict["note"]


def test_a_failing_workflow_exits_two():
    run = _fake_gh(
        {"o/r": [DOCS_SYNC]},
        {"docs-sync.lock.yml": _runs(("completed", "failure", "2026-08-21T05:43:07Z"))},
    )
    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert status == 2
    assert "AGENTIC WORKFLOW FAILING" in report


def test_a_healthy_workflow_exits_zero_and_names_the_repos_swept():
    run = _fake_gh(
        {"o/r": [DOCS_SYNC]},
        {"docs-sync.lock.yml": _runs(("completed", "success", "2026-08-25T06:00:00Z"))},
    )
    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert status == 0
    assert "Swept 1 repo(s): o/r" in report


def test_an_unreadable_repo_never_prints_what_a_clean_sweep_prints():
    """`error` is no instrument. Collapsing it into 0 is the one banned answer."""
    run = _fake_gh({}, {}, failures={"o/r"})
    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert status == 1
    assert "COULD NOT READ" in report
    assert results == []


@pytest.mark.parametrize("state", ["active", "disabled_manually"])
def test_a_repo_with_no_agentic_workflows_is_not_an_error(state):
    run = _fake_gh({"o/r": [dict(BUILD, state=state)]}, {})
    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert (results, errors) == ([], [])
    assert status == 0
    assert "No gh-aw workflows found" in report
