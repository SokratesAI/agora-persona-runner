"""Did the default branch actually build after the merge?

Cycle 801, on the gap Cycle 795 named and left open: `tools.preflight`
had thirty-six checks and not one of them asked whether the build on
`main` succeeded. `open_prs` judges open pull requests, `ci_health` asks
whether a build *can* reach GitHub at all, `agentic_health` reads gh-aw
workflow history -- a merge whose post-merge build went red is absent
from all three, and absent reads exactly like quiet. `#93
(SokratesAI/agora-claude-bridge)` was red on `main` from 08:40 on
2026-09-02 and I only found it four hours later, by going to look at why
a pod had not rolled.

    python3 -m tools.main_build

**The obvious version of this is wrong, and that is why it is a module
and not a one-liner.** `gh run list --branch main --limit 1` returns the
newest run of *any* workflow, which on 2026-09-02 was `Agentic
Maintenance` for `sokrates-docs` and `Secret scan` for
`platform-config` -- neither of them the build. A repo whose build went
red an hour before a scheduled scan ran green would read as green. So
this asks the other way round: resolve the default branch's HEAD commit,
then ask GitHub for every workflow run *on that exact commit*.

**Five verdicts, four of which raise.**

`RED` is a completed run on HEAD that concluded badly. That is the `#93`
shape and it is the whole reason this exists.

`NOT BUILT` is a repository that declares at least one workflow, whose
HEAD commit has no run at all. The workflow probe is `open_prs`'
reasoning reused: the four `*-config` repos declare zero workflows, so
"no run" is the expected answer there and calling it a finding would be
the negative result that was guaranteed in advance. What this still
cannot see is a workflow whose `paths:` filter excluded the files in one
particular merge -- that is a legitimately empty commit and it is printed
as a caveat rather than folded into either bucket.

`NO IMAGE` is the third state Cycle 795 asked for by name: a run exists
on HEAD and its conclusion is `skipped`, so nothing was produced. On a
pull request `build-push` is skipped here by design and `open_prs`
deliberately does not read that as a failure -- but on the default
branch that same job is exactly what a merge is for, so a skipped run
here means the merge landed and no image came out of it. Same
conclusion, opposite meaning, because the branch is different.

`BLOCKED` is the fifth, added Cycle 802, and it exists because `RED`
merged two causes with different owners. All five red branches this tool
found on its first sweep were red for the same reason and none of them
had a broken build: GitHub refused to start the job, with the annotation
*"The job was not started because recent account payments have failed or
your spending limit needs to be increased."* A refused run concludes
`failure` exactly like a real one, so `RED` was the honest verdict from
the payload and the wrong instruction to a cycle -- it says debug the
code, and there is no code to debug. Re-running all five after private
Actions came back on 2026-09-01 turned four of them green immediately.

`RUNNING` never raises: a merge four minutes old is not a finding.

Exit status, matching `tools.open_prs` and `tools.security_alerts`: 0
when nothing needs a hand, 2 when a branch needs one, 1 when something
was unreadable. "I could not check" never reads as "nothing here".
"""

import argparse
import json
import subprocess
import sys

from tools.agentic_health import start_failure
from tools.open_prs import has_workflows
from tools.security_alerts import repos_in_org

#: Conclusions GitHub reports for a run that did not pass. `SKIPPED` is
#: absent here and handled on its own: on the default branch it is the
#: `NO IMAGE` verdict, which is a different thing to say than "red".
BAD_CONCLUSIONS = {
    "failure",
    "timed_out",
    "cancelled",
    "action_required",
    "startup_failure",
    "stale",
}

#: How many runs to read for one commit. A commit with more workflows than
#: this would be judged on a subset, so the page limit is checked rather
#: than assumed -- the same guard `repos_in_org` puts on the org listing.
RUNS_PAGE = 50


def _gh(args):
    """Run `gh` and return `(exit_code, stdout, stderr)`."""
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def head_of_default_branch(repo, run=None):
    """`(sha, error)` for the default branch's newest commit.

    `commits/HEAD` resolves the default branch server-side, so this does
    not have to know whether a repo calls it `main` or something else --
    and asking for runs by `head_sha` afterwards means the branch name is
    never needed as a query parameter either. My first version spent a
    second call per repository on `.default_branch` purely so the report
    could print the word `main`; that is 25 REST calls on every sweep,
    forever, for a word, and the commit sha already names the thing.
    """
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/commits/HEAD", "--jq", ".sha"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    sha = out.strip()
    if not sha:
        return None, "the default branch HEAD came back empty"
    return sha, None


def runs_for_commit(repo, sha, run=None):
    """`(runs, error)` -- every workflow run GitHub has for one commit."""
    code, out, err = (run or _gh)(
        [
            "api",
            f"repos/{repo}/actions/runs?head_sha={sha}&per_page={RUNS_PAGE}",
            "--jq",
            "[.workflow_runs[] | {id: .id, name: .name, status: .status, "
            "conclusion: .conclusion, url: .html_url}]",
        ]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        payload = json.loads(out or "[]")
    except ValueError:
        return None, "gh returned something that is not JSON"
    if not isinstance(payload, list):
        return None, "gh returned a JSON object where a list of runs was expected"
    if len(payload) >= RUNS_PAGE:
        return None, (
            f"the run listing came back at the {RUNS_PAGE}-run page limit, so "
            "this commit cannot be judged on a complete set"
        )
    return payload, None


def newest_per_workflow(runs):
    """One run per workflow name -- the first GitHub listed, which is newest.

    GitHub returns runs newest first, so a re-run of a workflow that failed
    an hour ago must not leave the old failure in the judged set. Without
    this, a red run that was re-run green would be raised forever.
    """
    seen = {}
    for entry in runs:
        name = (entry or {}).get("name") or "?"
        if name not in seen:
            seen[name] = entry
    return list(seen.values())


def failing_runs(runs):
    """The runs `judge` blames for a `red` verdict -- one rule, two callers.

    `main` needs their ids, to ask GitHub whether those runs ever started a
    job. Re-selecting them there would be a second copy of the rule that
    decides what `red` means, and this repo's own backlog is mostly what
    happens when one fact lives in two places.
    """
    return [
        r for r in newest_per_workflow(runs)
        if (r.get("conclusion") or "").lower() in BAD_CONCLUSIONS
    ]


def judge(runs, workflow_count):
    """`(verdict, detail, url)` for one repository's default branch.

    `workflow_count` is the repository's own, or `None` when it could not
    be read -- and an unreadable count never turns an empty run list into a
    finding, because then the finding would rest on the thing that failed.

    The url is the *offending* run's, not the newest one's. That
    distinction is the same one the module docstring is about, and it
    caught me while I was writing this: `vault-bridge`'s HEAD carries a
    green `Tests` and a red `Build and Deploy`, and my first version
    linked whichever GitHub listed first, so a correct RED verdict printed
    a link to a passing run.
    """
    judged = newest_per_workflow(runs)

    bad = failing_runs(runs)
    if bad:
        names = ", ".join(sorted({r.get("name") or "?" for r in bad}))
        return (
            "red",
            f"{len(bad)} workflow(s) failed on this commit: {names}",
            bad[0].get("url") or "",
        )

    running = [r for r in judged if (r.get("status") or "").lower() != "completed"]
    if running:
        names = ", ".join(sorted({r.get("name") or "?" for r in running}))
        return (
            "running",
            f"{len(running)} workflow(s) still going: {names}",
            running[0].get("url") or "",
        )

    skipped = [
        r for r in judged if (r.get("conclusion") or "").lower() == "skipped"
    ]
    if skipped:
        names = ", ".join(sorted({r.get("name") or "?" for r in skipped}))
        return (
            "no_image",
            f"{len(skipped)} workflow(s) were skipped on the default branch, "
            f"so nothing was produced: {names}",
            skipped[0].get("url") or "",
        )

    if not judged:
        if workflow_count is None:
            return (
                "unreadable",
                "no run exists for this commit, and this repo's workflow count "
                "could not be read",
                "",
            )
        if workflow_count == 0:
            return (
                "no_ci",
                "this repo declares no workflows, so none was expected",
                "",
            )
        return (
            "not_built",
            f"no run exists for this commit, and this repo declares "
            f"{workflow_count} workflow(s), so one was expected",
            "",
        )

    return "ok", f"{len(judged)} workflow(s) passed on this commit", ""


#: The four verdicts that make this exit 2. `running`, `no_ci` and `ok`
#: are deliberately absent: none of them is a thing for a cycle to go and do.
#: `blocked` *is* here, and that is the one difference from
#: `tools.agentic_health`, where the same verdict deliberately does not raise.
#: There, the fix was a payment nobody could make in a pull request and the
#: owner had already decided to wait. Here the fix is one command -- `gh run rerun`
#: -- so a cycle has something to go and do, and the label is what tells it to
#: do that instead of debugging code that never ran.
RAISING = ("red", "blocked", "no_image", "not_built")

_HEADINGS = {
    "red": "RED — a workflow failed on the default branch",
    "blocked": (
        "BLOCKED BEFORE IT STARTED — GitHub refused to run it; "
        "re-run it, do not debug it"
    ),
    "no_image": "NO IMAGE — the run was skipped, so the merge produced nothing",
    "not_built": "NOT BUILT — a run was expected on this commit and never appeared",
    "running": "still building",
    "no_ci": "no workflows on this repo",
    "ok": "green on the default branch",
}


def format_report(results, swept, errors, caveat_repos):
    """The whole report, findings first."""
    lines = []
    by_verdict = {}
    for row in results:
        by_verdict.setdefault(row["verdict"], []).append(row)

    for verdict in RAISING:
        rows = by_verdict.get(verdict) or []
        if not rows:
            continue
        lines.append(f"{_HEADINGS[verdict]} — {len(rows)}")
        for row in sorted(rows, key=lambda r: r["repo"]):
            lines.append(f"  {row['repo']}  default branch at {row['sha'][:7]}")
            lines.append(f"      {row['detail']}")
            if row["url"]:
                lines.append(f"      {row['url']}")

    for row in by_verdict.get("unreadable") or []:
        lines.append(f"COULD NOT JUDGE — {row['repo']}: {row['detail']}")

    for verdict in ("running", "no_ci", "ok"):
        rows = by_verdict.get(verdict) or []
        if not rows:
            continue
        lines.append(f"{_HEADINGS[verdict]} — {len(rows)}")
        for row in sorted(rows, key=lambda r: r["repo"]):
            lines.append(f"  {row['repo']}  {row['detail']}")

    if not any(by_verdict.get(v) for v in RAISING):
        lines.append("Nothing to act on.")

    for message in errors:
        lines.append(f"⚠ {message}")
    if caveat_repos:
        lines.append(
            "NOT JUDGED  whether a workflow's `paths:` filter excluded the files "
            "in one particular merge. That is a legitimately empty commit on a "
            "repo that has workflows, and it is why NOT BUILT names the repo's "
            f"workflow count rather than asserting a run was owed: "
            f"{', '.join(caveat_repos)}"
        )
    lines.append(
        f"Judged the default branch HEAD of {swept} repo(s), by asking GitHub for "
        "every run on that exact commit rather than for the newest run on the "
        "branch — the newest run on a branch is often a scheduled scan, not the build."
    )
    return "\n".join(lines)


def _blocked_or_red(repo, runs, detail, errors, run=None):
    """Re-read a `red` verdict as `blocked` when no failing run ever started.

    A run GitHub refused to start concludes `failure` like any other, so the
    verdict function above cannot tell the two apart -- it judges the payload
    it was handed, and that payload says `failure` in both cases. The
    difference is only visible one call deeper, in whether any job on the run
    executed a single step, which is what `tools.agentic_health.start_failure`
    already reads. It is imported rather than reimplemented.

    **Every failing run has to have been refused, not just one.** A branch
    where one workflow was refused and another genuinely failed is a broken
    branch, and calling it `blocked` would tell a cycle to re-run something
    that will fail again for a real reason. So a single started failure holds
    the `red` verdict for the whole repository.

    An unreadable answer leaves `red` standing and files an error. That is the
    safe direction: `red` costs a cycle a look, `blocked` costs it a re-run,
    and neither is silence -- both raise.
    """
    failed = failing_runs(runs)
    if not failed:
        return "red", detail
    reasons = []
    for entry in failed:
        run_id = (entry or {}).get("id")
        if not run_id:
            errors.append(
                f"{repo}: a failing run came back with no id, so whether it "
                "ever started could not be read"
            )
            return "red", detail
        start, err = start_failure(repo, run_id, run=run)
        if err or start is None:
            errors.append(
                f"{repo}: could not read whether run {run_id} ever started "
                f"-- {err or 'no detail'}"
            )
            return "red", detail
        if start.get("started"):
            return "red", detail
        reasons.append(f"{entry.get('name') or '?'}: {start.get('reason')}")
    return "blocked", "; ".join(reasons)


def main(argv=None, run=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--org", default="SokratesAI",
        help="GitHub org to sweep (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    repos, error, archived = repos_in_org(args.org, run=run)
    if error:
        print(f"COULD NOT LIST THE ORG — {error}")
        return 1

    errors = []
    results = []
    caveat_repos = []
    for repo in repos:
        sha, err = head_of_default_branch(repo, run=run)
        if err:
            errors.append(f"{repo}: could not read the default branch HEAD — {err}")
            results.append(
                {"repo": repo, "sha": "", "url": "",
                 "verdict": "unreadable", "detail": err}
            )
            continue
        runs, err = runs_for_commit(repo, sha, run=run)
        if err:
            errors.append(f"{repo}: could not read the runs on {sha[:7]} — {err}")
            results.append(
                {"repo": repo, "sha": sha, "url": "",
                 "verdict": "unreadable", "detail": err}
            )
            continue
        count = None
        if not runs:
            count, count_err = has_workflows(repo, run=run)
            if count_err:
                errors.append(f"{repo}: could not read the workflow count — {count_err}")
        verdict, detail, url = judge(runs, count)
        if verdict == "red":
            verdict, detail = _blocked_or_red(repo, runs, detail, errors, run=run)
        if verdict == "not_built":
            caveat_repos.append(repo)
        results.append(
            {"repo": repo, "sha": sha, "url": url,
             "verdict": verdict, "detail": detail}
        )

    print(format_report(results, len(repos), errors, caveat_repos))
    if archived:
        print(
            f"{len(archived)} archived repo(s) left out — they are read-only, "
            "so their default branch cannot move."
        )
    if any(r["verdict"] in RAISING for r in results):
        return 2
    if any(r["verdict"] == "unreadable" for r in results) or errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
