"""Tests for `tools.main_build`."""

import json

from tools import main_build


def run(name, conclusion="success", status="completed", url=None, run_id=None):
    return {
        "id": run_id if run_id is not None else abs(hash(name)) % 10**8,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "url": url or f"https://example.invalid/{name}",
    }


#: A job that executed steps -- a run that really failed.
STARTED_JOBS = {"jobs": [{"id": 1, "conclusion": "failure", "steps": [
    {"name": "test", "conclusion": "failure"}]}]}

#: A job with no steps at all -- what GitHub leaves behind when it refuses
#: to start a run. `tools.agentic_health.start_failure` keys on exactly this.
REFUSED_JOBS = {"jobs": [{"id": 1, "conclusion": "failure", "steps": []}]}

REFUSED_ANNOTATION = [{
    "annotation_level": "failure",
    "message": "The job was not started because recent account payments have failed",
}]


class TestJudge:
    def test_a_failed_workflow_is_red(self):
        verdict, detail, url = main_build.judge(
            [run("Tests"), run("Build and Deploy", "failure")], workflow_count=2
        )
        assert verdict == "red"
        assert "Build and Deploy" in detail

    def test_red_links_the_run_that_failed_not_the_newest_one(self):
        # This is the whole module in one assertion. `vault-bridge`'s HEAD on
        # 2026-09-02 carried a green `Tests` listed first and a red `Build and
        # Deploy` listed second; linking the first run printed a passing run
        # under a RED heading.
        _, _, url = main_build.judge(
            [
                run("Tests", url="https://example.invalid/green"),
                run("Build and Deploy", "failure", url="https://example.invalid/red"),
            ],
            workflow_count=2,
        )
        assert url == "https://example.invalid/red"

    def test_a_green_branch_is_ok(self):
        verdict, detail, _ = main_build.judge([run("build")], workflow_count=1)
        assert verdict == "ok"
        assert "1 workflow(s) passed" in detail

    def test_a_skipped_run_on_the_default_branch_produced_no_image(self):
        # On a pull request `build-push` is skipped by design and `open_prs`
        # deliberately does not read that as a failure. On the default branch
        # the same conclusion means the merge landed and no image came out.
        verdict, detail, url = main_build.judge(
            [run("build", "skipped", url="https://example.invalid/skipped")],
            workflow_count=1,
        )
        assert verdict == "no_image"
        assert "nothing was produced" in detail
        assert url == "https://example.invalid/skipped"

    def test_a_run_still_going_never_raises(self):
        verdict, _, _ = main_build.judge(
            [run("build", conclusion=None, status="in_progress")], workflow_count=1
        )
        assert verdict == "running"
        assert verdict not in main_build.RAISING

    def test_a_failure_outranks_a_run_still_going(self):
        verdict, _, _ = main_build.judge(
            [run("build", "failure"), run("scan", conclusion=None, status="queued")],
            workflow_count=2,
        )
        assert verdict == "red"

    def test_no_run_on_a_repo_that_declares_workflows_is_not_built(self):
        verdict, detail, _ = main_build.judge([], workflow_count=6)
        assert verdict == "not_built"
        assert "6 workflow(s)" in detail

    def test_no_run_is_not_a_finding_on_a_repo_with_no_workflows(self):
        # The `*-config` repos declare none, so a run was never coming there.
        verdict, _, _ = main_build.judge([], workflow_count=0)
        assert verdict == "no_ci"
        assert verdict not in main_build.RAISING

    def test_an_unreadable_workflow_count_never_becomes_a_finding(self):
        verdict, _, _ = main_build.judge([], workflow_count=None)
        assert verdict == "unreadable"
        assert verdict not in main_build.RAISING

    def test_skipped_is_not_in_the_bad_conclusions_set(self):
        # It has its own verdict, which says a different thing than "red".
        assert "skipped" not in main_build.BAD_CONCLUSIONS

    def test_a_conclusion_in_upper_case_is_still_judged(self):
        verdict, _, _ = main_build.judge(
            [run("build", "FAILURE")], workflow_count=1
        )
        assert verdict == "red"


class TestNewestPerWorkflow:
    def test_a_rerun_hides_the_older_failure_of_the_same_workflow(self):
        # GitHub lists runs newest first. Without this, a red run that was
        # re-run green would be raised forever.
        judged = main_build.newest_per_workflow(
            [run("build", "success"), run("build", "failure")]
        )
        assert [r["conclusion"] for r in judged] == ["success"]

    def test_two_different_workflows_are_both_kept(self):
        judged = main_build.newest_per_workflow([run("build"), run("scan")])
        assert sorted(r["name"] for r in judged) == ["build", "scan"]

    def test_an_older_failure_of_a_rerun_workflow_does_not_raise(self):
        verdict, _, _ = main_build.judge(
            [run("build", "success"), run("build", "failure")], workflow_count=1
        )
        assert verdict == "ok"


class TestRunsForCommit:
    def test_a_full_page_is_unreadable_rather_than_judged_on_a_subset(self):
        payload = json.dumps([run(f"w{i}") for i in range(main_build.RUNS_PAGE)])
        runs, err = main_build.runs_for_commit(
            "o/r", "abc", run=lambda args: (0, payload, "")
        )
        assert runs is None
        assert "page limit" in err

    def test_a_short_page_is_returned(self):
        payload = json.dumps([run("build")])
        runs, err = main_build.runs_for_commit(
            "o/r", "abc", run=lambda args: (0, payload, "")
        )
        assert err is None
        assert [r["name"] for r in runs] == ["build"]

    def test_an_empty_body_reads_as_no_runs_not_as_an_error(self):
        runs, err = main_build.runs_for_commit(
            "o/r", "abc", run=lambda args: (0, "", "")
        )
        assert err is None
        assert runs == []

    def test_gh_failing_is_an_error_and_not_an_empty_list(self):
        runs, err = main_build.runs_for_commit(
            "o/r", "abc", run=lambda args: (1, "", "gh: not found")
        )
        assert runs is None
        assert "not found" in err

    def test_the_query_asks_for_this_exact_commit(self):
        seen = []

        def fake(args):
            seen.append(args)
            return 0, "[]", ""

        main_build.runs_for_commit("o/r", "deadbee", run=fake)
        assert "head_sha=deadbee" in " ".join(seen[0])


class TestHeadOfDefaultBranch:
    def test_it_resolves_head_without_naming_a_branch(self):
        seen = []

        def fake(args):
            seen.append(args)
            return 0, "abc123\n", ""

        sha, err = main_build.head_of_default_branch("o/r", run=fake)
        assert err is None
        assert sha == "abc123"
        assert "repos/o/r/commits/HEAD" in " ".join(seen[0])

    def test_it_costs_one_call_per_repo(self):
        # The first version spent a second call on `.default_branch` so the
        # report could print the word `main`. That is 25 REST calls a sweep
        # for a word the commit sha already identifies.
        seen = []

        def fake(args):
            seen.append(args)
            return 0, "abc123\n", ""

        main_build.head_of_default_branch("o/r", run=fake)
        assert len(seen) == 1

    def test_an_empty_sha_is_an_error(self):
        sha, err = main_build.head_of_default_branch(
            "o/r", run=lambda args: (0, "\n", "")
        )
        assert sha is None
        assert "empty" in err


class TestMain:
    def _fake(self, runs_payload, workflows="1", jobs=None):
        jobs = jobs if jobs is not None else STARTED_JOBS

        def fake(args):
            joined = " ".join(args)
            if args[:2] == ["repo", "list"]:
                return 0, json.dumps(
                    [{"nameWithOwner": "o/r", "isArchived": False}]
                ), ""
            if "commits/HEAD" in joined:
                return 0, "abc123\n", ""
            if "actions/runs?head_sha" in joined:
                return 0, json.dumps(runs_payload), ""
            if "/jobs" in joined:
                return 0, json.dumps(jobs), ""
            if "check-runs" in joined and "annotations" in joined:
                return 0, json.dumps(REFUSED_ANNOTATION), ""
            if "actions/workflows" in joined:
                return 0, f"{workflows}\n", ""
            return 0, "main\n", ""

        return fake

    def test_a_red_default_branch_exits_2(self, capsys):
        code = main_build.main([], run=self._fake([run("build", "failure")]))
        assert code == 2
        assert "RED" in capsys.readouterr().out

    def test_a_green_default_branch_exits_0(self, capsys):
        code = main_build.main([], run=self._fake([run("build")]))
        assert code == 0
        assert "Nothing to act on." in capsys.readouterr().out

    def test_a_repo_with_no_workflows_and_no_runs_exits_0(self, capsys):
        code = main_build.main([], run=self._fake([], workflows="0"))
        assert code == 0
        out = capsys.readouterr().out
        assert "Nothing to act on." in out
        assert "NOT BUILT" not in out

    def test_a_repo_with_workflows_and_no_run_exits_2(self, capsys):
        code = main_build.main([], run=self._fake([], workflows="6"))
        assert code == 2
        out = capsys.readouterr().out
        assert "NOT BUILT" in out
        assert "paths:" in out

    def test_an_unreadable_org_exits_1(self, capsys):
        code = main_build.main([], run=lambda args: (1, "", "boom"))
        assert code == 1
        assert "COULD NOT LIST THE ORG" in capsys.readouterr().out


class TestBlockedBeforeItStarted:
    """A run GitHub refused to start is not a broken branch.

    All five branches this tool raised on its first sweep were this shape,
    and the RED heading told a cycle to go and debug five builds that had
    never executed a line.
    """

    def _fake(self, runs_payload, jobs_by_run, annotation=REFUSED_ANNOTATION):
        def fake(args):
            joined = " ".join(args)
            if args[:2] == ["repo", "list"]:
                return 0, json.dumps(
                    [{"nameWithOwner": "o/r", "isArchived": False}]
                ), ""
            if "commits/HEAD" in joined:
                return 0, "abc123\n", ""
            if "actions/runs?head_sha" in joined:
                return 0, json.dumps(runs_payload), ""
            if "check-runs" in joined and "annotations" in joined:
                return 0, json.dumps(annotation), ""
            for run_id, payload in jobs_by_run.items():
                if f"actions/runs/{run_id}/jobs" in joined:
                    return 0, json.dumps(payload), ""
            if "actions/workflows" in joined:
                return 0, "1\n", ""
            return 0, "main\n", ""

        return fake

    def test_a_refused_run_is_blocked_not_red(self, capsys):
        code = main_build.main([], run=self._fake(
            [run("build", "failure", run_id=7)], {7: REFUSED_JOBS}
        ))
        out = capsys.readouterr().out
        assert "BLOCKED BEFORE IT STARTED" in out
        assert "RED —" not in out
        assert "recent account payments have failed" in out
        # It still raises: the fix is `gh run rerun`, which is a thing to do.
        assert code == 2

    def test_a_run_that_executed_steps_stays_red(self, capsys):
        code = main_build.main([], run=self._fake(
            [run("build", "failure", run_id=7)], {7: STARTED_JOBS}
        ))
        out = capsys.readouterr().out
        assert "RED —" in out
        assert "BLOCKED BEFORE IT STARTED" not in out
        assert code == 2

    def test_one_real_failure_beside_a_refusal_stays_red(self, capsys):
        # The guard worth keeping. Calling this branch `blocked` would tell a
        # cycle to re-run a build that is going to fail again for a real reason.
        code = main_build.main([], run=self._fake(
            [run("build", "failure", run_id=7),
             run("Deploy", "failure", run_id=8)],
            {7: REFUSED_JOBS, 8: STARTED_JOBS},
        ))
        out = capsys.readouterr().out
        assert "RED —" in out
        assert "BLOCKED BEFORE IT STARTED" not in out
        assert code == 2

    def test_an_unreadable_job_list_leaves_red_standing(self, capsys):
        def fake(args):
            joined = " ".join(args)
            if args[:2] == ["repo", "list"]:
                return 0, json.dumps(
                    [{"nameWithOwner": "o/r", "isArchived": False}]
                ), ""
            if "commits/HEAD" in joined:
                return 0, "abc123\n", ""
            if "actions/runs?head_sha" in joined:
                return 0, json.dumps([run("build", "failure", run_id=7)]), ""
            if "/jobs" in joined:
                return 1, "", "boom"
            return 0, "1\n", ""

        code = main_build.main([], run=fake)
        out = capsys.readouterr().out
        assert "RED —" in out
        assert "could not read whether run 7 ever started" in out
        assert code == 2


class TestFailingRuns:
    def test_it_is_the_same_selection_judge_makes(self):
        runs = [run("Tests"), run("Build", "failure"), run("Scan", "timed_out")]
        names = {r["name"] for r in main_build.failing_runs(runs)}
        assert names == {"Build", "Scan"}
        assert main_build.judge(runs, workflow_count=3)[0] == "red"

    def test_a_re_run_that_went_green_is_not_failing(self):
        # GitHub lists newest first; the stale failure must not be selected.
        runs = [run("build", "success", run_id=2), run("build", "failure", run_id=1)]
        assert main_build.failing_runs(runs) == []
