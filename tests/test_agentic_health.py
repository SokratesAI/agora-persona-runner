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
    """`(status, conclusion, createdAt)` triples as GitHub returns them.

    Each gets a `databaseId`, because a run without one cannot be asked
    whether it ever started and the real API always sends it.
    """
    return [
        {
            "status": s,
            "conclusion": c,
            "createdAt": t,
            "event": "schedule",
            "databaseId": 1000 + i,
        }
        for i, (s, c, t) in enumerate(pairs)
    ]


# A job that executed steps -- i.e. the workflow really ran and really
# failed. This is what the jobs endpoint answers unless a test says otherwise.
_JOB_THAT_RAN = {"id": 77, "name": "agent", "steps": [{"name": "Execute Gemini CLI"}]}
# A job with no steps at all: three seconds long, nothing executed. This is
# what GitHub returns when it refuses to start the run.
_JOB_THAT_NEVER_STARTED = {"id": 88, "name": "activation", "steps": []}


def _fake_gh(
    workflows_by_repo,
    runs_by_workflow,
    failures=(),
    jobs_by_run=None,
    annotations_by_job=None,
    newest_green_by_repo=None,
):
    """A `_gh` stand-in driven by dicts, so no test touches the network.

    `newest_green_by_repo` is the newest run on the repo that finished
    green, as `repos/{repo}/actions/runs?status=success` answers it --
    `None` (the default) means the repo has never had one.
    """
    jobs_by_run = jobs_by_run or {}
    annotations_by_job = annotations_by_job or {}
    newest_green_by_repo = newest_green_by_repo or {}

    def run(args):
        if args[0] == "api":
            path = args[1]
            if "/actions/runs/" in path and path.endswith("/jobs"):
                run_id = int(path.rsplit("/", 2)[-2])
                return 0, json.dumps({"jobs": jobs_by_run.get(run_id, [_JOB_THAT_RAN])}), ""
            if "/check-runs/" in path and path.endswith("/annotations"):
                job_id = int(path.rsplit("/", 2)[-2])
                return 0, json.dumps(annotations_by_job.get(job_id, [])), ""
            repo = path.split("/")[1] + "/" + path.split("/")[2]
            if "status=success" in path:
                when = newest_green_by_repo.get(repo)
                found = [{"created_at": when}] if when else []
                return 0, json.dumps({"workflow_runs": found}), ""
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


# --- Cycle 472: a run that never started is not a workflow that is broken ---


def test_a_run_that_never_started_is_blocked_not_failing():
    """The 08-21 `docs-sync` failure verbatim: two seconds, zero steps, one annotation.

    Folding this into the streak is what sent two cycles hunting for a
    Gemini key. A run that GitHub refused to start cannot be fixed by any
    key, any prompt, or any pull request.
    """
    run = _fake_gh(
        {"o/r": [DOCS_SYNC]},
        {
            "docs-sync.lock.yml": _runs(
                ("completed", "failure", "2026-08-21T05:43:07Z"),
                ("completed", "success", "2026-08-07T12:40:38Z"),
            )
        },
        jobs_by_run={1000: [_JOB_THAT_NEVER_STARTED]},
        annotations_by_job={
            88: [
                {
                    "annotation_level": "failure",
                    "message": (
                        "The job was not started because recent account payments "
                        "have failed or your spending limit needs to be increased."
                    ),
                }
            ]
        },
    )
    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert [r["verdict"] for r in results] == ["blocked"]
    assert status == 0, "no pull request fixes a spending limit; exit 2 means actionable"
    assert "BLOCKED BEFORE IT STARTED" in report
    assert "spending limit" in report
    assert "AGENTIC WORKFLOW FAILING" not in report
    # The streak is still on the page -- it is a true fact and the earlier
    # runs failed for a different reason.
    assert "last succeeded 2026-08-07T12:40:38Z" in report


def test_a_run_that_executed_steps_stays_failing():
    """The 08-14 failure: the agent ran for eight minutes and died inside a step."""
    run = _fake_gh(
        {"o/r": [DOCS_SYNC]},
        {"docs-sync.lock.yml": _runs(("completed", "failure", "2026-08-14T06:23:17Z"))},
        jobs_by_run={1000: [_JOB_THAT_RAN]},
    )
    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert [r["verdict"] for r in results] == ["failing"]
    assert status == 2
    assert "AGENTIC WORKFLOW FAILING" in report


def test_blocked_without_an_annotation_still_says_it_never_started():
    """GitHub does not always leave a reason. "It never started" is already the finding."""
    run = _fake_gh(
        {"o/r": [DOCS_SYNC]},
        {"docs-sync.lock.yml": _runs(("completed", "failure", "2026-08-21T05:43:07Z"))},
        jobs_by_run={1000: [_JOB_THAT_NEVER_STARTED]},
    )
    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert [r["verdict"] for r in results] == ["blocked"]
    assert status == 0
    assert "gave no reason" in report


def test_a_block_the_repo_has_run_past_is_lifted_and_actionable():
    """`sokrates-docs` verbatim: blocked 08-21, went public 08-25, green 08-26.

    The whole defect this closes is that the two states printed the same
    report. A repo that is still out of Actions minutes has no green run
    after the block; this one does, and a green conclusion is not
    something a refused job can produce.
    """
    run = _fake_gh(
        {"o/r": [DOCS_SYNC]},
        {
            "docs-sync.lock.yml": _runs(
                ("completed", "failure", "2026-08-21T05:43:07Z"),
                ("completed", "success", "2026-08-07T12:40:38Z"),
            )
        },
        jobs_by_run={1000: [_JOB_THAT_NEVER_STARTED]},
        annotations_by_job={
            88: [{"annotation_level": "failure", "message": "spending limit"}]
        },
        newest_green_by_repo={"o/r": "2026-08-26T02:10:27Z"},
    )
    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert [r["verdict"] for r in results] == ["lifted"]
    assert status == 2, "one dispatch fixes this, so it is a cycle's to act on"
    assert "BLOCK HAS LIFTED" in report
    assert "2026-08-26T02:10:27Z" in report
    assert "gh workflow run docs-sync.lock.yml --repo o/r" in report
    # The annotation stays on the page -- it is why the workflow stopped.
    assert "spending limit" in report
    assert "BLOCKED BEFORE IT STARTED" not in report


def test_a_green_run_older_than_the_block_does_not_lift_it():
    """The 08-07 success is what a *still*-blocked repo looks like: green, then dead."""
    run = _fake_gh(
        {"o/r": [DOCS_SYNC]},
        {
            "docs-sync.lock.yml": _runs(
                ("completed", "failure", "2026-08-21T05:43:07Z"),
                ("completed", "success", "2026-08-07T12:40:38Z"),
            )
        },
        jobs_by_run={1000: [_JOB_THAT_NEVER_STARTED]},
        newest_green_by_repo={"o/r": "2026-08-07T12:40:38Z"},
    )
    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert [r["verdict"] for r in results] == ["blocked"]
    assert status == 0
    assert "BLOCK HAS LIFTED" not in report


def test_an_unreadable_lift_check_leaves_the_block_standing_and_says_so():
    """Never escalate on a call that did not answer, the same as the jobs call."""

    def run(args):
        if args[0] == "api" and "status=success" in args[1]:
            return 1, "", "HTTP 403: Forbidden"
        return _fake_gh(
            {"o/r": [DOCS_SYNC]},
            {
                "docs-sync.lock.yml": _runs(
                    ("completed", "failure", "2026-08-21T05:43:07Z")
                )
            },
            jobs_by_run={1000: [_JOB_THAT_NEVER_STARTED]},
        )(args)

    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert [r["verdict"] for r in results] == ["blocked"]
    assert status == 0
    assert "could not check whether the block has lifted -- HTTP 403" in report


def test_an_unreadable_jobs_call_keeps_the_failing_verdict_and_says_it_could_not_check():
    """Never quieten a red workflow on a call that did not answer."""

    def run(args):
        if args[0] == "api" and args[1].endswith("/jobs"):
            return 1, "", "HTTP 403: Forbidden"
        return _fake_gh(
            {"o/r": [DOCS_SYNC]},
            {
                "docs-sync.lock.yml": _runs(
                    ("completed", "failure", "2026-08-21T05:43:07Z")
                )
            },
        )(args)

    results, errors = agentic_health.sweep(["o/r"], run=run)
    report, status = agentic_health.format_report(results, errors, ["o/r"])
    assert [r["verdict"] for r in results] == ["failing"]
    assert status == 2
    assert "could not check whether it started" in report
