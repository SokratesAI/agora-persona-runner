"""Is every Agora heartbeat still firing?

Cycle 475, on Edvard's comment on idea #141: *"I do think the Sentinel is
paused. Maybe turn it on again?"* He was right. `K3s Sentinel` --- the
daily cluster scan that reports crash-looping pods, unready deployments
and not-Ready nodes --- had `enabled: false` and had not run since
**2026-08-12**. Fourteen days.

The cost of those fourteen days is not hypothetical. `whatsapp-bridge`
crash-looped in `infra` for 37 hours and 314 restarts before Cycle 448
found it, and it was found in a cell of `tools.catalog`'s table, not by
the check whose entire job that is. The Sentinel would have named it the
next morning.

**Nothing here reads a heartbeat's own liveness**, which is why a switch
flipped a fortnight ago was found by the owner rather than by an
instrument. My opening checks read pods, security advisories, gh-aw runs,
document integrity and version pins. Every one of them is a check on
something *else*. The scheduler that runs this loop --- and the four other
schedules hanging off it --- was the one thing with no check on it at all.

    python3 -m tools.heartbeat_health

**Two different failures, kept apart on purpose.** A heartbeat can stop
because someone switched it off, or because it is switched on and the
schedule is not firing. `tools.agentic_health` had to learn this the hard
way one layer down: a streak counter merged two unrelated causes into one
number and sent two cycles chasing the wrong one. So `OFF` and `OVERDUE`
are separate verdicts with separate evidence lines, never a single
"unhealthy" count.

**A heartbeat that is deliberately off says so in its own name.** Both
`Workflow trial ... (disabled, manual only)` rows carry it, and that
convention already exists because the Heartbeats page shows the name to
Edvard. So a `(disabled` marker in the name means the off state is
documented where a reader can see it, and it prints as context. Anything
else that is off raises the status --- which is exactly what the Sentinel
would have done, since nothing anywhere recorded that it had been
switched off. The rule is printed in the report, so a false positive is
one rename away from quiet rather than a code change.

**The grace is two missed turns, not one.** A heartbeat that is a few
minutes late is a scheduler doing its job under load; one that has missed
two of its own scheduled turns is not late, it is stopped. The window is
derived from the schedule the heartbeat itself declares rather than from
a number I picked.

**A schedule I cannot parse is exit 1, never exit 0.** Same contract as
`security_alerts`, `agentic_health` and `cli_pin`: **2** means a heartbeat
has stopped, **1** means something was unreadable --- which never reads as
clean --- and **0** means every schedule answered and is firing.

The writes go to Agora's public app on :8080, the same route
`nova_heartbeats` uses and for the same reason: the internal API on :8081
accepts only the runner's own bookkeeping fields, and `enabled` is not one
of them. This module only reads.
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone

# Agora's public app. `nova_heartbeats` documents why this and not the
# internal :8081 API: the internal one is the runner's bookkeeping
# surface and does not carry `enabled` at all.
AGORA_PUBLIC = "http://agora.agents.svc.cluster.local:8080"

# Two missed turns. One is a scheduler under load; two is a scheduler
# that is not running this row.
_MISSED_TURNS_BEFORE_STOPPED = 2

# The marker that says an off state is deliberate and written where a
# reader of the Heartbeats page can see it.
_DELIBERATE_MARKER = "(disabled"


def _fetch(url=None, opener=None):
    """`(heartbeats, error)` --- every heartbeat Agora knows about.

    An empty list with no error is a true measurement. A list this could
    not read comes back as an error, because "no heartbeats" and "could
    not ask" are the two things this module exists to keep apart.
    """
    target = (url or AGORA_PUBLIC).rstrip("/") + "/heartbeats"
    try:
        with (opener or urllib.request.urlopen)(target, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return [], f"could not read {target}: {e}"
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("heartbeats", payload.get("data", []))
    if not isinstance(rows, list):
        return [], f"{target} returned no heartbeat list"
    return [r for r in rows if isinstance(r, dict)], None


def interval_seconds(schedule):
    """`(seconds, note)` --- how often this schedule claims to fire.

    Returns `(None, why)` for a shape this cannot read, which is a
    finding rather than a default: a guessed interval would silently
    decide whether a stopped heartbeat gets reported.
    """
    text = (schedule or "").strip()
    if not text:
        return None, "no schedule"
    if text.startswith("daily@"):
        return 24 * 3600, "daily"
    if text.startswith("every@"):
        # `every@20m@16:20` -- the trailing field is an anchor, not a period.
        body = text[len("every@") :].split("@", 1)[0].strip()
        unit = body[-1:].lower()
        factor = {"m": 60, "h": 3600, "d": 86400}.get(unit)
        try:
            count = int(body[:-1])
        except ValueError:
            count = 0
        if not factor or count <= 0:
            return None, f"unreadable every@ period {body!r}"
        return count * factor, f"every {body}"
    if text.startswith("cron@"):
        fields = text[len("cron@") :].split()
        if len(fields) != 5:
            return None, f"cron with {len(fields)} fields, expected 5"
        _minute, _hour, dom, mon, dow = fields
        if dom != "*" or mon != "*":
            return None, "cron with a day-of-month or month restriction"
        if dow == "*":
            return 24 * 3600, "cron, daily"
        days = set()
        for part in dow.split(","):
            part = part.strip()
            if not part.isdigit():
                return None, f"cron day-of-week {dow!r} is not a plain list"
            days.add(int(part) % 7)
        if not days:
            return None, f"cron day-of-week {dow!r} names no day"
        return int(7 * 86400 / len(days)), f"cron, {len(days)} day(s) a week"
    return None, f"unrecognised schedule {text!r}"


def _parse_stamp(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def judge(heartbeat, now):
    """One heartbeat's verdict, with the evidence that produced it."""
    name = heartbeat.get("name") or heartbeat.get("id") or "(unnamed)"
    schedule = heartbeat.get("schedule")
    last = _parse_stamp(heartbeat.get("lastRunAt"))
    created = _parse_stamp(heartbeat.get("createdAt"))
    seconds, note = interval_seconds(schedule)
    base = {"name": name, "schedule": schedule, "last": last, "note": note}

    if not heartbeat.get("enabled"):
        deliberate = _DELIBERATE_MARKER in name.lower()
        return dict(
            base,
            verdict="off_marked" if deliberate else "off",
            detail=(
                "off, and its own name says so"
                if deliberate
                else "off, and nothing says it was meant to be"
            ),
        )

    if seconds is None:
        return dict(base, verdict="unjudged", detail=note)

    # A heartbeat that has never run is measured from its creation: it
    # cannot be late for a turn that had not come round when it was made.
    reference, anchor = (last, "last ran") if last else (created, "created")
    if reference is None:
        return dict(base, verdict="unjudged", detail="no lastRunAt and no createdAt")

    gap = (now - reference).total_seconds()
    allowed = seconds * _MISSED_TURNS_BEFORE_STOPPED
    stamp = reference.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    evidence = (
        f"{note}; {anchor} {stamp}, {_duration(gap)} ago, "
        f"allowed {_duration(allowed)}"
    )
    if gap > allowed:
        return dict(base, verdict="overdue", detail=evidence)
    return dict(base, verdict="ok", detail=evidence)


def _duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def format_report(results, error):
    """`(text, status)` --- the report and its exit code."""
    lines = []
    if error:
        lines.append(f"COULD NOT READ — {error}")
        lines.append("This is no instrument, not a clean sweep.")
        return "\n".join(lines), 1

    off = [r for r in results if r["verdict"] == "off"]
    overdue = [r for r in results if r["verdict"] == "overdue"]
    unjudged = [r for r in results if r["verdict"] == "unjudged"]
    marked = [r for r in results if r["verdict"] == "off_marked"]
    ok = [r for r in results if r["verdict"] == "ok"]

    for row in off:
        lines.append(f"OFF — {row['name']}: {row['detail']} (schedule {row['schedule']})")
        if row["last"]:
            stamp = row["last"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"      it did run once: last at {stamp}")
        lines.append(
            "      turn it back on with: curl -X PATCH -H 'Content-Type: application/json'"
            f" -d '{{\"enabled\":true}}' {AGORA_PUBLIC}/heartbeats/<id>"
        )
    for row in overdue:
        lines.append(f"OVERDUE — {row['name']}: {row['detail']}")
    for row in unjudged:
        lines.append(f"NOT JUDGED — {row['name']}: {row['detail']}")
    for row in marked:
        lines.append(f"off  {row['name']} — {row['detail']}")
    for row in ok:
        lines.append(f"ok   {row['name']} — {row['detail']}")

    if not results:
        lines.append("Agora answered with no heartbeats at all.")

    lines.append(f"Read {len(results)} heartbeat(s) from {AGORA_PUBLIC}.")
    lines.append(
        "A heartbeat that is off on purpose carries "
        f"'{_DELIBERATE_MARKER}' in its own name; any other off row is reported."
    )
    if off or overdue:
        return "\n".join(lines), 2
    if unjudged or not results:
        return "\n".join(lines), 1
    return "\n".join(lines), 0


def main(argv=None):
    rows, error = _fetch()
    now = datetime.now(timezone.utc)
    results = [judge(row, now) for row in rows]
    report, status = format_report(results, error)
    print(report)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
