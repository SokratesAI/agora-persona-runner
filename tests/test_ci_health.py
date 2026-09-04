"""Two cycles guessed at why a build would not start; one call would have said.

The incident this was built for: Cycle 487 watched runner#424 sit `queued`
for fourteen minutes with zero jobs and reasoned its way to the GitHub
Actions billing block, because that is the failure this loop has a memory
of. It was a GitHub-side Actions outage opened four seconds after the run
was created.

The two tests that pin the design rather than the plumbing are
`a_queued_run_inside_the_grace_is_not_a_stall` (a run queued forty seconds
is normal, and a checker that called it broken would be red on every
healthy push) and `an_outage_and_a_stalled_run_are_separate_lines` — the
`agentic_health` lesson, where one merged number sent two cycles after the
wrong half.
"""

import json
import subprocess
import urllib.error
from datetime import datetime, timedelta, timezone

from tools import ci_health

NOW = datetime(2026, 8, 26, 15, 45, tzinfo=timezone.utc)


class _Response:
    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def status_page(actions="operational", incidents=(), raw=None):
    """A fake urlopen answering githubstatus' summary endpoint."""
    def opener(request, timeout=None):
        if raw is not None:
            if isinstance(raw, int):
                raise urllib.error.HTTPError(request.full_url, raw, "boom", {}, None)
            return _Response(raw)
        return _Response(json.dumps({
            "components": [{"name": "Git Operations", "status": "operational"},
                           {"name": "Actions", "status": actions}],
            "incidents": [{"name": n} for n in incidents],
        }))
    return opener


def gh(runs=None, jobs=None, fail=None, completed=None,
       history=None, job_payload=None, annotations=None,
       usage=None, repo_list=None):
    """A fake `subprocess.run` answering the `gh api` calls this tool makes.

    `completed` is the newest completed run, as `{"id": ..., "created_at": ...}`
    or `None` — the measurement that separates an abandoned run from a stall.

    `history` is what `blocked_repo` sees instead: the newest few completed
    runs as `[{"id": ..., "conclusion": ...}]`. It is a separate parameter
    from `completed` because the two calls ask for different fields off the
    same endpoint, and a fake that answered both with one list would let a
    test pass on a shape production never returns. They are told apart by
    `per_page`, exactly as the tool writes them.

    `job_payload` is the full job list for a run — `[{"id": ..., "steps": ...}]`
    — as opposed to `jobs`, which is the bare `.total_count` the stall check
    asks for. `annotations` is keyed by job id.
    """
    runs, jobs = runs or {}, jobs or {}
    history, job_payload, annotations = history or {}, job_payload or {}, annotations or {}

    def runner(cmd, **kwargs):
        assert cmd[:2] == ["gh", "api"], cmd
        path = cmd[2]
        if fail is not None and fail in path:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="gh: not found")
        if "/settings/billing/usage" in path:
            # `check` reads the meter once a repo is found blocked, so the fake
            # has to answer it or every billing-block test dies in the fake.
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"usageItems": usage or []}), stderr="")
        if "/repos?" in path:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(repo_list or []), stderr="")
        if "/annotations" in path:
            job_id = int(path.split("/check-runs/")[1].split("/")[0])
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(annotations.get(job_id, [])), stderr="")
        if "/jobs" in path:
            run_id = int(path.split("/runs/")[1].split("/")[0])
            if len(cmd) == 3:  # no `-q`: blocked_repo wants the whole payload
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps({"jobs": job_payload.get(run_id, [])}),
                    stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(jobs.get(run_id, 0)), stderr="")
        if "status=completed" in path:
            repo = path.split("repos/")[1].split("/actions")[0]
            if "per_page=1" in path:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps([completed] if completed else []), stderr="")
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(history.get(repo, [])), stderr="")
        repo = path.split("repos/")[1].split("/actions")[0]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(runs.get(repo, [])), stderr="")
    return runner


# GitHub sends `annotation_level` on every annotation and `_first_annotation`
# now reads it, so a fixture without one is a fixture simpler than reality.
BILLING = ("The job was not started because recent account payments have failed "
           "or your spending limit needs to be increased. Please check the "
           "'Billing & plans' section in your settings")


def failed(run_id):
    return {"id": run_id, "conclusion": "failure"}


def completed_at(run_id, minutes_ago):
    return {"id": run_id,
            "created_at": (NOW - timedelta(minutes=minutes_ago))
            .strftime("%Y-%m-%dT%H:%M:%SZ")}


def queued(run_id, minutes_ago, branch="b", status="queued"):
    return {"id": run_id, "name": "build", "status": status,
            "head_branch": branch,
            "created_at": (NOW - timedelta(minutes=minutes_ago))
            .strftime("%Y-%m-%dT%H:%M:%SZ")}


def run_check(**kwargs):
    kwargs.setdefault("opener", status_page())
    kwargs.setdefault("run", gh())
    kwargs.setdefault("repos", ["Org/repo"])
    kwargs.setdefault("now", NOW)
    return ci_health.check(**kwargs)


def test_all_clear_is_zero_and_names_what_it_swept():
    status, lines = run_check()
    assert status == 0, lines
    body = "\n".join(lines)
    assert "Actions `operational`" in body
    assert "nothing in flight" in body
    assert "Org/repo" in body


def test_the_incident_this_was_built_for_turns_red():
    """2026-08-26: Actions major_outage, incident opened four seconds after the run."""
    status, lines = run_check(
        opener=status_page(actions="major_outage",
                           incidents=["Incident with Actions"]),
        run=gh(runs={"Org/repo": [queued(32984347949, 33)]}, jobs={32984347949: 0}))
    assert status == 2, lines
    body = "\n".join(lines)
    assert "GITHUB-SIDE" in body
    assert "Incident with Actions" in body
    assert "does not end in one" in body


def test_a_queued_run_inside_the_grace_is_not_a_stall():
    """The design test. Every healthy push spends a minute queued."""
    status, lines = run_check(
        run=gh(runs={"Org/repo": [queued(1, 0.7)]}, jobs={1: 0}))
    assert status == 0, lines
    assert "inside the 5m grace" in "\n".join(lines)
    assert "STALLED" not in "\n".join(lines)


def test_a_long_run_with_jobs_is_slow_not_stalled():
    """Zero jobs is the symptom. A run with jobs is running."""
    status, lines = run_check(
        run=gh(runs={"Org/repo": [queued(2, 40, status="in_progress")]}, jobs={2: 3}))
    assert status == 0, lines
    body = "\n".join(lines)
    assert "slow " in body and "3 job(s) created" in body
    assert "STALLED" not in body


def test_a_stalled_run_is_red_even_when_github_says_it_is_fine():
    """The billing-block shape: GitHub healthy, our own runs creating no jobs."""
    status, lines = run_check(
        run=gh(runs={"Org/repo": [queued(3, 30)]}, jobs={3: 0}))
    assert status == 2, lines
    body = "\n".join(lines)
    assert "STALLED" in body and "0 jobs" in body
    assert "Actions `operational`" in body


def test_an_outage_and_a_stalled_run_are_separate_lines():
    """`agentic_health`'s lesson: one merged verdict sends cycles after the wrong half."""
    status, lines = run_check(
        opener=status_page(actions="major_outage", incidents=["Incident with Actions"]),
        run=gh(runs={"Org/repo": [queued(4, 30)]}, jobs={4: 0}))
    assert status == 2, lines
    github_side = [l for l in lines if l.startswith("GITHUB-SIDE")]
    stalled = [l for l in lines if l.startswith("STALLED")]
    assert len(github_side) == 1 and len(stalled) == 1, lines


def test_degraded_performance_is_not_a_blocker():
    """Actions has answered `degraded_performance` on days this loop merged fine."""
    status, lines = run_check(opener=status_page(actions="degraded_performance"))
    assert status == 0, lines
    assert "GITHUB-SIDE" not in "\n".join(lines)


def test_an_unreadable_status_page_is_one_not_zero():
    status, lines = run_check(opener=status_page(raw=503))
    assert status == 1, lines
    body = "\n".join(lines)
    assert "COULD NOT READ" in body
    assert "not the same as nothing to check" in body


def test_a_status_page_with_no_actions_component_is_unreadable():
    """A schema change must not read as healthy."""
    status, lines = run_check(
        opener=status_page(raw=json.dumps({"components": [{"name": "Pages",
                                                           "status": "operational"}]})))
    assert status == 1, lines
    assert "no component named Actions" in "\n".join(lines)


def test_a_refused_gh_call_is_one_not_zero():
    status, lines = run_check(run=gh(fail="actions/runs"))
    assert status == 1, lines
    assert "COULD NOT READ  Org/repo" in "\n".join(lines)


def test_an_outage_outranks_an_unreadable_repo():
    """Red beats unknown: the cycle still must not plan a merge."""
    status, lines = run_check(
        opener=status_page(actions="major_outage"),
        run=gh(fail="actions/runs"))
    assert status == 2, lines


def test_no_placeable_checkout_is_unreadable_not_clean():
    status, lines = run_check(repos=[])
    assert status == 1, lines
    assert "no checkout here names a GitHub repo" in "\n".join(lines)


def test_the_report_refuses_to_call_an_empty_queue_evidence():
    """The guaranteed-positive guard, stated in the output rather than assumed."""
    _, lines = run_check()
    assert "it is evidence that nobody pushed" in "\n".join(lines)


def test_utc_is_compared_as_utc():
    """Cycle 446 invented a 100-minute stall out of the two-hour summer offset."""
    oslo = timezone(timedelta(hours=2))
    verdicts, why = ci_health.stalled_runs(
        "Org/repo", 5, gh(runs={"Org/repo": [queued(9, 1)]}, jobs={9: 0}),
        now=NOW.astimezone(oslo))
    assert why is None, why
    assert verdicts[0][0] == "clear", verdicts


# --- a push GitHub created no run for (Cycle 492) -------------------------

def gh_commits(sha="abc1234def", minutes_ago=30, push_history=1, runs_on_sha=1,
               fail=None):
    """A fake `subprocess.run` answering the three calls `unrun_pushes` makes."""
    def runner(cmd, **kwargs):
        assert cmd[:2] == ["gh", "api"], cmd
        path = cmd[2]
        if fail is not None and fail in path:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="gh: refused")
        if "/commits" in path:
            body = [{"sha": sha,
                     "date": (NOW - timedelta(minutes=minutes_ago))
                     .strftime("%Y-%m-%dT%H:%M:%SZ")}] if sha else []
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body), stderr="")
        if "/settings/billing/usage" in path:
            # `check` reads the meter on every sweep now, so a fake that did not
            # answer this would fail four unrelated tests inside the fixture.
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"usageItems": []}), stderr="")
        if "/repos?" in path:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([]), stderr="")
        if "event=push" in path:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(push_history), stderr="")
        if "head_sha=" in path:
            assert sha in path, path
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(runs_on_sha), stderr="")
        # the in-flight queue call from stalled_runs
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([]), stderr="")
    return runner


def test_the_incident_this_half_was_built_for_turns_red():
    """2026-08-26 19:06 Oslo: runner#425 merged and GitHub created no push run."""
    status, lines = run_check(run=gh_commits(runs_on_sha=0))
    assert status == 2, lines
    body = "\n".join(lines)
    assert [l for l in lines if l.startswith("NO RUN")], lines
    assert "created no workflow run at all" in body
    assert "does not end in one" in body


def test_a_head_commit_with_a_run_is_clear():
    """Two-sided: a checker that flagged every head commit would pass the test above."""
    status, lines = run_check(run=gh_commits(runs_on_sha=2))
    assert status == 0, lines
    assert not [l for l in lines if l.startswith("NO RUN")], lines
    assert "2 run(s)" in "\n".join(lines)


def test_a_commit_inside_the_grace_is_not_a_missing_run():
    """A push forty seconds old has not had time to get a run."""
    status, lines = run_check(run=gh_commits(minutes_ago=0.7, runs_on_sha=0))
    assert status == 0, lines
    assert not [l for l in lines if l.startswith("NO RUN")], lines
    assert "inside the 5m grace" in "\n".join(lines)


def test_a_repo_that_never_runs_on_push_is_not_judged():
    """The guaranteed-negative guard: no push workflow means no run either way."""
    status, lines = run_check(run=gh_commits(push_history=0, runs_on_sha=0))
    assert status == 0, lines
    assert not [l for l in lines if l.startswith("NO RUN")], lines
    assert "says nothing" in "\n".join(lines)


def test_an_unreadable_commit_list_is_one_not_zero():
    status, lines = run_check(run=gh_commits(fail="/commits"))
    assert status == 1, lines
    assert "COULD NOT READ  Org/repo" in "\n".join(lines)


def test_an_unreadable_run_count_is_one_not_zero():
    status, lines = run_check(run=gh_commits(fail="head_sha="))
    assert status == 1, lines
    assert "COULD NOT READ  Org/repo" in "\n".join(lines)


def test_a_repo_with_no_commits_is_clear():
    status, lines = run_check(run=gh_commits(sha=""))
    assert status == 0, lines
    assert "no commits to check" in "\n".join(lines)


def test_the_no_run_check_is_compared_as_utc():
    """Same trap as the queue check: gh answers UTC, this loop writes Oslo."""
    oslo = timezone(timedelta(hours=2))
    verdicts, why = ci_health.unrun_pushes(
        "Org/repo", 5, gh_commits(minutes_ago=1, runs_on_sha=0),
        now=NOW.astimezone(oslo))
    assert why is None, why
    assert verdicts[0][0] == "clear", verdicts


# --- an abandoned run is not a stall (Cycle 495) ---------------------------

def test_a_queued_run_that_later_runs_have_overtaken_is_not_a_blocker():
    """2026-08-26 20:04: run 32984347949 queued 173m through an outage that ended.

    Its pull request had merged and eight later runs had gone green, and this
    tool still said a merge could not complete. Cycle 494 believed it.
    """
    status, lines = run_check(
        run=gh(runs={"Org/repo": [queued(32984347949, 173)]},
               jobs={32984347949: 0},
               completed=completed_at(32995409057, 30)))
    assert status == 0, lines
    body = "\n".join(lines)
    assert [l for l in lines if l.startswith("ABANDONED")], lines
    assert "32995409057" in body
    assert "does not end in one" not in body


def test_a_stall_with_no_completed_run_after_it_still_blocks():
    """Two-sided: quietening on any completed run at all would pass the test above.

    A run that completed *before* the queued one is exactly what an outage
    leaves behind, so it is not evidence that jobs are starting now.
    """
    status, lines = run_check(
        run=gh(runs={"Org/repo": [queued(7, 173)]}, jobs={7: 0},
               completed=completed_at(6, 200)))
    assert status == 2, lines
    assert [l for l in lines if l.startswith("STALLED")], lines


def test_a_stall_in_a_repo_with_no_completed_run_still_blocks():
    """Nothing to compare against quietens nothing."""
    status, lines = run_check(
        run=gh(runs={"Org/repo": [queued(7, 173)]}, jobs={7: 0}, completed=None))
    assert status == 2, lines
    assert [l for l in lines if l.startswith("STALLED")], lines


def test_an_unreadable_completed_query_is_one_not_zero():
    """Being unable to check is not the same as nothing to check."""
    status, lines = run_check(
        run=gh(runs={"Org/repo": [queued(7, 173)]}, jobs={7: 0},
               fail="status=completed"))
    assert status == 1, lines
    assert "COULD NOT READ  Org/repo" in "\n".join(lines)


def test_an_outage_still_blocks_even_when_the_queued_run_is_abandoned():
    """The two measurements stay separate: githubstatus red is its own verdict."""
    status, lines = run_check(
        opener=status_page(actions="major_outage", incidents=["Incident with Actions"]),
        run=gh(runs={"Org/repo": [queued(9, 173)]}, jobs={9: 0},
               completed=completed_at(10, 30)))
    assert status == 2, lines
    body = "\n".join(lines)
    assert "GITHUB-SIDE" in body
    assert [l for l in lines if l.startswith("ABANDONED")], lines


def test_the_billing_block_this_tool_called_green():
    """Cycle 496 merged into platform-config on a check that said `ok`.

    Every run in that repo since 13:47 Oslo on 2026-08-26 was created, given
    no job, and marked `failure` two seconds later. Nothing was ever queued,
    every commit carried a run, and both of the older checks answered clean.
    """
    status, lines = run_check(
        run=gh(history={"Org/repo": [failed(9), failed(8), failed(7)]},
               job_payload={9: [{"id": 900, "steps": []}]},
               annotations={900: [{"annotation_level": "failure", "message": BILLING}]}))
    body = "\n".join(lines)
    assert "CANNOT GO GREEN  Org/repo" in body
    assert "recent account payments have failed" in body
    assert "cannot go green" in body
    assert status == 0, lines


def test_a_warning_annotation_is_never_quoted_as_the_reason():
    """GitHub stacks routine warnings above the real cause on the same job.

    Cycle 598 fixed exactly this in `agentic_health` and my reviewer found
    the twin here untouched. Every never-started job in this org carries a
    single `failure` annotation today, so this had not misfired -- which is
    a fact about today's data, not about the rule.
    """
    noise = (
        "Node.js 20 is deprecated. The following actions target Node.js 20 "
        "but are being forced to run on Node.js 24."
    )
    status, lines = run_check(
        run=gh(history={"Org/repo": [failed(9), failed(8), failed(7)]},
               job_payload={9: [{"id": 900, "steps": []}]},
               annotations={900: [
                   {"annotation_level": "warning", "message": noise},
                   {"annotation_level": "failure", "message": BILLING},
               ]}))
    body = "\n".join(lines)
    assert "Node.js 20 is deprecated" not in body
    assert "recent account payments have failed" in body
    assert status == 0, lines


def test_a_billing_block_does_not_veto_a_merge_into_another_repo():
    """The reason it does not raise: a red check for six days is one nobody reads.

    The owner has already decided to wait this block out until 1 September, and
    a merge into any repo whose jobs still run is unaffected by it.
    """
    status, lines = run_check(
        repos=["Org/blocked", "Org/fine"],
        run=gh(history={"Org/blocked": [failed(9)],
                        "Org/fine": [{"id": 5, "conclusion": "success"}]},
               job_payload={9: [{"id": 900, "steps": []}]},
               annotations={900: [{"annotation_level": "failure", "message": BILLING}]}))
    assert status == 0, lines
    body = "\n".join(lines)
    assert "CANNOT GO GREEN  Org/blocked" in body
    assert "Org/fine: a success is among the newest" in body
    summary = next(l for l in lines if l.startswith("A pull request into"))
    assert "Org/blocked" in summary and "Org/fine" not in summary


def test_a_red_test_suite_is_not_a_blocked_account():
    """The distinction the whole check turns on.

    A run whose jobs executed steps failed at something somebody wrote. Only
    a run where no job executed anything says the account was refused.
    """
    status, lines = run_check(
        run=gh(history={"Org/repo": [failed(9), failed(8)]},
               job_payload={9: [{"id": 900, "steps": [{"name": "pytest"}]}]}))
    assert status == 0, lines
    body = "\n".join(lines)
    assert "CANNOT GO GREEN" not in body
    assert "what failed is code, not the account" in body


def test_one_success_among_the_newest_runs_ends_the_question():
    """The guard against a positive guaranteed in advance, and it runs first.

    If jobs execute in this repo at all, nothing about the account is wrong,
    and no annotation needs reading to know it.
    """
    status, lines = run_check(
        run=gh(history={"Org/repo": [failed(9), {"id": 8, "conclusion": "success"}]},
               job_payload={9: [{"id": 900, "steps": []}]},
               annotations={900: [{"annotation_level": "failure", "message": BILLING}]}))
    assert status == 0, lines
    assert "CANNOT GO GREEN" not in "\n".join(lines)


def test_no_completed_run_is_not_a_verdict():
    """A repo nobody has pushed to has no completed run, healthy or dead."""
    status, lines = run_check(run=gh())
    assert status == 0, lines
    assert "no completed run here to judge" in "\n".join(lines)


def test_an_unreadable_job_list_is_not_clean():
    status, lines = run_check(
        run=gh(history={"Org/repo": [failed(9)]}, fail="/runs/9/jobs"))
    assert status == 1, lines
    assert "COULD NOT READ  Org/repo" in "\n".join(lines)


def test_a_missing_annotation_still_reports_the_repo():
    """The finding is that no step ran. The quote is detail on top of it."""
    status, lines = run_check(
        run=gh(history={"Org/repo": [failed(9)]},
               job_payload={9: [{"id": 900, "steps": []}]}))
    assert status == 0, lines
    body = "\n".join(lines)
    assert "CANNOT GO GREEN  Org/repo" in body
    assert "GitHub gave no reason" in body


def test_a_cancelled_run_is_not_a_refused_account():
    """A cancelled run has no steps either, and somebody pressed that button.

    The success guard only rules out a run that passed. Without this, five
    cancellations in a row read as GitHub refusing to start jobs — a finding
    with nothing under it.
    """
    status, lines = run_check(
        run=gh(history={"Org/repo": [{"id": 9, "conclusion": "cancelled"}, failed(8)]},
               job_payload={9: [{"id": 900, "steps": []}]}))
    assert status == 0, lines
    body = "\n".join(lines)
    assert "CANNOT GO GREEN" not in body
    assert "not a run GitHub refused to start" in body


def test_the_sweep_reaches_a_repo_with_no_checkout_here():
    """The blind spot this widening was built for.

    `sokrates-cli` has never been cloned by this loop and its default branch
    had been failing since 2026-08-28 with nobody looking. The old derivation
    was the workspace checkouts alone, on the reasoning that a repo with no
    checkout is one a cycle cannot push to — which `create_pr` disproves: it
    commits and opens a pull request with no clone at all, and Cycle 688
    merged one on exactly that repo. So the repos come from the org listing
    now, and this asserts the org repo is swept rather than that the call was
    made: a delegation test that only checks the callee would pass with the
    result thrown away.
    """
    swept = {}

    def fake_org_sweep(run=None):
        return ["Org/cloned", "Org/never-cloned"], [], ["Swept Org: 2 repo(s) in the org."], False

    original = ci_health._repos_to_sweep
    ci_health._repos_to_sweep = fake_org_sweep
    try:
        status, lines = run_check(
            repos=None,
            run=gh(history={"Org/cloned": [{"id": 1, "conclusion": "success"}],
                            "Org/never-cloned": [failed(9)]},
                   job_payload={9: [{"id": 900, "steps": []}]},
                   annotations={900: [{"annotation_level": "failure", "message": BILLING}]}))
    finally:
        ci_health._repos_to_sweep = original
    body = "\n".join(lines)
    assert "CANNOT GO GREEN  Org/never-cloned" in body, body
    assert "Swept Org: 2 repo(s) in the org." in body, body
    assert "Swept 2 repo(s), grace" in body, body
    assert status == 0, lines


def test_an_org_listing_that_failed_is_not_a_clean_sweep():
    """`incomplete` from the org listing is the sweep saying it is smaller than
    it claims, and a smaller sweep must not read as nothing to find."""
    def fake_org_sweep(run=None):
        return ["Org/repo"], [], ["⚠ Org: COULD NOT LIST THE ORG — boom."], True

    original = ci_health._repos_to_sweep
    ci_health._repos_to_sweep = fake_org_sweep
    try:
        status, lines = run_check(repos=None)
    finally:
        ci_health._repos_to_sweep = original
    assert status == 1, lines
    assert "COULD NOT LIST THE ORG" in "\n".join(lines)


def test_the_report_is_in_repo_order_not_in_whichever_gh_answered_first():
    """The sweep runs the repos concurrently now. A report whose line order
    depends on which `gh` returned first is one no cycle can diff against the
    last run, so the results are replayed in the order the repo list gives."""
    import time

    def slow_first(args, **kwargs):
        if "Org/aaa" in " ".join(args):
            time.sleep(0.05)
        return gh(history={"Org/aaa": [{"id": 1, "conclusion": "success"}],
                           "Org/zzz": [{"id": 2, "conclusion": "success"}]})(args, **kwargs)

    status, lines = run_check(repos=["Org/aaa", "Org/zzz"], run=slow_first)
    assert status == 0, lines
    first = next(i for i, l in enumerate(lines) if "Org/aaa" in l)
    last = next(i for i, l in enumerate(lines) if "Org/zzz" in l)
    assert first < last, lines


# --- billing_meter ------------------------------------------------------
# Cycle 734. `blocked_repo` quotes an annotation that says two different
# things joined by "or" — payments failed, or the spending limit is $0 — and
# nine cycles each went and derived which one by hand. These pin the split
# that made the derivation wrong every time: the private minute count and the
# public one are separate numbers, and only the private one is measured
# against the 2,000 included minutes.

def billing_gh(usage=None, repos=None, fail=None, runs=None, seen=None, now=None):
    """A fake `subprocess.run` for the `gh api` calls `billing_meter` makes.

    `runs` is `{"owner/repo": (month_total, recent_total)}` for the
    `/actions/runs?created=>=` counts `recent_private_rate` asks for. The
    month window starts on the 1st, the recent one is `now - 24h` and the
    nested one is `now - 12h`, so they are told apart by the date in the
    query rather than by call order.

    A third element sets the nested count independently. **Omitting it does
    not mean zero, it means steady** -- half the 24h runs fell in the newest
    12 hours -- because the nested count exists only to be compared against
    the wider one, and a fixture that answered zero would make every test
    that does not care about stationarity assert against a `NOT STEADY` line
    it never asked for. That is what the first draft of this did: the 24h
    figure was served to both queries, so the nested window read twice the
    rate of the window containing it, and every existing test carried a
    spurious finding without one of them failing.
    """
    def runner(cmd, **kwargs):
        assert cmd[:2] == ["gh", "api"], cmd
        path = cmd[2]
        if seen is not None:
            seen.append(path)
        if fail is not None and fail in path:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="gh: refused")
        if "/settings/billing/usage" in path:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"usageItems": usage or []}), stderr="")
        if "/repos?" in path:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(repos or []), stderr="")
        if "/actions/runs?" in path:
            full = path.split("/repos/", 1)[1].split("/actions/", 1)[0]
            counts = (runs or {}).get(full, (0, 0))
            month_total, recent_total = counts[0], counts[1]
            # A count is an integer -- the tool refuses a `total_count` that is
            # not one, and a float default made every window unreadable.
            nested_total = counts[2] if len(counts) > 2 else recent_total // 2
            # The two windows are told apart by the date in the query. At
            # exactly 00:00 on the 2nd, `now - 24h` IS the 1st at midnight and
            # the two queries are byte-identical -- a fixture that guessed
            # would answer both with the month figure and no test would fail.
            # Refuse instead of guessing, so a future test written at that
            # boundary dies loudly rather than passing on the wrong number.
            month_marker = f"created=%3E%3D{now:%Y-%m}-01T00:00:00Z" if now else None
            is_month = month_marker is not None and month_marker in path
            if month_marker is not None and now.day == 2 and now.hour == 0 \
                    and now.minute == 0 and now.second == 0:
                raise AssertionError(
                    "at 00:00 on the 2nd the month window and the 24h window are the "
                    "same instant; this fixture cannot tell them apart")
            nested_marker = "created=%3E%3D{:%Y-%m-%dT%H:%M:%S}Z".format(
                now - timedelta(hours=ci_health.NESTED_WINDOW_HOURS)) if now else None
            if is_month:
                total = month_total
            elif nested_marker is not None and nested_marker in path:
                total = nested_total
            else:
                total = recent_total
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"total_count": total}), stderr="")
        raise AssertionError(f"unexpected call {path}")
    return runner


def usage_item(repo, minutes, net=0.0, product="actions",
               unit="Minutes", sku="Actions Linux"):
    """One row as the usage endpoint really sends it.

    `unitType` and `sku` are not decoration. The endpoint mixes
    `Actions storage` rows measured in `GigabyteHours` into the same list, and
    a fixture without the field let this tool add gigabyte-hours to a minute
    count for a month without anyone seeing it.
    """
    return {"product": product, "repositoryName": repo, "sku": sku,
            "unitType": unit, "quantity": minutes, "netAmount": net}


def test_the_meter_splits_private_minutes_from_public_ones():
    # August's real shape, scaled down: nearly everything is on a public repo,
    # and a total alone would say "far past 2,000" about an allowance that is
    # almost untouched. Three distinct numbers so no two can be confused.
    run = billing_gh(
        usage=[usage_item("agora-persona-runner", 500.0, net=0.0),
               usage_item("platform-config", 7.0, net=1.5),
               usage_item("agora-claude-bridge", 90.0),
               usage_item("copilot-thing", 1000.0, product="copilot")],
        repos=[{"name": "agora-persona-runner", "private": False},
               {"name": "agora-claude-bridge", "private": False},
               {"name": "platform-config", "private": True}])
    lines, over = ci_health.billing_meter("SokratesAI", run=run,
                                          now=datetime(2026, 8, 31, 22, tzinfo=timezone.utc))
    assert over is False
    text = "\n".join(lines)
    assert "597 metered Actions minute(s)" in text      # copilot is not Actions
    assert "7 on private repo(s)" in text
    assert "590 on public" in text
    assert "7 of 2,000" in text
    assert "net owed $1.50" in text


def test_an_unreadable_repo_list_does_not_read_as_zero_private_minutes():
    # The failure that would matter: if visibility cannot be read and the
    # minutes default to "public", the report says the allowance is unspent
    # and a cycle concludes the block cannot be the allowance. Unknown has to
    # stay unknown, and say so.
    run = billing_gh(usage=[usage_item("platform-config", 40.0)], fail="/repos?")
    lines, over = ci_health.billing_meter("SokratesAI", run=run,
                                          now=datetime(2026, 8, 31, tzinfo=timezone.utc))
    assert over is False
    text = "\n".join(lines)
    assert "0 on private repo(s)" in text
    assert "40 on repo(s) whose visibility could not be read" in text
    assert "partial:" in text


def test_a_meter_it_cannot_read_is_not_a_clean_meter():
    run = billing_gh(fail="/settings/billing/usage")
    lines, why = ci_health.billing_meter("SokratesAI", run=run,
                                         now=datetime(2026, 8, 31, tzinfo=timezone.utc))
    assert lines is None
    assert "gh: refused" in why


def test_the_meter_is_printed_on_every_sweep():
    # This test used to assert the opposite, and the inversion is the finding.
    # The meter only ran under `cannot_go_green` because it was built to explain
    # a refusal — so when the refusals stopped on 2026-09-01, the meter stopped
    # running, on the exact morning the owner asked to have the 2,000 minutes
    # watched. A gauge that reads only while the thing is already on fire is not
    # monitoring.
    _status, clean = ci_health.check(
        opener=status_page(), run=gh(runs={"SokratesAI/a": []}, history={"SokratesAI/a": []}),
        repos=["SokratesAI/a"], now=NOW)
    assert [line for line in clean if line.startswith("METER")], clean


def test_a_meter_it_cannot_read_makes_the_sweep_unreadable():
    # `gh()` answers the usage endpoint; `gh_commits()` did not until this
    # cycle, and the four tests that broke on it were the honest signal.
    status, lines = ci_health.check(
        opener=status_page(),
        run=gh(runs={"SokratesAI/a": []}, history={"SokratesAI/a": []}, fail="billing/usage"),
        repos=["SokratesAI/a"], now=NOW)
    assert status == 1, lines
    assert [l for l in lines if l.startswith("METER   could not read")], lines


# --- burn_forecast ------------------------------------------------------
# The owner, 2026-09-01, 🔴 Immediately: "We have 2000minutes of CI runs for
# private repos. This must last us until 1.okt ... we need to monitor it and
# adjust our usage of it if its oversubscribed." A level does not answer that.

def test_a_part_day_does_not_get_a_forecast():
    # The real trap on the morning this was built: 10 private minutes at 08:00
    # on the 1st is 0.33 days of sample, and dividing by it projects 900 for
    # September off one morning of me re-running builds by hand.
    lines, over = ci_health.burn_forecast(
        10.0, datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc))
    text = "\n".join(lines)
    assert over is False
    assert "NOT FORECASTING" in text
    assert "lands at" not in text


def test_a_burn_that_overruns_the_allowance_raises():
    # 700 minutes with 6 days gone is 117/day against a 67/day budget.
    lines, over = ci_health.burn_forecast(
        700.0, datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc))
    text = "\n".join(lines)
    assert over is True
    assert "OVERSUBSCRIBED" in text
    assert "lands at 3500" in text


def test_a_burn_inside_the_allowance_does_not_raise():
    # Two-sided against the test above: same 6 days elapsed, a rate that fits.
    # Without this, a forecaster that raised on every month would pass.
    lines, over = ci_health.burn_forecast(
        60.0, datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc))
    text = "\n".join(lines)
    assert over is False
    assert "within budget" in text
    assert "OVERSUBSCRIBED" not in text


def test_an_oversubscribed_month_raises_the_whole_check():
    run = gh(runs={"SokratesAI/a": []}, history={"SokratesAI/a": []},
             usage=[usage_item("platform-config", 900.0)],
             repo_list=[{"name": "platform-config", "private": True}])
    status, lines = ci_health.check(
        opener=status_page(), run=run, repos=["SokratesAI/a"],
        now=datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc))
    assert status == 2, lines
    assert "projected past the monthly allowance" in "\n".join(lines)


def test_storage_gigabyte_hours_are_not_counted_as_minutes():
    # The bug this found: every `product == "actions"` row was summed as a
    # minute, and `Actions storage` is measured in GigabyteHours. It rounded
    # away in September and was the wrong kind of thing all the same.
    run = billing_gh(
        usage=[usage_item("platform-config", 40.0),
               usage_item("platform-config", 500.0, unit="GigabyteHours",
                          sku="Actions storage")],
        repos=[{"name": "platform-config", "private": True}])
    lines, over = ci_health.billing_meter(
        "SokratesAI", run=run, now=datetime(2026, 9, 20, tzinfo=timezone.utc))
    text = "\n".join(lines)
    assert "40 on private repo(s)" in text
    assert "500.000 GigabyteHour(s)" in text
    assert over is False


def test_a_windows_minute_costs_two():
    # GitHub charges the allowance 2x for Windows. A meter that counted raw
    # minutes would say 1,000 of 2,000 with the allowance actually spent.
    run = billing_gh(
        usage=[usage_item("platform-config", 1000.0, sku="Actions Windows")],
        repos=[{"name": "platform-config", "private": True}])
    lines, _over = ci_health.billing_meter(
        "SokratesAI", run=run, now=datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert "2000 on private repo(s)" in "\n".join(lines)


def test_a_runner_sku_with_no_published_multiplier_is_named():
    # Counted at 1x, because guessing high would invent an overrun — but said
    # out loud, because 1x is the direction that understates.
    run = billing_gh(
        usage=[usage_item("platform-config", 10.0, sku="Actions Quantum")],
        repos=[{"name": "platform-config", "private": True}])
    lines, _over = ci_health.billing_meter(
        "SokratesAI", run=run, now=datetime(2026, 9, 20, tzinfo=timezone.utc))
    text = "\n".join(lines)
    assert "NOT JUDGED  runner SKU `Actions Quantum`" in text
    assert "10 on private repo(s)" in text


# --- recent_private_rate ------------------------------------------------
# Cycle 893. `burn_forecast` divided the month's private minutes by the days
# elapsed, and a month-to-date average cannot see what a merge costs *today*.
# Measured 2026-09-04: `platform-config` ran 199 workflow runs in 24 hours
# against a flat budget of 67 private minutes a day for the whole org, while
# the month-to-date rate this tool printed read 80.7. The average was
# understating the live burn by a factor of two and a half, and it dilutes
# further every day the month runs. These pin the second rate.

def test_the_recent_rate_is_priced_from_the_meter_not_from_one_minute_per_run():
    # 300 minutes over 600 month-to-date runs is 0.5 minutes a run, measured.
    # 200 runs in the newest 24h is therefore 100 minutes a day -- and the
    # assumption this replaces, one minute per run, would have said 200.
    run = billing_gh(
        usage=[usage_item("platform-config", 300.0)],
        repos=[{"name": "platform-config", "private": True}],
        runs={"SokratesAI/platform-config": (600, 200)},
        now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
    lines, rate = ci_health.recent_private_rate(
        {"platform-config": 300.0}, 300.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    assert abs(rate - 100.0) < 1e-9
    text = "\n".join(lines)
    assert "0.50 measured private minute(s) per run" in text
    assert "100.0 private minute(s)/day" in text


def test_a_hot_recent_window_raises_while_the_month_average_is_still_comfortable():
    # The failure this exists for. Half the month gone and only 400 of 2,000
    # spent, so the month-to-date rate lands at 800 and reads "within budget"
    # -- while the newest day is spending 120/day, which eats the remaining
    # 1,600 minutes in 13 days against the 20 days of month left.
    lines, over = ci_health.burn_forecast(
        400.0, datetime(2026, 9, 11, 0, tzinfo=timezone.utc), recent_rate=120.0)
    text = "\n".join(lines)
    assert "within budget" in text, text
    assert "OVERSUBSCRIBED AT THE CURRENT RATE" in text, text
    assert over is True


def test_a_cool_recent_window_does_not_clear_a_month_that_is_already_over():
    # The mirror, and the direction that would cost: minutes already on the
    # meter are spent. A quiet day cannot un-spend them, so the cumulative
    # verdict still raises.
    lines, over = ci_health.burn_forecast(
        1900.0, datetime(2026, 9, 11, 0, tzinfo=timezone.utc), recent_rate=1.0)
    assert over is True
    assert "OVERSUBSCRIBED" in "\n".join(lines)


def test_a_repo_that_cannot_be_counted_is_named_rather_than_counted_as_zero():
    # A 404 read as zero runs understates the burn with no symptom at all,
    # which is the one direction that costs here.
    run = billing_gh(
        usage=[usage_item("platform-config", 300.0)],
        repos=[{"name": "platform-config", "private": True}],
        runs={}, fail="/actions/runs?")
    lines, rate = ci_health.recent_private_rate(
        {"platform-config": 300.0}, 300.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    assert rate is None
    text = "\n".join(lines)
    assert "NOT JUDGED" in text
    assert "platform-config" in text


def test_the_meter_asks_for_the_owner_qualified_repo():
    # The billing API names a repo bare. `/repos/platform-config/actions/runs`
    # is a 404, so the owner has to be put back on before the count is asked
    # for -- and a 404 counted as zero is exactly the silent understatement
    # the test above is about.
    seen = []
    run = billing_gh(
        usage=[usage_item("platform-config", 300.0)],
        repos=[{"name": "platform-config", "private": True}],
        runs={"SokratesAI/platform-config": (600, 200)}, seen=seen,
        now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
    ci_health.recent_private_rate(
        {"platform-config": 300.0}, 300.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    asked = [p for p in seen if "/actions/runs?" in p]
    assert len(asked) == 3, seen
    assert all(p.startswith("/repos/SokratesAI/platform-config/actions/runs?") for p in asked), asked
    # `created=>=` has to reach GitHub as `created=%3E%3D`. Sent raw, `gh api`
    # produces a filter the endpoint ignores, and an unfiltered count is every
    # run the repo has ever had -- 1,792 against 230 on `platform-config`
    # today. Minutes-per-run would come out an order of magnitude too small and
    # a burn that is over the allowance would read as comfortable.
    assert all("created=%3E%3D" in p for p in asked), asked
    assert any("created=%3E%3D2026-09-01T00:00:00Z" in p for p in asked), asked
    assert any("created=%3E%3D2026-09-09T12:00:00Z" in p for p in asked), asked


def test_no_runs_this_month_refuses_to_price_the_window():
    # Minutes per run is undefined at zero runs. Refusing is the same call
    # `burn_forecast` makes on a part-day: an undefined ratio is not a rate.
    run = billing_gh(
        usage=[usage_item("platform-config", 300.0)],
        repos=[{"name": "platform-config", "private": True}],
        runs={"SokratesAI/platform-config": (0, 0)},
        now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
    lines, rate = ci_health.recent_private_rate(
        {"platform-config": 300.0}, 300.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    assert rate is None
    assert "minutes per run is undefined" in "\n".join(lines)


def test_the_recent_window_still_speaks_before_the_month_is_forecastable():
    # The first two days of the month are exactly when the average refuses and
    # the recent window is the only measurement there is.
    lines, over = ci_health.burn_forecast(
        100.0, datetime(2026, 9, 2, 0, tzinfo=timezone.utc), recent_rate=200.0)
    text = "\n".join(lines)
    assert "NOT FORECASTING" in text, text
    assert "OVERSUBSCRIBED AT THE CURRENT RATE" in text, text
    assert over is True


def test_an_uncounted_repo_does_not_inflate_the_price_per_run():
    # The failure the author found re-reading the diff. The meter knows what
    # every private repo spent; the run counter may not reach all of them.
    # Dividing ALL the minutes by SOME of the runs inflates minutes-per-run,
    # which inflates the recent rate and raises this check on a repo it could
    # not read -- an alarm manufactured out of a failed API call.
    #
    # Here `platform-config` spent 300 minutes over 600 runs (0.50/run) and
    # `operator` spent 700 minutes whose runs cannot be counted at all. Priced
    # over the whole 1000 minutes the ratio would be 1.67/run and the rate
    # 333/day; priced over the population that was actually counted it is
    # 0.50/run and 100/day.
    def run(cmd, **kwargs):
        path = cmd[2]
        if "/repos/SokratesAI/operator/actions/runs?" in path:
            # `operator` answers, and answers zero: its 700 minutes were spent
            # earlier in the month by runs GitHub has since expired out of the
            # runs API. It is counted, so the sweep is whole and the rate is
            # rated -- but its minutes must not be priced against another
            # repo's runs.
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"total_count": 0}), stderr="")
        if "/repos/SokratesAI/platform-config/actions/runs?" in path:
            # The month window is the 1st at midnight; the recent one is
            # `now - 24h`. Only the `>=` is percent-encoded in the real query
            # -- the colons go over the wire raw -- so match the timestamp
            # exactly rather than guessing at the encoding.
            total = 600 if "created=%3E%3D2026-09-01T00:00:00Z" in path else 200
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"total_count": total}), stderr="")
        raise AssertionError(path)

    lines, rate = ci_health.recent_private_rate(
        {"platform-config": 300.0, "operator": 700.0}, 1000.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    text = "\n".join(lines)
    assert "0.50 measured private minute(s) per run" in text, text
    assert abs(rate - 100.0) < 1e-9, rate
    assert "left out of the price" in text and "operator" in text, text


def test_a_repo_counted_for_the_month_but_not_the_window_refuses_to_rate():
    # The reviewer's finding on this diff, and it reversed the author's first
    # answer. If the month window reads and the recent one fails, the repo's
    # runs are in the denominator and its recent activity is not, so the rate
    # comes out at or near zero -- and on the org's dominant repo that is not
    # a proportional shortfall, it is the whole signal. Fed to `burn_forecast`
    # a zero rate prints `within budget` and exits 0, in exactly the spiking
    # repo case this feature exists to catch. "Low" is only the safe direction
    # for a number nobody acts on; this one gates the exit status. So refuse.
    calls = []

    def run(cmd, **kwargs):
        path = cmd[2]
        calls.append(path)
        if "created=%3E%3D2026-09-01T00:00:00Z" in path:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"total_count": 400}), stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="gh: refused")

    lines, rate = ci_health.recent_private_rate(
        {"platform-config": 200.0}, 200.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    assert rate is None, rate
    text = "\n".join(lines)
    assert "NOT JUDGED" in text and "platform-config" in text, text
    assert "not rated at all" in text, text
    # and the number that would have been printed must not appear at all --
    # a zero rate on the page is what a reader would have acted on.
    assert "private minute(s)/day" not in text, text


# --- the burn rate is not stationary -------------------------------------
# Cycle 921. One window cannot tell a steady burn from one that changed
# inside it. `platform-config` folded three workflows into one at 03:43 UTC
# on 2026-09-04; fifteen hours later the 24h window still held two thirds
# pre-fold runs and this tool printed 203.9 minutes/day, which `burn_forecast`
# turned into "8.3 days left". Counted over the newest 12h -- all of it after
# the fold -- the same repos ran 100 runs a day, and the honest answer was
# about fourteen days. Cycle 917 caught it by hand and wrote the workaround
# into the handoff. These pin it in the instrument instead.

def test_a_burn_that_changed_inside_the_window_is_named_rather_than_averaged():
    # The live shape on 2026-09-04, scaled: 200 runs across the 24h window
    # but only 50 of them in the newest 12h. Averaged, that reads 100
    # minutes/day; the half that is actually still happening reads 50.
    run = billing_gh(
        usage=[usage_item("platform-config", 300.0)],
        repos=[{"name": "platform-config", "private": True}],
        runs={"SokratesAI/platform-config": (600, 200, 50)},
        now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
    lines, rate = ci_health.recent_private_rate(
        {"platform-config": 300.0}, 300.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    text = "\n".join(lines)
    # The 24h rate is unchanged -- this reports beside it, it does not replace
    # it, because counts cannot say which of the two windows is the truth.
    assert abs(rate - 100.0) < 1e-9
    assert "NOT STEADY" in text
    assert "50.0 private minute(s)/day against the 100.0/day above" in text
    assert "names the gap rather than picking a rate" in text


def test_the_disagreement_is_quoted_as_a_range_of_days_not_a_single_figure():
    # The number the owner actually reads. 1,700 minutes left at 50/day is 34
    # days and at 100/day is 17; quoting either alone is a measurement this
    # tool did not take.
    run = billing_gh(
        usage=[usage_item("platform-config", 300.0)],
        repos=[{"name": "platform-config", "private": True}],
        runs={"SokratesAI/platform-config": (600, 200, 50)},
        now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
    lines, _ = ci_health.recent_private_rate(
        {"platform-config": 300.0}, 300.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    text = "\n".join(lines)
    assert "between 17.0 and 34.0 day(s)" in text


def test_a_steady_burn_says_nothing_at_all():
    # Half the 24h runs in the newest 12h is the same rate twice over. A line
    # on every clean sweep is a line nobody reads, which is how the 400-chip
    # rule fails from the other end.
    run = billing_gh(
        usage=[usage_item("platform-config", 300.0)],
        repos=[{"name": "platform-config", "private": True}],
        runs={"SokratesAI/platform-config": (600, 200, 100)},
        now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
    lines, rate = ci_health.recent_private_rate(
        {"platform-config": 300.0}, 300.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    assert abs(rate - 100.0) < 1e-9
    assert "NOT STEADY" not in "\n".join(lines)


def test_a_quiet_nested_window_is_too_noisy_to_call_and_stays_silent():
    # Four runs in twelve hours against eight in twenty-four. The rates are
    # 2/day and 2/day here, but the point is the bar: the threshold is the
    # Poisson noise on the nested count, so a window with almost nothing in
    # it cannot raise a finding no matter which way it leans. 6 runs in the
    # nested window against 8 in the wide one is a 3.0/day gap under a
    # +-4.9/day bar.
    run = billing_gh(
        usage=[usage_item("platform-config", 300.0)],
        repos=[{"name": "platform-config", "private": True}],
        runs={"SokratesAI/platform-config": (600, 8, 6)},
        now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
    lines, _ = ci_health.recent_private_rate(
        {"platform-config": 300.0}, 300.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    assert "NOT STEADY" not in "\n".join(lines)


def test_an_empty_nested_window_still_has_a_bar_of_one_run():
    # sqrt(0) is zero and a bar of zero calls every window apart, including
    # one that stopped for an hour. The floor is one run: 0 runs in 12h
    # against 200 in 24h is a real stop and does report.
    run = billing_gh(
        usage=[usage_item("platform-config", 300.0)],
        repos=[{"name": "platform-config", "private": True}],
        runs={"SokratesAI/platform-config": (600, 200, 0)},
        now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
    lines, _ = ci_health.recent_private_rate(
        {"platform-config": 300.0}, 300.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    text = "\n".join(lines)
    assert "NOT STEADY" in text
    # No range: a rate of zero divides into no number of days.
    assert "day(s), not the single figure below" not in text


def test_an_empty_nested_window_under_an_empty_wide_one_says_nothing():
    # This is what the floor of one run is for, and the test above does not
    # pin it: with a busy 24h window an empty nested one reports either way.
    # Here the whole day held a single run. `sqrt(0)` is zero, so without the
    # floor the bar is zero and a difference of half a minute a day -- one run
    # -- reads as the burn changing. There is nothing here to conclude.
    run = billing_gh(
        usage=[usage_item("platform-config", 300.0)],
        repos=[{"name": "platform-config", "private": True}],
        runs={"SokratesAI/platform-config": (600, 1, 0)},
        now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
    lines, _ = ci_health.recent_private_rate(
        {"platform-config": 300.0}, 300.0, "SokratesAI",
        datetime(2026, 9, 10, 12, tzinfo=timezone.utc), run=run)
    assert "NOT STEADY" not in "\n".join(lines)
