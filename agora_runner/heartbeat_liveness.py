"""Is every Agora heartbeat still firing? --- the judgement, without the report.

This is the half of `tools.heartbeat_health` that `nova-site` needs, split
out of it in Cycle 541 for the owner's idea #117: *"A check that compares
each heartbeat's cadence against the newest conversation it produced, and
says so on the health endpoint."* The check has existed since Cycle 475;
the endpoint half had never been built, and the reason it could not simply
import the tool is measured rather than assumed --- `Dockerfile` copies
`agora_runner/`, `run.py` and `run_nova_site.py` and nothing else, so
`tools/` is not in the image the site runs from.

So the judging moved down here and the tool re-exports it. One copy, and a
change to the grace or to the schedule grammar reaches both readers at
once; a second copy in `nova_site` would be the two-files-that-have-to-agree
failure this loop keeps finding in other people's infrastructure.

**What the endpoint adds over the tool is who is watching.** `tools.heartbeat_health`
runs when a cycle runs, which means the one heartbeat it cannot vouch for is
the hourly loop's own: if that stops, nothing runs the check that would say
so. `nova-site` is a long-lived process on a schedule of its own, so its
answer to "is every heartbeat firing" survives the loop going quiet.

The verdicts and their evidence are unchanged --- `OFF` and `OVERDUE` stay
separate, a `(disabled` marker in a heartbeat's own name means the off state
is documented, and a schedule this cannot parse is `unjudged` rather than
assumed healthy.
"""

import json
import urllib.request
from datetime import datetime, timedelta, timezone

from agora_runner.config import OSLO

# How long the site is willing to wait on Agora before calling the read a
# failure. The CLI tool allows 20 seconds because a human is watching it;
# `/api/health` is asked when something has just broken and an answer that
# hangs is worse than an answer that says it could not reach Agora.
SITE_TIMEOUT_SECONDS = 3


# Agora's public app. `nova_heartbeats` documents why this and not the
# internal :8081 API: the internal one is the runner's bookkeeping
# surface and does not carry `enabled` at all.
AGORA_PUBLIC = "http://agora.agents.svc.cluster.local:8080"

# Two missed turns. One is a scheduler under load; two is a scheduler
# that is not running this row.
_MISSED_TURNS_BEFORE_STOPPED = 2

# How late a *named* slot may be before it counts as missed. Measured
# 2026-08-31 against the five schedules Agora was carrying: it started them
# 0m, 8m, 9m, 13m and 13m after the minute they asked for. Six hours is
# twenty-seven times the worst of those, and it is an absolute number
# rather than a share of the period on purpose --- a weekly heartbeat is
# not allowed to be a week late just because its period is a week. It also
# covers the two hours `OSLO` moves by if `agora_runner.config` falls back
# to UTC on an image with no tzdata.
_SLOT_GRACE_SECONDS = 6 * 3600

# The marker that says an off state is deliberate and written where a
# reader of the Heartbeats page can see it.
_DELIBERATE_MARKER = "(disabled"


def _fetch(url=None, opener=None, timeout=20):
    """`(heartbeats, error)` --- every heartbeat Agora knows about.

    An empty list with no error is a true measurement. A list this could
    not read comes back as an error, because "no heartbeats" and "could
    not ask" are the two things this module exists to keep apart.
    """
    target = (url or AGORA_PUBLIC).rstrip("/") + "/heartbeats"
    try:
        with (opener or urllib.request.urlopen)(target, timeout=timeout) as resp:
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


def _slot_time(schedule):
    """`(hour, minute, days)` --- the wall-clock slot a schedule names.

    `days` is the set of `datetime.weekday()` values the slot falls on, or
    `None` for every day. Returns `None` for a schedule that names no slot
    at all --- `every@20m@16:20` repeats through the day and its anchor is
    not a slot in this sense, so it keeps the averaged rule below.
    """
    text = (schedule or "").strip()
    if text.startswith("daily@"):
        body = text[len("daily@") :].strip()
        parts = body.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return None
        hour, minute = int(parts[0]), int(parts[1])
        if hour > 23 or minute > 59:
            return None
        return hour, minute, None
    if text.startswith("cron@"):
        fields = text[len("cron@") :].split()
        if len(fields) != 5:
            return None
        minute, hour, dom, mon, dow = fields
        if dom != "*" or mon != "*":
            return None
        if not minute.isdigit() or not hour.isdigit():
            return None
        minute, hour = int(minute), int(hour)
        if hour > 23 or minute > 59:
            return None
        if dow == "*":
            return hour, minute, None
        days = set()
        for part in dow.split(","):
            part = part.strip()
            if not part.isdigit():
                return None
            # cron counts Sunday as 0; `weekday()` counts Monday as 0.
            days.add((int(part) % 7 - 1) % 7)
        if not days:
            return None
        return hour, minute, days
    return None


def last_due_slot(schedule, now):
    """The most recent moment this schedule asked to be run at, or `None`.

    Agora reads `cron@` and `daily@` in Europe/Oslo --- see
    `agora_runner.tools_schemas`, and measured against the fleet on
    2026-08-31, where every one of the five schedules fired exactly its
    stated hour in Oslo and therefore two hours before that hour in UTC.
    So the slot has to be built in Oslo and compared in UTC.

    This exists because the averaged interval below cannot see a slot. A
    weekly heartbeat gets `7 * 86400 * 2` of grace, so one that stops firing
    is green for a fortnight --- which is the exact fourteen-day blindness
    this module was written to end, reproduced for anything that runs less
    often than daily.
    """
    found = _slot_time(schedule)
    if found is None:
        return None
    hour, minute, days = found
    local = now.astimezone(OSLO)
    for back in range(0, 8):
        day = (local - timedelta(days=back)).date()
        candidate = datetime(
            day.year, day.month, day.day, hour, minute, tzinfo=OSLO
        )
        if candidate > now:
            continue
        if days is not None and candidate.weekday() not in days:
            continue
        return candidate
    return None


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

    # The sharper instrument first, where the schedule names a slot. A slot
    # that came round after this heartbeat existed, is more than the grace
    # in the past, and is not covered by `lastRunAt`, was missed --- and
    # that is true however long the period is. `reference` is not used here
    # on purpose: measuring a never-run heartbeat from its creation is right
    # for the averaged rule and wrong for this one, because creation is not
    # a run and the question is whether a slot went unanswered.
    slot = last_due_slot(schedule, now)
    if slot is not None and (created is None or slot > created):
        late = (now - slot).total_seconds()
        if late > _SLOT_GRACE_SECONDS and (last is None or last < slot):
            slot_stamp = slot.astimezone(OSLO).strftime("%Y-%m-%d %H:%M Oslo")
            never = "has never run" if last is None else f"last ran {stamp}"
            return dict(
                base,
                verdict="overdue",
                detail=(
                    f"{note}; asked to run at {slot_stamp}, "
                    f"{_duration(late)} ago, and {never}"
                ),
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


def liveness(url=None, opener=None, now=None, timeout=SITE_TIMEOUT_SECONDS):
    """The `heartbeats` block `/api/health` serves --- JSON-ready, never raising.

    `ok` is false when a heartbeat is off without saying so in its own name,
    or is overdue by two of its own turns. A schedule this cannot parse is
    `unjudged`, which is **not** ok either: `tools.heartbeat_health` exits 1
    rather than 0 on one, and an endpoint that reported a heartbeat it could
    not judge as healthy would be the guaranteed-positive failure --- a green
    answer that would have been green regardless.

    A read that fails carries `error` and `ok: false` for the same reason.
    "Agora did not answer" and "every heartbeat is firing" are different
    facts and this never merges them.
    """
    rows, error = _fetch(url=url, opener=opener, timeout=timeout)
    if error:
        return {"ok": False, "error": error, "heartbeats": []}
    now = now or datetime.now(timezone.utc)
    judged = [judge(row, now) for row in rows]
    out = []
    for row in judged:
        last = row["last"]
        out.append(
            {
                "name": row["name"],
                "schedule": row["schedule"],
                "verdict": row["verdict"],
                "detail": row["detail"],
                "lastRunAt": last.astimezone(timezone.utc).isoformat() if last else None,
            }
        )
    # Agora answering with no heartbeats at all is not a healthy loop; it is
    # the scheduler having lost every row, which is the largest version of
    # what this watches for.
    bad = {"off", "overdue", "unjudged"}
    return {
        "ok": bool(out) and not any(r["verdict"] in bad for r in out),
        "error": None,
        "heartbeats": out,
    }
