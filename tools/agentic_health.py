"""Are this org's agentic workflows still running, or have they quietly died?

Cycle 466. The owner's capture asks for every repo to be self-documenting
with gh-aw. One repo already is: `SokratesAI/sokrates-docs` carries a
`docs-sync` gh-aw workflow that reads four repos and opens a docs PR. It
last produced a successful run on **2026-08-07**, and every scheduled run
since -- 08-07, 08-14, 08-21 -- failed. Nineteen days, three failures,
and the only way to know was to open that repo's Actions tab, which no
step in `prompt.md` does.

That is the `security_alerts` shape one more time: a real finding that is
visible only to whoever happens to look somewhere for another reason. A
scheduled agentic workflow is worse than a Dependabot alert in one
specific way -- it is *supposed* to be silent when it has nothing to do.
`noop` is a legitimate outcome for `docs-sync`, so "no pull request
appeared this week" reads exactly like "the docs were already accurate".
Nothing distinguishes a healthy quiet week from a dead one except the run
history, and nothing here read the run history.

    python3 -m tools.agentic_health

**What counts as agentic.** gh-aw compiles `foo.md` into `foo.lock.yml`
and it is the lock file that GitHub actually runs, so a workflow whose
path ends in `.lock.yml` is one. That is the marker rather than the name,
because the name is the author's and drifts; the suffix is the
compiler's.

**The one thing this deliberately misses**, said out loud rather than
discovered later: gh-aw also generates `agentics-maintenance.yml`, which
carries a gh-aw header but not the `.lock.yml` suffix. It is the
framework's own housekeeping rather than a workflow somebody wrote to do
a job, so its failure is not "an automation is dead" -- and catching it
would mean fetching the body of every workflow file in every repo, which
is roughly eighty extra API calls to find a file whose name is already
known. If that judgement turns out wrong, the fix is a second suffix
here, not a content scan.

**Five verdicts, kept apart, for `security_alerts`' reason.** `healthy`
means the most recent *completed* run succeeded. `failing` means it did
not. `error` means the run history could not be read at all -- which is
no instrument, not a healthy workflow, and must never print what a clean
sweep prints. A workflow with no runs yet is `never-run` and is reported
without raising the status: a workflow merged an hour ago has not failed.

`blocked` is the fourth, added Cycle 472, and it exists because this tool
told three cycles the wrong story. `docs-sync`'s three-run streak is not
one failure repeated: the 08-07 and 08-14 runs executed the agent and it
died inside `Execute Gemini CLI` (the job sets an `ai_credits_rate_limit_error`
output), while the 08-21 run failed **two seconds in with no step
executed at all** and one annotation on it: *"The job was not started
because recent account payments have failed or your spending limit needs
to be increased."* Those are different problems with different owners,
and folding them into `3 completed run(s) in a row ended 'failure'` sent
two cycles hunting for a Gemini key that could not have fixed a run which
never started. So when the newest failing run executed no steps, this
reads the annotation and says so.

**A blocked run does not raise the exit status**, which is
`security_alerts`' ALREADY-FIXED contract exactly: there is no pull
request that fixes an account's spending limit, so printing it as
actionable makes every cycle re-derive it. It prints loudly under its own
heading with the annotation quoted. The owner already knows -- 2026-08-26,
on his comments board: *"I do not want to pay for the ci runs. We just
have to wait until September 1st."* Exit 0 here means "nothing for a cycle
to act on", not "nothing is wrong".

`lifted` is the fifth, added Cycle 514, and it exists because `blocked`
inherits its whole verdict from an annotation on one old run and never
asks whether that annotation is still true. `sokrates-docs` went **public**
on 2026-08-25 (issue #109), Actions minutes are free on public repos, and
that repo has executed jobs to success twice since -- 08-25 20:19 and
08-26 02:10 UTC. The 08-21 block was over. This tool still printed
*"Nothing here is fixable by a pull request; the account is"*, three
cycles of handoff still said docs-sync could not be retested before
1 September. A manual dispatch at 2026-08-27 01:46 UTC started
immediately, ran for six minutes and died inside `Execute Gemini CLI` --
which is the *other* half of the streak, and the only half still real.
Nobody was ever going to notice, because `blocked` was designed not to be
actionable.

So a block is only current if nothing contradicts it. The contradiction
worth trusting is a **successful run on the same repo, created after the
blocked one** -- a `success` conclusion is a thing only a job that really
executed can produce, so a private repo out of Actions minutes cannot
fake one. When that exists the verdict becomes `lifted` and **does raise
the exit status**, because unlike a spending limit there is now something
a cycle can do in one command: dispatch the workflow. That is the same
both-directions test `prompt.md` asks for -- what would I have seen if
the block had lifted? The same report, which is the whole defect.

**A run still in progress is not a verdict.** The newest run can be
`in_progress` for ten minutes on a scheduled sweep, and reading that as
either outcome would make this tool's answer depend on when it was
called. In-progress runs are skipped and the newest *completed* one
decides; if every run on record is still going, that is `never-run` with
a note, not a guess.

**A `failing` verdict now says how it died, and that is Cycle 598.**
`docs-sync` succeeded on a manual dispatch at 2026-08-28 03:37 UTC and
then its scheduled run at 17:22 failed. This tool printed *"1 completed
run(s) in a row ended 'failure'"* -- the same sentence it prints for a
bug in the prompt, for an exhausted upstream API quota, and for a run
GitHub killed on a step timeout. The real cause was the third: the Gemini
free tier refused it mid-run (250,000 input tokens/day on
`gemini-3.5-flash-lite`) and the step then hit *"The action 'Execute
Gemini CLI' has timed out after 20 minutes."* Nothing here said so, so
reading it meant opening the log by hand, which is exactly the
accidental-noticing this module exists to replace. The jobs payload it
already fetches names the failing job and step; one more call gets
GitHub's own failure annotation. Same lesson as `blocked` one level
further in: a streak counter merges causes.

**A manual dispatch no longer decides the verdict, and that is Cycle
622.** A workflow with a `schedule` is judged on its scheduled runs
alone. `schedule_health` learned this one layer down -- it judges "only
against runs GitHub started itself", because manual dispatches are
precisely what made a dead workflow look alive -- and this module did
not. Measured on `docs-sync`: every scheduled run since 2026-08-07 has
failed and not one has succeeded, while this printed *"1 completed
run(s) in a row ended 'failure'; last succeeded 2026-08-28"*, and the
handoff written off that sentence called a three-week outage a new
failure. The green run was Cycle 618's own `workflow_dispatch`. Note
the shape, because it is worse than an ordinary blind spot: the report
above asks a cycle to *dispatch the workflow* as the recommended
action, so the fix this tool suggested was resetting the counter this
tool judges by. The dispatch result is demoted, never dropped -- it is
real evidence about whether the workflow *can* pass, which is a
different question from whether it *is* passing -- and a workflow with
no scheduled runs on record is still judged on everything it has.

Exit 2 means at least one agentic workflow is dead in a way a cycle can
act on -- failing on its own terms, or held down by a block that has
since lifted and never re-run. Exit 1 means something was unreadable.
Exit 0 means there is nothing for a cycle to act on, and it says which
repos answered so "checked and clean" can never be confused with "never
looked".
"""

import json
import subprocess
import sys
from datetime import datetime

# How many runs back to read per workflow. Enough to say "failed the last
# three scheduled runs and last succeeded on the 7th" rather than just
# "the newest one is red", which is the sentence that tells you whether
# this is a blip or a fortnight.
_RUN_WINDOW = 12


def _gh(args):
    """Run `gh` and return `(exit_code, stdout, stderr)`."""
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def agentic_workflows(repo, run=None):
    """`(workflows, error)` -- the gh-aw lock workflows in one repo.

    A repo with no Actions at all answers with an empty list and no error;
    that is a true measurement and not a failure. A repo the call could not
    read answers with an error, because those two are not the same thing
    and this module's whole job is refusing to merge them.
    """
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/actions/workflows", "--paginate"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return [], blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        payload = json.loads(out)
    except ValueError:
        return [], "gh returned something that is not JSON"
    entries = payload.get("workflows") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return [], "gh returned no workflow list"
    found = []
    for entry in entries:
        path = (entry or {}).get("path") or ""
        if not path.endswith(".lock.yml"):
            continue
        found.append(
            {
                "repo": repo,
                "path": path,
                "name": entry.get("name") or path.rsplit("/", 1)[-1],
                "state": entry.get("state") or "unknown",
            }
        )
    return found, None


def run_history(workflow, run=None):
    """`(runs, error)` -- the newest runs of one workflow, newest first.

    Only `completed` runs carry a conclusion, so the caller filters; this
    returns what GitHub said, unedited, so the filtering is visible in one
    place rather than smeared across two.
    """
    code, out, err = (run or _gh)(
        [
            "run",
            "list",
            "--repo",
            workflow["repo"],
            "--workflow",
            workflow["path"].rsplit("/", 1)[-1],
            "--limit",
            str(_RUN_WINDOW),
            "--json",
            "conclusion,status,createdAt,event,databaseId",
        ]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return [], blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        payload = json.loads(out)
    except ValueError:
        return [], "gh returned something that is not JSON"
    if not isinstance(payload, list):
        return [], "gh returned a JSON object where a list of runs was expected"
    return payload, None


def _failure_annotation(repo, job_id, run=None):
    """The first `failure`-level annotation GitHub left on a job, or `None`.

    **The level filter is the whole function.** The 2026-08-28 `docs-sync`
    run carries two annotations and the first one is a *warning* about
    Node.js 20 being deprecated; the second is the actual cause, *"The
    action 'Execute Gemini CLI' has timed out after 20 minutes."* Taking
    the first non-empty message -- which is what this module did while
    only the never-started path used it -- would have published a routine
    deprecation notice as the reason a workflow is dead.

    Unreadable is `None` rather than an error: the caller always already
    has a finding, and this is detail on top of it.
    """
    if job_id is None:
        return None
    code, out, _err = (run or _gh)(
        ["api", f"repos/{repo}/check-runs/{job_id}/annotations"]
    )
    if code != 0:
        return None
    try:
        notes = json.loads(out)
    except ValueError:
        return None
    if not isinstance(notes, list):
        return None
    for note in notes:
        note = note or {}
        if (note.get("annotation_level") or "").lower() != "failure":
            continue
        message = (note.get("message") or "").strip()
        if message:
            return message
    return None


def start_failure(repo, run_id, run=None):
    """`(detail, error)` -- what actually happened to the newest failed run.

    A run where *no* job executed a single step never started, and GitHub
    puts the reason in a check-run annotation rather than in any log --
    which is why `gh run view --log-failed` answers `log not found` on
    exactly the run you most want to read. That is
    `{"started": False, "reason": ...}`, and it is what turns a `failing`
    verdict into `blocked`.

    A run that *did* execute steps is `{"started": True, ...}` and stays
    `failing` -- but Cycle 598 is the third time this module has learned
    that one number merges causes. `1 completed run(s) in a row ended
    'failure'` is what a timeout, an exhausted upstream API quota and a
    genuine bug all print, and they have three different owners. The
    2026-08-28 run died on *"The action 'Execute Gemini CLI' has timed out
    after 20 minutes."* after the Gemini free tier refused it mid-run, and
    reading that took a cycle opening the log by hand -- which is the
    accidental-noticing this whole module exists to replace. So the failing
    branch now carries the job, the step and GitHub's own failure
    annotation, from the payload it was already fetching plus one call.

    Two calls, and only on a workflow already known to be failing, so the
    common sweep pays nothing. An unreadable annotation is not an error
    here: the finding is already in hand and the quote is detail on top.
    """
    code, out, err = (run or _gh)(["api", f"repos/{repo}/actions/runs/{run_id}/jobs"])
    if code != 0:
        blob = (err or out or "").strip()
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        payload = json.loads(out)
    except ValueError:
        return None, "gh returned something that is not JSON"
    jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(jobs, list) or not jobs:
        return None, "gh returned no job list"

    if not any((job or {}).get("steps") for job in jobs):
        quoted = _failure_annotation(repo, (jobs[0] or {}).get("id"), run=run)
        if quoted:
            return {
                "started": False,
                "reason": f"the job never started -- GitHub says: {quoted}",
            }, None
        return {
            "started": False,
            "reason": "the job never started, and GitHub gave no reason on the run",
        }, None

    # It ran. Name where it died, from the payload already in hand.
    failed_job = next(
        (job for job in jobs if (job or {}).get("conclusion") == "failure"), None
    )
    detail = {"started": True, "job": None, "step": None, "quote": None}
    if failed_job is None:
        return detail, None
    detail["job"] = failed_job.get("name")
    steps = failed_job.get("steps")
    if isinstance(steps, list):
        detail["step"] = next(
            (
                (step or {}).get("name")
                for step in steps
                if (step or {}).get("conclusion") == "failure"
            ),
            None,
        )
    detail["quote"] = _failure_annotation(repo, failed_job.get("id"), run=run)
    return detail, None


def failure_sentence(detail):
    """The one clause a `failing` verdict gains, or `""` if there is nothing to add.

    Deliberately additive: the streak and the last green date are still
    the finding, and this says *how* the newest one died so a cycle does
    not have to open the log to find out whether it is even actionable.
    """
    where = ""
    if detail.get("step") and detail.get("job"):
        where = f"failed at step '{detail['step']}' in job '{detail['job']}'"
    elif detail.get("step"):
        where = f"failed at step '{detail['step']}'"
    elif detail.get("job"):
        where = f"failed in job '{detail['job']}'"
    quote = detail.get("quote")
    if where and quote:
        return f"{where} -- GitHub says: {quote}"
    if where:
        return where
    if quote:
        return f"GitHub says: {quote}"
    return ""


def _as_timestamp(text):
    """An ISO-8601 UTC stamp as a comparable value, or `None` if it is not one."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def block_has_lifted(repo, blocked_at, run=None):
    """`(evidence, error)` -- proof the repo has executed a job since the block.

    One call. `?status=success` asks GitHub for runs on the repo that
    finished green, across every workflow, and a green conclusion is the
    one thing a repo GitHub is refusing to start jobs for cannot produce.
    Comparing against a *failed* newer run would prove nothing -- that is
    what a repo still blocked looks like.

    **Repo-wide is deliberate.** A spending limit is an account fact, not
    a workflow fact, so any workflow in the repo getting off the ground is
    the evidence. This never claims the blocked workflow itself now works
    -- that is the dispatch the report asks for.

    **It takes the maximum rather than the first row.** GitHub documents
    no sort order for `GET /repos/{repo}/actions/runs`; it happens to
    answer newest-first today, and building on that would be assuming an
    ordering nobody promised. `max()` over a page costs the same call.

    **Both timestamps are parsed, not compared as text.** `blocked_at`
    comes from `gh run list --json createdAt` and these come from the REST
    API's `created_at` -- two producers, and a fractional second from
    either would sort wrongly as a string (`...07.5Z` < `...07Z`) while
    parsing correctly. Anything unparseable is an error rather than a
    quiet miss.

    Returns `(None, None)` when every green run predates the block -- the
    honest answer, and the one that leaves the `blocked` verdict alone.
    """
    if not blocked_at:
        return None, "the blocked run carried no createdAt to compare against"
    blocked_ts = _as_timestamp(blocked_at)
    if blocked_ts is None:
        return None, f"could not read the blocked run's date {blocked_at!r}"
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/actions/runs?status=success&per_page=30"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        payload = json.loads(out)
    except ValueError:
        return None, "gh returned something that is not JSON"
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        return None, "gh returned no run list"
    newest, newest_ts = None, None
    for entry in runs:
        when = (entry or {}).get("created_at")
        if not when:
            continue
        stamp = _as_timestamp(when)
        if stamp is None:
            return None, f"could not read a successful run's date {when!r}"
        if newest_ts is None or stamp > newest_ts:
            newest, newest_ts = when, stamp
    if newest_ts is None or newest_ts <= blocked_ts:
        return None, None
    return newest, None


def verdict_for(workflow, runs):
    """Fold a run list into one verdict plus the numbers behind it.

    The consecutive-failure count and the last green date are the whole
    point: "red" is a fact about one run and tells you nothing about
    whether to act, while "red three scheduled runs running, last green
    nineteen days ago" is the finding.
    """
    completed = [r for r in runs if (r or {}).get("status") == "completed"]
    if not completed:
        note = (
            "every run on record is still in progress"
            if runs
            else "no runs on record yet"
        )
        return {"verdict": "never-run", "note": note, "failures": 0, "last_good": None}

    judged, kind, aside = _runs_to_judge(completed)
    label = "completed" if kind == "any" else "scheduled"

    last_good = next(
        (r["createdAt"] for r in judged if r.get("conclusion") == "success"), None
    )
    if judged[0].get("conclusion") == "success":
        return {
            "verdict": "healthy",
            "note": _with_aside(
                f"last {label} run succeeded, {judged[0]['createdAt']} (UTC)", aside
            ),
            "failures": 0,
            "last_good": last_good,
        }

    streak = 0
    for entry in judged:
        if entry.get("conclusion") == "success":
            break
        streak += 1
    tail = (
        f"last succeeded {last_good} (UTC)"
        if last_good
        else f"never succeeded in the newest {len(judged)} {label} run(s)"
    )
    return {
        "verdict": "failing",
        "note": _with_aside(
            f"{streak} {label} run(s) in a row ended "
            f"'{judged[0].get('conclusion')}'; {tail}",
            aside,
        ),
        "failures": streak,
        "last_good": last_good,
        # The caller needs the newest red run to ask whether it ever started,
        # and its date to ask whether whatever stopped it is still stopping
        # anything.
        "newest_id": judged[0].get("databaseId"),
        "newest_at": judged[0].get("createdAt"),
    }



def _runs_to_judge(completed):
    """`(runs, kind, aside)` -- which runs decide the verdict, and why.

    A workflow with a `schedule` on it is judged on its scheduled runs
    alone. `schedule_health` learned this one layer down and this module
    did not: a manual dispatch is a cycle running the workflow by hand,
    and this module's own report is what asks a cycle to do that, so
    letting a green dispatch reset the streak means the recommended
    action blinds the instrument that recommended it.

    Measured Cycle 622 on `docs-sync`: every scheduled run since
    2026-08-07 has failed and not one has succeeded, while this printed
    "1 completed run(s) in a row ended 'failure'; last succeeded
    2026-08-28" -- a three-week outage rendered as yesterday's blip,
    because Cycle 618 dispatched it by hand at 03:37 and that run went
    green.

    The dispatch result is never dropped, only demoted to `aside`: it is
    real evidence about whether the workflow *can* pass, which is a
    different question from whether it *is* passing.
    """
    scheduled = [r for r in completed if (r or {}).get("event") == "schedule"]
    if not scheduled:
        return completed, "any", ""
    newest = completed[0]
    if newest.get("event") == "schedule":
        return scheduled, "schedule", ""
    return (
        scheduled,
        "schedule",
        f"judged on scheduled runs only -- the newest run is a "
        f"{newest.get('event')} that ended '{newest.get('conclusion')}' "
        f"on {newest.get('createdAt')} (UTC)",
    )


def _with_aside(note, aside):
    """Append the demoted dispatch sentence, when there is one."""
    return f"{note}; {aside}" if aside else note


def sweep(repos, run=None):
    """`(results, errors)` -- one verdict per agentic workflow found."""
    results, errors = [], []
    for repo in repos:
        workflows, err = agentic_workflows(repo, run=run)
        if err:
            errors.append(f"{repo}: could not list workflows -- {err}")
            continue
        for workflow in workflows:
            runs, err = run_history(workflow, run=run)
            if err:
                errors.append(f"{workflow['repo']} {workflow['path']}: {err}")
                continue
            entry = dict(workflow)
            entry.update(verdict_for(workflow, runs))
            if entry["verdict"] == "failing" and entry.get("newest_id"):
                detail, err = start_failure(entry["repo"], entry["newest_id"], run=run)
                if err:
                    # Not fatal: the streak is still a true finding. Say the
                    # follow-up could not be made rather than implying it was.
                    entry["note"] += f"; could not check whether it started -- {err}"
                elif detail and detail.get("started"):
                    sentence = failure_sentence(detail)
                    if sentence:
                        entry["note"] += f"; {sentence}"
                elif detail:
                    entry["verdict"] = "blocked"
                    entry["blocked_note"] = detail["reason"]
                    since, err = block_has_lifted(
                        entry["repo"], entry.get("newest_at"), run=run
                    )
                    if err:
                        # Same rule as above: never quieten and never escalate
                        # on a call that did not answer. `blocked` stands and
                        # the page says the follow-up was not made.
                        entry["blocked_note"] += (
                            f"; could not check whether the block has lifted -- {err}"
                        )
                    elif since:
                        entry["verdict"] = "lifted"
                        entry["lifted_note"] = (
                            f"but {entry['repo']} ran a job to success on {since} "
                            "(UTC), after that block -- so the block is history and "
                            "this workflow simply has not been re-run"
                        )
            results.append(entry)
    return results, errors


def format_report(results, errors, swept):
    """The printed report, and the exit status it implies."""
    lines = []
    failing = [r for r in results if r["verdict"] == "failing"]
    blocked = [r for r in results if r["verdict"] == "blocked"]
    lifted = [r for r in results if r["verdict"] == "lifted"]
    never = [r for r in results if r["verdict"] == "never-run"]
    healthy = [r for r in results if r["verdict"] == "healthy"]

    if failing:
        lines.append(
            f"AGENTIC WORKFLOW FAILING — {len(failing)} workflow(s) are dead and "
            "reporting nothing about it."
        )
        for entry in sorted(failing, key=lambda r: -r["failures"]):
            lines.append(f"  {entry['repo']}  {entry['path']}")
            lines.append(f"      {entry['note']}")
            lines.append(
                f"      https://github.com/{entry['repo']}/actions/workflows/"
                f"{entry['path'].rsplit('/', 1)[-1]}"
            )
    if blocked:
        lines.append(
            f"BLOCKED BEFORE IT STARTED — {len(blocked)} workflow(s). Nothing here is "
            "fixable by a pull request; the account is."
        )
        for entry in blocked:
            lines.append(f"  {entry['repo']}  {entry['path']}")
            lines.append(f"      {entry['blocked_note']}")
            lines.append(f"      earlier history: {entry['note']}")
            lines.append(
                f"      https://github.com/{entry['repo']}/actions/workflows/"
                f"{entry['path'].rsplit('/', 1)[-1]}"
            )
    if lifted:
        lines.append(
            f"BLOCK HAS LIFTED — {len(lifted)} workflow(s) held down by something that "
            "has since stopped applying. One dispatch each answers whether they work."
        )
        for entry in lifted:
            leaf = entry["path"].rsplit("/", 1)[-1]
            lines.append(f"  {entry['repo']}  {entry['path']}")
            lines.append(f"      {entry['blocked_note']}")
            lines.append(f"      {entry['lifted_note']}")
            lines.append(f"      earlier history: {entry['note']}")
            lines.append(f"      gh workflow run {leaf} --repo {entry['repo']}")
    for entry in never:
        lines.append(f"NOT YET RUN — {entry['repo']} {entry['path']}: {entry['note']}")
    for entry in healthy:
        lines.append(f"ok  {entry['repo']}  {entry['path']} — {entry['note']}")
    if not results:
        lines.append("No gh-aw workflows found in the repos this sweep could read.")

    if errors:
        lines.append(
            f"COULD NOT READ — {len(errors)}; this is no instrument, not a clean sweep."
        )
        lines.extend(f"  {blob}" for blob in errors)

    lines.append(f"Swept {len(swept)} repo(s): {', '.join(swept)}")
    lines.append(
        "Agentic means a `.lock.yml` workflow; gh-aw's own "
        "`agentics-maintenance.yml` is out of scope on purpose."
    )
    if failing or lifted:
        return "\n".join(lines), 2
    if errors:
        return "\n".join(lines), 1
    return "\n".join(lines), 0


def main(argv=None):
    from tools.security_alerts import _repos_to_sweep

    repos, _unplaceable, notes, incomplete = _repos_to_sweep()
    results, errors = sweep(repos)
    if incomplete:
        errors.extend(notes)
    report, status = format_report(results, errors, repos)
    print(report)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
