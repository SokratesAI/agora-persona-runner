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

**Four verdicts, kept apart, for `security_alerts`' reason.** `healthy`
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

**A run still in progress is not a verdict.** The newest run can be
`in_progress` for ten minutes on a scheduled sweep, and reading that as
either outcome would make this tool's answer depend on when it was
called. In-progress runs are skipped and the newest *completed* one
decides; if every run on record is still going, that is `never-run` with
a note, not a guess.

Exit 2 means at least one agentic workflow is failing on its own terms --
someone's automation is dead and has been reporting nothing about it.
Exit 1 means something was unreadable. Exit 0 means there is nothing for a
cycle to act on, and it says which repos answered so "checked and clean"
can never be confused with "never looked".
"""

import json
import subprocess
import sys

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


def start_failure(repo, run_id, run=None):
    """`(reason, error)` -- why a failed run never got off the ground, or `(None, None)`.

    A run whose jobs executed steps failed at something somebody wrote. A
    run where *no* job executed a single step never started, and GitHub
    puts the reason in a check-run annotation rather than in any log --
    which is why `gh run view --log-failed` answers `log not found` on
    exactly the run you most want to read.

    Two calls, and only on a workflow already known to be failing, so the
    common sweep pays nothing. An unreadable annotation is not an error
    here: "the job never started" is already the finding, and the quote is
    detail on top of it.
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
    if any((job or {}).get("steps") for job in jobs):
        return None, None

    job_id = (jobs[0] or {}).get("id")
    quoted = None
    if job_id is not None:
        code, out, _err = (run or _gh)(
            ["api", f"repos/{repo}/check-runs/{job_id}/annotations"]
        )
        if code == 0:
            try:
                notes = json.loads(out)
            except ValueError:
                notes = []
            if isinstance(notes, list):
                quoted = next(
                    (
                        (n or {}).get("message", "").strip()
                        for n in notes
                        if (n or {}).get("message", "").strip()
                    ),
                    None,
                )
    if quoted:
        return f"the job never started -- GitHub says: {quoted}", None
    return "the job never started, and GitHub gave no reason on the run", None


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

    last_good = next(
        (r["createdAt"] for r in completed if r.get("conclusion") == "success"), None
    )
    if completed[0].get("conclusion") == "success":
        return {
            "verdict": "healthy",
            "note": f"last completed run succeeded, {completed[0]['createdAt']} (UTC)",
            "failures": 0,
            "last_good": last_good,
        }

    streak = 0
    for entry in completed:
        if entry.get("conclusion") == "success":
            break
        streak += 1
    tail = (
        f"last succeeded {last_good} (UTC)"
        if last_good
        else f"never succeeded in the newest {len(completed)} run(s)"
    )
    return {
        "verdict": "failing",
        "note": (
            f"{streak} completed run(s) in a row ended "
            f"'{completed[0].get('conclusion')}'; {tail}"
        ),
        "failures": streak,
        "last_good": last_good,
        # The caller needs the newest red run to ask whether it ever started.
        "newest_id": completed[0].get("databaseId"),
    }


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
                reason, err = start_failure(entry["repo"], entry["newest_id"], run=run)
                if err:
                    # Not fatal: the streak is still a true finding. Say the
                    # follow-up could not be made rather than implying it was.
                    entry["note"] += f"; could not check whether it started -- {err}"
                elif reason:
                    entry["verdict"] = "blocked"
                    entry["blocked_note"] = reason
            results.append(entry)
    return results, errors


def format_report(results, errors, swept):
    """The printed report, and the exit status it implies."""
    lines = []
    failing = [r for r in results if r["verdict"] == "failing"]
    blocked = [r for r in results if r["verdict"] == "blocked"]
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
    if failing:
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
