"""Can a pull request I open right now actually reach `main`, and whose problem is it if not?

Cycle 488, on the second cycle in a row that spent real time guessing at
this. Cycle 487 pushed runner#424 at 17:11 Oslo, watched its build sit
`queued` for fourteen minutes having created **zero jobs**, and wrote into
the handoff that this looked like the GitHub Actions billing block that
killed `docs-sync` on 21 August — with an honest "measure it before you
assume it" beside it, because it could not read the billing API.

It was not the billing block. Measured Cycle 488 in one call:
`githubstatus.com` had opened an **Actions major outage** at
`2026-08-26T15:11:58Z`, four seconds after that run was created. Nothing in
this repo, this org or this account was wrong. The distinction is not
academic — the billing story ends in "nothing merges until 1 September and
the owner has to act", and the outage story ends in "wait, then merge", and
a cycle that picks the wrong one either stalls or plans around a wall that
is not there.

    python3 -m tools.ci_health

**Two measurements, deliberately not merged into one verdict.** That is
this loop's own lesson from `tools.agentic_health`, which printed one
three-run failure streak that was two unrelated causes and sent two cycles
after the wrong half: a single number reads as a diagnosis. So this asks

1. **Is GitHub Actions itself healthy?** The `Actions` component on
   `githubstatus.com`. This is the question no amount of poking at our own
   repos can answer, and it is the one that was true.
2. **Are my own runs actually starting jobs?** For every repo this loop
   has a checkout of, any run still `queued` past the grace, and the job
   count on it. **Zero jobs is the symptom**; a run with jobs is merely
   slow, which is a different sentence.

and prints them as separate lines with separate causes.

**A third question, added Cycle 497, and the two above cannot answer it.**
Both of them watch a run that is *in flight*. A repo GitHub refuses to run
jobs for because the account owes money has nothing in flight at all: the
run is created, no job starts, and the whole thing is `completed` and
`failure` about two seconds later. Measured — every run in
`SokratesAI/platform-config` since 13:47 Oslo on 2026-08-26 died that way,
and this tool printed `ok` for that repo while Cycle 496 merged into it with
a red check. So `blocked_repo` asks: when a run here completes, does any job
execute a single step? See its docstring for why a finding there is loud in
the text and deliberately does not raise the exit status.

**A queued run is not the measurement, and a 200 from the status page is
not either.** A run that has been queued forty seconds is normal, and the
status summary answers `operational` for a healthy Actions and for a
status page that has not noticed yet — so a green from one of these is not
a green overall, and the report says which of the two answered. The
positive result that would be guaranteed in advance here is "no queued
runs": a repo nobody has pushed to today has none, whether Actions is up
or on fire. That is why the sweep names the repos that had nothing in
flight rather than counting them as evidence.

**And a queued run outlives the thing that queued it.** Run 32984347949 was
created four seconds into the 2026-08-26 Actions outage and was still
`queued` with zero jobs at 20:04 Oslo — three hours later, three minutes
after githubstatus resolved that incident, with its own pull request merged
and eight later runs in the same repo finished. Measured Cycle 495: this
tool read `operational` off the status page, read that one orphan off the
queue, and printed "a merge cannot complete right now" anyway. A run that
has been abandoned is a scar, not a symptom, and the two are identical from
the queue alone — so that verdict would have stood every cycle from then on,
because nothing ever takes an abandoned run out of a queue.

So a zero-job run is quietened — printed as `ABANDONED`, status deliberately
not raised — only on a positive measurement: **a run in the same repo
created after it that has since completed**, which is proof GitHub has been
starting jobs in the interval this one has been sitting. No completed run to
compare against quietens nothing.

**The grace is five minutes and it is measured, not chosen.** Every
complete build in `agora-persona-runner` on 2026-08-26 finished inside
2m54s, wall clock, queue included. A run that has not created a *single*
job in five minutes is not slow.

Exit status, matching `tools.security_alerts`, `tools.cli_pin`,
`tools.agentic_health`, `tools.heartbeat_health` and
`tools.helm_repo_health` so a cycle can read it without parsing the text:
**2 means a merge cannot complete right now**, 1 means something was
unreadable — which never reads as clean — and 0 means CI is in a state
where a PR can land, naming what it swept.

**Note the deliberate difference from `agentic_health`'s rule.** That tool
does *not* raise its status for the Actions billing block, because there is
no pull request that fixes an account's spending limit and treating it as
actionable made every cycle re-derive it. A GitHub-side outage has no pull
request either — but it is still exit 2 here, because the action this tool
exists to trigger is not a fix. It is **pick a cycle that does not end in a
merge**, which is step 2's decision and is exactly as actionable when the
cause is GitHub's as when it is ours.
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request

STATUS_URL = "https://www.githubstatus.com/api/v2/summary.json"
USER_AGENT = "nova-ci-health/1"

# Every complete build in agora-persona-runner on 2026-08-26 finished inside
# 2m54s, queue included. Five minutes with zero jobs created is not slow.
DEFAULT_GRACE_MINUTES = 5

# githubstatus' own vocabulary. `degraded_performance` is deliberately not
# here: Actions has answered that on days this loop merged fine, so treating
# it as a blocker would refuse work on a healthy pipeline.
BLOCKING_STATUSES = {"major_outage", "partial_outage"}


def actions_status(opener=urllib.request.urlopen):
    """`(status, incidents, None)` or `(None, None, why)` for GitHub's Actions component.

    `status` is githubstatus' own token (`operational`, `major_outage`, ...).
    `incidents` is the list of unresolved incident names that mention Actions,
    so the report can say *which* outage rather than only that there is one.
    """
    request = urllib.request.Request(STATUS_URL, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return None, None, f"HTTP {exc.code} fetching {STATUS_URL}"
    except Exception as exc:  # noqa: BLE001 -- any network shape is "unreadable"
        return None, None, f"could not fetch {STATUS_URL}: {exc}"
    try:
        body = json.loads(raw)
    except ValueError as exc:
        return None, None, f"{STATUS_URL} is not JSON: {exc}"
    if not isinstance(body, dict):
        return None, None, f"{STATUS_URL} is not a status summary"

    status = None
    for component in body.get("components") or []:
        if isinstance(component, dict) and component.get("name") == "Actions":
            status = component.get("status")
            break
    if status is None:
        return None, None, f"{STATUS_URL} carries no component named Actions"

    incidents = [
        i.get("name", "?") for i in (body.get("incidents") or [])
        if isinstance(i, dict) and "action" in i.get("name", "").lower()
    ]
    return status, incidents, None


def _gh_json(args, run):
    """`(parsed, None)` or `(None, why)` for one `gh api` call."""
    try:
        proc = run(["gh", "api", *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"gh failed: {exc}"
    if proc.returncode != 0:
        return None, f"gh failed: {(proc.stderr or proc.stdout).strip().splitlines()[0] if (proc.stderr or proc.stdout).strip() else 'no output'}"
    try:
        return json.loads(proc.stdout), None
    except ValueError as exc:
        return None, f"gh returned something that is not JSON: {exc}"


def newest_completed_run(repo, run=subprocess.run):
    """`(run_dict_or_None, None)` or `(None, why)` for the repo's newest completed run.

    The one measurement that tells an abandoned run from a live stall. See
    `stalled_runs` below for why it exists.
    """
    body, why = _gh_json(
        [f"repos/{repo}/actions/runs?status=completed&per_page=1", "-q",
         '[.workflow_runs[] | {id, created_at}]'],
        run)
    if body is None:
        return None, why
    return (body[0] if body else None), None


def stalled_runs(repo, grace_minutes=DEFAULT_GRACE_MINUTES, run=subprocess.run, now=None):
    """`(verdicts, None)` or `(None, why)` for one repo's in-flight runs.

    A verdict is `(state, text)` where state is `"stalled"`, `"abandoned"`,
    `"slow"` or `"clear"`. `now` is an aware datetime for the test; production
    reads the clock. Timestamps from `gh` are UTC and are compared as UTC — this
    loop writes Oslo everywhere else and that arithmetic is where Cycle 446
    invented a 100-minute stall out of the summer offset.

    **`abandoned` is Cycle 495's correction and it is the difference between a
    symptom and a scar.** A queued run with zero jobs is a fact; "therefore a
    merge cannot complete right now" is an inference, and it stops being true
    the moment Actions recovers while the orphaned run stays in the queue
    forever. Measured 2026-08-26 20:04 Oslo: run 32984347949 was created four
    seconds into that afternoon's outage and was still `queued` with zero jobs
    three hours later — three minutes after githubstatus resolved the incident,
    with its own pull request merged and eight later runs in the same repo
    finished. This tool read `operational` off the status page and still said a
    merge could not complete, off that orphan alone, and would have gone on
    saying it indefinitely: nothing removes an abandoned run from a queue.

    So the quietening measurement is the same shape `security_alerts` uses for
    an already-patched advisory — **only ever a positive**: has GitHub created
    and *completed* any run in this repo since this one was created? If yes,
    Actions has been starting jobs since, so this queued run says nothing about
    now. If there is no completed run to compare against, nothing is quietened
    and the stall stands.
    """
    from datetime import datetime, timezone

    now = now or datetime.now(timezone.utc)
    body, why = _gh_json(
        [f"repos/{repo}/actions/runs?per_page=30", "-q",
         '[.workflow_runs[] | select(.status != "completed") '
         '| {id, name, status, created_at, head_branch}]'],
        run)
    if body is None:
        return None, why
    if not body:
        return [("clear", f"ok       {repo}: nothing in flight")], None

    verdicts = []
    for item in body:
        created = item.get("created_at") or ""
        try:
            started = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            return None, f"{repo}: run {item.get('id')} has an unreadable created_at {created!r}"
        waited = (now - started).total_seconds() / 60.0
        where = f"{repo}#{item.get('id')} ({item.get('head_branch') or '?'})"
        if waited < grace_minutes:
            verdicts.append(("clear", f"ok       {where}: {item.get('status')} {waited:.0f}m, inside the {grace_minutes}m grace"))
            continue
        count, count_why = _gh_json(
            [f"repos/{repo}/actions/runs/{item.get('id')}/jobs", "-q", ".total_count"], run)
        if count is None:
            return None, f"{where}: {count_why}"
        if count == 0:
            newer, newer_why = newest_completed_run(repo, run)
            if newer_why is not None:
                return None, f"{where}: {newer_why}"
            later = None
            if newer:
                try:
                    later = datetime.fromisoformat(
                        (newer.get("created_at") or "").replace("Z", "+00:00"))
                except ValueError:
                    later = None
            if later is not None and later > started:
                gap = (later - started).total_seconds() / 60.0
                verdicts.append((
                    "abandoned",
                    f"ABANDONED  {where}: queued {waited:.0f}m with 0 jobs, but run "
                    f"{newer.get('id')} was created {gap:.0f}m after it and has "
                    f"completed — GitHub has started jobs since, so this one is a "
                    f"leftover from an earlier incident, not a symptom of now"))
                continue
            verdicts.append((
                "stalled",
                f"STALLED  {where}: queued {waited:.0f}m and GitHub has created "
                f"0 jobs — nothing is running, this run cannot go green"))
        else:
            verdicts.append((
                "slow",
                f"slow     {where}: {item.get('status')} {waited:.0f}m with "
                f"{count} job(s) created — running, just not finished"))
    return verdicts, None


def unrun_pushes(repo, grace_minutes=DEFAULT_GRACE_MINUTES, run=subprocess.run, now=None):
    """`(verdicts, None)` or `(None, why)` for a commit GitHub created no run for.

    `stalled_runs` above can only see runs that *exist*. Measured Cycle 492,
    twice in one hour: the merge of runner#425 landed on `main` and GitHub
    created no push run for it, and a push to a pull-request branch two
    minutes later created no run either. Both times the repo would have read
    `nothing in flight` -- which this tool's own report calls out as "evidence
    that nobody pushed" -- and that sentence was false, because I had just
    pushed. A run that never gets created is the *quieter* half of a broken
    Actions and the more common one: the 2026-08-26 outage produced exactly
    one two-hour stalled run and then stopped creating runs at all.

    So this asks the opposite question: take the newest commit on the default
    branch, and if it is older than the grace and carries no workflow run,
    GitHub never started one.

    **The guard is what makes a negative here mean anything.** A repo whose
    workflows do not trigger on a push to the default branch has no run on
    that commit whether Actions is healthy or dead -- a positive result
    guaranteed in advance. So a repo is only judged when it has at least one
    historical `push` run to compare against, and one that has none says so
    rather than passing quietly.
    """
    from datetime import datetime, timezone

    now = now or datetime.now(timezone.utc)
    head, why = _gh_json(
        [f"repos/{repo}/commits?per_page=1", "-q",
         '[.[0] | {sha, date: .commit.committer.date}]'], run)
    if head is None:
        return None, why
    if not head or not head[0].get("sha"):
        return [("clear", f"ok       {repo}: no commits to check")], None
    sha, date = head[0]["sha"], head[0].get("date") or ""
    try:
        committed = datetime.fromisoformat(date.replace("Z", "+00:00"))
    except ValueError:
        return None, f"{repo}: head commit {sha[:7]} has an unreadable date {date!r}"
    age = (now - committed).total_seconds() / 60.0
    if age < grace_minutes:
        return [("clear", f"ok       {repo}: default-branch head {sha[:7]} is "
                          f"{age:.0f}m old, inside the {grace_minutes}m grace")], None

    history, why = _gh_json(
        [f"repos/{repo}/actions/runs?event=push&per_page=1", "-q", ".total_count"], run)
    if history is None:
        return None, why
    if not history:
        return [("clear", f"ok       {repo}: no workflow has ever run on a push here, "
                          f"so a missing run on {sha[:7]} says nothing")], None

    count, why = _gh_json(
        [f"repos/{repo}/actions/runs?head_sha={sha}&per_page=1", "-q", ".total_count"], run)
    if count is None:
        return None, why
    if count == 0:
        return [("norun",
                 f"NO RUN   {repo}: default-branch head {sha[:7]} was pushed "
                 f"{age:.0f}m ago and GitHub created no workflow run at all — "
                 f"this repo normally runs one on every push")], None
    return [("clear", f"ok       {repo}: default-branch head {sha[:7]} has "
                      f"{count} run(s)")], None


# GitHub's own wording when it refuses to start a job because the account
# owes money. Quoted from the annotation on SokratesAI/sokrates-docs run
# 32937... on 2026-08-21 and on every platform-config run since 13:47 Oslo on
# 2026-08-26: "The job was not started because recent account payments have
# failed or your spending limit needs to be increased."
BILLING_PHRASES = ("recent account payments have failed", "spending limit")

# What GitHub calls a run that tried and lost. `cancelled`, `skipped` and
# `action_required` are deliberately not here: a cancelled run has no steps
# either, and reading a human pressing the stop button five times as a
# refused account would be a finding with nothing under it.
FAILED_CONCLUSIONS = {"failure", "startup_failure", "timed_out"}


def _first_annotation(repo, job_id, run):
    """The first `failure`-level check-run annotation on a job, or `None`.

    Same call `tools.agentic_health._failure_annotation` makes, and for the
    same reason: when GitHub refuses to start a job it puts the reason here
    and nowhere else, so `gh run view --log-failed` answers `log not found`
    on exactly the run you most want to read.

    **The level filter is the part that matters and it was missing here.**
    This took the first non-empty message of any level, and GitHub stacks
    routine warnings above the real cause -- the 2026-08-28 `docs-sync` job
    carries a Node.js 20 deprecation warning first and *"The action
    'Execute Gemini CLI' has timed out after 20 minutes."* second. Every
    never-started job in this org happens to carry exactly one annotation
    today, so this had not misfired yet; that is a fact about today's data,
    not about the rule. Cycle 598 fixed the twin and my own reviewer caught
    that this copy had been left behind.
    """
    if job_id is None:
        return None
    body, _why = _gh_json([f"repos/{repo}/check-runs/{job_id}/annotations"], run)
    if not isinstance(body, list):
        return None
    for note in body:
        note = note or {}
        if (note.get("annotation_level") or "").lower() != "failure":
            continue
        message = (note.get("message") or "").strip()
        if message:
            return message
    return None


def blocked_repo(repo, run=subprocess.run, sample=5):
    """`(verdicts, None)` or `(None, why)` for a repo where no run can execute.

    **The hole Cycle 496 fell into.** Both checks above watch runs that are
    *in flight*: `stalled_runs` wants a run sitting in the queue and
    `unrun_pushes` wants a commit with no run on it. A billing-blocked repo
    has neither. GitHub creates the run, refuses to start the job, and marks
    the whole thing `completed` / `failure` about two seconds later — so the
    queue is empty, every commit has a run against it, and this tool printed
    `ok` for `SokratesAI/platform-config` while every run in it since 13:47
    Oslo on 2026-08-26 had died that way and Cycle 496 merged there red.

    So this asks the third question: **when a run here does complete, does
    anything actually execute?**

    The guard against a positive guaranteed in advance runs first and it is a
    success: if any of the newest `sample` completed runs concluded
    `success`, jobs are being run in this repo and nothing else here matters.
    Only when all of them failed is the newest one opened, and the finding is
    that **no job on it executed a single step** — which is the difference
    between an account that will not pay for a runner and a test suite that
    is red.

    **It deliberately does not raise the exit status**, which is the same
    call `tools.agentic_health` makes on the same annotation and the opposite
    of the one this tool makes for a GitHub-side outage. An outage is over in
    hours and exit 2 there means "merge later today". A billing block is over
    when the owner pays; the owner has already decided to wait this one out
    until 1 September (comments board, 2026-08-26: *"I do not want to pay for
    the ci runs. We just have to wait until September 1st."*), so raising
    would paint this check red for six days running, and a check that is
    always red is one nobody reads. The finding belongs in the text, where a
    cycle choosing what to merge will see it.
    """
    body, why = _gh_json(
        [f"repos/{repo}/actions/runs?status=completed&per_page={sample}", "-q",
         '[.workflow_runs[] | {id, conclusion}]'],
        run)
    if body is None:
        return None, why
    if not body:
        return [("clear", f"ok       {repo}: no completed run here to judge")], None
    if any((item.get("conclusion") or "") == "success" for item in body):
        return [("clear", f"ok       {repo}: a success is among the newest "
                          f"{len(body)} completed run(s) — jobs execute here")], None

    newest = body[0]
    if (newest.get("conclusion") or "") not in FAILED_CONCLUSIONS:
        return [("clear", f"ok       {repo}: the newest completed run concluded "
                          f"`{newest.get('conclusion')}` — that is not a run GitHub "
                          f"refused to start")], None
    payload, why = _gh_json([f"repos/{repo}/actions/runs/{newest.get('id')}/jobs"], run)
    if payload is None:
        return None, f"{repo}#{newest.get('id')}: {why}"
    jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(jobs, list) or not jobs:
        return [("clear", f"ok       {repo}: the newest {len(body)} completed run(s) "
                          f"failed, but gh listed no job on run "
                          f"{newest.get('id')} to judge")], None
    if any((job or {}).get("steps") for job in jobs):
        return [("clear", f"ok       {repo}: the newest completed run executed steps "
                          f"— what failed is code, not the account")], None

    quoted = _first_annotation(repo, (jobs[0] or {}).get("id"), run)
    lower = (quoted or "").lower()
    if quoted and any(phrase in lower for phrase in BILLING_PHRASES):
        cause = f"GitHub says: {quoted}"
    elif quoted:
        cause = f"no step ran and GitHub says: {quoted}"
    else:
        cause = "no step ran and GitHub gave no reason on the run"
    return [("blocked",
             f"CANNOT GO GREEN  {repo}: the newest {len(body)} completed run(s) all "
             f"failed and run {newest.get('id')} executed no step at all — "
             f"{cause}")], None


def _repos_to_sweep():
    """The repos this loop could open a PR on today: the ones it has a checkout of.

    Same derivation as `security_alerts._repos_from_workspace`, and for the
    same reason — a hardcoded list of "the repos we touch" has gone stale in
    this repo twice. Unlike that sweep there is no org widening here: a repo
    with no checkout is one this cycle cannot push to anyway, so its queue
    says nothing about whether *my* work can land.
    """
    from tools.tidy_workspace import origin_repos, workspace_roots

    repos, unplaceable = origin_repos(workspace_roots())
    return sorted(repos), unplaceable


def check(opener=urllib.request.urlopen, run=subprocess.run,
          grace_minutes=DEFAULT_GRACE_MINUTES, repos=None, now=None):
    """Return `(exit_status, lines)`."""
    lines, unreadable, blocked, cannot_go_green = [], [], False, []

    status, incidents, why = actions_status(opener)
    if status is None:
        lines.append(f"COULD NOT READ: {why}")
        unreadable.append("githubstatus")
    elif status in BLOCKING_STATUSES:
        blocked = True
        lines.append(f"GITHUB-SIDE  Actions is `{status}` on githubstatus.com right now.")
        for name in incidents:
            lines.append(f"             open incident: {name}")
        lines.append("             There is no pull request that fixes this and nothing here is wrong.")
    else:
        lines.append(f"ok       githubstatus.com reports Actions `{status}`")

    if repos is None:
        repos, unplaceable = _repos_to_sweep()
        for clone in unplaceable:
            lines.append(f"⚠ {clone}: could not place this checkout on GitHub, not swept")
    if not repos:
        lines.append("COULD NOT READ: no checkout here names a GitHub repo, so no queue was measured.")
        unreadable.append("workspace")

    for repo in repos:
        verdicts, repo_why = stalled_runs(repo, grace_minutes, run, now)
        if verdicts is None:
            lines.append(f"COULD NOT READ  {repo}: {repo_why}")
            unreadable.append(repo)
            continue
        for state, text in verdicts:
            lines.append(text)
            if state == "stalled":
                blocked = True

        verdicts, repo_why = unrun_pushes(repo, grace_minutes, run, now)
        if verdicts is None:
            lines.append(f"COULD NOT READ  {repo}: {repo_why}")
            unreadable.append(repo)
            continue
        for state, text in verdicts:
            lines.append(text)
            if state == "norun":
                blocked = True

        verdicts, repo_why = blocked_repo(repo, run)
        if verdicts is None:
            lines.append(f"COULD NOT READ  {repo}: {repo_why}")
            unreadable.append(repo)
            continue
        for state, text in verdicts:
            lines.append(text)
            if state == "blocked":
                cannot_go_green.append(repo)

    lines.append(f"Swept {len(repos)} repo(s) with a checkout here, grace {grace_minutes}m: "
                 f"{', '.join(repos) or 'none'}.")
    lines.append("A repo with nothing queued is not evidence that Actions works — "
                 "it is evidence that nobody pushed. The `NO RUN` line above is the "
                 "check that separates those two.")
    if cannot_go_green:
        lines.append(
            f"A pull request into {', '.join(cannot_go_green)} cannot go green — "
            f"GitHub is creating the run and refusing to start the job. There is no "
            f"pull request that fixes that and a merge into any other repo above is "
            f"unaffected, so the status is deliberately not raised.")

    if blocked:
        lines.append("A merge cannot complete right now. Pick a cycle that does not end in one.")
        return 2, lines
    if unreadable:
        lines.append("Being unable to check is not the same as nothing to check.")
        return 1, lines
    return 0, lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--grace-minutes", type=float, default=DEFAULT_GRACE_MINUTES,
                        help="how long a run may sit with zero jobs before it is stalled")
    parser.add_argument("--repo", action="append", dest="repos",
                        help="sweep this repo instead of the workspace checkouts (repeatable)")
    args = parser.parse_args(argv)
    status, lines = check(grace_minutes=args.grace_minutes, repos=args.repos)
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
