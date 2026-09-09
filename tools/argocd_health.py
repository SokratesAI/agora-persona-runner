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
Application object cannot say which child is red. So a child's health is
measured off the child, one kind at a time, and never read out of the
Application.

A SealedSecret whose `Synced` condition is False is the first: the
controller refusing to write the Secret git declares, which is a real
GitOps outage in its own right and is what was actually wrong here. A
Deployment past its own availability budget is the second — the same
verdict `tools.workload_health` reaches, reused rather than re-derived,
because hand-probing the children of one Degraded Application cost cycle
1271 nine minutes and that is exactly the work an instrument should do.

Other kinds are deliberately not guessed at: a kind this cannot judge is
named as unexplained rather than reported clean. The unexplained line says
which probes ran, so "nothing found" cannot be confused with "nothing
looked".

`lastTransitionTime` is printed beside every verdict for the same reason
`agentic_health` prints the streak and the last green date: "Degraded"
says nothing about whether to act, and "Degraded for 3 days, and the only
failing Job is one a later run succeeded past" is the finding.

**A `Suspended` Application whose CronJob children git suspended is a
decision, not a finding.** `sokratesai-infra` reads `Synced, Suspended`
permanently because two of the CronJobs it owns carry `spec.suspend: true`
in their manifests, and this check raised on it every sweep. That is the
`security_alerts` already-fixed problem in a different suit: a permanent
finding makes `preflight` collapse the whole check to one standing line, so
a real `Degraded` arriving tomorrow would read as the same unchanged row.
The excuse needs the Application to be `Synced` as well, and that is the
argument rather than a convenience — ArgoCD diffs `spec.suspend`, so a
CronJob suspended by hand shows the Application `OutOfSync`. A `Suspended`
this cannot attribute to a suspended CronJob child still raises.

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

from tools import workload_health

# ArgoCD's own health vocabulary. `Progressing` is deliberately not in
# here: a sync in flight is the normal state a few seconds after every
# merge, and a checker that called it a finding would fire on its own
# cycle's deploy.
UNHEALTHY = {"Degraded", "Missing", "Unknown", "Suspended"}

# `Suspended` is in that set and stays in it, but it is the one member that
# has an innocent cause this check can prove. ArgoCD reports an Application
# `Suspended` when a child is deliberately paused, and the commonest such
# child here is a CronJob carrying `spec.suspend: true` -- which is a line
# somebody wrote in a manifest, not a fault. `sokratesai-infra` holds two of
# them (`agents/heartbeat-liveness`, `infra/claude-child-reaper`), so the
# Application reads `Suspended` permanently and this check raised on it
# permanently, which is worse than useless: `preflight` then collapses the
# whole check to one standing line and a real `Degraded` arriving later
# reads as the same unchanged finding.
#
# The excuse is only taken when the Application is *also* `Synced`, and that
# is the whole argument rather than a convenience. ArgoCD diffs `spec.suspend`
# like any other field, so a CronJob suspended by hand on the cluster shows the
# Application `OutOfSync`. `Synced` + a suspended CronJob child therefore means
# git asked for the suspension. Anything else keeps raising, including a
# `Suspended` this cannot attribute to a CronJob at all -- a paused Deployment
# or Rollout would land there, and it should be looked at.

# ...but only for as long as it is plausibly still in flight. A rollout that
# never converges stays `Progressing` forever, and until Cycle 1270 that read
# as `ok` with no bound at all: `sokratesai-infra` had been `Progressing`
# since 2026-09-08 10:28 UTC -- 20 hours, its sync operation long since
# `Succeeded` -- and this check printed `ok  sokratesai-infra: Synced,
# Progressing` and exited 0.
#
# The bound is derived, not chosen for comfort. The slowest rollout measured
# in this cluster is Marcus at about four minutes, and `workload_health`
# judges an unavailable Deployment against its own terminationGracePeriodSeconds
# plus five minutes. Thirty minutes is several times the slowest real
# convergence here and two orders of magnitude under the stall it exists to
# catch, so it cannot fire on a cycle's own deploy while a wedged rollout can
# no longer hide in it.
PROGRESSING_GRACE_SECONDS = 30 * 60


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
        # Deployments are carried for the same reason CronJobs and
        # SealedSecrets are: they are a kind whose health this can go and
        # measure itself when the Application object refuses to name it.
        deployments = [
            (r.get("namespace") or "", r.get("name") or "")
            for r in resources
            if r.get("kind") == "Deployment"
        ]
        apps.append({
            "name": meta.get("name", "?"),
            "sync": (status.get("sync") or {}).get("status") or "Unknown",
            "health": health.get("status") or "Unknown",
            "since": health.get("lastTransitionTime") or "",
            "cronjobs": cronjobs,
            "sealed": sealed,
            "deployments": deployments,
        })
    return apps, None


def read_suspended_cronjobs(runner=subprocess.run):
    """The (namespace, name) of every CronJob with `spec.suspend: true`.

    Returns (set, None) or (None, why). Absent-and-false are the same answer
    here: `spec.suspend` is optional and unset means running.
    """
    body, why = _run(runner, ["kubectl", "get", "cronjobs", "-A", "-o", "json"])
    if why:
        return None, why

    suspended = set()
    for item in body.get("items") or []:
        if not (item.get("spec") or {}).get("suspend"):
            continue
        meta = item.get("metadata") or {}
        suspended.add((meta.get("namespace") or "", meta.get("name") or ""))
    return suspended, None


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

    The value is `{"why": <the Synced condition's message>, "since": <its
    lastTransitionTime>}` when that condition is anything but True, and the
    key is absent when the SealedSecret is fine. A SealedSecret with no
    conditions at all has not been reconciled yet and is not a finding — the
    controller writes them on its first pass.

    `since` is carried because the message alone says nothing about whether
    this is new. The four this fires on today last transitioned on
    2026-07-22 and 2026-07-27, so they have never once synced — and every
    cycle that read the bare message read a six-week-old outage as a fresh
    one, and went looking for what changed.
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
            broken[key] = {
                "why": (
                    condition.get("message")
                    or condition.get("reason")
                    or "Synced is not True"
                ),
                "since": condition.get("lastTransitionTime") or "",
            }
    return broken, None


# The one SealedSecret failure a pull request cannot fix, spelled out here
# because four cycles have now each paid to re-derive that. The controller
# refuses to overwrite a Secret it does not own, so the SealedSecret in git
# is never applied and git has quietly stopped being the source of truth for
# that credential. The obvious guess is a second declaration somewhere; it is
# wrong — measured Cycle 978, each of the four names this fires on today is
# declared exactly once across `platform-config`. The fix is a write against
# the *live* Secret, and both of this loop's accounts are refused `get
# secrets` in every namespace, let alone `annotate`.
_UNOWNED_SECRET = "is not managed by SealedSecret"


def takeover_remedy(why, namespace, name):
    """The remedy line for a SealedSecret blocked on a Secret it does not own.

    Returns None for every other SealedSecret failure — a wrong key, a
    namespace mismatch, a controller that has not caught up. That is the
    point rather than caution: a remedy printed beside a message it does not
    fit reads as instructions, and a cycle following it would annotate a
    Secret whose problem was something else entirely.
    """
    if _UNOWNED_SECRET not in (why or ""):
        return None
    return (
        f"kubectl annotate secret -n {namespace} {name} "
        "sealedsecrets.bitnami.com/managed=true"
    )


def declared_suspensions(app, suspended_cronjobs):
    """The app's own CronJob children that git has suspended, as `ns/name`.

    Empty for an Application that is not `Synced`, because then the
    suspension is not demonstrably what git asked for — see `UNHEALTHY`.
    """
    if app.get("sync") != "Synced":
        return []
    return sorted(
        f"{ns}/{name}"
        for ns, name in app.get("cronjobs") or []
        if (ns, name) in (suspended_cronjobs or set())
    )


def _deployment_why(row):
    """Why an unavailable Deployment is past its own budget, in one clause."""
    budget = workload_health._duration(row["budget"].total_seconds())
    reason = row.get("reason") or "no reason given"
    if row.get("down") is None:
        return (f"Available=False with no readable transition time, so it "
                f"cannot be shown to be inside its own {budget} budget "
                f"({reason})")
    return (f"Available=False for "
            f"{workload_health._duration(row['down'].total_seconds())}, past "
            f"the {budget} its own terminationGracePeriodSeconds allows "
            f"({reason})")


def unhealthy_children(app, broken_sealed, past_budget=None):
    """The immediate children of `app` that are measurably unhealthy.

    Immediate is the operative word and it is why the *set* of children comes
    from the Application's own `status.resources` rather than from a sweep of
    the cluster: that list is exactly what ArgoCD aggregates the App health
    from.

    Their *health*, though, cannot come from there. This controller does not
    persist per-resource health, so every entry in `status.resources` carries
    no health key at all and this function used to be able to name exactly one
    kind of child — a SealedSecret, judged by a separate read. `past_budget`
    is the second such read: `workload_health.unavailable`'s past-budget half,
    keyed `(namespace, name)`, which is a real measurement of a Deployment
    child taken off the Deployment itself. `None` means nobody probed, and is
    not the same answer as `{}`, which means the probe ran and found nothing.
    """
    rows = []
    for key in app.get("sealed") or []:
        broken = broken_sealed.get(key)
        if broken:
            rows.append({
                "kind": "SealedSecret",
                "namespace": key[0],
                "name": key[1],
                "why": broken["why"],
                "since": broken.get("since") or "",
            })
    for key in app.get("deployments") or []:
        down = (past_budget or {}).get(key)
        if down:
            rows.append({
                "kind": "Deployment",
                "namespace": key[0],
                "name": key[1],
                "why": _deployment_why(down),
                "since": down.get("since") or "",
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


def _age_seconds(since, now):
    """Seconds since an RFC3339 stamp, or None when it cannot be read.

    None is not zero and not "fresh": a stamp this cannot parse proves
    nothing about how long a rollout has been running, and the caller has to
    say so rather than pick a direction.
    """
    if not since:
        return None
    try:
        at = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        return None
    seconds = (now - at).total_seconds()
    return None if seconds < 0 else seconds


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


def _progressing_too_long(app, now):
    """Seconds an app has been `Progressing` past the grace, or None.

    None covers all three of "not Progressing", "inside the grace" and "the
    stamp is unreadable" -- the last of those on purpose. A `Progressing` with
    no readable `lastTransitionTime` cannot be *shown* to be stuck, and raising
    on it would report a clock problem as a rollout problem. It still prints
    with `age ?` on the ok line, so it cannot read as measured.
    """
    if app.get("health") != "Progressing":
        return None
    seconds = _age_seconds(app.get("since") or "", now)
    if seconds is None or seconds <= PROGRESSING_GRACE_SECONDS:
        return None
    return seconds


def report(apps, jobs_by_owner, now, broken_sealed=None,
           suspended_cronjobs=None, past_budget=None):
    """The printed lines and the exit status, as (lines, status)."""
    lines = []
    actionable = False
    unexplained = []
    broken_sealed = broken_sealed or {}
    suspended_cronjobs = suspended_cronjobs or set()

    for app in sorted(apps, key=lambda a: a["name"]):
        age = _age(app["since"], now)
        aged = f", {age}" if age else ""
        if app["sync"] != "Synced":
            actionable = True
            lines.append(
                f"OUT OF SYNC  {app['name']}: git and the cluster disagree "
                f"({app['sync']})")
        if app["health"] not in UNHEALTHY:
            if _progressing_too_long(app, now) is not None:
                actionable = True
                lines.append(
                    f"STUCK PROGRESSING  {app['name']}: Progressing for "
                    f"{age or '?'}, since {app['since']} — past the "
                    f"{PROGRESSING_GRACE_SECONDS // 60}m a real rollout in this "
                    "cluster takes, so this is a rollout that is not converging "
                    "rather than one in flight")
                continue
            if app["sync"] == "Synced":
                lines.append(
                    f"ok      {app['name']}: Synced, {app['health']}{aged}")
            continue

        if app["health"] == "Suspended":
            paused = declared_suspensions(app, suspended_cronjobs)
            if paused:
                lines.append(
                    f"ok      {app['name']}: Synced, Suspended{aged} — "
                    f"{len(paused)} CronJob(s) suspended in git, which is what "
                    f"ArgoCD is reporting: {', '.join(paused)}")
                continue

        actionable = True
        stale, live = stale_job_failures(app["cronjobs"], jobs_by_owner)
        children = unhealthy_children(app, broken_sealed, past_budget)
        lines.append(f"UNHEALTHY  {app['name']}: {app['health']}{aged}")
        for row in children:
            lines.append(
                f"           {row['kind']} {row['namespace']}/{row['name']} "
                f"is not healthy: {row['why']}")
            remedy = takeover_remedy(row["why"], row["namespace"], row["name"])
            if remedy:
                # Still raises. This is a real GitOps outage and going quiet
                # on it would be the `security_alerts` already-fixed carve-out
                # applied to something that is not fixed. What the line adds is
                # that no pull request closes it, so the next cycle can stop at
                # reading it instead of re-diagnosing it.
                lines.append(
                    f"             no pull request fixes this — the remedy is a "
                    f"cluster write this loop is refused: {remedy}")
                lines.append(
                    "             it overwrites the live Secret with what git "
                    "holds, and this account cannot read either to compare "
                    "them, so it is the owner's call to run")
                waited = _age(row.get("since"), now)
                if waited:
                    lines.append(
                        f"             it has been failing for {waited}, since "
                        f"{row['since']} — nothing here has moved it and "
                        "nothing here can, so the wait is on the owner")
        for row in live:
            lines.append(
                f"           {row['namespace']}/{row['job']} failed and "
                f"{row['cronjob']} has not succeeded since")
        if not children:
            unexplained.append(f"{app['name']} ({app['health']}{aged})")
            probed = (
                "no Deployment child of it is past its own availability "
                "budget either"
                if past_budget is not None else
                "and no Deployment child of it was probed")
            lines.append(
                "           no immediate child of it is measurably unhealthy — "
                f"{probed}. This controller does not persist per-resource "
                "health, so for every other kind the Application object "
                "cannot name the one that is")
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
    at = now or datetime.datetime.now(datetime.timezone.utc)
    jobs_by_owner = {}
    broken_sealed = {}
    suspended_cronjobs = set()
    # None, not {}: "nobody probed" and "probed and found nothing" are
    # different answers and the unexplained line prints which one it is.
    past_budget = None
    if any(a["health"] == "Suspended" for a in apps):
        suspended_cronjobs, why = read_suspended_cronjobs(runner)
        if why:
            print(f"COULD NOT READ  {why}")
            return 1
    if any(a["health"] in UNHEALTHY for a in apps):
        jobs_by_owner, why = read_jobs(runner)
        if why:
            print(f"COULD NOT READ  {why}")
            return 1
        broken_sealed, why = read_sealed_secrets(runner)
        if why:
            print(f"COULD NOT READ  {why}")
            return 1
        # Same bargain as the Jobs read above: only paid for when something
        # is actually unhealthy, and a refusal is `COULD NOT READ` rather
        # than a quiet `no Deployment child is unhealthy`, which is what an
        # unprobed cluster would otherwise look like.
        deployments, why = workload_health.read_deployments(runner)
        if why:
            print(f"COULD NOT READ  {why}")
            return 1
        past, _rolling = workload_health.unavailable(deployments, at)
        past_budget = {(d["namespace"], d["name"]): d for d in past}

    lines, status = report(
        apps, jobs_by_owner, at,
        broken_sealed, suspended_cronjobs, past_budget)
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
