"""How much of each container's CPU time is the kernel taking away at its own limit?

Agora sat at 499m of a 500m CPU limit with 78.7% of its scheduling periods
throttled for about a week, `GET /conversations` went from 440 ms to 10-13 s,
and the thing that eventually reported it was the Sunday architecture-critique
run reading Prometheus by hand on 2026-09-06. Nothing in the hourly sweep
watched it. Every CPU number this loop prints is a *usage* number --
`kubectl top` says 499m and 499m is under the limit, so it reads as healthy
right up to the point where requests are queueing behind the throttle.

Throttling is the honest counter and the kernel keeps it. `container_cpu_cfs_periods_total`
counts every 100 ms scheduling period in which the cgroup had something to run;
`container_cpu_cfs_throttled_periods_total` counts the ones where it hit its
quota and was stopped early. The ratio is the fraction of the container's own
runnable time that it spent waiting rather than working, and no usage figure
can express it: a container pinned at exactly its limit and a container using
half of it both look like a number below a ceiling.

**This is the CPU half of `tools.limit_headroom`, deliberately shaped like it.**
That check reads each container against its own *memory* limit and says so in
its first paragraph; it has no CPU judgement at all, and neither does
`tools.workload_health` (which sums declared limits per node), `tools.node_memory`
or `tools.oom_rank`. Grepping the other preflight tools for the noun before
adding a judgement is the guard Cycle 952 wrote down after duplicating one, and
the answer here was that nothing owns this.

**Cumulative ratios are useless here and they are what a first attempt reaches
for.** These counters run from container start. Measured 2026-09-06 14:12 Oslo,
agora's lifetime ratio is 83566/167779 = **49.8%** -- almost all of it earned
during the week it was broken -- while its rate over the two minutes either side
of that reading is 294/1247 = **23.6%**, and the endpoint that used to take 13 s
answered in 0.20 s. A check keyed on the lifetime figure would have gone on
raising for as long as the container lived, which is a positive result
guaranteed in advance. So this scrapes twice and judges the delta.

**The floor is not tidiness, it is the same trap in miniature.** A cgroup only
accumulates periods while it has a runnable task, so an idle container gathers a
handful in a window and a ratio over a handful is noise. Measured cluster-wide
over 20 s on 2026-09-06, crossplane's `function-patch-and-transform` accumulated
**3** periods and one of them was throttled, which is 33.3% and means nothing at
all. `MIN_PERIODS` drops a container that was barely scheduled rather than
reporting a percentage of three.

**Where the raising line came from.** In the same sweep the whole cluster read:
couchdb 31.5%, agora 24.3%, otel-collector 2.7%, redis 1.5%, and 0.0% for the
other sixteen containers that declare a CPU limit. The incident was 78.7%. A
line at 25% would raise on a healthy agora answering in 200 ms, and a line at
80% would have let the incident run. **50%** is where a container spends more
of its runnable time stopped than running, it is above everything in the cluster
today so the check is not guaranteed to fire, and it is well under the 78.7%
that actually hurt. Every container over the floor is printed with its ratio
whatever the verdict, so couchdb at 31.5% is visible without being an alarm --
the interface carries the data, the threshold only carries the alarm.

Scope, said plainly: this judges containers that declare a CPU limit, because
a container with no limit cannot be throttled and its counters read zero
forever. It says how many it skipped for that reason.
"""

import argparse
import json
import re
import subprocess
import time

#: 100 ms each, so 100 of them is ten seconds of the cgroup actually having
#: work to schedule. Below this a ratio is noise -- see the module docstring.
MIN_PERIODS = 100

#: More of its runnable time stopped than running. See the module docstring for
#: the cluster-wide distribution this was picked against.
RAISE_PCT = 50.0

#: Long enough that a container doing real work clears MIN_PERIODS, short enough
#: to sit inside a preflight sweep.
DEFAULT_WINDOW = 20

#: One window is a spot reading. See `combine` for why more than one is a
#: different measurement rather than a longer one.
DEFAULT_SAMPLES = 1

_METRIC = re.compile(
    r'^container_cpu_cfs_(throttled_)?periods_total\{([^}]*)\}\s+([0-9.eE+-]+)'
)
_LABEL = re.compile(r'(\w+)="([^"]*)"')


def parse_cadvisor(text):
    """cAdvisor exposition text -> {(namespace, pod, container): {periods, throttled}}.

    Rows with an empty `container` label are the pod-level cgroup, which
    aggregates the containers underneath it; counting both would double every
    pod (`cadvisor instruments parent and children`), so they are dropped.
    """
    out = {}
    for line in text.splitlines():
        m = _METRIC.match(line)
        if not m:
            continue
        labels = dict(_LABEL.findall(m.group(2)))
        container = labels.get("container", "")
        if not container:
            continue
        key = (labels.get("namespace", ""), labels.get("pod", ""), container)
        field = "throttled" if m.group(1) else "periods"
        out.setdefault(key, {})[field] = float(m.group(3))
    return out


def deltas(before, after):
    """Per-container (periods, throttled) growth between two scrapes.

    A container present in only one scrape started or died inside the window
    and has no meaningful delta, so it is left out. A negative delta means the
    container restarted and its counters reset; that is not a rate either.
    """
    rows = {}
    for key, now in after.items():
        was = before.get(key)
        if was is None:
            continue
        dp = now.get("periods", 0.0) - was.get("periods", 0.0)
        dt = now.get("throttled", 0.0) - was.get("throttled", 0.0)
        if dp <= 0 or dt < 0:
            continue
        rows[key] = (dp, dt)
    return rows


def combine(samples):
    """Several `deltas` outputs, oldest first -> the totals and each sample's own ratio.

    One 20-second window is a spot reading, and a container whose throttling
    swings gets a verdict that depends on which twenty seconds the sweep
    happened to land on. Measured on couchdb: 3.3% at 14:41 Oslo, 100.0% at
    14:54, 99.3% at 15:38 -- three readings, two verdicts, one container. The
    aggregate answers "how much of its runnable time did it spend stopped over
    the whole span", which is the number a decision wants; the per-sample list
    beside it answers "was that steady or was it a spike", which no single
    ratio can express.

    Totals are summed over the samples a container actually appeared in, so a
    container that restarted mid-run is rated on the windows it was there for
    rather than dropped. The count of samples it appeared in is returned so the
    caller can say so.
    """
    totals, series = {}, {}
    for sample in samples:
        for key, (dp, dt) in sample.items():
            tp, tt = totals.get(key, (0.0, 0.0))
            totals[key] = (tp + dp, tt + dt)
            series.setdefault(key, []).append((dp, dt))
    return totals, series


def _spread_line(entries, min_periods):
    """The per-sample ratios under a judged container, or None if there is one sample.

    A sample in which the container was barely scheduled is counted, not rated:
    the same reason MIN_PERIODS exists, applied one window down. Rating it would
    put a percentage of three periods into a line whose whole job is to say
    whether the number above it is steady.
    """
    if len(entries) < 2:
        return None
    rated = [dt / dp * 100.0 for dp, dt in entries if dp >= min_periods]
    quiet = len(entries) - len(rated)
    if not rated:
        return (f"      over {len(entries)} sample(s): none of them accumulated "
                f"{min_periods} period(s), so the total above is rated and the "
                "spread is not")
    shown = " ".join(f"{pct:.1f}%" for pct in rated)
    line = f"      over {len(entries)} sample(s): {shown}"
    if quiet:
        line += f" ({quiet} too quiet to rate)"
    if len(rated) > 1:
        line += f" -- {min(rated):.1f}% to {max(rated):.1f}%"
    return line


def judge(rows, window_s, min_periods=MIN_PERIODS, raise_pct=RAISE_PCT,
          series=None):
    """Lines to print and the exit code, from `deltas` output."""
    judged, skipped = [], []
    for key, (dp, dt) in rows.items():
        if dp < min_periods:
            skipped.append((dp, key))
        else:
            judged.append((dt / dp * 100.0, dp, key))
    judged.sort(reverse=True)
    skipped.sort(reverse=True)

    lines, harmed = [], []
    for pct, dp, key in judged:
        ns, pod, container = key
        mark = "THROTTLED" if pct > raise_pct else "  ok     "
        lines.append(f"  {mark}  {pct:5.1f}%  {int(dp)} period(s)  {ns}/{pod} [{container}]")
        spread = _spread_line((series or {}).get(key, []), min_periods)
        if spread:
            lines.append(spread)
        if pct > raise_pct:
            harmed.append(key)
    if skipped:
        lines.append(
            f"  NOT JUDGED  {len(skipped)} container(s) accumulated fewer than "
            f"{min_periods} scheduling period(s) in {window_s:.0f}s, so they were "
            "barely runnable and a ratio over that few periods is noise, not a "
            "signal: " + ", ".join(f"{ns}/{pod} [{c}] at {int(dp)}"
                                   for dp, (ns, pod, c) in skipped)
        )
    if not judged:
        lines.append(
            "  COULD NOT JUDGE  no container accumulated enough scheduling "
            "periods to rate. That is not a clean bill of health; it means the "
            "window was too short or nothing with a CPU limit was running."
        )
        return lines, 1
    return lines, (2 if harmed else 0)


def _kubectl(args):
    return subprocess.run(["kubectl"] + args, capture_output=True, text=True)


def node_names():
    r = _kubectl(["get", "nodes", "-o", "json"])
    if r.returncode != 0:
        return None, r.stderr.strip() or "kubectl get nodes failed"
    return [n["metadata"]["name"] for n in json.loads(r.stdout).get("items", [])], None


def scrape(node):
    r = _kubectl(["get", "--raw", f"/api/v1/nodes/{node}/proxy/metrics/cadvisor"])
    if r.returncode != 0:
        return None, r.stderr.strip() or f"cadvisor scrape failed on {node}"
    return parse_cadvisor(r.stdout), None


def _scrape_all(nodes):
    merged, problems = {}, []
    for node in nodes:
        parsed, why = scrape(node)
        if parsed is None:
            problems.append(f"{node}: {why}")
            continue
        merged.update(parsed)
    return merged, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW,
                        help="seconds between the two cAdvisor scrapes")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES,
                        help="consecutive windows to take; more than one prints "
                             "each window's own ratio beside the total")
    parser.add_argument("--raise-pct", type=float, default=RAISE_PCT,
                        help="raise above this percentage of throttled periods")
    args = parser.parse_args(argv)

    print("CPU THROTTLE")
    nodes, why = node_names()
    if nodes is None:
        print(f"COULD NOT READ: {why}")
        return 1

    before, problems = _scrape_all(nodes)
    if not before:
        print("COULD NOT READ: " + ("; ".join(problems) or "no cAdvisor rows on any node"))
        return 1
    t0 = time.time()
    samples = []
    for _ in range(max(1, args.samples)):
        time.sleep(args.window)
        after, more = _scrape_all(nodes)
        problems += more
        samples.append(deltas(before, after))
        before = after
    window = time.time() - t0

    rows, series = combine(samples)
    lines, code = judge(rows, window, raise_pct=args.raise_pct, series=series)
    for line in lines:
        print(line)
    for problem in problems:
        print(f"  COULD NOT READ  {problem}")
        code = max(code, 1)
    print(
        f"Judged {len(rows)} container(s) with a CPU limit across {len(nodes)} node(s) "
        f"over {window:.0f}s in {len(samples)} sample(s), raising above "
        f"{args.raise_pct:.0f}% of scheduling "
        "periods throttled. A container with no CPU limit cannot be throttled and "
        "keeps no periods at all, so it is absent here rather than passing."
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
