"""How much of my own memory limit do I actually need?

Cycle 711, on the owner's issue #41 — *"You seem to need more and more
memory. That is fine, until a point. What do you want to do, give
yourself more memory or should we also buy a new node?"* My answer on
2026-08-09 said I would come back with a distribution rather than an
anecdote, and claimed *"I now measure my own peak at the end of each
cycle."* Measured this cycle: nothing in this repo or in
`agora-claude-bridge` reads a peak, a high-water mark or an OOM counter.
`grep -rn 'maxrss\\|peak_rss\\|memory.peak\\|VmHWM' --include=*.py` over
both checkouts returns nothing. That sentence has been on his board for
three weeks describing an instrument that does not exist, while the same
board carries issue #131 (server1 out of memory) and idea #179 (buy a
Hetzner node) waiting on the number it promised.

`tools.host_memory_trend` and `tools.workload_health` both watch the
*host*. Neither reads this container's own limit, and the limit is the
thing that kills a cycle: Cycle 52 was OOM-killed at 1Gi with the box
itself half empty.

**The instrument is `memory.events`, not `memory.peak`, and getting that
backwards is why this is a file rather than one line in an existing
check.** Read from the bridge pod this cycle:

    memory.max     2147483648   (2048.0 MiB)
    memory.peak    2139783168   (2040.7 MiB — 99.7% of the limit)
    memory.current 1225273344   (1168.5 MiB)

A high-water mark 7 MiB below a hard kill boundary reads as an emergency
and is not one. `memory.current` counts the page cache, the kernel
reclaims cache before it kills anything, and this container reads a lot
of files — `memory.stat` says `anon 266137600` (253.8 MiB) against `file
921509888` (878.8 MiB), of which `inactive_file 727896064` is cold and
first to go. So the peak was going to arrive at the limit sooner or later
whatever the real demand was, on any container that reads files at all.
`prompt.md` names this exact shape: *a positive result that was
guaranteed in advance*, which feels like evidence in a way a guaranteed
negative never does.

The kernel already keeps the honest counter. `memory.events` records
`max` — how many times allocation hit the limit and forced reclaim — and
`oom_kill`. Both read **0** on the bridge pod, so this cgroup has never
once been squeezed at its limit, and the 99.7% is exactly what it looks
like when nothing is wrong. That is also why nothing here invents a
percentage threshold: `max` counts reclaim-at-the-limit, which happens
many times before the kernel gives up on a cgroup, so the counter *is*
the early warning and it is the kernel's, not a number I picked.
`personality.md`: a limit needs a danger, and I have to have measured it.

Scope, said plainly rather than left to be assumed: this reads the cgroup
of **the pod it runs in**, because that is the only cgroup either of this
loop's shells can see — `/proc/self/cgroup` is `0::/` in both, so the
namespace root is the container's own and the host tree is not reachable
from here. Run it in `Bash` for the bridge pod and in `terminal_exec` for
the runner pod; it names which one it read and says the other was not
judged. Measured on the runner pod the same cycle: limit 256 MiB, peak
48.5 MiB, `max 0` — the peak sits at 19% there rather than 99.7%, on the
same healthy verdict, which is the clearest demonstration that the peak
carries no signal about pressure.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Where cgroup v2 mounts a container's own cgroup. Namespaced, so this is
#: this container's, not the node's.
DEFAULT_CGROUP_ROOT = os.environ.get("NOVA_CGROUP_ROOT", "/sys/fs/cgroup")

#: The `memory.events` counters that mean harm, and what each one says. Both
#: are cumulative since the container started, so neither needs a ledger.
HARM_EVENTS = (
    ("oom_kill", "task(s) in this cgroup have been OOM-killed"),
    ("max", "time(s) allocation reached the limit and forced reclaim"),
)

MIB = 1024 * 1024


def _read(root, name):
    try:
        return Path(root, name).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None


def read_keyed(root, name):
    """`{key: int}` from a cgroup file of `key value` lines, or `None`."""
    raw = _read(root, name)
    if raw is None:
        return None
    out = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return out


def read_cgroup(root=DEFAULT_CGROUP_ROOT):
    """`(reading, None)` for this container's own cgroup, or `(None, why)`.

    A missing file is reported rather than defaulted. A cgroup v1 host, or a
    runtime that does not expose the controller, has no `memory.events`, and
    that is a missing instrument, not a healthy container.
    """
    events = read_keyed(root, "memory.events")
    stat = read_keyed(root, "memory.stat")
    if events is None or stat is None:
        return None, (
            f"{root}/memory.events or memory.stat could not be read, so nothing "
            "here knows whether this container has ever been squeezed at its "
            "limit. That is cgroup v2 with the memory controller on; a v1 host "
            "or a runtime that hides the controller has neither file."
        )
    missing = [key for key, _ in HARM_EVENTS if key not in events]
    if missing:
        return None, (
            f"{root}/memory.events carries no "
            + ", ".join(sorted(missing))
            + " counter, so the harm it records cannot be read."
        )

    raw_max = _read(root, "memory.max")
    limit = None
    if raw_max is not None and raw_max != "max":
        try:
            limit = int(raw_max)
        except ValueError:
            limit = None

    def number(name):
        raw = _read(root, name)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    return {
        "pod": os.environ.get("HOSTNAME", "unknown"),
        "limit": limit,
        "current": number("memory.current"),
        "peak": number("memory.peak"),
        "events": events,
        "anon": stat.get("anon"),
        "kernel": stat.get("kernel"),
        "slab": stat.get("slab"),
        "inactive_file": stat.get("inactive_file"),
        "file": stat.get("file"),
    }, None


def mib(value):
    return None if value is None else value / MIB


def percent_of_limit(value, limit):
    if value is None or not limit:
        return None
    return 100.0 * value / limit


def working_set(reading):
    """`memory.current` minus the cold page cache — what `kubectl top` shows."""
    current = reading.get("current")
    inactive = reading.get("inactive_file")
    if current is None or inactive is None:
        return None
    return current - inactive


def unreclaimable(reading):
    """Anonymous plus kernel memory: what the limit actually has to hold.

    The page cache is not in here on purpose. The kernel reclaims cache at
    the limit rather than killing, so cache counted against the limit turns
    every file-reading container into a false finding — which is the whole
    mistake this module's docstring is about.
    """
    anon = reading.get("anon")
    kernel = reading.get("kernel")
    if anon is None:
        return None
    return anon + (kernel or 0)


def judge(reading):
    """`(lines, exit_code)` — 2 if the kernel has recorded harm, else 0."""
    lines = []
    events = reading["events"]
    harmed = [(key, words, events[key]) for key, words in HARM_EVENTS if events[key] > 0]

    limit = reading["limit"]
    limit_words = f"{mib(limit):.1f} MiB" if limit else "no limit set"
    for key, words, count in harmed:
        lines.append(
            f"SQUEEZED AT THE LIMIT — memory.events.{key} is {count}: {count} "
            f"{words}, against a limit of {limit_words}. This is the kernel's "
            "own count since the container started, not an inference."
        )

    ws = working_set(reading)
    live = unreclaimable(reading)
    parts = []
    if live is not None:
        pct = percent_of_limit(live, limit)
        tail = f" ({pct:.1f}% of the limit)" if pct is not None else ""
        parts.append(f"anonymous+kernel {mib(live):.1f} MiB{tail}")
    if ws is not None:
        parts.append(f"working set {mib(ws):.1f} MiB")
    if limit:
        parts.append(f"limit {mib(limit):.1f} MiB")
    if parts:
        lines.append("  " + ", ".join(parts) + f" — pod {reading['pod']}.")

    peak = reading["peak"]
    if peak is not None:
        pct = percent_of_limit(peak, limit)
        tail = f", {pct:.1f}% of the limit" if pct is not None else ""
        lines.append(
            f"  NOT JUDGED  memory.peak is {mib(peak):.1f} MiB{tail} — and it carries "
            "no signal about pressure. It is a high-water mark of memory.current, "
            "which counts the page cache the kernel reclaims before it kills "
            "anything, so on a container that reads files it arrives at the "
            "limit whatever the real demand is. memory.events above is the "
            "instrument."
        )
    lines.append(
        "  CANNOT JUDGE  any cgroup but this pod's own. /proc/self/cgroup is "
        "0::/ in both of this loop's shells, so the namespace root is the "
        "container's and the node's tree is not reachable from here — run this "
        "in the other shell to judge the other pod."
    )
    return lines, (2 if harmed else 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cgroup-root", default=DEFAULT_CGROUP_ROOT,
                        help="where this container's cgroup v2 files are")
    args = parser.parse_args(argv)

    print("MEMORY HEADROOM")
    reading, why = read_cgroup(args.cgroup_root)
    if reading is None:
        print(f"COULD NOT READ: {why}")
        return 1
    lines, code = judge(reading)
    for line in lines:
        print(line)
    limit = reading["limit"]
    limit_words = f"a {mib(limit):.1f} MiB limit" if limit else "no configured limit"
    reached = sum(1 for key, _ in HARM_EVENTS if reading["events"][key] > 0)
    print(f"Judged 1 cgroup — this pod's own — against {limit_words}, on the "
          f"{len(HARM_EVENTS)} counter(s) the kernel keeps since the container "
          f"started; {reached} of them is non-zero.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
