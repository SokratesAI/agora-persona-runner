"""How close is each container to its own memory limit, before the kernel finds out?

Cycle 981 raised grafana's memory limit after server2's kernel killed it at
11:20 Oslo on 2026-09-05. Nothing in this loop had warned first. The kernel log
line is exact -- `memory: usage 262144kB, limit 262144kB` and `Killed process
445782 (grafana)` holding 190.3 MiB -- and `tools.oom_history` read it out of
that node's `kern.log`, which is a record of a kill that already happened.

Every other memory instrument here answers a different question.
`tools.node_memory` reads how much a *node* has left. `tools.workload_health`
reads what a node has been *promised*. `tools.oom_rank` reads who the kernel
picks first when a node runs out. `tools.memory_headroom` reads this one
container's own cgroup. None of them reads a container against **its own
declared limit**, and that limit is what killed grafana on a node with 4.8 GiB
free at the time. The node was fine. The ceiling was not.

**Read RSS, not working set and not the high-water mark.** Measured 2026-09-05
17:42 Oslo, `container_memory_max_usage_bytes / container_spec_memory_limit_bytes`
is exactly **100.0%** for `agents/agora` and `obsidian/couchdb`, and neither pod
has restarted once. Those counters include reclaimable page cache, so a
container that reads files will walk its usage up to its ceiling and sit there
while the kernel scrapes cache back off it -- the same shape `tools.memory_headroom`
already documents for this pod's own `memory.peak`. A check keyed on either of
them raises on every file-reading container forever, which is a positive result
guaranteed in advance. `container_memory_rss` is anonymous memory: the part the
kernel cannot reclaim, and the part grafana died holding.

**`container_memory_failcnt` cannot be used here and it looks like it can.**
The kernel logged grafana at `failcnt 86` five hours before this file was
written. Measured over the same window,
`max_over_time(container_memory_failcnt{container!=""}[24h]) > 0` returns **no
series at all** -- cAdvisor does not populate it under cgroup v2. A check built
on it would come back clean whatever was happening, which is the guaranteed
*negative* the other way round.

**The window has to be checked against the store, not assumed.** Prometheus's
TSDB was given a disk on 2026-09-05 (platform-config#711) and started empty;
measured here at 17:43 Oslo it held **3.7 hours** of history. A
`max_over_time(...[24h])` over a store that young is a 3.7-hour peak wearing a
24-hour label, and a peak that short reads low for anything with a daily cycle.
So this module asks Prometheus how far back it actually goes
(`prometheus_tsdb_lowest_timestamp_seconds`) and refuses to return a clean
verdict when the store covers less than `MIN_COVERAGE` of the window asked for.
Unreadable is a finding; a green light from a blind instrument is not.

**The threshold is calibrated on the one kill this cluster has produced with a
named victim, and that is a thin base rather than a law.** Grafana was holding
190.3 MiB of a 256 MiB limit -- 74.3% -- in steady state, leaving 66 MiB for
every file it mapped and every query it answered, and that was not enough.
`RAISE_AT = 0.70` sits just under it. It is one data point; if a second kill
lands at a lower ratio, this number moves and the reason belongs beside it.
"""

import argparse
import json
import urllib.parse

from tools.alerts import PROMETHEUS, _get

MIB = 1024.0**2

#: Peak RSS as a fraction of the container's own limit, at or above which this
#: raises. See the docstring: grafana died at 0.743 of its limit.
RAISE_AT = 0.70

#: A container between this and RAISE_AT is printed as worth watching but does
#: not raise -- half the ceiling is the point where a doubling is a kill.
WATCH_AT = 0.50

#: The store has to cover at least this much of the requested window before a
#: peak taken over it means anything.
MIN_COVERAGE = 0.75


def query(expr, base=PROMETHEUS, get=_get):
    """The result of `expr` as a list of Prometheus samples.

    A `scalar` result -- what `time()` answers with -- is normalised into the
    one-sample shape a vector has, so a caller reading a single number does not
    have to know which of the two forms its expression produces.
    """
    path = "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    data = get(base, path)
    kind = data.get("resultType")
    if kind == "scalar":
        return [{"metric": {}, "value": data["result"]}]
    if kind != "vector":
        raise ValueError("expected an instant vector or scalar, got %r" % kind)
    return data.get("result") or []


def _scalar(samples):
    if not samples:
        return None
    return float(samples[0]["value"][1])


def store_coverage_hours(base=PROMETHEUS, get=_get):
    """How many hours of history this Prometheus actually holds, or `None`.

    `None` means the store would not say, which is not the same as zero and is
    not folded into it -- the caller has to be able to tell "empty" from
    "did not answer".
    """
    oldest = _scalar(query("prometheus_tsdb_lowest_timestamp_seconds", base, get))
    now = _scalar(query("time()", base, get))
    if oldest is None or now is None:
        return None
    return max(0.0, (now - oldest) / 3600.0)


def _key(metric):
    return (
        metric.get("namespace") or "?",
        metric.get("pod") or "?",
        metric.get("container") or "?",
        metric.get("node") or "?",
    )


def read_containers(window_hours, base=PROMETHEUS, get=_get):
    """`[(namespace, pod, container, node, peak_rss, limit)]` for every limited container.

    A container with no memory limit is left out entirely rather than given an
    infinite ceiling: "how close is it to its limit" has no answer when there is
    no limit, and `tools.workload_health` owns whether one should exist.
    """
    window = "%dh" % int(window_hours) if float(window_hours).is_integer() else "%gh" % window_hours
    limits = {
        _key(s["metric"]): float(s["value"][1])
        for s in query('container_spec_memory_limit_bytes{container!=""} > 0', base, get)
    }
    peaks = {
        _key(s["metric"]): float(s["value"][1])
        for s in query(
            'max_over_time(container_memory_rss{container!=""}[%s])' % window, base, get
        )
    }
    rows = []
    for key, limit in limits.items():
        peak = peaks.get(key)
        if peak is None:
            continue
        rows.append(key + (peak, limit))
    rows.sort(key=lambda row: row[4] / row[5], reverse=True)
    return rows


def verdict(peak, limit):
    """`"raise"`, `"watch"` or `"ok"` for one container's peak against its limit."""
    if limit <= 0:
        return None
    ratio = peak / limit
    if ratio >= RAISE_AT:
        return "raise"
    if ratio >= WATCH_AT:
        return "watch"
    return "ok"


def _line(row):
    namespace, pod, container, node, peak, limit = row
    return "%5.1f%%  %s/%s (%s) on %s — peak %dMi of a %dMi limit" % (
        100.0 * peak / limit,
        namespace,
        container,
        pod,
        node,
        int(peak / MIB),
        int(limit / MIB),
    )


def report(window_hours, base=PROMETHEUS, get=_get, out=print):
    """Print the sweep. Returns the exit code."""
    try:
        covered = store_coverage_hours(base, get)
    except (OSError, ValueError) as exc:
        out("CANNOT READ how far back Prometheus goes: %s" % exc)
        out("Nothing was judged, so this is not a clean result.")
        return 1

    try:
        rows = read_containers(window_hours, base, get)
    except (OSError, ValueError) as exc:
        out("CANNOT READ the container memory series: %s" % exc)
        out("Nothing was judged, so this is not a clean result.")
        return 1

    if not rows:
        out("CANNOT READ — no container carries both a memory limit and an RSS "
            "series, so there was nothing to judge.")
        return 1

    raising = [row for row in rows if verdict(row[4], row[5]) == "raise"]
    watching = [row for row in rows if verdict(row[4], row[5]) == "watch"]

    for row in raising:
        out("  NEAR LIMIT  " + _line(row))
    for row in watching:
        out("  watch       " + _line(row))
    if rows and not raising and not watching:
        out("  ok          highest is " + _line(rows[0]))

    out("Judged %d container(s) that declare a memory limit, on peak "
        "container_memory_rss over the last %gh. RSS is anonymous memory: the "
        "part the kernel cannot reclaim, and the part grafana died holding. "
        "Working set and max-usage both count page cache and read at or near "
        "100%% of the limit for containers that are perfectly healthy."
        % (len(rows), float(window_hours)))
    out("NOT JUDGED  containers with no memory limit at all — there is no "
        "ceiling to be close to, and whether one should exist is "
        "tools.workload_health's question.")

    short = covered is None or covered < MIN_COVERAGE * float(window_hours)
    if covered is None:
        out("CANNOT READ how much history the store holds, so the peak above "
            "may be over any window at all.")
    elif short:
        out("STORE TOO YOUNG — Prometheus holds %.1fh of history against the "
            "%gh window asked for, so every peak above is really a %.1fh peak."
            % (covered, float(window_hours), covered))

    # A window shorter than the one asked for can only make a peak read *lower*,
    # never higher, so a container found over the line in it is over the line
    # for real and the finding stands. It is the clean verdict that a young
    # store cannot support, which is why the order here is raise first.
    if raising:
        out("NEAR ITS LIMIT — %d container(s) peaked at %d%% or more of their "
            "own memory limit. That is where grafana was (74.3%%) when server2 "
            "killed it on 2026-09-05." % (len(raising), int(RAISE_AT * 100)))
        return 2
    if short:
        out("So this sweep is unreadable rather than clean: a peak taken over "
            "too little history reads low, and nothing here can tell a quiet "
            "container from an unobserved one.")
        return 1
    return 0


def main(argv=None, out=print):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=PROMETHEUS, help="Prometheus base URL")
    parser.add_argument(
        "--window-hours",
        type=float,
        default=24.0,
        help="how far back to take each container's peak RSS (default 24)",
    )
    args = parser.parse_args(argv)
    return report(args.window_hours, base=args.url, out=out)


if __name__ == "__main__":
    raise SystemExit(main())
