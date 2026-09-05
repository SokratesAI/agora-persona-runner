"""Is every Kubernetes CronJob in this cluster still succeeding on its own schedule?

Cycle 856, on my own issue filed Cycle 823 and open since. This cluster runs
twelve CronJobs and nothing here read one. `tools.schedule_health` judges
GitHub Actions schedules and `tools.heartbeat_health` judges Agora's; the
CronJobs -- `deploy-rollback` among them, which is the automatic revert that
undoes a bad deploy with nobody watching -- had no instrument at all. So the
watchdog had no watchdog, and a suspended or failing one looked exactly like
a quiet one.

    python3 -m tools.cronjob_health

**It reads the live cluster, never git**, the same call `helm_repo_health` and
`argocd_health` make: a manifest ArgoCD has not synced is not what is running,
and `spec.suspend` is a live field a person can flip with `kubectl patch`
without touching a repo.

**The verdict is positional, not temporal, and that is the whole design.**
The obvious check is "has it run in the last N minutes", and that is wrong
here for the reason `rollback_watch` is positional too: N is a number I would
have invented, and it has to be re-derived for every schedule from `*/5` to
`0 23 * * 6` -- a weekly job is nine days quiet between successes and healthy
the whole time. So this counts **scheduled slots** instead. Kubernetes writes
`status.lastScheduleTime` when it creates a Job and `status.lastSuccessfulTime`
when one finishes, and the number of firings of the CronJob's *own* cron
between those two stamps is the number of consecutive runs that did not
succeed. `deploy-rollback` measured 2 slots behind at 21:51 Oslo on 09-03
(scheduled 19:50Z, last succeeded 19:40Z, `*/5 * * * *`); a healthy weekly job
measures 0 whether it ran an hour ago or six days ago.

**The grace is one slot and it is derived, not chosen.** A run created this
minute has not succeeded yet and never could have, so a CronJob is always
allowed to be exactly one slot behind. Two is the first number that cannot be
explained by a run still in flight.

**A suspended CronJob is not judged, and that is not the same as passing.**
`heartbeat_health` raises on a heartbeat that is switched off unless its own
name says the switch was deliberate, and `argocd_health` counts ArgoCD's
`Suspended` health as unhealthy -- so the honest description of this is that
it does *neither*, for one reason: this loop's kubectl is read-only, no pull
request re-enables a CronJob, and there is no annotation anywhere in this
cluster marking a suspension as intended. Raising would put the check red on
its first run and every run after, which is the same as off, and there is
today no marker to make an exception against.

What it does instead is refuse to call it clean. The line opens
`NOT JUDGED`, which is `preflight`'s own caveat form -- a stem in
`CAVEAT_STEMS` behind a shouted head -- so a suspension is pulled out and
printed under the collapsed row rather than buried in the tail of a summary
sentence in a row marked `ok`. The names ride on the summary line as well,
because that is the line the collapse keeps. `agents/heartbeat-liveness` has
been suspended since 2026-05-01 and has never succeeded; that is the shape
this rule exists for, and the moment anything in this cluster marks a
suspension deliberate, this should raise on the ones that are not.

**`NEVER SCHEDULED` and `NEVER SUCCEEDED` are separate from `BEHIND`**, the
same call `schedule_health` makes on NEVER FIRED versus OVERDUE. A CronJob
Kubernetes has never created a Job for is a controller that does not know
about it; one it has scheduled and that has never once finished is broken
from birth; one that used to work and stopped is a regression. Three
different causes, three different first questions, so they are never merged
into one count.

**A CronJob younger than its own first slot is not judged at all**, which is
the same call the suspend rule makes and for the same reason. Neither
`lastScheduleTime` nor `lastSuccessfulTime` exists until a slot has come
round, so a brand-new CronJob carries exactly the empty status of a
controller that has never heard of it. Measured Cycle 920:
`agents/agora-backup` was created at 05:26 Oslo on a `40 3 * * *` schedule
and read `NEVER SCHEDULED` -- the loudest verdict here -- for the thirteen
and a half hours between its creation and my reading it, with another nine
to go before its first firing was even due. `preflight` exited 2 on it every
cycle in between. The grace is `GRACE_SLOTS`, the same one slot the
BEHIND branch already allows and for the same argument: the newest slot's
run may still be in flight, so one is explainable and two is not. It fails
loud rather than quiet -- a `creationTimestamp` that is missing or
unreadable buys no grace and the old verdict stands, because an age nobody
can read is not an excuse.

Exit status, matching `tools.argocd_health`, `tools.heartbeat_health` and
`tools.schedule_health` so a cycle can read it without parsing the text:
**2 means a CronJob is failing on its own schedule**, 1 means something was
unreadable -- which includes kubectl being refused, a schedule this cannot
parse, and finding no CronJobs at all, and never reads as clean -- and 0
means every CronJob swept is inside one slot of its own schedule, naming
what it swept either way.
**A schedule is only half of it, and the other half cost a night's backup.**
`agents/agora-backup` fired for the first time at 03:40 Oslo on 2026-09-05 and
both attempts aborted with `expected exactly one *_agents_agora-data directory
under /storage, found 0`. The CronJob was pinned to `server1`; the volume had
moved to `server2` the day before, and a local-path volume is a directory on
one node's disk that exists nowhere else. Every schedule verdict here was
correct and said nothing -- the job was on time, on the wrong box. So this now
also compares each CronJob's `kubernetes.io/hostname` pin against the node its
declared `CLAIM` is actually on, which is the one thing a manifest cannot know
about itself: the node is a property of the running cluster, and the next
volume move will change it again.

Two sources answer where a claim is, and they are not equally good. The
PersistentVolume carries the node in its own `nodeAffinity` and is authoritative;
a Pod that mounts the claim carries the same answer but only while it runs.
`agents/whatsapp-auth-backup` copies `infra_whatsapp-bridge-auth`, whose
Deployment is parked at 0 replicas, so the Pod-derived reading is silent there
and this check printed `CANNOT SEE` on the one job it most needed to place. It
reads both now, the volume winning where both answer, and falls back to Pods
alone -- saying so out loud -- where reading a PersistentVolume is refused.

"""

import argparse
import datetime
import json
import subprocess
import sys
import zoneinfo

from tools.schedule_health import cron_matches

#: A CronJob may always be exactly one slot behind: the run for the newest
#: slot was created this minute and has not had time to finish. Two slots is
#: the first gap a run still in flight cannot explain.
GRACE_SLOTS = 1

#: How many slots the walk will count before giving up and saying "at least".
#: `heartbeat-liveness` last ran on 2026-05-01, which at `*/5` is about
#: 35,000 slots -- and every one of them past the second says the same thing.
#:
#: The walk is minute by minute, reusing `schedule_health.cron_matches` rather
#: than writing this loop's second cron parser. The cap bounds the frequent
#: schedules and not the rare ones, so the worst case is a *weekly* job that
#: has been failing for a year: measured 1.8s, against 52 slots found and the
#: cap never reached. That is affordable against twelve CronJobs and it is why
#: there is no second cap on elapsed minutes -- the number would be invented,
#: and this one is not.
MAX_SLOTS = 100


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


def read_cronjobs(runner=subprocess.run):
    """Every live CronJob, as (list, None) or (None, why)."""
    body, why = _run(runner, ["kubectl", "get", "cronjobs", "-A", "-o", "json"])
    if why:
        return None, why

    rows = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        rows.append({
            "namespace": meta.get("namespace") or "?",
            "name": meta.get("name") or "?",
            "schedule": (spec.get("schedule") or "").strip(),
            "timezone": (spec.get("timeZone") or "").strip(),
            "suspended": bool(spec.get("suspend")),
            "created": meta.get("creationTimestamp") or "",
            "scheduled": status.get("lastScheduleTime") or "",
            "succeeded": status.get("lastSuccessfulTime") or "",
            "pinned_node": _pinned_node(spec),
            "claim": _declared_claim(spec),
        })
    return rows, None


def _pod_spec(spec):
    """A CronJob spec's Pod template spec, or an empty dict."""
    template = ((spec.get("jobTemplate") or {}).get("spec") or {})
    return ((template.get("template") or {}).get("spec") or {})


def _pinned_node(spec):
    """The hostname a CronJob's Pods are pinned to, or ""."""
    selector = _pod_spec(spec).get("nodeSelector") or {}
    return (selector.get("kubernetes.io/hostname") or "").strip()


def _declared_claim(spec):
    """The `<namespace>_<claim>` a CronJob says it copies, or "".

    Read off the `CLAIM` environment variable rather than inferred from the
    job's name, for the reason `backup_health` gives about coverage: matching
    on names is wrong in both directions here, and `CLAIM` is the string the
    running script actually looks for under its hostPath mount.
    """
    for container in _pod_spec(spec).get("containers") or []:
        for env in container.get("env") or []:
            if env.get("name") == "CLAIM" and env.get("value"):
                return str(env["value"]).strip()
    return ""


def read_claim_nodes(runner=subprocess.run):
    """Which node each mounted PersistentVolumeClaim sits on.

    Keyed `<namespace>_<claim>`, which is how a volume-backup CronJob names
    the claim it covers and how local-path names the directory on disk.

    The node comes from the Pod that mounts the claim, which answers the
    question only while something is running. `read_pv_nodes` is the
    authoritative source and does not need a Pod; this stays as the fallback
    for a claim no PersistentVolume names, and for a cluster where reading
    PersistentVolumes is refused.
    """
    body, why = _run(runner, ["kubectl", "get", "pods", "-A", "-o", "json"])
    if why:
        return None, why

    nodes = {}
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        node = (spec.get("nodeName") or "").strip()
        if not node:
            # An unscheduled Pod names no node, so it says nothing about
            # where its volume is.
            continue
        namespace = meta.get("namespace") or "?"
        for volume in spec.get("volumes") or []:
            pvc = (volume.get("persistentVolumeClaim") or {}).get("claimName")
            if pvc:
                nodes[f"{namespace}_{pvc}"] = node
    return nodes, None


def _pv_node(spec):
    """The single hostname a PersistentVolume's nodeAffinity pins it to, or "".

    local-path writes exactly one `kubernetes.io/hostname` value here, because
    the volume is a directory on that node's disk. A PV that names more than one
    hostname is not a local-path volume pinned to a node, so it answers nothing
    about where a hostPath backup should run and is deliberately skipped rather
    than resolved to whichever value came first.
    """
    required = ((spec.get("nodeAffinity") or {}).get("required") or {})
    found = set()
    for term in required.get("nodeSelectorTerms") or []:
        for expression in term.get("matchExpressions") or []:
            if expression.get("key") != "kubernetes.io/hostname":
                continue
            if expression.get("operator") != "In":
                # NotIn/Exists narrow the placement without naming it, so they
                # cannot answer "which node is this directory on".
                continue
            for value in expression.get("values") or []:
                value = str(value).strip()
                if value:
                    found.add(value)
    if len(found) != 1:
        return ""
    return found.pop()


def read_pv_nodes(runner=subprocess.run):
    """Which node each bound PersistentVolume sits on, keyed `<namespace>_<claim>`.

    This is the authoritative answer and `read_claim_nodes` is the fallback: a
    local-path PersistentVolume carries its node in `spec.nodeAffinity`, and
    that is true whether or not anything is mounting the volume. Reading it off
    a running Pod is only true while a Pod runs, which is why
    `agents/whatsapp-auth-backup` printed `CANNOT SEE` -- the Deployment holding
    `infra/whatsapp-bridge-auth` is parked at 0 replicas.

    Returns `(nodes, why)`. A `why` means the read failed and the caller falls
    back; it must never be collapsed into an empty mapping, because "no
    PersistentVolume is pinned" and "I was refused" are different answers and
    only the first one is a clean bill of health.
    """
    body, why = _run(runner, ["kubectl", "get", "pv", "-o", "json"])
    if why:
        return None, why

    nodes = {}
    for item in body.get("items") or []:
        spec = item.get("spec") or {}
        claim = spec.get("claimRef") or {}
        namespace = (claim.get("namespace") or "").strip()
        name = (claim.get("name") or "").strip()
        if not namespace or not name:
            # An unbound PersistentVolume backs no claim, so no CronJob names it.
            continue
        node = _pv_node(spec)
        if node:
            nodes[f"{namespace}_{name}"] = node
    return nodes, None


def judge_pin(row, claim_nodes):
    """Whether a CronJob is pinned to the node its claim is actually on.

    Returns `(verdict, detail)` where `verdict` is `ok`, `CANNOT SEE` or
    `PINNED TO THE WRONG NODE`, or `(None, None)` when the question does not
    apply to this CronJob.

    This is a second axis and not part of `judge`: a job on the wrong node is
    perfectly on schedule right up until it runs, and then it fails for a
    reason its schedule cannot express. `agents/agora-backup` fired for the
    first time at 03:40 Oslo on 2026-09-05 and aborted with `expected exactly
    one *_agents_agora-data directory under /storage, found 0` -- it was
    pinned to server1 and `agents/agora-data` had moved to server2 the day
    before. A local-path volume is a directory on one node's disk, so a job
    that reads one by hostPath has to name that node, and nothing compared the
    two.
    """
    claim = row.get("claim") or ""
    pinned = row.get("pinned_node") or ""
    if not claim or not pinned:
        return None, None
    node = claim_nodes.get(claim)
    if node is None:
        # No running Pod mounts this claim, so its node is not readable from
        # here. This prints and deliberately does not raise, the same call
        # `security_alerts` makes on an already-fixed advisory: a workload
        # parked at zero replicas is an ordinary state -- `infra/whatsapp-bridge`
        # is one today -- and a check that is red forever on a legitimate
        # decision is one that stops being read.
        return "CANNOT SEE", (
            f"pinned to {pinned} and nothing readable names the node {claim} "
            f"is on — no PersistentVolume carries a hostname affinity for it "
            f"and no scheduled Pod mounts it")
    if node != pinned:
        return "PINNED TO THE WRONG NODE", (
            f"pinned to {pinned} and {claim} is on {node} — a local-path "
            f"volume is a directory on one node's disk, so this job reads an "
            f"empty path and fails on its next firing")
    return "ok", f"pinned to {pinned}, which is where {claim} is"


def _as_datetime(text):
    """An RFC3339 stamp as an aware datetime, or None when it cannot be read."""
    if not text:
        return None
    try:
        at = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.timezone.utc)
    return at


def slots_behind(schedule, succeeded, scheduled, timezone=""):
    """Firings of `schedule` after `succeeded` and up to `scheduled`.

    Returns (count, capped) or raises `ValueError` on a schedule or a zone
    that cannot be read -- either must cost the whole check its clean
    verdict, never one CronJob its judgement.

    **`timezone` is `spec.timeZone` and reading it is not optional.** Three
    of this cluster's twelve CronJobs declare `Europe/Oslo`, and the cron
    fields are Kubernetes' to interpret in *that* zone, not in UTC. Matching
    them against a UTC clock is wrong by the offset all year and wrong by a
    whole extra firing across a DST transition: `0 0 * * *` on `Europe/Oslo`
    over the October 2026 changeover measures 7 slots behind for a job that
    succeeded on every one of those days. An empty value is UTC, which is
    what Kubernetes itself does.

    `succeeded` is exclusive and `scheduled` inclusive, so a CronJob whose
    newest scheduled run is the one that succeeded measures 0. `capped` says
    the walk stopped at `MAX_SLOTS` and the real number is at least that.
    """
    if timezone:
        try:
            zone = zoneinfo.ZoneInfo(timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timeZone {timezone!r}: {exc}") from exc
    else:
        zone = datetime.timezone.utc

    start = succeeded.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    end = scheduled.replace(second=0, microsecond=0)
    count = 0
    moment = start
    while moment <= end:
        if cron_matches(schedule, moment.astimezone(zone)):
            count += 1
            if count >= MAX_SLOTS:
                return count, True
        moment += datetime.timedelta(minutes=1)
    return count, False


def _slots_since_creation(row, now):
    """Firings of this CronJob's schedule since Kubernetes created it.

    `None` when `creationTimestamp` is missing or unreadable, which is the
    loud direction to fail in: without an age there is no grace, and the
    caller judges the empty status the way it always did.
    """
    created = _as_datetime(row.get("created", ""))
    if created is None or created > now:
        return None
    count, _ = slots_behind(row["schedule"], created, now, row.get("timezone", ""))
    return count


def judge(row, now):
    """One CronJob's verdict, as (verdict, detail) or raising `ValueError`.

    `verdict` is one of `ok`, `NOT JUDGED`, `NEVER SCHEDULED`,
    `NEVER SUCCEEDED`, `BEHIND`. Only the last three raise; the caller owns
    that decision so the rule stays in one place.

    `now` is required rather than defaulted, because a clock bound in a
    signature is a clock a test cannot move.
    """
    if row["suspended"]:
        since = row["succeeded"] or row["scheduled"] or "never run"
        return "NOT JUDGED", f"suspended in the live cluster; last success {since}"

    scheduled = _as_datetime(row["scheduled"])
    succeeded = _as_datetime(row["succeeded"])

    if scheduled is None or succeeded is None:
        # Neither stamp exists until a slot has come round, so a CronJob
        # younger than its own first slot carries the same empty status as a
        # controller that has never heard of it. Measured Cycle 920:
        # `agents/agora-backup` was created 05:26 Oslo on a `40 3 * * *`
        # schedule and read `NEVER SCHEDULED` — the loudest verdict this check
        # has — from its creation until its first firing the next morning.
        # That is `schedule_health`'s NEVER FIRED wearing the wrong cause, and
        # a check that goes red on every new CronJob for a day is one that
        # stops being read.
        young = _slots_since_creation(row, now)
        if young is not None and young <= GRACE_SLOTS:
            wanted = "scheduled" if scheduled is None else "succeeded"
            return "NOT JUDGED", (
                f"created {row['created']} and {young} slot(s) of its own "
                f"schedule have come round since — too young to have "
                f"{wanted} yet, not a verdict about it")

    if scheduled is None:
        return "NEVER SCHEDULED", (
            "Kubernetes has never created a Job for it — the controller does "
            "not know about this schedule")

    if succeeded is None:
        return "NEVER SUCCEEDED", (
            f"scheduled since {row['scheduled']} and no run has ever finished")

    count, capped = slots_behind(
        row["schedule"], succeeded, scheduled, row.get("timezone", ""))
    if count <= GRACE_SLOTS:
        return "ok", f"{count} slot(s) behind, last success {row['succeeded']}"
    at_least = "at least " if capped else ""
    return "BEHIND", (
        f"{at_least}{count} consecutive run(s) have not succeeded — scheduled "
        f"{row['scheduled']}, last success {row['succeeded']}")


def report(rows, now=None, claim_nodes=None):
    """The printed lines and the exit status, as (lines, status)."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    if claim_nodes is None:
        claim_nodes = {}
    lines = []
    actionable = False
    unreadable = False
    suspended = []
    young = []
    pin_judged = 0
    pin_unseen = []

    for row in sorted(rows, key=lambda r: (r["namespace"], r["name"])):
        who = f"{row['namespace']}/{row['name']}"
        try:
            verdict, detail = judge(row, now)
        except ValueError as exc:
            unreadable = True
            lines.append(
                f"CANNOT JUDGE  {who}: schedule {row['schedule']!r} — {exc}")
            continue
        if verdict == "ok":
            lines.append(f"ok      {who} ({row['schedule']}): {detail}")
        elif verdict == "NOT JUDGED":
            (suspended if row["suspended"] else young).append(who)
            lines.append(f"NOT JUDGED  {who} ({row['schedule']}): {detail}")
        else:
            actionable = True
            lines.append(f"{verdict}  {who} ({row['schedule']}): {detail}")

        pin_verdict, pin_detail = judge_pin(row, claim_nodes)
        if pin_verdict == "PINNED TO THE WRONG NODE":
            pin_judged += 1
            actionable = True
            lines.append(f"{pin_verdict}  {who}: {pin_detail}")
        elif pin_verdict == "CANNOT SEE":
            pin_unseen.append(who)
            lines.append(f"CANNOT SEE  {who}: {pin_detail}")
        elif pin_verdict == "ok":
            pin_judged += 1

    swept = (
        f"Judged {len(rows)} CronJob(s) from the live cluster, not from git. "
        f"The verdict is scheduled slots between lastSuccessfulTime and "
        f"lastScheduleTime, so a weekly job and a five-minute one are read the "
        f"same way; {GRACE_SLOTS} slot of grace, because the newest run may "
        f"still be in flight.")
    if suspended:
        # `preflight` collapses a check that exits 0 to its last line carrying
        # a digit, which is this one. A suspend is the whole reason this check
        # does not raise, so its names ride on the line the collapse keeps —
        # the same fix `argocd_health` made for its stale Job failures.
        swept += (
            f" Suspended in the live cluster and deliberately not raised: "
            f"{', '.join(suspended)}.")
    if young:
        # Same reason as the suspended names above: `preflight` collapses a
        # check that exits 0 to its last line carrying a digit, and a CronJob
        # that was declined rather than passed has to survive that collapse.
        swept += (
            f" Younger than {GRACE_SLOTS} slot of their own schedule and "
            f"therefore not judged: {', '.join(young)}.")
    swept += (
        f" {pin_judged} of them name both a node and the claim they copy, and "
        f"those two were compared.")
    if pin_unseen:
        # Same reason as the suspended and young names above: `preflight`
        # collapses a check that exits 0 to its last line carrying a digit.
        swept += (
            f" Pinned to a node that neither a PersistentVolume's hostname "
            f"affinity nor a scheduled Pod places, so the pin could not be "
            f"compared: {', '.join(pin_unseen)}.")
    lines.append(swept)
    if suspended:
        lines.append(
            "A suspended CronJob is not judged rather than passed — this "
            "loop's kubectl is read-only, so no pull request re-enables one, "
            "and nothing in this cluster marks a suspension deliberate.")
    if actionable:
        return lines, 2
    return lines, (1 if unreadable else 0)


def main(argv=None, runner=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    rows, why = read_cronjobs(runner)
    if why:
        print(f"COULD NOT READ  {why}")
        return 1
    if not rows:
        # An empty list from a working kubectl is not a clean bill of health,
        # it is no instrument: this cluster demonstrably runs CronJobs, so
        # zero of them means the query looked in the wrong place.
        print("COULD NOT READ  kubectl returned no CronJobs at all")
        return 1

    claim_nodes, why = read_claim_nodes(runner)
    if why:
        # A CronJob's schedule is still worth judging without this, but a pin
        # that could not be compared must not read as a pin that agreed, so
        # this says so and the status below never comes back clean.
        print(f"COULD NOT READ  {why} — no pin was compared")
        claim_nodes = None
        unreadable_pods = True
    else:
        unreadable_pods = False

    # The PersistentVolume's own nodeAffinity wins where both answer, because
    # it is where the directory is rather than where a Pod happened to land,
    # and it answers for a volume nothing is mounting. A refusal here is not
    # fatal: before platform-config#685 this account could not read a
    # PersistentVolume at all, and the Pod-derived reading above is what the
    # check ran on. It is said out loud rather than silently degraded to.
    pv_nodes, pv_why = read_pv_nodes(runner)
    if pv_why:
        print(f"COULD NOT READ  {pv_why} — pins were compared against the "
              f"Pods that mount each claim instead, which says nothing about "
              f"a volume no Pod is mounting")
    elif claim_nodes is not None:
        claim_nodes = {**claim_nodes, **pv_nodes}
    else:
        claim_nodes = dict(pv_nodes)
        unreadable_pods = False

    lines, status = report(rows, claim_nodes=claim_nodes)
    if unreadable_pods and status == 0:
        status = 1
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
