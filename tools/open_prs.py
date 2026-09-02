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

`NO CI RUN` is the `#566` shape: the pull request has no check-runs *and*
its repository has at least one workflow, so a run was expected and never
appeared. The workflow probe is the whole point -- the four `*-config`
repos return `total_count: 0` from `actions/workflows`, so a check-run
was never coming there and calling that a finding would be the negative
result that was guaranteed in advance. What this cannot see is a workflow
whose `paths:` filter excludes the files in one particular pull request;
that is a legitimate empty rollup on a repo with workflows, and it is
printed as a caveat rather than silently folded into either bucket.

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
import json
import subprocess
import sys
from datetime import datetime, timezone

from tools.security_alerts import repos_in_org

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


def judge(pr, now, workflow_count, max_age_days=DEFAULT_MAX_AGE_DAYS):
    """`(verdict, detail)` for one pull request.

    `workflow_count` is the repository's own, or `None` when it could not
    be read -- and an unreadable count never turns an empty rollup into a
    finding, because then the finding would rest on the thing that failed.
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
            verdict, detail = judge(
                pr, now, workflow_counts.get(repo), max_age_days=args.max_age_days
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
