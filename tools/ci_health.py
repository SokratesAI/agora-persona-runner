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
2. **Are my own runs actually starting jobs?** For every non-archived repo
   in the org — not just the ones with a checkout here, see
   `_repos_to_sweep` — any run still `queued` past the grace, and the job
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
import calendar
import concurrent.futures
import math
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.request

STATUS_URL = "https://www.githubstatus.com/api/v2/summary.json"
USER_AGENT = "nova-ci-health/1"

# Every complete build in agora-persona-runner on 2026-08-26 finished inside
# 2m54s, queue included. Five minutes with zero jobs created is not slow.
DEFAULT_GRACE_MINUTES = 5
# Three `gh` calls per repo across ~23 repos. Eight at a time keeps the
# sweep inside `preflight`'s window without opening a connection per repo.
DEFAULT_MAX_WORKERS = 8

# githubstatus' own vocabulary. `degraded_performance` is deliberately not
# here: Actions has answered that on days this loop merged fine, so treating
# it as a blocker would refuse work on a healthy pipeline.
BLOCKING_STATUSES = {"major_outage", "partial_outage"}

#: What one minute on each runner costs against the included allowance.
#: GitHub bills Linux at 1x, Windows at 2x and macOS at 10x. A SKU that is
#: not here is counted at 1x **and named** in the report rather than
#: silently multiplied by a number I guessed -- understating the burn is the
#: direction that costs, so an unpriced SKU has to be visible.
MINUTE_MULTIPLIER = {
    "Actions Linux": 1.0,
    "Actions Linux Slim": 1.0,
    "Actions Windows": 2.0,
    "Actions macOS": 10.0,
}

#: Private-repo Actions minutes included per calendar month on this org's plan.
#: The owner, 2026-09-01: "We have 2000minutes of CI runs for private repos."
INCLUDED_PRIVATE_MINUTES = 2000.0

#: A forecast needs this many days of the billing month behind it. See
#: `burn_forecast` for why refusing is the whole point of the number.
MIN_ELAPSED_DAYS = 2.0

#: How far back the second, recent-window rate looks. A day is the shortest
#: window that still averages over a whole sleep cycle -- this loop merges
#: nothing for the hours the owner is asleep and a great deal in the morning,
#: so anything shorter measures the time of day rather than the habit.
RECENT_WINDOW_HOURS = 24.0

#: A window nested inside `RECENT_WINDOW_HOURS`, used only to ask whether the
#: burn is steady across the day -- never as a rate to forecast from, for the
#: reason written above it. Half the window is the widest slice that can still
#: show a change made partway through it.
NESTED_WINDOW_HOURS = 12.0


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


def recent_private_rate(repos, used_private, org, now,
                        window_hours=RECENT_WINDOW_HOURS,
                        nested_hours=NESTED_WINDOW_HOURS,
                        allowance=INCLUDED_PRIVATE_MINUTES, run=subprocess.run):
    """`(lines, minutes_per_day_or_None)` — private-minute burn over the newest window.

    `burn_forecast` below divides the month's private minutes by the days
    elapsed, and **that average cannot see a change in what a merge costs.**
    Cycle 885 folded `platform-config`'s two jobs into one on the morning of
    2026-09-04, halving the billed minutes per merged pull request; the
    month-to-date rate keeps quoting the pre-fold number for the rest of
    September, diluting more slowly the longer the month runs. The failure is
    symmetric and the direction that costs is the other one: measured the same
    morning, `platform-config` alone ran 199 workflow runs in 24 hours against
    a flat budget of 67 private minutes a day for the whole org, while the
    month-to-date rate read 80.7. **The average was understating the live burn
    by a factor of two and a half.**

    So this measures the newest `window_hours` instead. GitHub's billing API
    answers month-to-date only -- there is no per-day series in it -- so the
    time distribution comes from run *counts*, which `/actions/runs` returns
    as `total_count` for a `created=>=` filter in one call per repo per
    window.

    **The conversion from runs to minutes is measured, never assumed.** A run
    is not a minute by definition; it is a minute on this org today because
    GitHub bills whole minutes per job and these jobs take about thirty
    seconds. Rather than hardcode that, this divides the meter's own
    month-to-date private minutes by the month-to-date run count on the same
    repos, so the ratio is re-derived every time and a repo that grows a
    second job or a five-minute suite moves it. If the month-to-date run count
    is zero the ratio is undefined and this refuses to rate rather than
    guessing at one -- the same refusal `burn_forecast` makes on a part-day.

    `repos` is the private-spender mapping `billing_meter` already built, so
    this costs three API calls per repo that actually spent a minute this
    month and nothing at all for the rest of the org.

    **One window cannot tell a steady burn from one that changed inside it,
    and on 2026-09-04 that cost a wrong date in front of the owner.** The fold
    described above landed at 03:43 UTC. Fifteen hours later this printed
    "newest 24h: 199 run(s) ... 203.9 private minute(s)/day" and `burn_forecast`
    turned it into "spends them in 8.3 day(s)" -- but two thirds of those runs
    were spent before the fold, at three workflows per event instead of one.
    Counted over the newest 12 hours, all of it after the fold, the same repos
    ran 100 runs a day. The honest answer was about fourteen days, not eight.
    Cycle 917 caught it by hand and wrote "re-measure from the post-fix window
    before quoting a date" into the handoff, which is a human standing in for
    an instrument; Cycle 921 (this) put it in the instrument.

    So a second, nested count over `nested_hours` runs beside the first. It is
    **not** a rate to forecast from -- the paragraph on `RECENT_WINDOW_HOURS`
    says why a sub-day window measures the time of day -- and this deliberately
    does not pick a winner between the two, because counts alone cannot
    separate "the cost of a merge changed" from "the owner is asleep". What it
    can say is that the two disagree by more than counting noise, and that a
    single days-to-exhaustion figure is therefore a range rather than a
    measurement. Naming the disagreement is the finding; diagnosing it is a
    cycle's job.

    **The threshold is derived rather than chosen.** Run counts are counting
    statistics, so the noise on the nested count is its Poisson standard
    deviation, `sqrt(n)`, converted to a rate; the windows are called apart
    when they differ by more than twice that. A quiet nested window therefore
    has a wide bar and stays silent, which is right -- with four runs in it
    there is nothing to conclude. The floor of one run is there because
    `sqrt(0)` is zero, and a bar of zero calls every window apart.
    """
    if not repos:
        return ["        NOT JUDGED  no private repo spent a minute this month, so there "
                "is no recent window to measure."], None

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    window_start = now - timedelta(hours=window_hours)
    nested_start = now - timedelta(hours=nested_hours)
    month_runs = 0
    recent_runs = 0
    nested_runs = 0
    priced_minutes = 0.0
    unread = []
    unpaired = []
    for repo in sorted(repos):
        # The billing API names a repo bare -- `platform-config`, not
        # `SokratesAI/platform-config` -- so the owner has to come from the
        # caller. A bare name sent to `/repos/{r}/actions/runs` 404s, and a
        # 404 counted as zero runs would understate the burn silently.
        full = repo if "/" in repo else f"{org}/{repo}"
        for since, bucket in ((month_start, "month"), (window_start, "recent"),
                              (nested_start, "nested")):
            payload, why = _gh_json(
                [f"/repos/{full}/actions/runs?per_page=1&created=%3E%3D{since:%Y-%m-%dT%H:%M:%SZ}"],
                run)
            # `payload` is whatever the endpoint sent. An error body is a
            # list or a string as readily as a dict, and `.get` on one of
            # those is an AttributeError that would take the whole check down
            # rather than reporting a repo it could not count.
            if not isinstance(payload, dict) or not isinstance(payload.get("total_count"), int):
                unread.append(f"{full} ({why or 'no total_count in the response'})")
                break
            if bucket == "month":
                month_runs += payload["total_count"]
                # Only a repo whose runs were actually counted may contribute
                # its minutes to the price. The meter knows what every repo
                # spent; the runs API may not still hold the runs that spent
                # it -- GitHub expires run records, so a repo can carry real
                # minutes and answer zero. Dividing those minutes by another
                # repo's runs inflates minutes-per-run, which inflates the
                # recent rate. Pairing minutes with runs one repo at a time
                # keeps the ratio a ratio of one population.
                if payload["total_count"] > 0:
                    priced_minutes += repos[repo]
                elif repos[repo] > 0:
                    unpaired.append(f"{full} ({repos[repo]:.0f} minute(s), no run record left)")
            elif bucket == "recent":
                recent_runs += payload["total_count"]
            else:
                nested_runs += payload["total_count"]

    lines = []
    if unread:
        lines.append(f"        NOT JUDGED  {len(unread)} private repo(s) could not be counted: "
                     f"{', '.join(sorted(set(unread)))}")
    if unpaired:
        # This one is printed and does NOT stop the rating. A repo whose runs
        # have aged out of the API spent its minutes in the past, which is
        # what the month-to-date rate already accounts for; it says nothing
        # about the newest 24 hours and excluding it is the honest pairing.
        lines.append(f"        left out of the price  {len(unpaired)} repo(s) whose minutes "
                     f"have no run record to pair with: {', '.join(sorted(set(unpaired)))}")
    # **A partial count refuses rather than reporting low, and that is the
    # opposite of what the first draft of this did.** The rate here gates an
    # exit status. If the busiest repo's month call lands and its recent call
    # does not, its runs are in the denominator and its activity is not, and
    # the rate comes out at or near zero -- which prints `within budget` and
    # exits 0 in exactly the spiking-repo case this exists to catch. "Low" is
    # only the safe direction for a number nobody acts on. Everywhere else in
    # this file missing information overstates risk (an unpriced runner SKU is
    # counted and named, an unreadable repo never reads as clean), so a window
    # that could not be swept whole is not a window.
    if unread or month_runs <= 0 or priced_minutes <= 0:
        if unread:
            lines.append("        NOT JUDGED  the recent window is not rated at all, because "
                         "a repo missing from one of its two counts drags the rate toward "
                         "zero rather than shrinking it proportionally, and this rate raises "
                         "the check. The month-to-date rate below is unaffected.")
        else:
            lines.append("        NOT JUDGED  no workflow run was counted this month on any "
                         "repo whose minutes could be paired with it, so minutes per run is "
                         "undefined and the recent window cannot be priced.")
        return lines, None

    minutes_per_run = priced_minutes / month_runs
    window_days = window_hours / 24.0
    rate = recent_runs / window_days * minutes_per_run
    lines.append(
        f"        newest {window_hours:.0f}h: {recent_runs} run(s) at "
        f"{minutes_per_run:.2f} measured private minute(s) per run "
        f"({priced_minutes:.0f} minute(s) over {month_runs} run(s) this month) — "
        f"{rate:.1f} private minute(s)/day.")
    lines.extend(_stationarity(rate, nested_runs, minutes_per_run,
                               window_hours, nested_hours, used_private, allowance))
    return lines, rate


def _stationarity(rate, nested_runs, minutes_per_run,
                  window_hours, nested_hours, used_private, allowance):
    """Lines saying whether the burn held steady across `window_hours`.

    Empty when the nested window agrees with the whole one inside counting
    noise, which is the common case and the one nobody needs a line about.
    See `recent_private_rate` for why this reports a disagreement instead of
    resolving it.

    It never touches the exit status. `rate` is unchanged and `burn_forecast`
    still judges on it, so a burn that was already over the allowance stays
    over and one that was not does not become a finding for having moved.
    What this changes is what the report claims to know.
    """
    nested_days = nested_hours / 24.0
    nested_rate = nested_runs / nested_days * minutes_per_run
    # Poisson noise on the nested count, in the same units as the rate. The
    # floor of one run keeps an empty nested window from having a zero bar.
    noise = max(math.sqrt(nested_runs), 1.0) / nested_days * minutes_per_run
    if abs(nested_rate - rate) <= 2 * noise:
        return []
    remaining = allowance - used_private
    lines = [
        f"        NOT STEADY  the newest {nested_hours:.0f}h ran {nested_runs} run(s), "
        f"which is {nested_rate:.1f} private minute(s)/day against the "
        f"{rate:.1f}/day above — a gap wider than the ±{2 * noise:.1f}/day of counting "
        f"noise on {nested_runs} run(s), so the {window_hours:.0f}h figure above is "
        f"not one steady rate.",
        "        Counts cannot say whether that is a change in what a merge costs or "
        "just the hour of day, so this names the gap rather than picking a rate. "
        "Find the cause before quoting either number.",
    ]
    if remaining > 0 and min(nested_rate, rate) > 0:
        lo = remaining / max(nested_rate, rate)
        hi = remaining / min(nested_rate, rate)
        lines.append(
            f"        so the {remaining:.0f} minute(s) left last somewhere between "
            f"{lo:.1f} and {hi:.1f} day(s), not the single figure below.")
    return lines


def _month_position(now):
    """`(days_in_month, days_elapsed)` — elapsed counts the part-day we are in."""
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    into_today = (now.hour * 3600 + now.minute * 60 + now.second) / 86400.0
    return float(days_in_month), (now.day - 1) + into_today


def burn_forecast(used_private, now, allowance=INCLUDED_PRIVATE_MINUTES,
                  min_elapsed_days=MIN_ELAPSED_DAYS, recent_rate=None,
                  window_hours=RECENT_WINDOW_HOURS):
    """`(lines, over)` — is this month's private-minute burn on track to overrun?

    The owner, `issues.md` 2026-09-01, the morning the meter reset: *"We have
    2000minutes of CI runs for private repos. This must last us until 1.okt
    ... we need to monitor it and adjust our usage of it if its
    oversubscribed. Please monitor it and look at the next days of usage and
    then make a decision if more or less should be done."*

    `billing_meter` already printed the level, and **a level cannot answer
    that question**: 400 minutes spent is comfortable on the 20th and an
    emergency on the 2nd. So this prints the rate, where the rate lands on
    the last day of the month, and how many minutes a day are left if the
    rest of the month is to fit.

    **It refuses to forecast from a part-day, and the refusal is the point.**
    At 08:00 on the 1st the month is 0.33 days old; the ten private minutes
    on the meter right now are one morning of me re-running builds by hand to
    prove the block had lifted, and dividing them by a third of a day
    projects 900 minutes for September from a sample that is not a habit.
    Under `min_elapsed_days` this prints the level, the daily budget and says
    plainly that it is not forecasting yet -- the same refusal
    `tools.host_memory_trend` makes until its ledger is six hours deep.

    `over` is only ever True on a real projection, never on the refusal.

    **Two rates, judged separately, and `over` is either of them.**
    `recent_rate` comes from `recent_private_rate` above and is the newest
    `window_hours` of burn; the month-to-date average here is the whole month
    so far. They answer different questions and merging them into one number
    would lose exactly the information that matters — the average says whether
    the month is on track given everything already spent, and the recent rate
    says whether what I am doing *now* fits. Measured 2026-09-04 they were
    80.7 and roughly 200 minutes a day, and a cycle reading only the first
    would have concluded the fold that morning had solved it.

    The month-to-date rate is never *replaced* by the recent one. A quiet day
    does not undo minutes already on the meter, so a recent rate below budget
    while the average is over is still over — the allowance is cumulative.
    """
    days_in_month, elapsed = _month_position(now)
    remaining_days = days_in_month - elapsed
    budget_per_day = allowance / days_in_month
    lines = [
        f"BURN    {used_private:.0f} of {allowance:.0f} included private minute(s) used, "
        f"{elapsed:.2f} of {days_in_month:.0f} day(s) into the month, "
        f"{remaining_days:.2f} day(s) left.",
        f"        the flat budget is {budget_per_day:.0f} private minute(s)/day; "
        f"{(allowance - used_private) / remaining_days:.0f}/day is what is left over the "
        f"rest of the month." if remaining_days > 0 else
        "        the month is over; there is no rest of it to spread the remainder over.",
    ]
    if elapsed < min_elapsed_days:
        lines.append(
            f"        NOT FORECASTING — {elapsed:.2f} day(s) of the month is under the "
            f"{min_elapsed_days:.0f}-day floor. A rate divided out of a part-day is an "
            f"extrapolation from one morning, not a habit. This line becomes a forecast "
            f"on its own.")
        # The recent window is not an extrapolation from a part-month -- it is
        # its own measurement over its own hours -- so the refusal above does
        # not silence it. This is the one branch where the recent rate is the
        # *only* rate there is.
        if recent_rate is not None and used_private + recent_rate * remaining_days > allowance:
            lines.append(
                f"        OVERSUBSCRIBED AT THE CURRENT RATE — the month-to-date average is "
                f"not forecastable yet, but the newest {window_hours:.0f}h alone spends the "
                f"remaining {allowance - used_private:.0f} minute(s) in "
                f"{(allowance - used_private) / recent_rate:.1f} day(s) against "
                f"{remaining_days:.1f} day(s) of month left.")
            return lines, True
        return lines, False

    rate = used_private / elapsed
    projected = rate * days_in_month
    lines.append(
        f"        rate {rate:.1f} private minute(s)/day, which lands at {projected:.0f} "
        f"for the month against the {allowance:.0f} included.")
    over_recent = False
    if recent_rate is not None:
        remaining_budget = allowance - used_private
        recent_projected = used_private + recent_rate * remaining_days
        lines.append(
            f"        at the newest-{window_hours:.0f}h rate of {recent_rate:.1f}/day the "
            f"month lands at {recent_projected:.0f} against the {allowance:.0f} included.")
        if recent_projected > allowance:
            over_recent = True
            burns_out = remaining_budget / recent_rate if recent_rate > 0 else float("inf")
            lines.append(
                f"        OVERSUBSCRIBED AT THE CURRENT RATE — {remaining_budget:.0f} minute(s) "
                f"left, and the newest {window_hours:.0f}h spends them in {burns_out:.1f} day(s) "
                f"against {remaining_days:.1f} day(s) of month remaining. This is what merging "
                f"costs today, not what it averaged.")

    if projected > allowance:
        lines.append(
            f"        OVERSUBSCRIBED — at this rate the allowance runs out with "
            f"{(allowance / rate):.1f} of {days_in_month:.0f} day(s) gone. Cut private-repo "
            f"CI or move a repo public; the biggest private spender is named above.")
        return lines, True
    lines.append(
        f"        within budget — {projected / allowance * 100:.0f}% of the allowance at "
        f"this rate, with {allowance - projected:.0f} minute(s) of headroom.")
    return lines, over_recent


def billing_meter(org, run=subprocess.run, now=None):
    """`(lines, over)` or `(None, why)` — what the org's Actions meter says, and where the month lands.

    `blocked_repo` above answers *that* GitHub is refusing to start jobs and
    quotes the annotation, which says two different things joined by an
    "or": *"recent account payments have failed or your spending limit needs
    to be increased."* Those have different owners and different fixes, and
    the annotation does not say which one it is. So every cycle that hit
    this went and derived it by hand — 223, 225, 227, 230, 231, 232, 233,
    364 and 734 all re-read the same endpoint — and the answer then lived in
    a board row rather than in an instrument.

    **Measured Cycle 734, and the number is not the one the row assumed.**
    `SokratesAI` burned 6,257 Actions minutes in August, and **11 of them
    were on a private repo.** The other 6,246 are on repos that were made
    public in Cycle 235 precisely so their minutes would stop counting.
    Every item in the ledger is discounted to `netAmount` 0.00, in August,
    July and June, so nothing is owed and no payment can have failed. Yet a
    re-run of a `platform-config` job at 22:07 UTC on 31 August was refused
    with that same annotation, with 1,989 of the 2,000 included private
    minutes unused.

    The only model that fits both facts is that the gate counts **every**
    metered minute, public ones included, against the 2,000 — the cumulative
    total crossed 2,000 on 15 August, which is the day builds started dying
    in three seconds. That is an inference from two measurements and not
    something GitHub documents, so this prints the numbers and says which
    half each one answers rather than printing the conclusion.

    It reads the current month, because that is the window the allowance is
    scoped to. Repos are split by visibility from `/orgs/{org}/repos`, one
    page of 100; more than that and the split is flagged partial rather than
    quietly wrong.

    **Two corrections, Cycle 750, both from the owner's 1 September capture.**

    First, this used to sum `quantity` across every `product == "actions"`
    row, and one of those rows is `Actions storage`, whose `unitType` is
    `GigabyteHours`. Gigabyte-hours were being added to a minute count. It
    happened to round away — September's storage rows are hundredths of a
    unit — so the number looked right and was the wrong kind of thing. Only
    `unitType == "Minutes"` counts as a minute now, and storage gets its own
    line rather than being dropped, because a row nobody prints is a row
    nobody can question.

    Second, minutes are not all worth one minute: GitHub charges the
    allowance 2x for a Windows runner and 10x for macOS. `MINUTE_MULTIPLIER`
    holds those, and a SKU it has never seen is counted at 1x **and named**,
    because the direction that costs is understating the burn.
    """
    now = now or datetime.now(timezone.utc)
    usage, why = _gh_json(
        [f"/orgs/{org}/settings/billing/usage?year={now.year}&month={now.month}"], run)
    if not isinstance(usage, dict) or not isinstance(usage.get("usageItems"), list):
        return None, why or "the usage endpoint returned no usageItems"

    repos, repo_why = _gh_json([f"/orgs/{org}/repos?per_page=100&type=all"], run)
    visibility, partial = {}, repo_why
    if isinstance(repos, list):
        for repo in repos:
            if isinstance(repo, dict) and repo.get("name"):
                visibility[repo["name"]] = "public" if not repo.get("private") else "private"
        if len(repos) >= 100:
            partial = "the org has 100 or more repos and only the first page was read"
    else:
        partial = repo_why or "could not read repo visibility"

    minutes = {"private": 0.0, "public": 0.0, "unknown": 0.0}
    per_repo = {}
    storage_units = 0.0
    unpriced_skus = set()
    net = 0.0
    for item in usage["usageItems"]:
        if not isinstance(item, dict) or item.get("product") != "actions":
            continue
        try:
            quantity = float(item.get("quantity") or 0)
            net += float(item.get("netAmount") or 0)
        except (TypeError, ValueError):
            continue
        if item.get("unitType") != "Minutes":
            storage_units += quantity
            continue
        sku = item.get("sku") or ""
        rate = MINUTE_MULTIPLIER.get(sku)
        if rate is None:
            unpriced_skus.add(sku or "(unnamed sku)")
            rate = 1.0
        charged = quantity * rate
        name = item.get("repositoryName") or "(unnamed repo)"
        where = visibility.get(item.get("repositoryName"), "unknown")
        minutes[where] += charged
        if where == "private":
            per_repo[name] = per_repo.get(name, 0.0) + charged

    total = sum(minutes.values())
    lines = [
        f"METER   {org}, {now:%B %Y} so far: {total:.0f} metered Actions minute(s) — "
        f"{minutes['private']:.0f} on private repo(s), {minutes['public']:.0f} on public, "
        f"{minutes['unknown']:.0f} on repo(s) whose visibility could not be read.",
        f"        net owed ${net:.2f} — that is the 'payments have failed' half of the "
        f"annotation, and $0.00 means nothing is unpaid.",
        f"        the 2,000 included minutes are scoped to private repos, so "
        f"{minutes['private']:.0f} of 2,000 is the allowance half. If jobs are refused "
        f"with that allowance unspent, the gate is counting the public minutes too.",
    ]
    if storage_units:
        lines.append(f"        plus {storage_units:.3f} GigabyteHour(s) of Actions storage, "
                     f"which is a different unit and is not a minute.")
    for sku in sorted(unpriced_skus):
        lines.append(f"        NOT JUDGED  runner SKU `{sku}` has no published multiplier here "
                     f"and was counted at 1x; if it is Windows or macOS the burn above is low.")
    for repo, spent in sorted(per_repo.items(), key=lambda pair: -pair[1]):
        lines.append(f"        {spent:>6.0f} private minute(s) — {repo}")
    if per_repo:
        biggest = max(per_repo, key=lambda name: per_repo[name])
        lines.extend(floor_share(f"{org}/{biggest}", run=run))
    if partial:
        lines.append(f"        partial: {partial}")

    recent_lines, recent_rate = recent_private_rate(
        per_repo, minutes["private"], org, now, run=run)
    lines.extend(recent_lines)
    forecast_lines, over = burn_forecast(minutes["private"], now, recent_rate=recent_rate)
    lines.extend(forecast_lines)
    return lines, over


#: How many of the biggest private spender's newest runs `floor_share` samples.
#: One `gh` call per run on top of one to list them, so this is a real cost
#: inside `preflight` -- twenty is what keeps it near two seconds concurrently.
#: It is a sample and the report says so; the thing being estimated is a
#: ratio, and job durations on this org cluster tightly (median 26s on
#: 2026-09-04) rather than spreading, so twenty is plenty to see the shape.
FLOOR_SAMPLE_RUNS = 20


def floor_share(repo, run=subprocess.run, sample=FLOOR_SAMPLE_RUNS,
                max_workers=DEFAULT_MAX_WORKERS):
    """`lines` — how much of a repo's bill is the per-job floor rather than compute.

    The owner, comments board 2026-09-04 21:19: *"Explain to me, what repos
    are using so much minutes and why?"* The meter above answers **what** —
    `platform-config`, 277 of 312 private minutes this month. It has never
    been able to answer **why**, and the why is not a bigger version of the
    what: it is that GitHub bills a private job in whole minutes, rounded up,
    so a job's existence costs a minute and its duration very often costs
    nothing at all.

    That distinction has already cost this loop a cycle. Cycle 924 cut the
    secret scan off `platform-config` expecting minutes back, and got none —
    the scan ran about eleven seconds inside a job that billed a whole minute
    either way. The lesson was written into a handoff paragraph, which is
    exactly where a measured fact goes to die: the next cycle to look at this
    bill reads a per-repo minute count with nothing beside it saying that
    shortening a step cannot move it.

    So this prints the ratio. Measured against `platform-config`'s newest 24
    hours on 2026-09-04: **161 jobs billed 161 minutes for 60 minutes of
    compute, and not one of the 161 ran past a minute.** Roughly five eighths
    of that bill is the floor, and the only lever on it is the number of jobs
    — which here is two per merged pull request, one on the pull request and
    one on the resulting `main` commit.

    **The verdict can come out either way and that is the point.** A repo
    whose jobs run four minutes each would report a floor share near zero and
    a line saying a shorter step *does* move the bill. A check whose answer is
    fixed in advance measures nothing (see `stalled_runs` for the version of
    this that bit), so the `over` count is printed whatever it is, and it is
    what picks the closing sentence.

    Deliberately never raises. This explains a bill; `burn_forecast` above is
    what decides whether the bill is a problem, and two things raising on one
    fact reads as two findings.
    """
    runs, why = _gh_json(
        [f"/repos/{repo}/actions/runs?per_page={sample}", "-q",
         "[.workflow_runs[] | .id]"], run)
    if runs is None:
        return [f"        FLOOR   could not sample {repo}'s runs: {why}"]
    if not runs:
        return [f"        FLOOR   NOT JUDGED  {repo} has no run in the API to sample, so "
                f"the shape of its bill cannot be measured from here."]

    def jobs_of(run_id):
        return _gh_json(
            [f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100", "-q",
             "[.jobs[] | {conclusion, started_at, completed_at}]"], run)

    fetched, unreadable = [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        for jobs, job_why in pool.map(jobs_of, runs):
            if jobs is None:
                unreadable += 1
                continue
            fetched.extend(jobs)

    seconds, undated = [], 0
    for job in fetched:
        if not isinstance(job, dict) or job.get("conclusion") == "skipped":
            continue
        started, completed = job.get("started_at"), job.get("completed_at")
        if not started or not completed:
            undated += 1
            continue
        try:
            began = datetime.fromisoformat(started.replace("Z", "+00:00"))
            ended = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        except ValueError:
            undated += 1
            continue
        seconds.append(max(0.0, (ended - began).total_seconds()))

    if not seconds:
        return [f"        FLOOR   NOT JUDGED  none of {repo}'s newest {len(runs)} run(s) "
                f"carried a job with both a start and an end, so nothing was measured."]

    billed = sum(max(1, math.ceil(one / 60.0)) for one in seconds)
    compute = sum(seconds) / 60.0
    over = sum(1 for one in seconds if one > 60.0)
    ordered = sorted(seconds)
    median = ordered[len(ordered) // 2]
    lines = [
        f"        FLOOR   {repo}, newest {len(runs)} run(s): {len(seconds)} job(s) billed "
        f"{billed} minute(s) for {compute:.1f} minute(s) of compute — "
        f"{(1 - compute / billed) * 100:.0f}% of that is the whole minute GitHub "
        f"rounds every private job up to.",
    ]
    if over:
        lines.append(
            f"        {over} of {len(seconds)} job(s) ran past a minute (median {median:.0f}s, "
            f"longest {ordered[-1]:.0f}s), so a slower step here really does cost extra "
            f"minutes and shortening one can move this bill.")
    else:
        lines.append(
            f"        not one of the {len(seconds)} ran past a minute (median {median:.0f}s, "
            f"longest {ordered[-1]:.0f}s), so no step here is long enough to bill a second "
            f"minute and shortening one saves nothing. The only lever is the number of "
            f"jobs — {len(seconds) / len(runs):.1f} per run, and a run per pull request "
            f"plus a run per resulting commit.")
    if unreadable:
        lines.append(f"        partial: {unreadable} of {len(runs)} sampled run(s) would not "
                     f"give up their jobs, so the counts above are a floor, not the total.")
    if undated:
        lines.append(f"        partial: {undated} job(s) carried no start or end and were left "
                     f"out rather than counted as zero.")
    return lines


def _sweep_repo(repo, grace_minutes, run, now):
    """Every per-repo probe for one repo, in order.

    Returns `(lines, blocked, unreadable, cannot_go_green)`. Lifted out of
    `check` Cycle 688 for one reason: it is three `gh` calls per repo, the
    sweep below went from five repos to the whole org, and serially that is
    a minute and a half inside a `preflight` whose whole budget is fifty
    seconds. The probes stay in order and a probe that cannot read still
    short-circuits the two after it — an unreadable repo is unreadable, and
    asking it two more questions does not make it less so.
    """
    verdicts, why = stalled_runs(repo, grace_minutes, run, now)
    if verdicts is None:
        return [f"COULD NOT READ  {repo}: {why}"], False, True, False
    lines = [text for _state, text in verdicts]
    blocked = any(state == "stalled" for state, _text in verdicts)

    verdicts, why = unrun_pushes(repo, grace_minutes, run, now)
    if verdicts is None:
        lines.append(f"COULD NOT READ  {repo}: {why}")
        return lines, blocked, True, False
    lines.extend(text for _state, text in verdicts)
    blocked = blocked or any(state == "norun" for state, _text in verdicts)

    verdicts, why = blocked_repo(repo, run)
    if verdicts is None:
        lines.append(f"COULD NOT READ  {repo}: {why}")
        return lines, blocked, True, False
    lines.extend(text for _state, text in verdicts)
    cannot = any(state == "blocked" for state, _text in verdicts)
    return lines, blocked, False, cannot


def _repos_to_sweep():
    """`(repos, unplaceable, notes, incomplete)` — every non-archived repo in
    the org, not just the ones with a checkout here.

    **This used to be the workspace checkouts alone, and the reason written
    down for that was measurably wrong.** It said "a repo with no checkout is
    one this cycle cannot push to anyway, so its queue says nothing about
    whether *my* work can land". Cycle 688 opened and merged a pull request
    on `SokratesAI/sokrates-cli` — a repo no cycle has ever cloned — with the
    `create_pr` tool, which commits and opens in one call and needs no
    checkout at all. So the premise was false, and the cost of it was that
    `sokrates-cli`'s default branch had been failing since 2026-08-28 and no
    instrument in this loop said so; the only cycle that could ever have
    found it was one that happened to look at that repo for something else,
    which is the accidental-noticing `security_alerts` was widened to the org
    to kill, for the same reason, in Cycle 432.

    That widening is where this now gets its repos: `security_alerts`
    derives the orgs from the checkouts rather than naming one, so nothing
    here hardcodes `SokratesAI`, and the workspace repos are unioned back in
    so a checkout outside any of those orgs cannot drop out of the sweep.
    """
    from tools.security_alerts import _repos_to_sweep as org_sweep

    # `check`'s `run` is deliberately not forwarded, and taking no injector
    # here is the point: this module's `run` is `subprocess.run` and returns a
    # `CompletedProcess`, while `security_alerts` injects a callable returning
    # `(code, out, err)`. Handing one to the other raised
    # `FileNotFoundError: 'repo'` on the first live run of this widening —
    # every test here stubs this whole function, so nothing but running it
    # against the real org would have found that.
    return org_sweep()


def check(opener=urllib.request.urlopen, run=subprocess.run,
          grace_minutes=DEFAULT_GRACE_MINUTES, repos=None, now=None,
          max_workers=DEFAULT_MAX_WORKERS):
    """Return `(exit_status, lines)`."""
    lines, unreadable, blocked, cannot_go_green = [], [], False, []
    oversubscribed = False

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
        repos, unplaceable, notes, incomplete = _repos_to_sweep()
        for clone in unplaceable:
            lines.append(f"⚠ {clone}: could not place this checkout on GitHub, not swept")
        lines.extend(notes)
        if incomplete:
            unreadable.append("org listing")
    if not repos:
        lines.append("COULD NOT READ: no checkout here names a GitHub repo, so no queue was measured.")
        unreadable.append("workspace")

    # Concurrent, then replayed in repo order: the report has to read the same
    # way on every run, and `as_completed` order is whichever `gh` answered first.
    results = {}
    if repos:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_sweep_repo, repo, grace_minutes, run, now): repo
                       for repo in repos}
            for future in concurrent.futures.as_completed(futures):
                results[futures[future]] = future.result()
    for repo in repos:
        repo_lines, repo_blocked, repo_unreadable, repo_cannot = results[repo]
        lines.extend(repo_lines)
        blocked = blocked or repo_blocked
        if repo_unreadable:
            unreadable.append(repo)
        if repo_cannot:
            cannot_go_green.append(repo)

    lines.append(f"Swept {len(repos)} repo(s), grace {grace_minutes}m: "
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

    # The meter used to run *only* under `cannot_go_green`, because it was built
    # to explain a refusal. The refusals stopped on 2026-09-01 and the meter
    # therefore stopped running -- so on the exact morning the owner asked to have
    # the 2,000 minutes monitored, nothing was reading them. It runs every sweep
    # now. Two `gh` calls, once per org, whatever the repos did.
    for org in sorted({repo.split("/")[0] for repo in repos if "/" in repo}):
        meter_lines, meter_over = billing_meter(org, run=run, now=now)
        if meter_lines is None:
            lines.append(f"METER   could not read {org}'s Actions meter: {meter_over}")
            unreadable.append(f"{org} meter")
        else:
            lines.extend(meter_lines)
            oversubscribed = oversubscribed or meter_over

    if blocked:
        lines.append("A merge cannot complete right now. Pick a cycle that does not end in one.")
        return 2, lines
    if oversubscribed:
        lines.append("The private-minute burn is projected past the monthly allowance. "
                     "That is the one thing the owner asked to be told about, so it raises.")
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
                        help="sweep this repo instead of every repo in the org (repeatable)")
    args = parser.parse_args(argv)
    status, lines = check(grace_minutes=args.grace_minutes, repos=args.repos)
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
