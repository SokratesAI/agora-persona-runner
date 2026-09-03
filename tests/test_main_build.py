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


WORKFLOW = """
name: build
jobs:
  test:
    steps:
      - uses: actions/checkout@v7
  vault-drift:
    steps:
      - uses: actions/checkout@v7
      - uses: actions/checkout@v7
        with:
          repository: SokratesAI/agora-persona-runner
          path: ..
      - run: python -m tools.sync_contract . ..
  named:
    name: pretty name
    steps:
      - uses: actions/checkout@v7
        with:
          repository: SokratesAI/other
"""


class TestComparedRepos:
    """Which repositories a job checks out besides its own.

    Read off the workflow rather than kept in a list here, so a third
    comparison added tomorrow is found without editing this module.
    """

    def _parsed(self):
        import yaml

        return yaml.safe_load(WORKFLOW)

    def test_it_finds_the_other_repo_a_job_checks_out(self):
        assert main_build.compared_repos(
            self._parsed(), "vault-drift", "SokratesAI/agora-claude-bridge"
        ) == ["SokratesAI/agora-persona-runner"]

    def test_a_job_that_only_checks_out_itself_compares_nothing(self):
        assert main_build.compared_repos(
            self._parsed(), "test", "SokratesAI/agora-claude-bridge"
        ) == []

    def test_the_repo_being_judged_is_not_its_own_comparison(self):
        # The same workflow read from the runner side: the `repository:` it
        # names IS this repo, so nothing about it is perishable.
        assert main_build.compared_repos(
            self._parsed(), "vault-drift", "SokratesAI/agora-persona-runner"
        ) == []

    def test_it_matches_a_jobs_display_name_not_only_its_key(self):
        # GitHub reports the job's `name:` when it has one, so keying on the
        # mapping key alone would never match the job that actually failed.
        assert main_build.compared_repos(
            self._parsed(), "pretty name", "SokratesAI/agora-claude-bridge"
        ) == ["SokratesAI/other"]

    def test_an_unknown_job_name_compares_nothing(self):
        assert main_build.compared_repos(self._parsed(), "nope", "o/r") == []

    def test_a_repository_named_by_an_expression_is_skipped(self):
        # The live `build-push` job checks out `${{ env.CONFIG_REPO }}` to
        # commit the new digest to it. Passing that string to the GitHub API
        # is a guaranteed error on every red run in this org, and the repo it
        # names is not a comparison in the first place.
        import yaml

        parsed = yaml.safe_load(
            "jobs:\n"
            "  build-push:\n"
            "    steps:\n"
            "      - uses: actions/checkout@v7\n"
            "        with:\n"
            "          repository: ${{ env.CONFIG_REPO }}\n"
        )
        assert main_build.compared_repos(parsed, "build-push", "o/r") == []


class TestFailingJobs:
    def test_it_keeps_only_the_jobs_that_did_not_pass(self):
        payload = json.dumps([
            {"id": 1, "name": "test", "conclusion": "success"},
            {"id": 2, "name": "vault-drift", "conclusion": "failure"},
        ])
        jobs, err = main_build.failing_jobs(
            "o/r", 7, run=lambda args: (0, payload, "")
        )
        assert err is None
        assert [j["name"] for j in jobs] == ["vault-drift"]

    def test_it_asks_for_the_jobs_of_that_run(self):
        seen = []

        def fake(args):
            seen.append(args)
            return 0, "[]", ""

        main_build.failing_jobs("o/r", 7, run=fake)
        assert "repos/o/r/actions/runs/7/jobs" in " ".join(seen[0])

    def test_a_gh_failure_is_an_error_not_an_empty_list(self):
        jobs, err = main_build.failing_jobs(
            "o/r", 7, run=lambda args: (1, "", "boom")
        )
        assert jobs is None
        assert "boom" in err


class TestWorkflowAt:
    def test_it_decodes_and_parses_the_file(self):
        import base64

        blob = base64.b64encode(WORKFLOW.encode()).decode()
        parsed, err = main_build.workflow_at(
            "o/r", "abc123", ".github/workflows/build.yaml",
            run=lambda args: (0, blob, ""),
        )
        assert err is None
        assert "vault-drift" in parsed["jobs"]

    def test_it_reads_the_file_at_that_commit_not_at_head(self):
        seen = []

        def fake(args):
            seen.append(args)
            return 0, "", ""

        main_build.workflow_at("o/r", "abc123", "wf.yaml", run=fake)
        assert "ref=abc123" in " ".join(seen[0])

    def test_a_run_with_no_workflow_path_is_an_error(self):
        parsed, err = main_build.workflow_at("o/r", "abc123", None)
        assert parsed is None
        assert "no workflow path" in err

    def test_unparseable_yaml_is_an_error(self):
        import base64

        blob = base64.b64encode(b"a: [1,\n  b: 2\n").decode()
        parsed, err = main_build.workflow_at(
            "o/r", "abc123", "wf.yaml", run=lambda args: (0, blob, "")
        )
        assert parsed is None
        assert "did not parse" in err


class TestPerishableNotes:
    """A red that compared against another repo's moving default branch.

    `agora-claude-bridge` sat red on `main` for four and a half hours on
    2026-09-03 for exactly this reason and nothing said so.
    """

    def _fake(self, moved_at, job_name="vault-drift"):
        import base64

        blob = base64.b64encode(WORKFLOW.encode()).decode()

        def fake(args):
            joined = " ".join(args)
            if "/jobs" in joined:
                return 0, json.dumps(
                    [{"id": 55, "name": job_name, "conclusion": "failure"}]
                ), ""
            if "contents/" in joined:
                return 0, blob, ""
            if "commits/HEAD" in joined:
                return 0, moved_at, ""
            return 0, "", ""

        return fake

    def _runs(self):
        entry = run("build", "failure", run_id=99)
        entry["path"] = ".github/workflows/build.yaml"
        entry["started"] = "2026-09-03T05:44:25Z"
        return [entry]

    def test_a_comparison_repo_that_moved_since_the_run_is_flagged(self):
        errors = []
        notes = main_build.perishable_notes(
            "SokratesAI/agora-claude-bridge", "abc123", self._runs(), errors,
            run=self._fake("2026-09-03T05:55:00Z"),
        )
        assert errors == []
        assert len(notes) == 1
        assert notes[0].startswith("PERISHABLE")
        assert "gh run rerun 99" in notes[0]
        assert "--job 55" in notes[0]

    def test_a_comparison_repo_that_has_not_moved_says_the_red_is_real(self):
        errors = []
        notes = main_build.perishable_notes(
            "SokratesAI/agora-claude-bridge", "abc123", self._runs(), errors,
            run=self._fake("2026-09-03T05:40:00Z"),
        )
        assert errors == []
        assert len(notes) == 1
        assert "PERISHABLE" not in notes[0]
        assert "not moved" in notes[0]

    def test_a_failing_job_that_compares_nothing_gets_no_note(self):
        errors = []
        notes = main_build.perishable_notes(
            "SokratesAI/agora-claude-bridge", "abc123", self._runs(), errors,
            run=self._fake("2026-09-03T05:55:00Z", job_name="test"),
        )
        assert notes == []
        assert errors == []

    def test_unreadable_jobs_are_an_error_not_a_silent_clean_note(self):
        errors = []
        notes = main_build.perishable_notes(
            "o/r", "abc123", self._runs(), errors,
            run=lambda args: (1, "", "boom"),
        )
        assert notes == []
        assert errors and "boom" in errors[0]

    def test_the_note_reaches_the_report(self):
        row = {
            "repo": "o/r", "sha": "abc1234", "url": "", "verdict": "red",
            "detail": "1 workflow(s) failed", "notes": ["PERISHABLE — go re-run it"],
        }
        out = main_build.format_report([row], 1, [], [])
        assert "PERISHABLE — go re-run it" in out


class TestTheNoteIsWiredIntoMain:
    """`main` must actually ask for the notes and print them.

    Without this the whole feature can be disconnected in one line and
    every other test here still passes -- which is what the mutation check
    found: replacing the `perishable_notes` call in `main` with `[]`
    SURVIVED.
    """

    def _fake(self):
        import base64

        blob = base64.b64encode(WORKFLOW.encode()).decode()
        entry = run("build", "failure", run_id=99)
        entry["path"] = ".github/workflows/build.yaml"
        entry["started"] = "2026-09-03T05:44:25Z"

        def fake(args):
            joined = " ".join(args)
            if args[:2] == ["repo", "list"]:
                return 0, json.dumps(
                    [{"nameWithOwner": "SokratesAI/agora-claude-bridge",
                      "isArchived": False}]
                ), ""
            if "commits/HEAD" in joined and "committer" in joined:
                return 0, "2026-09-03T05:55:00Z\n", ""
            if "commits/HEAD" in joined:
                return 0, "abc123\n", ""
            if "actions/runs?head_sha" in joined:
                return 0, json.dumps([entry]), ""
            if "/jobs" in joined and "conclusion: .conclusion" in joined:
                return 0, json.dumps(
                    [{"id": 55, "name": "vault-drift", "conclusion": "failure"}]
                ), ""
            if "/jobs" in joined:
                return 0, json.dumps(STARTED_JOBS), ""
            if "contents/" in joined:
                return 0, blob, ""
            return 0, "1\n", ""

        return fake

    def test_a_red_row_carries_the_rerun_command(self, capsys):
        code = main_build.main([], run=self._fake())
        out = capsys.readouterr().out
        assert code == 2
        assert "RED" in out
        assert "PERISHABLE" in out
        assert "gh run rerun 99 --repo SokratesAI/agora-claude-bridge --job 55" in out
