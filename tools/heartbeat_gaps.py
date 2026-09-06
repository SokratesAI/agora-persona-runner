"""How many scheduled heartbeat firings never produced a run?

`tools.heartbeat_health` asks whether a heartbeat is still firing at all ---
off, overdue, or alive. It is a liveness check and it answers yes for a
schedule that fires most of the time. **Nothing counts the firings that a
live schedule dropped**, and on 2026-09-06 I measured that number by hand
for the first time: Nova's own `every@15m` heartbeat produced 70 runs in
the 20 hours to 04:30 Oslo, against 80 slots on its own grid. Ten firings
happened in no pod.

That is not a scheduler fault. The runner Deployment is `strategy:
Recreate` with `terminationGracePeriodSeconds: 2880`, and while it drains
`agora_runner.main._serve_while_draining` deliberately passes
`start_heartbeats=False` --- a run started inside a drain would be
SIGKILLed at the deadline, which is the regression the drain exists to
prevent. So between a rollout landing and the replacement pod polling,
every slot on the grid is dropped on purpose. Roughly twelve image-changing
merges a day, roughly one dropped slot each.

**This check does not raise on that number and must not.** I have no
measured line for how many dropped slots is too many, and the remedy is a
design decision that nobody can take from a threshold: either overlap two
pollers (which needs a compare-and-swap on the heartbeat claim --- it is a
blind PATCH today, see `heartbeats.run_heartbeat`) or shorten the grace
below one tick (which cuts a live cycle off mid-sentence). Inventing an N
here would be the flinch `personality.md` names. Same contract as
`disk_health`'s slope: the number sits beside the verdict as context.

    python3 -m tools.heartbeat_gaps [--hours 24]

**Exit 0 means it read the record; exit 1 means it could not.** Unreadable
never reads as clean, and there is no exit 2 --- there is nothing here for
a cycle to act on that is not already a decision written down in the
handoff.

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


def format_report(results, error, window_hours, listed):
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
        if row["oldest_run"] and row["oldest_run"] > row["window_start"] + timedelta(
            seconds=row["period_seconds"] * 2
        ):
            lines.append(
                f"    the {listed} conversation(s) Agora listed reach back only to "
                f"{row['oldest_run'].astimezone(timezone.utc).strftime('%m-%d %H:%M')} UTC, "
                "so the window judged is shorter than the one asked for"
            )
    for row in unjudged:
        lines.append(f"NOT JUDGED — {row['name']}: {row['detail']}")

    lines.append(
        f"Read {listed} conversation(s) and {len(results)} heartbeat(s) from {AGORA_PUBLIC}."
    )
    lines.append(
        "A dropped firing is context, not a finding: the runner is strategy Recreate, "
        "and a pod draining a live cycle starts no new run on purpose. Nothing here "
        "raises, because the remedy is a design decision and not a threshold."
    )
    # Last, and carrying digits, because `tools.preflight.summary_line` takes
    # the last line with a number in it -- and the number worth carrying for
    # the rest of a cycle is the loss, not how many rows were read.
    dropped = sum(len(r["missed"]) for r in judged)
    slots = sum(r["expected"] for r in judged)
    lines.append(
        f"{dropped} of {slots} scheduled firing(s) in the last {window_hours}h produced "
        f"no run, across {len(judged)} judged heartbeat(s) and {len(unjudged)} unjudged."
    )
    if unjudged and not judged:
        return "\n".join(lines), 1
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
    report, status = format_report(results, error, args.hours, len(conversations))
    print(report)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
