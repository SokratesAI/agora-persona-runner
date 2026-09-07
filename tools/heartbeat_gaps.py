"""How many scheduled heartbeat firings never produced a run?

`tools.heartbeat_health` asks whether a heartbeat is still firing at all ---
off, overdue, or alive. It is a liveness check and it answers yes for a
schedule that fires most of the time. **Nothing counts the firings that a
live schedule dropped**, and on 2026-09-06 I measured that number by hand
for the first time: Nova's own `every@15m` heartbeat produced 70 runs in
the 20 hours to 04:30 Oslo, against 80 slots on its own grid. Ten firings
happened in no pod.

Some of those firings are dropped on purpose. While the runner drains,
`agora_runner.main._serve_while_draining` deliberately passes
`start_heartbeats=False` --- a run started inside a drain would be
SIGKILLed at the deadline, which is the regression the drain exists to
prevent. So a slot whose period contains a rollout can genuinely have had
no pod to fire in.

**A slot whose period contains no rollout has no such excuse, and this
check used to hand one to it anyway.** Until 2026-09-08 the report ended
on a fixed sentence saying the runner is `strategy: Recreate` and that a
dropped firing is therefore context rather than a finding. Both halves of
that were stale within a day of being written: the Deployment moved to
`RollingUpdate` on 2026-09-06, and the compare-and-swap this file names as
the blocker on overlapping two pollers shipped the same day (agora#89 and
runner#806 --- `ifLastRunAt`, 409 to the loser). Measured 2026-09-08 on
Nova's own `every@18m`: 5 of 88 slots produced no run, and **four of the
five had no rollout anywhere in their own period.** The sentence was
explaining away four lost cycles a day with a premise that had stopped
being true.

So the shape is not `Recreate` or otherwise by hardcoded assertion any
more --- it is read off the live Deployment, and each missed slot is
attributed or it is not:

* **explained** --- a ReplicaSet for the runner was created inside that
  slot's own period, so a rollout was in flight when the slot came round.
* **unexplained** --- no rollout in that period. That is a lost cycle with
  no known cause, and it is what this check now raises on.

The attribution window is the schedule's own period, not a tolerance I
picked. That is deliberately generous: it over-attributes rather than
under-attributes, so an *unexplained* slot is a claim this can defend.

    python3 -m tools.heartbeat_gaps [--hours 24]

**Exit 0 means every missed slot was attributable; exit 2 means at least
one was not; exit 1 means it could not read the record --- including the
Deployment, since a shape it could not read must not read as a shape that
excuses nothing.** Unreadable never reads as clean.

**A firing is counted by the conversation it created, not by `lastRunAt`.**
A heartbeat row carries only its newest run, so the row cannot answer a
question about a window. A rotating heartbeat writes one conversation per
run into a folder, so the folder is the record --- and the folder is read
off the heartbeat's own current `conversationId` rather than matched by
name, because the naming convention (`Nova — Cycle 1021`) is a display
string that has changed once already.

**Only a rotating heartbeat can be judged.** One that reuses a single
conversation leaves no per-run trace at this endpoint at all, so it is
reported as NOT JUDGED rather than as zero gaps --- the same reason
`heartbeat_health` keeps OFF and OVERDUE apart.

The grid is anchored on the newest run rather than on the schedule's own
`@16:00` anchor: the anchor is in the owner's timezone and the slot
spacing is what this measures, so anchoring on an observed firing cannot
manufacture a gap out of an hour's offset.
"""

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.heartbeat_liveness import (  # noqa: E402
    AGORA_PUBLIC,
    _fetch,
    _parse_stamp,
    interval_seconds,
)

#: How close to a slot a run has to land to count as that slot's run. Half
#: the period, so every run belongs to exactly one slot and no run can be
#: claimed by two. Not a tolerance to tune --- it is the midpoint.
_SLOT_SHARE = 0.5

#: The most conversations to ask Agora for in one read. Measured 2026-09-06:
#: Agora ignores this parameter and returns its whole list anyway --- 1,047
#: rows against a limit of 400 --- so this is a ceiling this asks for and
#: does not get, not one it enforces. It stays because the day the endpoint
#: starts honouring it, four days of Nova's ~96-a-day is the window worth
#: having; and either way the report names its own shortfall rather than
#: silently judging a partial window. The honest cost of that is a
#: megabyte-scale read once per preflight sweep.
_CONVERSATION_LIMIT = 400


def fetch_conversations(url=None, opener=None, timeout=20, limit=_CONVERSATION_LIMIT):
    """`(conversations, error)` --- every conversation Agora will list.

    An empty list with no error is a true measurement; a list this could
    not read is an error, never an empty one.
    """
    target = f"{(url or AGORA_PUBLIC).rstrip('/')}/conversations?limit={limit}"
    try:
        with (opener or urllib.request.urlopen)(target, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return [], f"could not read {target}: {e}"
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("conversations", payload.get("data", []))
    if not isinstance(rows, list):
        return [], f"{target} returned no conversation list"
    return [r for r in rows if isinstance(r, dict)], None


def _folder_of(heartbeat, conversations):
    """The folder a rotating heartbeat writes its runs into, or None.

    Read off the heartbeat's own current conversation rather than matched
    by name.
    """
    current = heartbeat.get("conversationId")
    if not current:
        return None
    for row in conversations:
        if row.get("id") == current:
            return row.get("folderId")
    return None


def missed_slots(run_times, period_seconds, window_start, now):
    """Slots on the grid, inside the window, that no run landed on.

    The grid is anchored on the newest run at or before `now` and walks
    backwards by `period_seconds`. Returns the missed slot times, newest
    first.
    """
    if period_seconds <= 0 or not run_times:
        return []
    runs = sorted(t for t in run_times if t <= now)
    if not runs:
        return []
    anchor = runs[-1]
    tolerance = timedelta(seconds=period_seconds * _SLOT_SHARE)
    period = timedelta(seconds=period_seconds)
    missed = []
    slot = anchor
    while slot >= window_start:
        if not any(abs((t - slot).total_seconds()) < tolerance.total_seconds() for t in runs):
            missed.append(slot)
        slot -= period
    return missed


#: The workload whose rollouts can legitimately eat a heartbeat slot. Only
#: this one: a draining persona-runner is the single reason a scheduled
#: firing can land in no pod, and naming it here keeps the excuse narrow.
_RUNNER_NAMESPACE = "agents"
_RUNNER_NAME = "agora-persona-runner"


def _kubectl(args, runner=subprocess.run):
    """`(parsed json, error)` --- never raises, never a partial read."""
    try:
        proc = runner(["kubectl"] + args, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl failed: {exc}"
    if proc.returncode != 0:
        return None, f"kubectl failed: {(proc.stderr or proc.stdout or '').strip()}"
    try:
        return json.loads(proc.stdout), None
    except (ValueError, TypeError) as exc:
        return None, f"kubectl returned something that is not JSON: {exc}"


def read_rollout_shape(runner=subprocess.run):
    """`(shape, error)` --- how the runner Deployment replaces its pod.

    Read live rather than asserted, because the last thing that asserted it
    here was wrong two days after it was written.
    """
    doc, error = _kubectl(
        ["get", "deploy", _RUNNER_NAME, "-n", _RUNNER_NAMESPACE, "-o", "json"], runner
    )
    if error:
        return None, error
    spec = (doc or {}).get("spec") or {}
    strategy = spec.get("strategy") or {}
    rolling = strategy.get("rollingUpdate") or {}
    return {
        "type": strategy.get("type") or "RollingUpdate",
        "maxUnavailable": rolling.get("maxUnavailable"),
        "maxSurge": rolling.get("maxSurge"),
        "grace": ((spec.get("template") or {}).get("spec") or {}).get(
            "terminationGracePeriodSeconds"
        ),
    }, None


def read_rollout_instants(runner=subprocess.run):
    """`(times, error)` --- when each of the runner's ReplicaSets was created.

    A new ReplicaSet is the instant a rollout starts, which is also the
    instant the outgoing pod is signalled and stops starting runs.
    """
    doc, error = _kubectl(
        ["get", "rs", "-n", _RUNNER_NAMESPACE, "-l", f"app={_RUNNER_NAME}", "-o", "json"],
        runner,
    )
    if error:
        return None, error
    times = []
    for item in (doc or {}).get("items", []):
        stamp = _parse_stamp(((item or {}).get("metadata") or {}).get("creationTimestamp"))
        if stamp is not None:
            times.append(stamp)
    return sorted(times), None


def attribute(missed, rollouts, period_seconds):
    """`(explained, unexplained)` --- which missed slots a rollout accounts for.

    A slot is explained when a rollout began inside that slot's own period,
    i.e. in `(slot - period, slot]`. The period is the schedule's, not a
    tolerance chosen here, and it is the generous reading on purpose: an
    unexplained slot is then a claim worth making.
    """
    period = timedelta(seconds=period_seconds)
    explained, unexplained = [], []
    for slot in missed:
        if any(slot - period < r <= slot for r in rollouts):
            explained.append(slot)
        else:
            unexplained.append(slot)
    return explained, unexplained


def judge(heartbeat, conversations, now, window_hours):
    """One row's verdict --- a dict, never a raise."""
    name = heartbeat.get("name") or heartbeat.get("id") or "<unnamed>"
    row = {"name": name, "schedule": heartbeat.get("schedule") or "", "verdict": "unjudged"}
    if not heartbeat.get("enabled"):
        row["detail"] = "disabled, so it is meant to fire never"
        return row
    if not heartbeat.get("rotateConversationEachRun"):
        row["detail"] = (
            "does not rotate its conversation each run, so a run leaves no "
            "per-run trace this endpoint can count"
        )
        return row
    period, note = interval_seconds(row["schedule"])
    if period is None:
        row["detail"] = f"schedule not readable: {note}"
        return row
    folder = _folder_of(heartbeat, conversations)
    if not folder:
        row["detail"] = (
            "its current conversation is not in the list Agora returned, so "
            "the folder its runs land in is unknown"
        )
        return row
    window_start = now - timedelta(hours=window_hours)
    runs = []
    for conv in conversations:
        if conv.get("folderId") != folder:
            continue
        stamp = _parse_stamp(conv.get("createdAt"))
        if stamp is not None and stamp >= window_start:
            runs.append(stamp)
    missed = missed_slots(runs, period, window_start, now)
    row.update(
        verdict="judged",
        period_seconds=period,
        runs=len(runs),
        missed=missed,
        expected=len(runs) + len(missed),
        oldest_run=min(runs) if runs else None,
        window_start=window_start,
    )
    return row


def format_report(results, error, window_hours, listed,
                  rollouts=None, shape=None, rollout_error=None):
    """`(text, status)` --- the report and its exit code."""
    lines = []
    if error:
        lines.append(f"COULD NOT READ — {error}")
        lines.append("This is no instrument, not a clean sweep.")
        return "\n".join(lines), 1

    judged = [r for r in results if r["verdict"] == "judged"]
    unjudged = [r for r in results if r["verdict"] == "unjudged"]

    for row in judged:
        share = (len(row["missed"]) / row["expected"] * 100) if row["expected"] else 0.0
        lines.append(
            f"{row['name']} ({row['schedule']}): {row['runs']} run(s) of "
            f"{row['expected']} slot(s) in the last {window_hours}h — "
            f"{len(row['missed'])} firing(s) produced no run ({share:.0f}%)."
        )
        if row["missed"]:
            stamps = ", ".join(
                t.astimezone(timezone.utc).strftime("%m-%d %H:%M")
                for t in row["missed"][:12]
            )
            more = "" if len(row["missed"]) <= 12 else f", and {len(row['missed']) - 12} more"
            lines.append(f"    missed slot(s), UTC: {stamps}{more}")
            explained, unexplained = ([], []) if rollout_error else attribute(
                row["missed"], rollouts or [], row["period_seconds"]
            )
            row["explained"], row["unexplained"] = explained, unexplained
            if rollout_error:
                lines.append(
                    "    NOT ATTRIBUTED — could not read the runner's rollouts, so "
                    "no slot here is excused and none is charged either"
                )
            else:
                if explained:
                    lines.append(
                        f"    {len(explained)} of them had a runner rollout inside their own "
                        "period, so there was a draining pod that starts no run: "
                        + ", ".join(
                            t.astimezone(timezone.utc).strftime("%m-%d %H:%M")
                            for t in explained[:12]
                        )
                    )
                if unexplained:
                    lines.append(
                        f"    UNEXPLAINED — {len(unexplained)} slot(s) had no rollout anywhere "
                        "in their own period, so a runner was up and the cycle was lost "
                        "anyway: "
                        + ", ".join(
                            t.astimezone(timezone.utc).strftime("%m-%d %H:%M")
                            for t in unexplained[:12]
                        )
                    )
        if row["oldest_run"] and row["oldest_run"] > row["window_start"] + timedelta(
            seconds=row["period_seconds"] * 2
        ):
            lines.append(
                f"    the {listed} conversation(s) Agora listed reach back only to "
                f"{row['oldest_run'].astimezone(timezone.utc).strftime('%m-%d %H:%M')} UTC, "
                "so the window judged is shorter than the one asked for"
            )
    # Grouped by reason rather than one line each. Seven of Agora's ten
    # heartbeats reuse a single conversation and structurally always will,
    # so a line apiece is seven lines of unchanging noise in a report every
    # cycle reads --- and a report nobody reads is the 400-chip failure
    # from the other end. Every name is still printed; only the reason is
    # said once.
    by_reason = {}
    for row in unjudged:
        by_reason.setdefault(row["detail"], []).append(row["name"])
    for detail, names in by_reason.items():
        lines.append(f"NOT JUDGED — {len(names)} heartbeat(s): {detail}")
        lines.append(f"    {', '.join(names)}")

    lines.append(
        f"Read {listed} conversation(s) and {len(results)} heartbeat(s) from {AGORA_PUBLIC}."
    )
    if rollout_error:
        lines.append(f"COULD NOT READ the runner's rollout shape — {rollout_error}")
    elif shape:
        detail = shape.get("type")
        if detail == "RollingUpdate":
            detail += (
                f" (maxUnavailable {shape.get('maxUnavailable')}, "
                f"maxSurge {shape.get('maxSurge')})"
            )
        lines.append(
            f"Runner rollout shape, read live: strategy {detail}, "
            f"terminationGracePeriodSeconds {shape.get('grace')}. "
            f"{len(rollouts or [])} ReplicaSet(s) carry a creation time to attribute against."
        )
    # Last, and carrying digits, because `tools.preflight.summary_line` takes
    # the last line with a number in it -- and the number worth carrying for
    # the rest of a cycle is the loss, not how many rows were read.
    dropped = sum(len(r["missed"]) for r in judged)
    slots = sum(r["expected"] for r in judged)
    unexplained_total = sum(len(r.get("unexplained") or []) for r in judged)
    lines.append(
        f"{dropped} of {slots} scheduled firing(s) in the last {window_hours}h produced "
        f"no run, {unexplained_total} of them with no rollout to explain it, across "
        f"{len(judged)} judged heartbeat(s) and {len(unjudged)} unjudged."
    )
    if unjudged and not judged:
        return "\n".join(lines), 1
    if rollout_error:
        # A shape it could not read must not read as a shape that excuses
        # nothing, and must not read as clean either.
        return "\n".join(lines), 1
    if any(r.get("unexplained") for r in judged):
        return "\n".join(lines), 2
    return "\n".join(lines), 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=24.0,
                        help="how far back to judge (default 24)")
    args = parser.parse_args(argv)

    heartbeats, error = _fetch()
    conversations = []
    if not error:
        conversations, error = fetch_conversations()
    now = datetime.now(timezone.utc)
    results = [] if error else [judge(h, conversations, now, args.hours) for h in heartbeats]
    shape, rollout_error = read_rollout_shape()
    rollouts = []
    if not rollout_error:
        rollouts, rollout_error = read_rollout_instants()
    report, status = format_report(
        results, error, args.hours, len(conversations), rollouts, shape, rollout_error
    )
    print(report)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
