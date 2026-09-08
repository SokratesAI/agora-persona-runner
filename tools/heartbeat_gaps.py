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

An unexplained slot is split one step further, because "no known cause"
was hiding two different bugs behind one number. Measured 2026-09-08 on
Nova's own heartbeat: **four of the five unexplained slots came round with
an earlier cycle still running, and exactly one run was in flight at each
of them --- against a concurrency limit of 3.** The fifth had nothing
running at all. Both keep raising --- this splits the finding, it does not
excuse either half. The interval a run covers is `createdAt` to
`lastMessageAt` on its own conversation, open at the start so a firing
never reads as covered by the run it produced.

For six hours this then wrote **"so the poller declined a tick it had room
for"** under those four, which was a cause it had not measured. A slot with
a run over it and room to spare is equally consistent with the poller never
having *reached* the tick --- `schedule_due` asks only about the most recent
anchored occurrence, so a pass that lands after two slot boundaries loses
the older slot without ever declining it (runner#916). Those are different
bugs with different fixes, and the sentence picked one. The instrument that
actually separates them is `agora_runner.dropped_ticks` (runner#915): the
poller records every decline, so a slot the ledger covers and has nothing
for is a slot nothing declined. So a covered slot now lands in one of three
places --- **declined** (a record names the guard), **unevaluated** (the
ledger covers it and is silent), or **unjudged** (the ledger does not reach
back that far, which is the honest answer for every slot before the recorder
shipped, and for all four of the ones above). Empty is not the same as
absent, and neither is a cause.

**A fourth answer, and the one that was silent everywhere.** A scheduler
pass that *raises* declines no tick and keeps its cadence, so it writes
neither of those ledgers, and the slot it loses arrives here as
`unevaluated` --- the name of the poller sleeping through a slot, which is a
different bug with a different fix. `pass_once` has always caught that
exception on purpose, and then said so only to a log that dies with the
container. `agora_runner.dropped_ticks.record_pass_failure` gives it a
document, and a missed slot whose own period holds one of those records is
taken out ahead of the covered/idle split and named as a raised pass.

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

#: Where the runner records a due tick it declined, and the only copy of
#: that reason that outlives the Pod. `agora_runner.dropped_ticks` writes it;
#: this reads it, because this tool runs on the bridge Pod where
#: `agora_runner.vault` has no working credentials.
DROP_LEDGER = "projects/sokrates/projects/agora/nova/resources/dropped-ticks.json"

#: Where the runner records a scheduler pass that started a whole beat late.
#: A declined tick above is a slot the poller looked at; this is the poller
#: not looking, which is how an anchored slot is lost without being declined.
LAG_LEDGER = "projects/sokrates/projects/agora/nova/resources/scheduler-lag.json"

#: Where the runner records a scheduler pass that RAISED. The third way a slot
#: is lost and the only one that used to leave nothing outside the Pod: a
#: raising scheduler declines no tick and keeps its cadence, so it writes
#: neither ledger above, and its lost slots land in `unevaluated` below ---
#: the name of the poller sleeping through a slot, a different bug.
FAIL_LEDGER = "projects/sokrates/projects/agora/nova/resources/scheduler-failures.json"

#: `vault_tool.py`, which exists on the bridge Pod only. Same constant and
#: same reason as `tools.roll_health`.
VAULT_TOOL = "/app/bridge/vault_tool.py"


def _vault_get(path, runner=subprocess.run):
    """The document as text, or `None` if it did not really return one.

    `get` prints `[not found: <path>]` on stdout and exits 0, so a return
    code alone reads a vanished ledger as an empty one --- which here would
    report "no reason was recorded" for a slot whose reason was recorded and
    then lost. Same shape as `roll_health._fetch`, for the same reason.
    """
    try:
        done = runner([sys.executable, VAULT_TOOL, "get", path],
                      capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    if not done.stdout.strip() or done.stdout.lstrip().startswith("[not found:"):
        return None
    return done.stdout


def read_drop_records(runner=subprocess.run):
    """`(records, error)` --- what the runner said about the ticks it dropped.

    `([], None)` is a real measurement: the ledger exists and holds nothing,
    or does not exist yet because nothing has been dropped since it shipped.
    An error is never an empty list, for the reason the module docstring
    gives about unreadable never reading as clean --- except that this one
    does not raise the exit status on its own. It is an explanation attached
    to a slot that is already being reported, not a finding of its own, and a
    missing explanation must not turn a healthy sweep red.
    """
    raw = _vault_get(DROP_LEDGER, runner=runner)
    if raw is None:
        return [], None
    try:
        records = json.loads(raw)
    except ValueError:
        return [], f"{DROP_LEDGER} is not JSON"
    if not isinstance(records, list):
        return [], f"{DROP_LEDGER} does not hold a list"
    return records, None


def read_lag_records(runner=subprocess.run):
    """`(records, error)` --- late scheduler passes the runner recorded.

    Same contract as `read_drop_records`: an empty list is a real measurement
    and never an error, and an unreadable ledger does not raise the exit
    status on its own, because this explains slots that are already being
    reported rather than finding anything itself.
    """
    raw = _vault_get(LAG_LEDGER, runner=runner)
    if raw is None:
        return [], None
    try:
        records = json.loads(raw)
    except ValueError:
        return [], f"{LAG_LEDGER} is not JSON"
    if not isinstance(records, list):
        return [], f"{LAG_LEDGER} does not hold a list"
    return records, None


def read_failure_records(runner=subprocess.run):
    """`(records, error)` --- scheduler passes that raised.

    Same contract as `read_drop_records` and `read_lag_records`: an empty list
    is a real measurement, an unreadable ledger reports its error and still
    does not raise the exit status, because this explains slots that are
    already being reported rather than finding anything itself.
    """
    raw = _vault_get(FAIL_LEDGER, runner=runner)
    if raw is None:
        return [], None
    try:
        records = json.loads(raw)
    except ValueError:
        return [], f"{FAIL_LEDGER} is not JSON"
    if not isinstance(records, list):
        return [], f"{FAIL_LEDGER} does not hold a list"
    return records, None


def failure_summary(records, now, window_hours):
    """One line about scheduler passes that raised in the window, or `None`.

    `None` for an empty window for the same reason `lag_summary` returns it:
    a healthy scheduler produces no records at all, and a line saying so on
    every run is a line nobody reads. The newest error text is quoted because
    the count alone says an outage happened and not which one.
    """
    cutoff = now - timedelta(hours=window_hours)
    inside, undated = [], 0
    for record in records:
        if not isinstance(record, dict):
            continue
        at = _parse_stamp(record.get("at"))
        if at is None:
            undated += 1
            continue
        if at >= cutoff:
            inside.append((at, record))
    if not inside and not undated:
        return None
    inside.sort()
    line = (f"SCHEDULER PASSES RAISED — {len(inside) + undated} recorded in the "
            f"last {window_hours:g}h; a pass that raises evaluates no tick, so "
            "it loses a slot without declining it")
    if inside:
        newest = inside[-1][1]
        stamp = inside[-1][0].astimezone(timezone.utc).strftime("%m-%d %H:%M")
        line += f". Newest {stamp} UTC: {newest.get('error') or 'no error recorded'}"
    if undated:
        line += f". {undated} carried no readable timestamp and are counted, not dated"
    return line


def lag_summary(records, now, window_hours):
    """One line about late scheduler passes in the window, or `None`.

    `None` for an empty window is deliberate: a healthy scheduler produces no
    records at all, and a line saying so on every run is a line nobody reads.
    A record with an unparseable stamp is counted and not dated, because
    dropping it would understate an outage.
    """
    if not records:
        return None
    cutoff = now - timedelta(hours=window_hours)
    inside = []
    for record in records:
        if not isinstance(record, dict):
            continue
        at = _parse_stamp(record.get("at"))
        if at is None or at >= cutoff:
            inside.append(record)
    if not inside:
        return None
    worst = max(inside, key=lambda r: r.get("gapSeconds") or 0)
    return (
        f"SCHEDULER LAG — {len(inside)} recorded pass(es) started more than a "
        f"beat late in the last {window_hours:g}h; the worst was "
        f"{worst.get('gapSeconds')}s after the previous pass "
        f"(interval {worst.get('intervalSeconds')}s, and that previous pass "
        f"itself took {worst.get('passSeconds')}s). A gap that is mostly its "
        "pass means run_due_heartbeats is slow; a long gap around a short "
        "pass means the scheduler thread was starved."
    )


def reasons_for(slots, records, period_seconds, heartbeat_id=None, field="reason"):
    """`{slot: [reason, ...]}` --- why the poller declined each slot.

    A record is matched to the slot whose own period contains the moment it
    was written, which is the same window `attribute` uses for a rollout: a
    due tick is dropped *during* the slot it belongs to. Records carrying no
    readable timestamp are skipped rather than guessed at --- a reason beside
    the wrong slot is worse than no reason, because this exists to be
    believed.

    `heartbeat_id` filters to one heartbeat when it is known; a record
    written before the field existed carries none and is never dropped for
    it, since the alternative is silently discarding the history this was
    built to keep.
    """
    period = timedelta(seconds=period_seconds)
    found = {slot: [] for slot in slots}
    for record in records:
        if not isinstance(record, dict):
            continue
        if heartbeat_id and record.get("heartbeatId") not in (None, heartbeat_id):
            continue
        at = _parse_stamp(record.get("at"))
        if at is None:
            continue
        for slot in slots:
            if slot <= at < slot + period:
                reason = record.get(field) or "no reason recorded"
                if reason not in found[slot]:
                    found[slot].append(reason)
    return found


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


def split_by_in_flight(slots, intervals):
    """`(covered, idle)` --- which of these slots had a run already going.

    A slot is *covered* when some earlier run had started and had not yet
    said its last word when the slot came round. That is not an excuse and
    this does not treat it as one. It is printed apart from an *idle* one
    because a slot lost with a cycle running and a slot lost with nothing
    running are different failures.

    What a covered slot is NOT is proof that the poller declined anything.
    This function knows only that a run overlapped the slot; the concurrency
    limit is 3, so the tick had room, and a tick with room that produced no
    run was either declined for another reason or never evaluated at all
    (`_skipped_occurrences`, runner#916). Those are different bugs with
    different fixes and only the runner's own drop-record ledger separates
    them -- see `split_by_drop_record`, which this hands its result to.

    `intervals` are `(start, last_message)` pairs. A run's own slot is not
    counted against it: the interval is treated as open at the start, so
    the firing that produced a run never reads as covered by it.
    """
    covered, idle = [], []
    for slot in slots:
        if any(start < slot <= end for start, end in intervals):
            covered.append(slot)
        else:
            idle.append(slot)
    return covered, idle


def ledger_start(records):
    """The oldest moment the drop-record ledger can speak for, or None.

    A slot earlier than this has no record because the recorder was not
    writing yet, which is a completely different thing from no tick having
    been declined -- and treating the two as one is how a report grows a
    cause it never measured. `agora_runner.dropped_ticks` keeps the newest
    200 records, so the oldest one is a lower bound on coverage: the ledger
    may reach further back than this, never less far.

    None for an empty or unreadable ledger, which means it speaks for
    nothing and every slot stays unjudged.
    """
    stamps = [
        _parse_stamp(record.get("at"))
        for record in records
        if isinstance(record, dict)
    ]
    stamps = [stamp for stamp in stamps if stamp is not None]
    return min(stamps) if stamps else None


def split_by_drop_record(slots, named, start):
    """`(declined, unevaluated, unjudged)` --- did the poller decline these?

    `named` is `reasons_for`'s mapping and `start` is `ledger_start`'s.

    * **declined** --- the poller wrote a reason inside this slot's period, so
      it looked at the tick and turned it down. The reason says which guard.
    * **unevaluated** --- the ledger covers this slot and holds nothing for
      it. `_drop_tick` records every decline at a doubling, so a slot the
      poller judged and declined leaves a record; a slot with none was never
      judged, which is the skipped-occurrence path rather than the limit.
    * **unjudged** --- the ledger does not reach back this far (or could not
      be read), so both of the above are still open and this says so instead
      of picking one.
    """
    declined, unevaluated, unjudged = [], [], []
    for slot in slots:
        if named.get(slot):
            declined.append(slot)
        elif start is not None and slot >= start:
            unevaluated.append(slot)
        else:
            unjudged.append(slot)
    return declined, unevaluated, unjudged


def judge(heartbeat, conversations, now, window_hours):
    """One row's verdict --- a dict, never a raise."""
    name = heartbeat.get("name") or heartbeat.get("id") or "<unnamed>"
    row = {"name": name, "id": heartbeat.get("id"),
           "schedule": heartbeat.get("schedule") or "", "verdict": "unjudged"}
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
    intervals = []
    for conv in conversations:
        if conv.get("folderId") != folder:
            continue
        stamp = _parse_stamp(conv.get("createdAt"))
        if stamp is None or stamp < window_start:
            continue
        runs.append(stamp)
        # `lastMessageAt` is the newest thing said in that run's own
        # conversation, which is the closest thing Agora keeps to "when the
        # run stopped". A run that has said nothing yet is a zero-length
        # interval rather than an open-ended one -- covering a slot needs
        # evidence, and absence of evidence must not manufacture it.
        intervals.append((stamp, _parse_stamp(conv.get("lastMessageAt")) or stamp))
    missed = missed_slots(runs, period, window_start, now)
    row.update(
        verdict="judged",
        period_seconds=period,
        runs=len(runs),
        missed=missed,
        intervals=intervals,
        expected=len(runs) + len(missed),
        oldest_run=min(runs) if runs else None,
        window_start=window_start,
    )
    return row


def format_report(results, error, window_hours, listed,
                  rollouts=None, shape=None, rollout_error=None,
                  drop_records=None, drop_error=None,
                  lag_records=None, lag_error=None,
                  fail_records=None, fail_error=None, now=None):
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
                    # A pass that RAISED is taken out first, and before the
                    # in-flight split rather than inside it: the scheduler
                    # raising loses a slot whether or not a run was going, so
                    # asking "was something in flight" about it answers a
                    # question that is not the cause. Leaving these in would
                    # file them as `unevaluated`, which names a different bug.
                    raised = reasons_for(unexplained, fail_records or [],
                                         row["period_seconds"], field="error")
                    failed = [t for t in unexplained if raised.get(t)]
                    if failed:
                        lines.append(
                            f"        {len(failed)} of them had a scheduler pass "
                            "raise inside their own period, so no tick was "
                            "evaluated at all: "
                            + ", ".join(
                                t.astimezone(timezone.utc).strftime("%m-%d %H:%M")
                                + f" ({'; '.join(raised[t])})"
                                for t in failed[:12]
                            )
                        )
                    unexplained = [t for t in unexplained if t not in failed]
                    covered, idle = split_by_in_flight(
                        unexplained, row.get("intervals") or []
                    )
                    # The poller's own account of these slots, read once and
                    # used twice: to say WHY a tick was declined, and -- for a
                    # slot the ledger covers and says nothing about -- to say
                    # that nothing declined it, which is a different bug.
                    # No `drop_error` branch: `read_drop_records` returns an
                    # empty list with every error it reports, so an unreadable
                    # ledger already lands on "speaks for nothing" here. The
                    # error itself is printed below.
                    named = reasons_for(unexplained, drop_records or [],
                                        row["period_seconds"], row.get("id"))
                    start = ledger_start(drop_records or [])
                    if covered:
                        # NOT `unjudged` -- that name is the outer list of
                        # heartbeat rows this function reports on further down.
                        declined, unevaluated, unattributed = split_by_drop_record(
                            covered, named, start
                        )
                        if declined:
                            lines.append(
                                f"        {len(declined)} of them came round with an "
                                "earlier run still going and the poller recorded "
                                "declining them, so a guard took the tick (reason "
                                "below): "
                                + ", ".join(
                                    t.astimezone(timezone.utc).strftime("%m-%d %H:%M")
                                    for t in declined[:12]
                                )
                            )
                        if unevaluated:
                            lines.append(
                                f"        {len(unevaluated)} of them came round with an "
                                "earlier run still going and the poller recorded "
                                "declining nothing, so the tick was never evaluated "
                                "rather than declined -- the skipped-occurrence path, "
                                "not the concurrency limit: "
                                + ", ".join(
                                    t.astimezone(timezone.utc).strftime("%m-%d %H:%M")
                                    for t in unevaluated[:12]
                                )
                            )
                        if unattributed:
                            lines.append(
                                f"        {len(unattributed)} of them came round with an "
                                "earlier run still going, and the drop-record ledger "
                                "does not reach back that far, so a declined tick and "
                                "one that was never evaluated look the same here: "
                                + ", ".join(
                                    t.astimezone(timezone.utc).strftime("%m-%d %H:%M")
                                    for t in unattributed[:12]
                                )
                            )
                    if idle:
                        lines.append(
                            f"        {len(idle)} of them came round with nothing running "
                            "at all: "
                            + ", ".join(
                                t.astimezone(timezone.utc).strftime("%m-%d %H:%M")
                                for t in idle[:12]
                            )
                        )
                    # The poller knew why it declined each of these and used
                    # to print it to stdout only, which is collected with the
                    # Pod -- so a slot lost yesterday could never be
                    # explained. `agora_runner.dropped_ticks` writes the same
                    # reason to the vault now; this is where it comes back.
                    if drop_error:
                        lines.append(
                            f"        NO REASONS READ — {drop_error}, so the "
                            "runner's own account of these slots is missing rather "
                            "than empty"
                        )
                    else:
                        for slot in unexplained[:12]:
                            if named.get(slot):
                                lines.append(
                                    "        "
                                    + slot.astimezone(timezone.utc).strftime("%m-%d %H:%M")
                                    + " — the poller said: "
                                    + "; ".join(named[slot])
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

    # Above the "read N conversations" footer, because this is a finding about
    # the loop and that is provenance. It sits outside the per-heartbeat rows
    # on purpose: the scheduler is one thread for every heartbeat in the pod,
    # so a late pass is not attributable to the row it happened to cost.
    if lag_error:
        lines.append(
            f"NO SCHEDULER LAG READ — {lag_error}, so how late the scheduler "
            "ran is missing rather than clean"
        )
    else:
        summary = lag_summary(lag_records or [], now or datetime.now(timezone.utc),
                              window_hours)
        if summary:
            lines.append(summary)
    if fail_error:
        lines.append(
            f"NO SCHEDULER FAILURES READ — {fail_error}, so passes that raised "
            "are missing rather than clean"
        )
    else:
        summary = failure_summary(fail_records or [],
                                  now or datetime.now(timezone.utc), window_hours)
        if summary:
            lines.append(summary)
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
    drop_records, drop_error = read_drop_records()
    lag_records, lag_error = read_lag_records()
    fail_records, fail_error = read_failure_records()
    report, status = format_report(
        results, error, args.hours, len(conversations), rollouts, shape,
        rollout_error, drop_records, drop_error, lag_records, lag_error,
        fail_records, fail_error, now
    )
    print(report)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
