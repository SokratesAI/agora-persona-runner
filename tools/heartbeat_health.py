"""Is every Agora heartbeat still firing?

Cycle 475, on the owner's comment on idea #141: *"I do think the Sentinel is
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
the owner. So a `(disabled` marker in the name means the off state is
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

import sys
from datetime import datetime, timezone

# The judging lives in `agora_runner.heartbeat_liveness` so `nova-site` can
# serve it on `/api/health` (idea #117, Cycle 541). The image the site runs
# from copies `agora_runner/` and not `tools/`, so the split is what makes
# one copy possible; this module keeps the report and the exit contract,
# which are a cycle's concern and not the endpoint's.
from agora_runner.heartbeat_liveness import (  # noqa: F401  (re-exported)
    AGORA_PUBLIC,
    _DELIBERATE_MARKER,
    _MISSED_TURNS_BEFORE_STOPPED,
    _duration,
    _fetch,
    _parse_stamp,
    interval_seconds,
    judge,
)


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
