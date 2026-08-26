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

**Three verdicts, kept apart, for `security_alerts`' reason.** `healthy`
means the most recent *completed* run succeeded. `failing` means it did
not. `error` means the run history could not be read at all -- which is
no instrument, not a healthy workflow, and must never print what a clean
sweep prints. A workflow with no runs yet is `never-run` and is reported
without raising the status: a workflow merged an hour ago has not failed.

**A run still in progress is not a verdict.** The newest run can be
`in_progress` for ten minutes on a scheduled sweep, and reading that as
either outcome would make this tool's answer depend on when it was
called. In-progress runs are skipped and the newest *completed* one
decides; if every run on record is still going, that is `never-run` with
a note, not a guess.

Exit 2 means at least one agentic workflow is failing -- someone's
automation is dead and has been reporting nothing about it. Exit 1 means
something was unreadable. Exit 0 means every agentic workflow this sweep
could see last completed green, and it says which repos answered so
"checked and clean" can never be confused with "never looked".
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
            results.append(entry)
    return results, errors


def format_report(results, errors, swept):
    """The printed report, and the exit status it implies."""
    lines = []
    failing = [r for r in results if r["verdict"] == "failing"]
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
