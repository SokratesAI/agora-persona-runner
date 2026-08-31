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

Exit 0 means it judged the trend and nothing is falling toward zero
inside the horizon. Exit 2 means something is. Exit 1 means it could not
judge.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.workload_health import read_meminfo, read_node_capacity  # noqa: E402

#: Two concurrent cycles both running `preflight` read and rewrite this
#: file, so the later writer can drop the earlier one's sample. That is
#: deliberate rather than unhandled: this is a sampled series and one lost
#: sample changes no slope, whereas a lock would make an instrument able to
#: block a cycle.
#: The pod's persistent volume. `$NOVA_WORKSPACE` is deliberately not used:
#: it points at a per-cycle worktree when cycles run concurrently, so a
#: ledger written there would start empty every other cycle.
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


def reading_now(meminfo, nodes, at=None):
    """One ledger row for this instant, or `(None, why)`.

    `why` is always about the instrument, never about the box.
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
    return {
        # `_at` is the parsed instant every comparison here uses; `save`
        # strips underscore keys, so it never reaches the file.
        "_at": when,
        "at": when.isoformat(),
        "host": host,
        "mem_total_mib": round(total_mib, 1),
        "mem_available_mib": round(available / 1024, 1),
        "swap_total_mib": round(meminfo.get("SwapTotal", 0) / 1024, 1),
        "swap_free_mib": round(meminfo.get("SwapFree", 0) / 1024, 1),
    }, None


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
        rate = slope_per_day(rows, field)
        if value is None or rate is None:
            lines.append(
                f"CANNOT TREND {label} — no usable series in the window."
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

    current, why = reading_now(meminfo, nodes)
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

    inside = window(rows, current["host"], current["_at"], args.window_hours)
    lines, actionable, judged = judge(inside, current, args.horizon_days)
    for line in lines:
        print(line)

    if not judged:
        return 1
    if actionable:
        print("This is tools.workload_health's blind spot on purpose: it judges the "
              "level, which is right for a spike and passes every reading of a leak "
              "until the last one.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
