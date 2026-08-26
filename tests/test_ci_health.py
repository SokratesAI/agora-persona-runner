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


def gh(runs=None, jobs=None, fail=None):
    """A fake `subprocess.run` answering the two `gh api` calls this tool makes."""
    runs, jobs = runs or {}, jobs or {}

    def runner(cmd, **kwargs):
        assert cmd[:2] == ["gh", "api"], cmd
        path = cmd[2]
        if fail is not None and fail in path:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="gh: not found")
        if "/jobs" in path:
            run_id = int(path.split("/runs/")[1].split("/")[0])
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(jobs.get(run_id, 0)), stderr="")
        repo = path.split("repos/")[1].split("/actions")[0]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(runs.get(repo, [])), stderr="")
    return runner


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
