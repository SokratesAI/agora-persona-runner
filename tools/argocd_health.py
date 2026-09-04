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

**A stale Job failure is not the cause, and this used to say it was.**
Cycle 513 wrote that ArgoCD holds an Application `Degraded` for as long
as a failed `Job` exists anywhere in its tree, so a failed Job with a
newer successful sibling from the same CronJob printed under
`STALE JOB FAILURE` and deliberately did not raise. That is wrong, and
Cycle 933 measured it from both ends. ArgoCD's own v3.3 documentation:
*"Argo CD App health is inferred from the health of its immediate child
resources as represented in the application source"* — and a Job created
by a CronJob is in no application source, so it is not an immediate child
and cannot hold anything `Degraded`. The measurement agrees: on 2026-09-05
`sokratesai-infra` was `Degraded`, this check blamed sixteen stale Jobs,
and not one of those Jobs appears in the Application's own
`status.resources`. What did was four SealedSecrets carrying
`Synced: False`.

So the stale Job lines stay — they are true, and they are the history a
CronJob keeps on purpose — but they are printed as history and **they no
longer excuse a `Degraded`**. An unhealthy Application whose cause this
check cannot name now raises, because Cycle 929 read the old quiet verdict
and planned a `resource.customizations.health.v1_Pod` Lua override to fix
Pods that were never the problem. A wrong explanation is worse than none.

**What it names instead: an immediate child that is actually unhealthy.**
This cluster's controller does not persist per-resource health, so every
entry in `status.resources` comes back with no health at all and the
Application object cannot say which child is red. The one signal that is
both cheap and unambiguous is a SealedSecret whose `Synced` condition is
False — the controller refusing to write the Secret git declares, which is
a real GitOps outage in its own right and is what was actually wrong here.
Other kinds are deliberately not guessed at: a kind this cannot judge is
named as unexplained rather than reported clean.

`lastTransitionTime` is printed beside every verdict for the same reason
`agentic_health` prints the streak and the last green date: "Degraded"
says nothing about whether to act, and "Degraded for 3 days, and the only
failing Job is one a later run succeeded past" is the finding.

Exit status, matching `tools.security_alerts`, `tools.cli_pin`,
`tools.agentic_health`, `tools.heartbeat_health` and
`tools.helm_repo_health` so a cycle can read it without parsing the text:
**2 means an Application is out of sync or unhealthy**, named cause or not,
1 means something was unreadable — which includes kubectl being refused,
and never reads as clean — and 0 means every Application answered Synced
and Healthy, naming what it swept either way.
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
        resources = status.get("resources") or []
        cronjobs = [
            (r.get("namespace") or "", r.get("name") or "")
            for r in resources
            if r.get("kind") == "CronJob"
        ]
        # The immediate children, which is the set ArgoCD actually
        # aggregates the App's health from. Anything a controller created
        # underneath one of these — a Job under a CronJob, a Pod under a
        # Job — is absent from here, and that absence is the whole point.
        sealed = [
            (r.get("namespace") or "", r.get("name") or "")
            for r in resources
            if r.get("kind") == "SealedSecret"
        ]
        apps.append({
            "name": meta.get("name", "?"),
            "sync": (status.get("sync") or {}).get("status") or "Unknown",
            "health": health.get("status") or "Unknown",
            "since": health.get("lastTransitionTime") or "",
            "cronjobs": cronjobs,
            "sealed": sealed,
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


def read_sealed_secrets(runner=subprocess.run):
    """Every SealedSecret, keyed by (namespace, name). (dict, None) or (None, why).

    The value is the `Synced` condition's message when that condition is
    anything but True, and None when the SealedSecret is fine. A
    SealedSecret with no conditions at all has not been reconciled yet and
    is not a finding — the controller writes them on its first pass.
    """
    body, why = _run(runner, ["kubectl", "get", "sealedsecrets", "-A", "-o", "json"])
    if why:
        return None, why

    broken = {}
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        key = (meta.get("namespace") or "", meta.get("name") or "")
        for condition in ((item.get("status") or {}).get("conditions") or []):
            if condition.get("type") != "Synced":
                continue
            if condition.get("status") == "True":
                continue
            broken[key] = (
                condition.get("message")
                or condition.get("reason")
                or "Synced is not True"
            )
    return broken, None


def unhealthy_children(app, broken_sealed):
    """The immediate children of `app` that are measurably unhealthy.

    Immediate is the operative word and it is why this reads the
    Application's own `status.resources` rather than the cluster: that list
    is exactly what ArgoCD aggregates the App health from.
    """
    rows = []
    for key in app.get("sealed") or []:
        message = broken_sealed.get(key)
        if message:
            rows.append({
                "kind": "SealedSecret",
                "namespace": key[0],
                "name": key[1],
                "why": message,
            })
    return rows


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


def report(apps, jobs_by_owner, now, broken_sealed=None):
    """The printed lines and the exit status, as (lines, status)."""
    lines = []
    actionable = False
    unexplained = []
    broken_sealed = broken_sealed or {}

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

        actionable = True
        stale, live = stale_job_failures(app["cronjobs"], jobs_by_owner)
        children = unhealthy_children(app, broken_sealed)
        lines.append(f"UNHEALTHY  {app['name']}: {app['health']}{aged}")
        for row in children:
            lines.append(
                f"           {row['kind']} {row['namespace']}/{row['name']} "
                f"is not healthy: {row['why']}")
        for row in live:
            lines.append(
                f"           {row['namespace']}/{row['job']} failed and "
                f"{row['cronjob']} has not succeeded since")
        if not children:
            unexplained.append(f"{app['name']} ({app['health']}{aged})")
            lines.append(
                "           no immediate child of it is measurably unhealthy — "
                "this controller does not persist per-resource health, so the "
                "Application object cannot name the one that is")
        if stale:
            # History, not a cause. A Job owned by a CronJob is created by a
            # controller and appears in no application source, so it is not an
            # immediate child and ArgoCD never aggregates it into App health.
            # One line rather than sixteen for the same reason: these used to
            # be the verdict, and leaving them at full length would keep them
            # reading like one. `tools.cronjob_health` is what judges them.
            lines.append(
                f"           (history, not the cause) {len(stale)} failed Job(s) "
                "retained by their CronJob's failedJobsHistoryLimit, every one "
                "succeeded past — tools.cronjob_health judges those")

    swept = (
        f"Read {len(apps)} ArgoCD Application(s) from the live cluster, not from git.")
    if unexplained:
        # `preflight` collapses a check to one line, and it picks the last
        # line carrying a digit — which is this one. Cycle 810 swept clean
        # here and still wrote "sokratesai-infra reports Degraded and I do
        # not know why or since when" into the handoff, while the answer and
        # the age were both three lines above the summary. The names ride on
        # the swept line specifically because that is the line the collapse
        # keeps.
        swept += (
            " Unhealthy with no immediate child this check can name as the"
            f" cause: {', '.join(unexplained)}.")
    lines.append(swept)
    if unexplained:
        lines.append(
            "An unexplained Degraded raises. It used to be excused by a stale Job "
            "failure, which cannot hold an Application Degraded at all — a Job "
            "under a CronJob is not an immediate child of the Application.")
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
    broken_sealed = {}
    if any(a["health"] in UNHEALTHY for a in apps):
        jobs_by_owner, why = read_jobs(runner)
        if why:
            print(f"COULD NOT READ  {why}")
            return 1
        broken_sealed, why = read_sealed_secrets(runner)
        if why:
            print(f"COULD NOT READ  {why}")
            return 1

    lines, status = report(
        apps, jobs_by_owner,
        now or datetime.datetime.now(datetime.timezone.utc),
        broken_sealed)
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
