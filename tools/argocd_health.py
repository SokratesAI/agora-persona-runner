"""Is every ArgoCD Application still Synced and Healthy — and if not, why?

Cycle 513. `sokratesai-infra` had been reporting `Degraded` since
2026-08-24 09:31 UTC — three days — and nothing in this loop reads
Application health at all. `tools.helm_repo_health` reads Applications,
but only their `spec.source`, so an Application whose chart resolves fine
and whose workloads are on fire reads as clean there. Step 1a runs seven
checks and every one of them measures something *else*: pods, advisories,
gh-aw runs, document integrity, version pins, heartbeats, chart indexes.
The thing that decides what is actually deployed in this cluster had
nothing looking at it.

    python3 -m tools.argocd_health

**It reads the live cluster, never git** — the same argument
`helm_repo_health` makes one layer up. An Application's health is a
statement about running objects, and `argocd/application.yaml` is
excluded from what ArgoCD syncs anyway, so a check built on the repo
would go green on a merge while the cluster stayed red.

**Sync and health are separate verdicts and this never merges them.**
`OutOfSync` means git and the cluster disagree; `Degraded` means the
cluster is unhappy with what it already has. They have different causes
and different fixes, and `agentic_health` had to learn one layer down
that a single rolled-up number sends cycles chasing the wrong half.

**The one judgement it makes: a stale Job failure is not an outage.**
ArgoCD holds an Application `Degraded` for as long as a failed `Job`
exists anywhere in its tree, and a `CronJob` keeps its failures around by
design (`failedJobsHistoryLimit`). So `sokratesai-infra` was `Degraded`
because `newspaper-rss-refresh` failed on 08-23 and 08-24 — while the
same CronJob had run successfully twice since. That is a true fact about
the past and it is not something a cycle should act on, so a failed Job
with a newer successful sibling from the same CronJob prints under
`STALE JOB FAILURE` and **deliberately does not raise the exit status**.
Same call as `security_alerts` on an already-fixed advisory and
`agentic_health` on a run GitHub refused to start: a finding that no
pull request can fix, printed every cycle, is a finding every cycle
re-derives and nobody acts on.

`lastTransitionTime` is printed beside every verdict for the same reason
`agentic_health` prints the streak and the last green date: "Degraded"
says nothing about whether to act, and "Degraded for 3 days, and the only
failing Job is one a later run succeeded past" is the finding.

Exit status, matching `tools.security_alerts`, `tools.cli_pin`,
`tools.agentic_health`, `tools.heartbeat_health` and
`tools.helm_repo_health` so a cycle can read it without parsing the text:
**2 means an Application is out of sync or unhealthy for a reason a cycle
could act on**, 1 means something was unreadable — which includes kubectl
being refused, and never reads as clean — and 0 means every Application
answered Synced and Healthy, or is unhealthy only because of a stale Job
failure, naming what it swept either way.
"""

import argparse
import datetime
import json
import subprocess
import sys

# ArgoCD's own health vocabulary. `Progressing` is deliberately not in
# here: a sync in flight is the normal state a few seconds after every
# merge, and a checker that called it a finding would fire on its own
# cycle's deploy.
UNHEALTHY = {"Degraded", "Missing", "Unknown", "Suspended"}


def _run(runner, args):
    try:
        proc = runner(args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl failed: {exc}"
    if proc.returncode != 0:
        return None, f"kubectl failed: {proc.stderr.strip() or proc.stdout.strip()}"
    try:
        body = json.loads(proc.stdout)
    except ValueError as exc:
        return None, f"kubectl returned something that is not JSON: {exc}"
    if not isinstance(body, dict):
        return None, "kubectl returned JSON that is not an object"
    return body, None


def read_applications(runner=subprocess.run):
    """Every live ArgoCD Application, as (list, None) or (None, why).

    Each entry carries the two verdicts separately plus the CronJobs the
    Application tracks, because a `Degraded` needs a cause and ArgoCD does
    not put per-resource health on the Application object — this cluster's
    `status.resourceHealthSource` is `appTree`, so that detail lives in the
    controller's cache and not in anything kubectl returns.
    """
    body, why = _run(runner, ["kubectl", "get", "applications", "-A", "-o", "json"])
    if why:
        return None, why

    apps = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        status = item.get("status") or {}
        health = status.get("health") or {}
        cronjobs = [
            (r.get("namespace") or "", r.get("name") or "")
            for r in status.get("resources") or []
            if r.get("kind") == "CronJob"
        ]
        apps.append({
            "name": meta.get("name", "?"),
            "sync": (status.get("sync") or {}).get("status") or "Unknown",
            "health": health.get("status") or "Unknown",
            "since": health.get("lastTransitionTime") or "",
            "cronjobs": cronjobs,
        })
    return apps, None


def read_jobs(runner=subprocess.run):
    """Every Job in the cluster, keyed by (namespace, owning CronJob).

    Returns (dict, None) or (None, why). A Job created by a CronJob names
    its parent in `ownerReferences`, which is the only link back — the
    generated name is `<cronjob>-<unix-minutes>` and parsing that would be
    a substring search standing in for a field that is right there.
    """
    body, why = _run(runner, ["kubectl", "get", "jobs", "-A", "-o", "json"])
    if why:
        return None, why

    by_owner = {}
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        status = item.get("status") or {}
        for owner in meta.get("ownerReferences") or []:
            if owner.get("kind") != "CronJob":
                continue
            key = (meta.get("namespace") or "", owner.get("name") or "")
            by_owner.setdefault(key, []).append({
                "name": meta.get("name", "?"),
                "created": meta.get("creationTimestamp") or "",
                "failed": bool(status.get("failed")),
                "succeeded": bool(status.get("succeeded")),
            })
    return by_owner, None


def stale_job_failures(cronjobs, jobs_by_owner):
    """Failed Jobs a later run of the same CronJob has already succeeded past.

    Returned as (stale, live). `stale` is the ones no pull request can
    fix — the CronJob works, an old failure is simply still on the cluster
    because `failedJobsHistoryLimit` keeps it there. `live` is a failure
    with nothing newer that worked, which is a real one.

    Comparison is on `creationTimestamp`, not on the name. The generated
    suffix happens to sort correctly today because it counts minutes, and
    relying on that would be reading a schedule out of a string.

    The timestamps are compared as strings, which is only safe because
    `metav1.Time` serialises to RFC3339 in UTC with a `Z` and no fractional
    part — every stamp kubectl returns has the same width and the same zone,
    so lexical order is chronological order. If these ever came from
    somewhere other than the API server, parse them.
    """
    stale, live = [], []
    for key in cronjobs:
        runs = jobs_by_owner.get(key) or []
        newest_success = max(
            (r["created"] for r in runs if r["succeeded"]), default="")
        for run in runs:
            if not run["failed"]:
                continue
            row = {"namespace": key[0], "cronjob": key[1], "job": run["name"],
                   "created": run["created"]}
            # A failure with no timestamp cannot be *proven* superseded, and
            # the empty string sorts before every real stamp — so the naive
            # comparison would call it stale and go quiet on it. Quiet is the
            # one direction this must never fail in.
            if not run["created"]:
                live.append(row)
            elif newest_success and newest_success > run["created"]:
                row["succeeded_since"] = newest_success
                stale.append(row)
            else:
                live.append(row)
    return stale, live


def _age(since, now):
    """"3 days" from an RFC3339 stamp, or "" when it cannot be read.

    Never raises: an unparseable timestamp must cost the age, not the
    verdict it sits beside.
    """
    if not since:
        return ""
    try:
        at = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        return ""
    seconds = (now - at).total_seconds()
    if seconds < 0:
        return ""
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def report(apps, jobs_by_owner, now):
    """The printed lines and the exit status, as (lines, status)."""
    lines = []
    actionable = False
    held = []

    for app in sorted(apps, key=lambda a: a["name"]):
        age = _age(app["since"], now)
        aged = f", {age}" if age else ""
        if app["sync"] != "Synced":
            actionable = True
            lines.append(
                f"OUT OF SYNC  {app['name']}: git and the cluster disagree "
                f"({app['sync']})")
        if app["health"] not in UNHEALTHY:
            if app["sync"] == "Synced":
                lines.append(f"ok      {app['name']}: Synced, {app['health']}")
            continue

        stale, live = stale_job_failures(app["cronjobs"], jobs_by_owner)
        if live or not stale:
            actionable = True
            lines.append(f"UNHEALTHY  {app['name']}: {app['health']}{aged}")
            for row in live:
                lines.append(
                    f"           {row['namespace']}/{row['job']} failed and "
                    f"{row['cronjob']} has not succeeded since")
            if not live:
                lines.append(
                    "           nothing in its tracked resources explains it — "
                    "ArgoCD keeps per-resource health in the app tree, not on "
                    "the Application, so open the UI")
            continue

        held.append(f"{app['name']} ({app['health']}{aged}, {len(stale)} Job(s))")
        lines.append(
            f"STALE JOB FAILURE  {app['name']}: {app['health']}{aged}, and every "
            f"failing Job it holds has been succeeded past")
        for row in stale:
            lines.append(
                f"           {row['namespace']}/{row['job']} failed "
                f"{row['created']}, {row['cronjob']} succeeded "
                f"{row['succeeded_since']}")

    swept = (
        f"Read {len(apps)} ArgoCD Application(s) from the live cluster, not from git.")
    if held:
        # `preflight` collapses a check that exits 0 to one line, and it picks
        # the last line carrying a digit — which was this one. So a
        # STALE JOB FAILURE, the whole reason this check does not raise,
        # vanished from the only report a cycle reads every morning: Cycle 810
        # swept clean here and still wrote "sokratesai-infra reports Degraded
        # and I do not know why or since when" into the handoff, while the
        # answer and the age were both three lines above the summary. The
        # names ride on the swept line specifically because that is the line
        # the collapse keeps.
        swept += (
            " Held unhealthy right now by stale Job failures and deliberately"
            f" not raised: {', '.join(held)}.")
    lines.append(swept)
    if held:
        lines.append(
            "A Degraded held open by a Job a later run succeeded past is not raised — "
            "no pull request fixes it and every cycle would re-derive it.")
    return lines, (2 if actionable else 0)


def main(argv=None, runner=subprocess.run, now=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    apps, why = read_applications(runner)
    if why:
        print(f"COULD NOT READ  {why}")
        return 1
    if not apps:
        # An empty list from a working kubectl is not a clean bill of
        # health, it is no instrument: this cluster runs ArgoCD, so zero
        # Applications means the query looked in the wrong place.
        print("COULD NOT READ  kubectl returned no Applications at all")
        return 1

    # Jobs are only ever consulted to explain an unhealthy Application, and
    # `kubectl get jobs -A` is a cluster-wide read that a restricted account
    # can be refused. Asking for it on a clean cluster would turn a green
    # answer into `COULD NOT READ` for data nothing was going to use.
    jobs_by_owner = {}
    if any(a["health"] in UNHEALTHY for a in apps):
        jobs_by_owner, why = read_jobs(runner)
        if why:
            print(f"COULD NOT READ  {why}")
            return 1

    lines, status = report(apps, jobs_by_owner, now or datetime.datetime.now(datetime.timezone.utc))
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
