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
        })
    return rows, None


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


def report(rows, now=None):
    """The printed lines and the exit status, as (lines, status)."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    lines = []
    actionable = False
    unreadable = False
    suspended = []
    young = []

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

    lines, status = report(rows)
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
