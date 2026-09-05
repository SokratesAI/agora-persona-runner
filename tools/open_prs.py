"""Open pull requests nobody is watching.

Cycle 795, on a gap Cycle 788 named and did not close: `#566` sat open
for two days having never had a CI run created at all, and not one of
the thirty-four checks in `tools.preflight` looks at an open pull
request. `ci_health` judges runs, `agentic_health` judges workflow
history and `schedule_health` judges schedules -- a pull request with an
empty `statusCheckRollup` is absent from all three, and absent reads
exactly like fine.

    python3 -m tools.open_prs

The first sweep, at 10:10 Oslo on 2026-09-02, judged six open pull
requests on the org's 25 live repositories and raised five: one with no
check-run on a repository that declares six workflows
(`platform-config#581`), one with a failed `test` (`whatsapp-bridge#4`),
and three green and unmerged, two of them open for thirty-six days
(`agent-runtime#1`, `vault#2`, `whatsapp-bridge#7`).

**Five verdicts, and only two of them raise.**

`NO CI RUN` is the `#566` shape: the pull request has no check-runs and a
run was going to appear. The question is which workflows *this pull
request* would have started, not how many the repository declares -- the
four `*-config` repos return `total_count: 0` from `actions/workflows`,
so nothing was ever coming there, and `platform-config` has six
workflows and (from #719 onward) none that runs on a pull request, which
is the same answer reached a different way. So the empty rollup is
judged against each active workflow's own trigger block, read at the
pull request's head; a workflow whose YAML or run history cannot be read
lands in `unreadable` rather than quietly clearing the finding. What
this still cannot see is a workflow whose `paths:` filter excludes the
files in one particular pull request; that is a legitimate empty rollup
on a repo whose workflows do run on pull requests, and it is printed as
a caveat rather than silently folded into either bucket.

`FAILING` is any check-run that concluded badly. `PENDING` is a run still
going and never raises -- a pull request opened four minutes ago is not a
finding.

`READY, UNMERGED` is the one worth explaining. Every check finished,
none failed, and it is still open. That is a good pull request nobody merged, and it is the shape this
loop produces when a cycle runs out of turn before it merges its own
work. The window is one day: this loop wakes about seventy times in a
day, so a finished pull request still open after one has outlived every
scheduled opportunity to merge it. That is a cadence, not a comfort
number, and `--max-age-days` moves it if the cadence moves.

The window is also why this did **not** raise `agora-persona-runner#633`
on its first run, and that is worth saying rather than trimming: #633 is
green on `test` and `vault-drift`, was opened at 19:42 UTC the previous
evening by a cycle that never merged it, and at 0.5 days old it sat
inside the window. It is exactly the shape this check is for and it will
raise tonight. A tighter window chosen so the example landed would have
been a number picked to flatter the tool.

**`SUPERSEDED` is what `READY, UNMERGED` was getting wrong.** A pull
request whose every changed file is already byte-identical on its base
branch has nothing left to merge, and until Cycle 807 it read as a green
pull request nobody had merged -- which is a thing to go and do. Two of
the three rows in that bucket on 2026-09-02 were this: `whatsapp-bridge#7`
bumped three Node pins that #11 had already landed on `main` the day
before, and `whatsapp-bridge#4` had been narrowed by its own second commit
to a logging change `main` then implemented better. The reason neither was
visible is mechanical rather than careless: a pull request's diff, and
`compare/base...head`, are both **three-dot** -- computed from the merge
base -- so they go on reporting the same additions forever after somebody
lands identical content by another route. `is_superseded` asks the other
question, file by file: is the blob at the head sha the same as the blob
at the base branch. It is asked only of a pull request that would
otherwise raise `ready`, costs two API calls per changed file, and is
bounded at `SUPERSEDED_MAX_FILES`. When it cannot be asked the answer is
`None` and the row stays `ready` with the reason appended -- an unanswered
question must never read as "this is fine".

**Deliberately held is a verdict, not a finding.** A draft, or a title
carrying the word `HELD`, is printed and never raises. Both conventions
already exist in this org -- `agora-persona-runner-config#15` has said
`HELD on` in its title since 2026-08-29 -- so this reads a convention
rather than inventing one.

Exit status, matching `tools.security_alerts` and `tools.cli_pin`: 0 when
nothing needs a hand, 2 when a pull request is stalled, 1 when something
was unreadable. "I could not check" never reads as "nothing here".
"""

import argparse
import base64
import json
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import quote

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.tools_github import _workflow_triggers_on_a_pull_request  # noqa: E402
from tools.security_alerts import repos_in_org  # noqa: E402

#: One day. See the module docstring: the loop wakes about seventy times
#: in a day, so a finished pull request still open after one has outlived
#: every scheduled opportunity to merge it.
DEFAULT_MAX_AGE_DAYS = 1.0

#: Conclusions GitHub reports for a check-run that did not pass. `SKIPPED`
#: and `NEUTRAL` are absent on purpose -- `build-push` is skipped on every
#: pull request in the bridge and runner repos by design, and reading that
#: as a failure would raise on every green pull request this loop opens.
BAD_CONCLUSIONS = {
    "FAILURE",
    "TIMED_OUT",
    "CANCELLED",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
    "STALE",
}

HELD_MARKER = "HELD"


def _gh(args):
    """Run `gh` and return `(exit_code, stdout, stderr)`."""
    proc = subprocess.run(
        ["gh"] + args, capture_output=True, text=True, timeout=60
    )
    return proc.returncode, proc.stdout, proc.stderr


def open_prs_for(repo, run=None):
    """`(prs, error)` for one `owner/name`.

    `run` is injected and resolved at call time rather than bound as a
    default argument, for `tools.security_alerts.alerts_for`'s reason: a
    default binds the function object at import, so a test that patches
    `_gh` would leave this calling the real `gh`.
    """
    code, out, err = (run or _gh)(
        [
            "pr", "list", "-R", repo, "--state", "open", "--limit", "100",
            "--json",
            # `files`, `headRefOid` and `baseRefName` are here for
            # `is_superseded`, which needs the changed paths and the two
            # refs to compare them at.
            "number,title,createdAt,isDraft,statusCheckRollup,url,files,headRefOid,baseRefName",
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
        return [], "gh returned a JSON object where a list of pull requests was expected"
    return payload, None


def has_workflows(repo, run=None):
    """`(count, error)` -- how many workflows the repository declares.

    A repository with none can never produce a check-run, so an empty
    rollup there is the expected answer and not a finding. Disabled
    workflows are counted too: GitHub reports them in `total_count`, and
    a workflow disabled after a run existed is a different question from
    a repository that has never had one.
    """
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/actions/workflows", "--jq", ".total_count"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        return int(out.strip()), None
    except ValueError:
        return None, "the workflow count came back as something that is not a number"


# The workflow count answers "does this repo run CI at all" and was standing
# in for "was a check-run ever going to appear on this pull request". Those
# stop being the same question the moment a repo keeps its workflows and
# stops running them on pull requests, which is what `platform-config` did on
# 2026-09-05 to stop paying a second billed Actions minute per merge. Its own
# trigger-drop pull request, #719, was then reported here as NO CI RUN
# forever, and `merge_pr` refused it for the same reason one layer over
# (runner#775, cycle 994). The YAML rule lives in `agora_runner.tools_github`
# and is imported rather than copied: two spellings of "does this trigger on a
# pull request" is how the two tools drift apart again.
WORKFLOW_FILE_PREFIX = ".github/workflows/"


def _workflow_ran_from_a_pull_request(repo, workflow_id, run=None):
    """For a workflow GitHub generates and the repository carries no file
    for -- `Dependency Graph` is `dynamic/dependabot/update-graph`, and the
    contents API answers 404 for it. `None` when the run history is
    unreadable, which is its own answer and not a negative one."""
    if workflow_id is None:
        return None
    code, out, _ = (run or _gh)(
        [
            "api",
            f"repos/{repo}/actions/workflows/{workflow_id}/runs"
            "?event=pull_request&per_page=1",
            "--jq",
            ".total_count",
        ]
    )
    if code != 0:
        return None
    try:
        return int(out.strip()) > 0
    except ValueError:
        return None


def pull_request_workflows(repo, ref, run=None):
    """`(on_pull_request, unreadable, error)` -- which of the repository's
    active workflows would have produced a check-run on this pull request.

    Read at the pull request's own head, because that is the copy GitHub
    evaluates for a `pull_request` trigger on a same-repo branch, and it is
    the only ref at which #719 -- the pull request that removes the trigger
    -- reads as push-only. (For a fork GitHub reads the base branch's copy;
    unreachable here, since every agent shares one GitHub account.)

    Unreadable is its own bucket rather than a negative, for the reason
    `judge` acts on: "I could not tell" must not read as "no run was
    coming", or the false alarm this closes is replaced by a silence.
    """
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/actions/workflows?per_page=100",
         "--jq", "[.workflows[] | {id, name, path, state}]"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return [], [], blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        workflows = json.loads(out)
    except ValueError:
        return [], [], "the workflow list came back as something that is not JSON"
    if not isinstance(workflows, list):
        return [], [], "the workflow list came back as something that is not a list"

    on_pull_request, unreadable = [], []
    for workflow in workflows:
        if not isinstance(workflow, dict) or workflow.get("state") != "active":
            continue
        name = workflow.get("name") or workflow.get("path") or "?"
        path = workflow.get("path")
        if not path or not path.startswith(WORKFLOW_FILE_PREFIX):
            verdict = _workflow_ran_from_a_pull_request(repo, workflow.get("id"), run=run)
        else:
            code, text, _ = (run or _gh)(
                ["api", f"repos/{repo}/contents/{quote(path)}?ref={quote(ref or '')}",
                 "--jq", ".content"]
            )
            if code != 0:
                unreadable.append(name)
                continue
            try:
                decoded = base64.b64decode(text.strip()).decode("utf-8")
            except Exception:
                unreadable.append(name)
                continue
            verdict = _workflow_triggers_on_a_pull_request(decoded)
        if verdict is None:
            unreadable.append(name)
        elif verdict:
            on_pull_request.append(name)
    return on_pull_request, unreadable, None


def parse_time(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_days(pr, now):
    opened = parse_time(pr.get("createdAt"))
    if opened is None:
        return None
    return (now - opened).total_seconds() / 86400


def is_held(pr):
    """Deliberately parked, by either of the two conventions in this org."""
    if pr.get("isDraft"):
        return True
    return HELD_MARKER in (pr.get("title") or "")


#: The most files a pull request may change and still be asked whether it
#: is superseded. The question costs two `gh api` calls per file, so it is
#: bounded rather than skipped: a 400-file pull request is not a thing this
#: loop opens, and answering "not judged" on one is honest.
SUPERSEDED_MAX_FILES = 20

#: What `blob_sha` returns for a path that does not exist at a ref. A real
#: blob sha is 40 hex characters, so this can never collide with one, and
#: two absences comparing equal is the answer we want: a pull request that
#: deletes a file `main` has already deleted has nothing left to merge.
ABSENT = "<absent>"


def blob_sha(repo, path, ref, run=None):
    """`(sha, error)` — the git blob sha of one path at one ref.

    `ABSENT` when the path is not there. That is not an error: a file the
    pull request adds is legitimately missing from the base branch, and a
    file it deletes is legitimately missing from the head.
    """
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/contents/{path}?ref={ref}", "-q", ".sha"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        if "404" in blob or "Not Found" in blob:
            return ABSENT, None
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    sha = out.strip()
    # A directory answers with a list, so `-q .sha` prints nothing. Treat
    # that as unreadable rather than as a match: an empty string compared
    # against an empty string is the false "identical" this whole check
    # exists to stop reporting.
    return (sha, None) if sha else (None, f"no blob sha for {path} at {ref}")


def is_superseded(repo, pr, run=None):
    """`(bool_or_None, detail)` — are the pull request's changes already on its base?

    Asked file by file rather than through the compare API, because both
    the pull request diff and `compare/base...head` are three-dot: they
    are computed from the merge base, so they keep reporting the same
    additions after somebody lands identical content on `main` by another
    route. That is exactly what happened to `whatsapp-bridge#7`, which
    read as `READY, UNMERGED — every check passed and nobody merged it`
    for two days while every one of its three pins was already on `main`
    from #11. Comparing the blob at head against the blob at base asks the
    only question that matters — would merging this change anything —
    and `None` means it could not be asked.
    """
    files = [f.get("path") for f in (pr.get("files") or []) if f.get("path")]
    if not files:
        return None, "the pull request lists no changed files"
    if len(files) > SUPERSEDED_MAX_FILES:
        return None, f"{len(files)} changed file(s), over the {SUPERSEDED_MAX_FILES} this asks about"
    head = pr.get("headRefOid")
    base = pr.get("baseRefName")
    if not head or not base:
        return None, "the pull request carries no head sha or base branch"
    for path in files:
        at_head, err = blob_sha(repo, path, head, run=run)
        if err:
            return None, err
        at_base, err = blob_sha(repo, path, base, run=run)
        if err:
            return None, err
        if at_head != at_base:
            return False, ""
    return True, f"all {len(files)} changed file(s) are already identical on {base}"


def judge(pr, now, workflow_count, pr_workflows=None,
          max_age_days=DEFAULT_MAX_AGE_DAYS):
    """`(verdict, detail)` for one pull request.

    `workflow_count` is the repository's own, or `None` when it could not
    be read -- and an unreadable count never turns an empty rollup into a
    finding, because then the finding would rest on the thing that failed.

    `pr_workflows` is `pull_request_workflows`'s `(on_pull_request,
    unreadable)` for this pull request, or `None` when the triggers were
    not read. `None` keeps the old count-only judgement, so a caller that
    does not probe is unchanged.
    """
    if is_held(pr):
        return "held", "draft" if pr.get("isDraft") else "title says HELD"

    rollup = pr.get("statusCheckRollup") or []
    bad = [c for c in rollup if (c.get("conclusion") or "") in BAD_CONCLUSIONS]
    if bad:
        names = ", ".join(sorted({c.get("name") or "?" for c in bad}))
        return "failing", f"{len(bad)} check(s) did not pass: {names}"

    pending = [c for c in rollup if not (c.get("conclusion") or "")]
    if pending:
        names = ", ".join(sorted({c.get("name") or "?" for c in pending}))
        return "pending", f"{len(pending)} check(s) still running: {names}"

    age = age_days(pr, now)
    if not rollup:
        if workflow_count is None:
            return "unreadable", "no check-runs, and this repo's workflow count could not be read"
        if workflow_count == 0:
            if age is not None and age > max_age_days:
                return "ready", f"no CI on this repo, open {age:.1f} day(s)"
            return "ok", "no CI on this repo, and inside the window"
        if pr_workflows is not None:
            on_pull_request, unreadable = pr_workflows
            if unreadable:
                return "unreadable", (
                    "no check-runs, and I could not read the trigger block of "
                    f"{len(unreadable)} of this repo's {workflow_count} workflow(s): "
                    + ", ".join(sorted(unreadable))
                )
            if not on_pull_request:
                # Every active workflow is push-only, so no check-run was
                # ever coming and an empty rollup is the expected answer --
                # the same place a repo with no workflows at all lands.
                if age is not None and age > max_age_days:
                    return "ready", (
                        f"no workflow here runs on a pull request, open {age:.1f} day(s)"
                    )
                return "ok", (
                    "no workflow here runs on a pull request, and inside the window"
                )
            return "no_run", (
                f"no check-run exists, and {len(on_pull_request)} of this repo's "
                f"{workflow_count} workflow(s) run on a pull request: "
                + ", ".join(sorted(on_pull_request))
            )
        return "no_run", (
            f"no check-run exists, and this repo declares {workflow_count} "
            f"workflow(s), so one was expected"
        )

    if age is not None and age > max_age_days:
        return "ready", f"every check passed, open {age:.1f} day(s)"
    return "ok", "every check passed, and inside the window"


#: The two verdicts that make this exit 2. `pending` and `held` are
#: deliberately absent: neither is a thing for a cycle to go and do.
RAISING = ("no_run", "failing", "ready")

#: Forty-five minutes, in days, because that is what `tools.claim` calls a
#: live cycle: a claim goes stale after 45 minutes because that is the hard
#: turn cap, so a pull request younger than this was opened by a cycle that
#: is very likely still running. Not a threshold I picked -- it is the same
#: number the claim ledger already uses to decide the same question.
LIVE_CYCLE_DAYS = 45.0 / (24.0 * 60.0)


def live_cycle_rows(results, max_age_days=LIVE_CYCLE_DAYS):
    """Open pull requests young enough that another cycle is probably still on them.

    This is not a verdict and it does not raise -- it cuts across all of them,
    because the pull request that matters here is usually `pending` or `ok`,
    which is to say filed under a heading whose whole purpose is "nothing to
    act on". That is exactly how it goes unread.

    Cycle 866 spent its hour re-fixing an indexed-Job grouping bug that #700
    had fixed five minutes earlier, and #700 was printed in that cycle's own
    preflight run, before it claimed anything, under `still running, inside
    the window`. The claim ledger did not catch it either: claiming is
    voluntary and #700's cycle took no claim on the handoff slug, so the
    `take` returned 0 and said nothing. A title is the only signal that says
    what another cycle is doing, and nothing was putting titles in front of me.
    """
    return sorted(
        (row for row in results
         if row.get("age") is not None and row["age"] <= max_age_days),
        key=lambda row: row["age"],
    )

_HEADINGS = {
    "no_run": "NO CI RUN — a check-run was expected and never appeared",
    "failing": "FAILING — a check-run did not pass",
    "ready": "READY, UNMERGED — every check passed and nobody merged it",
    "superseded": "SUPERSEDED — every changed file is already identical on the base branch",
    "pending": "still running, inside the window",
    "held": "deliberately held — draft, or the title says HELD",
    "ok": "open and healthy, inside the window",
}


def format_report(results, swept, errors, caveat_repos, max_age_days):
    """The whole report, findings first."""
    lines = []
    by_verdict = {}
    for row in results:
        by_verdict.setdefault(row["verdict"], []).append(row)

    live = live_cycle_rows(results)
    if live:
        # First, above the findings, because a cycle reads this list before it
        # picks its work and every other section is about work already done.
        lines.append(
            f"ANOTHER CYCLE MAY BE ON THESE — {len(live)} pull request(s) opened "
            "in the last 45 minutes, which is the claim window. Read the titles "
            "before you pick; a claim does not stop a cycle that took none."
        )
        for row in live:
            lines.append(f"  {row['repo']}#{row['number']}  {row['title']}")
            lines.append(f"      opened {row['age'] * 24 * 60:.0f} minute(s) ago"
                         f" — {row['url']}")

    for verdict in RAISING:
        rows = by_verdict.get(verdict) or []
        if not rows:
            continue
        lines.append(f"{_HEADINGS[verdict]} — {len(rows)}")
        for row in sorted(rows, key=lambda r: -(r["age"] or 0)):
            lines.append(f"  {row['repo']}#{row['number']}  {row['title']}")
            lines.append(f"      {row['detail']}")
            lines.append(f"      {row['url']}")

    for verdict in ("unreadable",):
        rows = by_verdict.get(verdict) or []
        for row in rows:
            lines.append(f"COULD NOT JUDGE — {row['repo']}#{row['number']}: {row['detail']}")

    for verdict in ("superseded", "pending", "held", "ok"):
        rows = by_verdict.get(verdict) or []
        if not rows:
            continue
        lines.append(f"{_HEADINGS[verdict]} — {len(rows)}")
        for row in sorted(rows, key=lambda r: -(r["age"] or 0)):
            lines.append(f"  {row['repo']}#{row['number']}  {row['title']} — {row['detail']}")

    if not any(by_verdict.get(v) for v in RAISING):
        lines.append("Nothing to act on.")

    for message in errors:
        lines.append(f"⚠ {message}")
    if caveat_repos:
        lines.append(
            "NOT JUDGED  whether a workflow's `paths:` filter excludes the files "
            "in one particular pull request. That is a legitimate empty rollup on "
            "a repo that has workflows, and it is why NO CI RUN names the repo's "
            f"workflow count rather than asserting a run was skipped: {', '.join(caveat_repos)}"
        )
    lines.append(
        f"Swept {swept} repo(s) for open pull requests; a finished one raises after "
        f"{max_age_days:g} day(s)."
    )
    return "\n".join(lines)


def main(argv=None, now=None, run=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--org", default="SokratesAI",
        help="GitHub org to sweep (default: %(default)s)",
    )
    parser.add_argument(
        "--max-age-days", type=float, default=DEFAULT_MAX_AGE_DAYS,
        help="how long a finished pull request may sit open before this "
             "exits 2 (default: one day of this loop's own wake-ups)",
    )
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc)

    repos, error, archived = repos_in_org(args.org, run=run)
    if error:
        print(f"COULD NOT LIST THE ORG — {error}")
        return 1

    errors = []
    results = []
    caveat_repos = []
    workflow_counts = {}
    trigger_reads = {}
    for repo in repos:
        prs, err = open_prs_for(repo, run=run)
        if err:
            errors.append(f"{repo}: could not list open pull requests — {err}")
            continue
        for pr in prs:
            needs_count = not (pr.get("statusCheckRollup") or []) and not is_held(pr)
            if needs_count and repo not in workflow_counts:
                count, count_err = has_workflows(repo, run=run)
                if count_err:
                    errors.append(f"{repo}: could not read the workflow count — {count_err}")
                workflow_counts[repo] = count
            pr_workflows = None
            if needs_count and workflow_counts.get(repo):
                # Only here: one contents call per active workflow, on a pull
                # request that has no check-runs at all on a repo that has
                # some. That is rare, and it is the only case where the
                # count alone gets the answer wrong.
                key = (repo, pr.get("headRefOid"))
                if key not in trigger_reads:
                    on_pr, unread, probe_err = pull_request_workflows(
                        repo, pr.get("headRefOid"), run=run
                    )
                    if probe_err:
                        errors.append(
                            f"{repo}: could not read the workflow triggers — {probe_err}"
                        )
                        trigger_reads[key] = None
                    else:
                        trigger_reads[key] = (on_pr, unread)
                pr_workflows = trigger_reads[key]
            verdict, detail = judge(
                pr, now, workflow_counts.get(repo), pr_workflows=pr_workflows,
                max_age_days=args.max_age_days
            )
            if verdict == "ready":
                # Only `ready` is asked. A failing or still-running pull
                # request has a different thing wrong with it, and a held
                # one was parked on purpose -- re-judging either would
                # spend API calls to change nothing. This is also the only
                # verdict where the answer turns a finding into a no-op.
                already, why = is_superseded(repo, pr, run=run)
                if already:
                    verdict, detail = "superseded", why
                elif already is None:
                    detail = f"{detail}; not judged as superseded — {why}"
            if verdict == "no_run" and repo not in caveat_repos:
                caveat_repos.append(repo)
            results.append(
                {
                    "repo": repo,
                    "number": pr.get("number"),
                    "title": (pr.get("title") or "")[:70],
                    "url": pr.get("url") or "",
                    "verdict": verdict,
                    "detail": detail,
                    "age": age_days(pr, now),
                }
            )

    print(format_report(results, len(repos), errors, caveat_repos, args.max_age_days))
    if archived:
        print(
            f"{len(archived)} archived repo(s) left out — they are read-only, "
            "so a pull request there cannot be merged."
        )
    # A stalled pull request outranks an unreadable one: `preflight` prints
    # the whole report for any non-zero exit, so the unreadable lines are in
    # front of the reader either way, and 2 is the code that says there is
    # something to go and do.
    if any(r["verdict"] in RAISING for r in results):
        return 2
    if any(r["verdict"] == "unreadable" for r in results) or errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
