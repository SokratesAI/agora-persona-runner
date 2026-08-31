"""Is server1's memory falling, and when does it run out?

Cycle 693, on the owner's issue #131. On 2026-08-29 the box had 487Mi of
7746Mi left and its whole 2GB of swap gone; the k3s control plane
restarted, ten pods went down with it and the persona runner was
OOMKilled. He found the cause from a host shell that I do not have --
thirteen `claude.exe` remote-control children, 2.4GB between them, twelve
of them stale, the oldest eleven days old and never reaped -- killed them,
and wrote the part that makes this a tool rather than a fixed bug:

    "That daemon isn't something either of us can patch (it's the Claude
    Code product itself, not platform-config), so this will recur --
    worth a periodic check (host free -m / stale claude.exe age) rather
    than a one-time fix."

`tools.workload_health` took the `free -m` half (runner#518) and it reads
a **level**: available memory against the largest configured container
limit, and swap free against 10% of swap total. A level is the right
judgement for a spike and the wrong one for a leak. A leak crosses a
level threshold exactly once, at the end, and every reading before that
is individually fine -- which is what the box looked like for the eleven
days those processes were accumulating. Measured this morning, both
numbers pass and one of them has moved a long way: swap went from 1808Mi
free right after he killed the twelve (2026-08-29) to 616Mi free
(2026-08-31 04:31 Oslo), 1192Mi in two days, while `workload_health` said
`SWAP  server1: 616Mi free of 2048Mi (30.1%)` and exited 0. It is right
to. 30% is not an incident. Losing 596Mi a day is.

So this is the trend half, and it needs history that nothing here keeps.
The cluster does run Prometheus, and it cannot answer: measured this
cycle, `prometheus-config` scrapes exactly three jobs -- itself, nats and
agora -- there is no node-exporter anywhere, so no node metric has ever
been recorded on this box. This keeps its own ledger instead, one line
per run on the pod's persistent volume, and reports the slope.

**What it will not do is read as clean when it cannot judge.** A ledger
with too few readings, or spanning too little time, exits 1 and says how
much more it needs -- a fresh ledger is no instrument, not a healthy box,
and that distinction is the whole reason the 08-29 outage was found by
accident. Same for a `/proc/meminfo` it cannot attribute to a node:
neither pod here mounts lxcfs, so that file is the host's, and the proof
is that `MemTotal` equals a node's capacity. If some future runtime fakes
it per container, this would record a container's memory as the host's
and every slope below would be about the wrong machine.

Cycle 699 added the two readings that need no history at all, because
"NOT ENOUGH HISTORY" was the whole of what this said for its first three
hours and the box it watches was two days from an incident when it was
written. `/proc/pressure/memory` is the kernel's own account of how long
*everything* was blocked on memory, and `/proc/vmstat`'s `oom_kill` is how
many tasks it has ended since boot -- both host-wide, both readable from
this pod with no grant, and both about harm already done rather than a
projection.

Cycle 705 added the third thing that needs no grant: *which side of the pod
boundary* the memory sits on, trended rather than read once. `nodes/proxy`
is Forbidden for both of this loop's service accounts -- measured Cycle 705
from the bridge pod and from the runner pod, and re-measured Cycle 709.

The note that used to sit here said `kubectl auth can-i get nodes/proxy`
answers `yes` while the call is refused, and concluded "that check is not
the one to trust". The check was fine; the question was wrong. In
`kubectl auth can-i`, `<resource>/<name>` is a **named object**, not a
subresource -- `can-i get nodes/definitely-not-a-real-node` also answers
`yes` (measured Cycle 709), because this loop holds `get list watch` on
`nodes` and every node name is covered by it. Ask about a subresource with
the flag that means one:

    kubectl auth can-i get --subresource=proxy nodes

which answers `no`, and agrees with the 403 the call itself returns. So
there is a working instrument for "may I read this subresource", and it is
one call, and it costs nothing.

Measured Cycle 709 in that form: `proxy`, `metrics` and `stats` are all
`no`. That third answer closes an avenue nobody had tested -- the kubelet
listens on `:10250` and serves `/metrics/cadvisor` (which carries a line
per cgroup on the node, including `/system.slice`, i.e. exactly the
attribution below) without going through `nodes/proxy` at all, but it
authorizes that path against `nodes/metrics`, and this loop does not have
it. The port is also refused by this namespace's own egress policy, in
0.0003s from both pods. Opening the NetworkPolicy alone would not have
worked, and now nobody has to build it to find that out.

What is readable from here
is `AnonPages` minus every pod's working set, and that is the number which
grew for eleven days before the 08-29 outage. `workload_health.attribution`
reads it at one instant; nothing trended it, and the headroom this file
already trends falls the same way whether a pod or a host process is the
cause. Naming the individual host *processes* still needs a `ps aux` from
the owner. `tools.workload_health` reads container states, so an OOM
kill of a host process -- the 2026-08-29 shape, twelve stale `claude.exe`
children -- is invisible to it by construction.

Both thresholds are calibrated against this box rather than picked: the
stall line is eleven times server1's own since-boot average, and the
OOM-kill line is a multiple of its own since-boot rate. See the constants.

Exit 0 means it judged the trend and nothing is falling toward zero inside
the horizon, and the box is neither stalling nor killing at a rate above
its own. Exit 2 means one of those three is true. Exit 1 means it could not
judge -- and note the order: a stall raises 2 even on a fresh ledger, so a
real incident is never reported as "not enough history".
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.workload_health import (  # noqa: E402
    read_meminfo,
    read_node_capacity,
    read_pod_working_set,
)

#: The ledger, on the pod's persistent volume. `$NOVA_WORKSPACE` is
#: deliberately not used: it points at a per-cycle worktree when cycles run
#: concurrently, so a ledger written there would start empty every other
#: cycle. Two concurrent cycles do read and rewrite this one file, and the
#: later writer can drop the earlier one's sample -- that is deliberate
#: rather than unhandled, because this is a sampled series and one lost
#: sample changes no slope, whereas a lock would make an instrument able to
#: block a cycle.
DEFAULT_LEDGER = os.environ.get("NOVA_HOST_MEMORY_LEDGER", "/data/nova-host-memory.jsonl")

#: How many readings to keep. At the 24-minute heartbeat this loop runs on
#: that is about five weeks, and the file is ~150 bytes a line.
DEFAULT_KEEP = 2000

#: Readings older than this are not part of the slope. Long enough that one
#: quiet night does not dominate it, short enough that a leak that started
#: yesterday is still visible.
DEFAULT_WINDOW_HOURS = 72

#: A slope needs both of these before it is allowed to project anything.
#: Two readings twenty minutes apart can describe any line at all.
MIN_READINGS = 6
MIN_SPAN_HOURS = 6.0

#: Project to zero. Anything further out than this is not a finding -- it
#: is arithmetic about a rate that will have changed by then.
DEFAULT_HORIZON_DAYS = 7.0

#: The two fields worth a trend, and what they are called in a sentence.
TRACKED = (
    ("mem_available_mib", "available memory"),
    ("swap_free_mib", "free swap"),
)

#: Where the kernel publishes system-wide stall time and the OOM-kill count.
#: Both are read from the same `/proc` this file already proves is the host's
#: by matching `MemTotal` to a node's capacity -- so the attribution below
#: covers them too, and neither needs a permission this loop does not have.
PRESSURE_PATH = "/proc/pressure/memory"
VMSTAT_PATH = "/proc/vmstat"

#: Percent of the last five minutes during which *every* runnable task was
#: stalled on memory before this calls it harm. Derived from this box rather
#: than picked: on 2026-08-31, at 136 days of uptime, `/proc/pressure/memory`
#: read `full total=104934927170` microseconds against 1.175e13 microseconds
#: of uptime -- 0.89% since boot. Ten percent is eleven times the box's own
#: long-run average and means one second in ten with nothing able to run.
#: `some` is deliberately not judged: it trips on a single process waiting
#: for one page and this box has averaged 1.42% of it while healthy.
FULL_STALL_PERCENT = 10.0

#: An OOM-kill rate this many times the box's own since-boot rate is the
#: finding. A rate rather than a count, and calibrated against the same box
#: rather than against a number I invented: 627 kills over 136 days is 4.6 a
#: day, so about one per five hours, and a check that raised on a single kill
#: would raise several times a day forever -- which is the "will I ever be
#: able to ignore this?" test that `journal-digest.md` says 13 of my checks
#: already fail.
OOM_RATE_MULTIPLE = 3.0

#: ...and never on fewer than this many kills in the window, so a quiet box
#: with a near-zero baseline cannot be tripped by one kill.
OOM_MIN_KILLS = 3


def read_pressure(path=PRESSURE_PATH):
    """`(some_avg300, full_avg300, full_total_us)` from PSI, or `(None, why)`.

    A kernel without `CONFIG_PSI` has no such file; that is a missing
    instrument, not a healthy host, and it is reported as one.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, (
            f"{path} could not be read, so nothing here knows whether the box "
            "is actually stalling on memory."
        )
    fields = {}
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        for token in parts[1:]:
            key, _, value = token.partition("=")
            try:
                fields[f"{parts[0]}_{key}"] = float(value)
            except ValueError:
                continue
    missing = [k for k in ("some_avg300", "full_avg300", "full_total")
               if k not in fields]
    if missing:
        return None, (
            f"{path} carries no {', '.join(missing)}, so its format is not the "
            "one this reads."
        )
    return (fields["some_avg300"], fields["full_avg300"], fields["full_total"]), None


def read_oom_kills(path=VMSTAT_PATH):
    """The host's cumulative OOM-kill count, or `(None, why)`.

    Counts every task the kernel's OOM killer has ended since boot, in a pod
    cgroup or outside every one of them. `tools.workload_health` reads
    container states, so a killed host process -- the 2026-08-29 shape -- is
    invisible to it by construction.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, f"{path} could not be read, so host OOM kills go uncounted."
    for line in raw.splitlines():
        name, _, value = line.partition(" ")
        if name == "oom_kill":
            try:
                return float(value), None
            except ValueError:
                return None, f"{path} carries an unparseable oom_kill line: {line!r}"
    return None, (
        f"{path} carries no oom_kill counter, so this kernel does not publish one."
    )


def read_uptime_days(path="/proc/uptime"):
    """`(days, None)` since this host booted, or `(None, why)`.

    It returns a `why` for the same reason its two siblings do, and the
    reason is sharper here: this is the denominator of the OOM-kill
    baseline, so losing it does not lose a printed number -- it turns the
    burst detector off. A bare `None` made that invisible.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
        return float(raw.split()[0]) / 86400.0, None
    except (OSError, UnicodeDecodeError) as exc:
        return None, (
            f"{path} could not be read ({exc.__class__.__name__}), so the "
            "OOM-kill baseline has no denominator."
        )
    except (ValueError, IndexError):
        return None, (
            f"{path} does not start with a number of seconds, so the OOM-kill "
            "baseline has no denominator."
        )


def reading_now(meminfo, nodes, at=None, pressure=None, oom_kills=None,
                uptime_days=None, pod_working_set=None):
    """One ledger row for this instant, or `(None, why)`.

    `why` is always about the instrument, never about the box. The pressure
    and OOM-kill readings are passed in rather than read here so that a
    kernel that publishes neither still produces a row -- the slope was
    working before they existed and must not start failing because of them.
    """
    total_mib = meminfo.get("MemTotal", 0) / 1024
    host = next((name for name, mib in nodes.items() if abs(mib - total_mib) < 1), None)
    if host is None:
        seen = ", ".join(f"{n} {m:.0f}Mi" for n, m in sorted(nodes.items())) or "none read"
        return None, (
            f"/proc/meminfo says {total_mib:.0f}Mi total, which matches no node's "
            f"capacity ({seen}). That is a container's view, not the host's, so a "
            "reading taken from it would be about the wrong machine."
        )
    available = meminfo.get("MemAvailable")
    if available is None:
        return None, (
            f"{host} matched, but /proc/meminfo carries no MemAvailable, which is "
            "the only field that says what can still be allocated."
        )
    when = (at or datetime.now(timezone.utc)).replace(microsecond=0)
    row = {
        # `_at` is the parsed instant every comparison here uses; `save`
        # strips underscore keys, so it never reaches the file.
        "_at": when,
        "at": when.isoformat(),
        "host": host,
        "mem_total_mib": round(total_mib, 1),
        "mem_available_mib": round(available / 1024, 1),
        "swap_total_mib": round(meminfo.get("SwapTotal", 0) / 1024, 1),
        "swap_free_mib": round(meminfo.get("SwapFree", 0) / 1024, 1),
    }
    if pressure is not None:
        some_avg300, full_avg300, full_total_us = pressure
        row["psi_some_avg300"] = round(some_avg300, 2)
        row["psi_full_avg300"] = round(full_avg300, 2)
        row["psi_full_total_us"] = full_total_us
    if oom_kills is not None:
        row["oom_kills"] = oom_kills
    if uptime_days is not None:
        row["uptime_days"] = round(uptime_days, 3)
    anon = meminfo.get("AnonPages")
    if anon is not None:
        row["anon_mib"] = round(anon / 1024, 1)
        if pod_working_set is not None:
            pods_mib, counted = pod_working_set
            row["pods_working_set_mib"] = round(pods_mib, 1)
            row["pod_count"] = counted
            # Can be negative, and that is a real reading rather than an error:
            # a Pod's working set counts page cache its cgroup holds, which is
            # not anonymous, so the two instruments overlap and can cross.
            # `attribute_slope` says so rather than clamping it.
            row["host_anon_mib"] = round(anon / 1024 - pods_mib, 1)
    return row, None


def load(path):
    """`(readings, skipped)` -- oldest first. A missing file is no readings."""
    rows, skipped = [], 0
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], 0
    except (OSError, UnicodeDecodeError):
        return None, 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            parsed = datetime.fromisoformat(row["at"])
        except (ValueError, TypeError, KeyError):
            skipped += 1
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        row["_at"] = parsed
        rows.append(row)
    rows.sort(key=lambda r: r["_at"])
    return rows, skipped


def save(path, rows, keep):
    """Write the newest `keep` rows back. `None` on failure, else how many."""
    kept = rows[-keep:] if keep > 0 else rows
    body = "\n".join(
        json.dumps({k: v for k, v in row.items() if not k.startswith("_")}, sort_keys=True)
        for row in kept
    )
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body + "\n" if body else "", encoding="utf-8")
    except OSError:
        return None
    return len(kept)


def slope_per_day(rows, field):
    """Least-squares Mi/day for `field`, or None when the line is degenerate."""
    points = []
    base = rows[0]["_at"]
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        points.append(((row["_at"] - base).total_seconds() / 86400.0, float(value)))
    if len(points) < 2:
        return None
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    denom = sum((x - mean_x) ** 2 for x, _ in points)
    if denom == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denom


def gated_slope(rows, field, min_readings=MIN_READINGS,
                min_span_hours=MIN_SPAN_HOURS):
    """`(rate, count, span_hours)` for `field`, rate None when its own series is too thin.

    `slope_per_day` skips the rows that do not carry `field`, so a gate the
    caller applied to the whole window says nothing about the sample the slope
    was actually fitted to. A field added to the ledger later is present on
    only the newest few readings, and that gap is not an edge case -- it is
    every field's first day. Measured 2026-08-31: the window was 19 readings
    over 6.0h, `host_anon_mib` was on 4 of them spanning 49 minutes, and the
    ATTRIBUTION line printed -7207Mi/day against a 1912Mi value, a rate that
    cannot hold for even seven hours. So the gate belongs on the series, not
    on the window that contains it.
    """
    carrying = [r for r in rows if r.get(field) is not None]
    span_hours = (
        (carrying[-1]["_at"] - carrying[0]["_at"]).total_seconds() / 3600.0
        if len(carrying) >= 2 else 0.0
    )
    if len(carrying) < min_readings or span_hours < min_span_hours:
        return None, len(carrying), span_hours
    return slope_per_day(carrying, field), len(carrying), span_hours


def window(rows, host, now, hours):
    """The rows for `host` inside the trailing window, oldest first."""
    cutoff = now - timedelta(hours=hours)
    return [r for r in rows if r.get("host") == host and r["_at"] >= cutoff]


def judge(rows, current, horizon_days, min_readings=MIN_READINGS,
          min_span_hours=MIN_SPAN_HOURS):
    """`(lines, actionable, judged)` for one host's windowed readings."""
    host = current["host"]
    lines = []
    span_hours = (rows[-1]["_at"] - rows[0]["_at"]).total_seconds() / 3600.0 if rows else 0.0
    if len(rows) < min_readings or span_hours < min_span_hours:
        lines.append(
            f"NOT ENOUGH HISTORY — {len(rows)} reading(s) spanning {span_hours:.1f}h "
            f"for {host}; a slope needs at least {min_readings} over {min_span_hours:.0f}h. "
            "This is no instrument yet, not a healthy host."
        )
        return lines, False, False

    actionable = False
    lines.append(
        f"TREND   {host}: {len(rows)} reading(s) over {span_hours:.1f}h."
    )
    for field, label in TRACKED:
        value = current.get(field)
        rate, carried, field_span = gated_slope(
            rows, field, min_readings, min_span_hours)
        if value is None:
            lines.append(
                f"CANNOT TREND {label} — this reading does not carry it."
            )
            continue
        if rate is None:
            lines.append(
                f"CANNOT TREND {label}: {value:.0f}Mi now — {carried} of the "
                f"{len(rows)} reading(s) in the window carry it, spanning "
                f"{field_span:.1f}h, and a slope needs at least {min_readings} "
                f"over {min_span_hours:.0f}h."
            )
            continue
        if rate >= 0:
            lines.append(
                f"  {label}: {value:.0f}Mi now, {rate:+.0f}Mi/day — not falling."
            )
            continue
        days_left = value / -rate
        detail = (f"  {label}: {value:.0f}Mi now, {rate:+.0f}Mi/day, "
                  f"zero in about {days_left:.1f} day(s)")
        if days_left <= horizon_days:
            actionable = True
            lines.append(
                f"FALLING — {host}'s {label} is {value:.0f}Mi and dropping "
                f"{-rate:.0f}Mi/day, which reaches zero in about {days_left:.1f} day(s), "
                f"inside the {horizon_days:.0f}-day horizon."
            )
        else:
            lines.append(detail + f", beyond the {horizon_days:.0f}-day horizon.")
    return lines, actionable, True


def attribute_slope(rows, current, min_readings=MIN_READINGS,
                    min_span_hours=MIN_SPAN_HOURS):
    """Which side of the Pod boundary the memory moved on, as lines.

    `judge` above trends the host's *headroom*, which falls the same way
    whether a Pod is leaking or a process on the host is. On 2026-08-29 that
    distinction was the whole diagnosis and it took a host shell to get:
    Pods were using 2,086Mi of a 7,745Mi box while `AnonPages` was 6,842Mi,
    so ~4,756Mi of anonymous memory belonged to something outside every Pod
    cgroup -- thirteen stale `claude.exe` children, as it turned out. Every
    other check in this loop reads Pods, so a leak on the host side is
    invisible to all of them by construction, and `workload_health`'s own
    `attribution` reads that split at one instant only.

    This does not raise. `judge` already raises on the headroom falling, and
    a second alarm on the same event is one alarm nobody reads -- the same
    call `security_alerts` makes on an already-fixed advisory. What this adds
    is the direction, so a cycle reading a FALLING line knows which side to
    go and look at.

    It names a side only when exactly one of the two is rising. When both
    are, it prints both rates and picks neither: "the larger of two positive
    numbers" is a comparison that means nothing at 1.0 against 0.9Mi/day, and
    the threshold that would make it mean something is a number I would have
    had to invent.
    """
    span_hours = ((rows[-1]["_at"] - rows[0]["_at"]).total_seconds() / 3600.0
                  if rows else 0.0)
    if len(rows) < min_readings or span_hours < min_span_hours:
        # `judge` has already said NOT ENOUGH HISTORY in the same report.
        return []
    host_now = current.get("host_anon_mib")
    pods_now = current.get("pods_working_set_mib")
    if host_now is None or pods_now is None:
        return ["CANNOT SEE the host/Pod split on this reading, so there is "
                "nothing to compare the window against."]
    host_rate, host_n, host_span = gated_slope(
        rows, "host_anon_mib", min_readings, min_span_hours)
    pods_rate, pods_n, pods_span = gated_slope(
        rows, "pods_working_set_mib", min_readings, min_span_hours)
    if host_rate is None or pods_rate is None:
        # The levels are real and this reading measured them; only the rates
        # are unavailable, so the levels stay and the rates go.
        return [
            f"ATTRIBUTION  outside every Pod cgroup: {host_now:.0f}Mi now. "
            f"Pods: {pods_now:.0f}Mi now.",
            f"CANNOT SEE which side is moving — host_anon_mib is on {host_n} "
            f"reading(s) spanning {host_span:.1f}h and pods_working_set_mib on "
            f"{pods_n} spanning {pods_span:.1f}h; a slope needs at least "
            f"{min_readings} over {min_span_hours:.0f}h. Readings taken before "
            "the split was recorded do not carry one, so this fills in as the "
            "window rolls forward.",
        ]
    lines = [
        f"ATTRIBUTION  outside every Pod cgroup: {host_now:.0f}Mi now, "
        f"{host_rate:+.0f}Mi/day. Pods: {pods_now:.0f}Mi now, "
        f"{pods_rate:+.0f}Mi/day."
    ]
    if host_now <= 0:
        lines.append(
            "  The Pods' working set is at or above the host's anonymous total, "
            "so they are holding page cache as well and the split is not "
            "measurable on this reading. The two figures come from different "
            "instruments and overlap.")
        return lines
    rising = [name for name, rate in (("the host is", host_rate),
                                      ("the Pods are", pods_rate))
              if rate > 0]
    if not rising:
        lines.append("  Neither side is growing over this window.")
    elif len(rising) == 2:
        lines.append("  Both sides are growing; this does not pick between them.")
    else:
        where = ("processes on the host itself (k3s, containerd, anything "
                 "hand-run), which no other check here can see"
                 if rising[0].startswith("the host")
                 else "the Pods, which tools.workload_health reads per container")
        lines.append(f"  Only {rising[0]} growing over this window — {where}.")
    return lines


def judge_harm(current, rows, full_stall_percent=FULL_STALL_PERCENT,
               oom_rate_multiple=OOM_RATE_MULTIPLE, oom_min_kills=OOM_MIN_KILLS):
    """`(lines, actionable)` for the two readings that need no history.

    The slope above cannot say anything until it has six readings over six
    hours. These can, on the very first run, because they are the box's own
    account of harm already done rather than a projection: PSI is how long
    everything was stalled, and `oom_kill` is how many tasks the kernel ended.
    A fresh ledger is still no trend -- it is just no longer no instrument.
    """
    lines, actionable = [], False

    full = current.get("psi_full_avg300")
    some = current.get("psi_some_avg300")
    if full is None or some is None:
        lines.append("CANNOT READ PRESSURE — no PSI in this reading, so nothing "
                     "here knows whether the box is stalling.")
    else:
        detail = (f"  stall: {full:.2f}% of the last 5 min with every task blocked "
                  f"on memory, {some:.2f}% with any task blocked")
        if full >= full_stall_percent:
            actionable = True
            lines.append(
                f"STALLING — {current['host']} spent {full:.2f}% of the last five "
                f"minutes with every runnable task blocked on memory, at or over "
                f"the {full_stall_percent:.0f}% line. Free memory is a forecast; "
                "this is the box already losing time."
            )
        else:
            lines.append(detail + ".")

    kills = current.get("oom_kills")
    uptime = current.get("uptime_days")
    if kills is None:
        # A caveat preflight does not match is dropped from the collapsed
        # report on an otherwise-clean run -- visible only under --verbose,
        # which is the one place nobody looks. `tools.preflight.is_caveat`
        # takes any shouted opening carrying one of its stems, so "CANNOT
        # COUNT" would match too now; this stays "CANNOT READ" because that is
        # the truer word for it, not because the matcher needs it.
        lines.append("CANNOT READ OOM KILLS — no oom_kill in this reading.")
        return lines, actionable

    baseline = kills / uptime if uptime else None
    if baseline is None:
        lines.append(
            f"CANNOT READ OOM RATE — {kills:.0f} kill(s) since boot, but no uptime "
            "to divide by, so the burst detector below cannot judge anything."
        )
    prior = [r for r in rows if r.get("oom_kills") is not None and r is not current]
    if not prior:
        lines.append(
            f"  OOM kills: {kills:.0f} since boot"
            + (f", {baseline:.1f}/day over {uptime:.0f} day(s) of uptime" if baseline
               else "")
            + " — no earlier reading carries the counter, so no recent rate yet."
        )
        return lines, actionable

    first = prior[0]
    span_days = (current["_at"] - first["_at"]).total_seconds() / 86400.0
    delta = kills - first["oom_kills"]
    if delta < 0:
        lines.append(
            f"  OOM kills: the counter went backwards ({first['oom_kills']:.0f} to "
            f"{kills:.0f}), so the box rebooted and the window is not comparable."
        )
        return lines, actionable
    if span_days <= 0:
        lines.append(f"  OOM kills: {kills:.0f} since boot; the window has no duration.")
        return lines, actionable

    rate = delta / span_days
    summary = (f"  OOM kills: {delta:.0f} in the last {span_days * 24:.1f}h "
               f"({rate:.1f}/day) against {kills:.0f} since boot")
    if baseline is not None:
        summary += f" ({baseline:.1f}/day)"
    # `baseline is not None`, not `baseline` -- a box that has never OOM-killed
    # anything has a baseline of exactly zero, and the truthiness test silently
    # made such a box unraisable. Zero needs no special case beyond that:
    # `rate >= 0` is true of any burst, so `oom_min_kills` is the judgement there.
    if (baseline is not None and delta >= oom_min_kills
            and rate >= baseline * oom_rate_multiple):
        actionable = True
        lines.append(
            f"OOM KILLING — {current['host']} killed {delta:.0f} task(s) in the last "
            f"{span_days * 24:.1f}h, {rate:.1f}/day against its own since-boot rate of "
            f"{baseline:.1f}/day. This counts kills outside every pod cgroup too, "
            "which is the half tools.workload_health cannot see."
        )
    else:
        lines.append(summary + ".")
    return lines, actionable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", default=DEFAULT_LEDGER,
                        help="where the readings are kept")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help="how many readings to retain")
    parser.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS,
                        help="how far back the slope is measured over")
    parser.add_argument("--horizon-days", type=float, default=DEFAULT_HORIZON_DAYS,
                        help="project to zero no further out than this")
    parser.add_argument("--no-record", action="store_true",
                        help="judge the ledger without appending to it")
    args = parser.parse_args(argv)

    print("HOST MEMORY TREND")

    meminfo, why = read_meminfo()
    if meminfo is None:
        print(f"COULD NOT READ: {why}")
        return 1
    nodes, why = read_node_capacity()
    if nodes is None:
        print(f"COULD NOT READ: {why}")
        return 1
    if not nodes:
        print("COULD NOT READ: kubectl returned no node capacities, so /proc/meminfo "
              "cannot be attributed to a host.")
        return 1

    pressure, pressure_why = read_pressure()
    oom_kills, oom_why = read_oom_kills()
    uptime_days, uptime_why = read_uptime_days()
    pod_working_set, pods_why = read_pod_working_set()
    current, why = reading_now(meminfo, nodes, pressure=pressure,
                               oom_kills=oom_kills, uptime_days=uptime_days,
                               pod_working_set=pod_working_set)
    if current is None:
        print(f"CANNOT ATTRIBUTE MEMORY — {why}")
        return 1

    rows, skipped = load(args.ledger)
    if rows is None:
        print(f"COULD NOT READ: the ledger at {args.ledger} is unreadable.")
        return 1
    if skipped:
        print(f"  {skipped} unparseable line(s) in {args.ledger} were skipped.")

    if args.no_record:
        print(f"  not recording this reading (--no-record); ledger {args.ledger}")
    else:
        rows.append(current)
        written = save(args.ledger, rows, args.keep)
        if written is None:
            print(f"COULD NOT WRITE: the ledger at {args.ledger} is not writable, so "
                  "no history accumulates and no slope will ever exist.")
            return 1
        print(f"  recorded; ledger {args.ledger} holds {written} reading(s).")

    print(f"  now: {current['mem_available_mib']:.0f}Mi of "
          f"{current['mem_total_mib']:.0f}Mi available, "
          f"{current['swap_free_mib']:.0f}Mi of {current['swap_total_mib']:.0f}Mi swap free.")

    if pressure is None:
        print(f"  {pressure_why}")
    if oom_kills is None:
        print(f"  {oom_why}")
    if uptime_days is None:
        print(f"  {uptime_why}")
    if pod_working_set is None:
        print(f"  CANNOT SEE the Pod split — {pods_why}. This reading records "
              "no host/Pod split, so it is not part of any attribution.")
    elif "host_anon_mib" in current:
        print(f"  of which {current['host_anon_mib']:.0f}Mi of anonymous memory is "
              f"outside every Pod cgroup ({current['anon_mib']:.0f}Mi anonymous "
              f"total, {current['pod_count']} Pod(s) using "
              f"{current['pods_working_set_mib']:.0f}Mi).")

    inside = window(rows, current["host"], current["_at"], args.window_hours)
    harm_lines, harmed = judge_harm(current, inside)
    for line in harm_lines:
        print(line)

    lines, actionable, judged = judge(inside, current, args.horizon_days)
    for line in lines:
        print(line)
    for line in attribute_slope(inside, current):
        print(line)

    if harmed or actionable:
        print("This is tools.workload_health's blind spot on purpose: it judges the "
              "level, which is right for a spike and passes every reading of a leak "
              "until the last one.")
        return 2
    if not judged:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
