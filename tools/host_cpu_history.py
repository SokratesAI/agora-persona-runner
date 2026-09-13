"""Did a node in this cluster run hot for hours, and which process was it?

Issue #169 is one question: server1 sat near 380% CPU for hours overnight on
2026-09-01/02 and nothing here can say what was burning it. Cycle 1463 built
the durable half -- `tools.host_memory_trend` now appends every sweep's CPU
rows to a JSONL ledger on the pod's volume, because the cluster reaps the
sweep Pods after eight hours and the rows died with them. **Nothing has ever
read that ledger back.** A write-only history answers no question at all, so
this is the reader: it walks the samples, finds a run of consecutive samples
where one node was busy for long enough to be an incident rather than a
spike, and names the processes that were burning it while it lasted.

Two things it is careful about, both measured off the live ledger rather
than reasoned about.

**The same sweep can be in the ledger more than once, and counting it twice
manufactures the exact shape this looks for.** The 18:00 sweep of 2026-09-12
is in there seven times -- same Pod, same stamp, same figure -- and taken at
face value that is a seven-sample plateau, which is precisely what "pegged
for hours" looks like. A sweep Pod is one measurement however many times its
log was read, so samples are keyed on the Pod name and a repeat is dropped.
I did not establish *how* the repeats got in; `host_memory_trend.record_cpu`
keys its append on the same Pod name and cycles overlap, so a race is the
obvious candidate and I have not proved it.

**The ledger has holes, and a hole is not a quiet period.** Today's file
covers 2026-09-12 18:00 to 2026-09-13 01:30 with nothing between 19:30 and
00:30 -- five hours in which the box could have been on fire. A run of hot
samples cannot be assembled across a gap, so a gap longer than the sustain
threshold is a window this cannot see into, and it is printed beside every
verdict including a clean one.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


#: The ledger `tools.host_memory_trend.record_cpu` writes. Same default and
#: same environment override, deliberately -- two spellings of one path is a
#: reader that silently judges a different file from the one being written.
DEFAULT_LEDGER = os.environ.get("NOVA_HOST_CPU_LEDGER",
                                "/data/nova-host-cpu.jsonl")

#: How far back a run has to start to be reported. Long enough that a spike
#: from a few days ago is still findable -- which is the whole complaint on
#: #169, where the incident was noticed after the fact -- and short enough
#: that a check does not keep re-reporting one incident forever.
DEFAULT_WINDOW_HOURS = 72.0

#: Fraction of a node's cores that counts as hot. Derived from this cluster
#: rather than picked: over the 12 distinct samples in the ledger today
#: server1 ranges 0.95 to 2.29 of its 4 cores (24%-57%) and server2 0.42 to
#: 1.19 (11%-30%), while the #169 incident is described at ~3.8 cores (95%).
#: 75% sits above every sample this box has ever recorded here and well below
#: the incident, so it separates them instead of splitting either.
DEFAULT_BUSY_FRACTION = 0.75

#: How long a node has to stay above that before it is a finding. The row
#: says "for hours"; the sweep fires every 30 minutes, so an hour is the
#: shortest run more than one sample can establish. A single hot sample is a
#: one-second spot reading of a whole box -- `cpu_throttle` learned that and
#: grew `--samples` for it -- and is reported as context, never as a verdict.
DEFAULT_MIN_SPAN_HOURS = 1.0

#: Processes named per hot run. The ledger keeps every row the sweep printed
#: (ten per node per sample), and a run of six samples is sixty rows; the top
#: few by mean load are the answer to "what was it", the rest are the tail
#: that is always there.
BURNER_TOP = 5


def read_samples(path):
    """`(samples, dropped, why)` from the ledger. `why` is set when unreadable.

    One sample per node per sweep. A malformed line is counted rather than
    raising, so a single truncated append cannot blind the whole reader, but
    a file where *nothing* parses is a broken instrument and says so.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw = [line for line in handle if line.strip()]
    except OSError as exc:
        return None, 0, (f"{path} could not be read ({exc}), so nothing here "
                         "knows what the nodes have been doing")
    samples, seen, repeats, malformed = [], set(), 0, 0
    for line in raw:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        pod, at = row.get("pod"), _parse_at(row.get("_at"))
        if not pod or at is None or row.get("cpu_busy_percent") is None:
            malformed += 1
            continue
        if pod in seen:
            repeats += 1
            continue
        seen.add(pod)
        samples.append({"at": at, "node": row.get("node") or "an unnamed node",
                        "pod": pod, "busy_percent": row["cpu_busy_percent"],
                        "rows": row.get("rows") or []})
    if not samples:
        return None, malformed, (
            f"{path} holds {len(raw)} line(s) and none of them is a usable "
            "sample, so this is an empty instrument rather than a quiet box")
    samples.sort(key=lambda s: (s["node"], s["at"]))
    return samples, {"repeats": repeats, "malformed": malformed}, None


def _parse_at(value):
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def read_cores(runner=subprocess.run):
    """`({node: cores}, why)` from the live nodes. Cores, not percent.

    The ledger stores percent of ONE core and never clamps it, so 380% is
    meaningless until you know the box has four. Without this the check
    cannot judge anything, which is why an unreadable answer is exit 1 and
    not a default -- a guessed core count turns a busy box into a quiet one.
    """
    try:
        proc = runner(["kubectl", "get", "nodes", "-o", "json"],
                      capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl could not be run ({exc})"
    if proc.returncode != 0:
        return None, (f"kubectl get nodes exited {proc.returncode}: "
                      f"{(proc.stderr or proc.stdout).strip()}")
    try:
        body = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, "kubectl get nodes did not answer JSON"
    cores = {}
    for item in body.get("items") or []:
        name = ((item.get("metadata") or {}).get("name"))
        raw = ((item.get("status") or {}).get("allocatable") or {}).get("cpu")
        try:
            cores[name] = float(str(raw).rstrip("m")) / (
                1000.0 if str(raw).endswith("m") else 1.0)
        except (TypeError, ValueError):
            continue
    if not cores:
        return None, "kubectl get nodes named no node with an allocatable cpu"
    return cores, None


def hot_runs(samples, cores, busy_fraction=DEFAULT_BUSY_FRACTION,
             min_span_hours=DEFAULT_MIN_SPAN_HOURS):
    """Consecutive hot samples per node, long enough to be an incident.

    A run breaks on the first sample under the threshold, **and on a hole**.
    Two hot samples nine hours apart are not a nine-hour run: the samples in
    between were never taken, and this does not get to assume they were hot.
    My first version had only the first half of that rule and reported
    exactly that nine-hour incident from two readings. The stride a run may
    cross is `min_span_hours`, the same number `gaps` prints as BLIND, so
    there is one threshold rather than two -- a hole this check calls a blind
    window is a hole it also refuses to reach over. That makes it
    under-report across a hole, which is the safe direction.
    """
    runs, by_node = [], {}
    for sample in samples:
        by_node.setdefault(sample["node"], []).append(sample)
    for node, series in sorted(by_node.items()):
        limit = cores.get(node)
        if limit is None:
            continue
        current = []
        for sample in series + [None]:
            hot = (sample is not None
                   and sample["busy_percent"] / 100.0 >= limit * busy_fraction)
            if hot and current:
                stride = ((sample["at"] - current[-1]["at"]).total_seconds()
                          / 3600.0)
                if stride > min_span_hours:
                    span = ((current[-1]["at"] - current[0]["at"])
                            .total_seconds() / 3600.0)
                    if span >= min_span_hours:
                        runs.append({"node": node, "cores": limit,
                                     "samples": list(current),
                                     "span_hours": span})
                    current = [sample]
                    continue
            if hot:
                current.append(sample)
                continue
            if current:
                span = ((current[-1]["at"] - current[0]["at"]).total_seconds()
                        / 3600.0)
                if span >= min_span_hours:
                    runs.append({"node": node, "cores": limit,
                                 "samples": list(current), "span_hours": span})
            current = []
    runs.sort(key=lambda run: run["samples"][0]["at"])
    return runs


def burners(run, top=BURNER_TOP):
    """`[(comm, mean_percent, peak_percent, samples)]` over a run's rows.

    Mean over the run rather than the peak sample, because the question is
    what held the box down for an hour and a process that spiked in one
    one-second window did not.

    A name is summed *within* a sample before anything else: the sweep prints
    one row per PID and this box runs three `python3` processes at once, so a
    per-row peak beside a per-sample mean reported a mean larger than its own
    peak and a process present in "15 of 5 sample(s)". The unit here is every
    process of that name, together, which is the unit the question is asked
    in -- "what was burning it" is never one PID.
    """
    per_sample = {}
    for index, sample in enumerate(run["samples"]):
        for row in sample["rows"]:
            now = row.get("cpu_now_percent")
            comm = row.get("comm")
            if now is None or not comm:
                continue
            slot = per_sample.setdefault(comm, {})
            slot[index] = slot.get(index, 0.0) + now
    named = [(comm, sum(by_index.values()) / max(len(run["samples"]), 1),
              max(by_index.values()), len(by_index))
             for comm, by_index in per_sample.items()]
    named.sort(key=lambda item: item[1], reverse=True)
    return named[:top]


def gaps(samples, min_span_hours=DEFAULT_MIN_SPAN_HOURS):
    """Holes per node longer than a run needs to be, newest first.

    A gap this long is a window in which a qualifying incident could have
    happened and left nothing behind, so it is a limit on every verdict this
    prints -- including a clean one.
    """
    found, by_node = [], {}
    for sample in samples:
        by_node.setdefault(sample["node"], []).append(sample)
    for node, series in sorted(by_node.items()):
        for older, newer in zip(series, series[1:]):
            hours = (newer["at"] - older["at"]).total_seconds() / 3600.0
            if hours > min_span_hours:
                found.append({"node": node, "hours": hours,
                              "from": older["at"], "to": newer["at"]})
    found.sort(key=lambda gap: gap["from"], reverse=True)
    return found


#: Every stamp Edvard reads is Oslo time (identity.md rule 7), and the offset
#: is +1 for five months of the year. A hardcoded +2 prints every winter
#: incident an hour off and no test would ever have noticed, because it
#: changes no verdict -- the comparisons all run on the underlying UTC.
OSLO = ZoneInfo("Europe/Oslo")


def _oslo(at):
    return at.astimezone(OSLO).strftime("%Y-%m-%d %H:%M")


def report(ledger=DEFAULT_LEDGER, window_hours=DEFAULT_WINDOW_HOURS,
           busy_fraction=DEFAULT_BUSY_FRACTION,
           min_span_hours=DEFAULT_MIN_SPAN_HOURS, now=None,
           runner=subprocess.run, out=sys.stdout):
    samples, counts, why = read_samples(ledger)
    if samples is None:
        print(f"CANNOT READ  {why}.", file=out)
        return 1
    cores, cores_why = read_cores(runner=runner)
    if cores is None:
        print(f"CANNOT JUDGE  {cores_why}, and the ledger stores percent of "
              "one core -- without the core count 380% and 38% read the "
              "same.", file=out)
        return 1
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    windowed = [s for s in samples if s["at"] >= cutoff]
    if not windowed:
        newest = max(s["at"] for s in samples)
        print(f"CANNOT JUDGE  {len(samples)} sample(s) in {ledger} and the "
              f"newest is {_oslo(newest)} Oslo, older than the "
              f"{window_hours:.0f}h window -- the sweep has stopped writing "
              "or the ledger has stopped being read.", file=out)
        return 1

    # Every node the ledger or the cluster knows about, not only the ones
    # that answered inside the window. A node whose sweep died and whose last
    # reading is older than the window used to vanish from this report
    # entirely -- no line, no caveat, verdict `ok` -- which is the exact
    # failure issue #169 is about: a box going dark while it may be on fire.
    nodes = sorted({s["node"] for s in windowed}
                   | {s["node"] for s in samples} | set(cores))
    span = (max(s["at"] for s in windowed) - min(s["at"] for s in windowed))
    print(f"CPU HISTORY  {len(windowed)} sample(s) across {len(nodes)} "
          f"node(s) over {span.total_seconds() / 3600.0:.1f}h of a "
          f"{window_hours:.0f}h window, from {ledger}", file=out)
    if counts["repeats"]:
        print(f"             {counts['repeats']} repeat(s) of a sweep "
              "already in the file were dropped -- one sweep Pod is one "
              "measurement however often its log was read.", file=out)
    if counts["malformed"]:
        print(f"             {counts['malformed']} line(s) could not be used.",
              file=out)
    silent = []
    for node in nodes:
        series = [s for s in windowed if s["node"] == node]
        limit = cores.get(node)
        if not series:
            older = [s for s in samples if s["node"] == node]
            last = (f"last seen {_oslo(max(s['at'] for s in older))} Oslo"
                    if older else "never in this ledger")
            if limit is None:
                # In neither the window nor the cluster: a node that has left.
                # Nothing raises on that; there is no pull request for it.
                print(f"  {node}  no sample in this window and not in the "
                      f"cluster's node list ({last}) — a node that has left.",
                      file=out)
                continue
            silent.append(node)
            print(f"  {node}  STOPPED REPORTING  in the cluster with "
                  f"{limit:.0f} core(s) and no sample inside the "
                  f"{window_hours:.0f}h window ({last}). Nothing here knows "
                  "what that node has been doing.", file=out)
            continue
        peak = max(series, key=lambda s: s["busy_percent"])
        seen = ("unknown cores" if limit is None
                else f"{limit:.0f} core(s), hot at {limit * busy_fraction:.1f}")
        print(f"  {node}  {len(series)} sample(s), {seen}; busiest "
              f"{peak['busy_percent'] / 100.0:.2f} core(s) at "
              f"{_oslo(peak['at'])} Oslo", file=out)
        if limit is None:
            print(f"    CANNOT SEE  {node} is in the ledger and not in the "
                  "cluster's node list, so its samples were not judged. "
                  "This deliberately does not raise: a node that has left "
                  "the cluster is not something a pull request closes, and "
                  "its old samples would keep this red forever.", file=out)

    holes = gaps(windowed, min_span_hours=min_span_hours)
    for hole in holes:
        print(f"  BLIND  {hole['node']} has no sample between "
              f"{_oslo(hole['from'])} and {_oslo(hole['to'])} Oslo "
              f"({hole['hours']:.1f}h) -- a run of that length could have "
              "happened in there and left nothing behind.", file=out)

    runs = hot_runs(windowed, cores, busy_fraction=busy_fraction,
                    min_span_hours=min_span_hours)
    # A hot run outranks a silent node: one is a finding a cycle can act on,
    # the other is a missing instrument, and the loud one goes first.
    if runs:
        for run in runs:
            first, last = run["samples"][0], run["samples"][-1]
            top = max(s["busy_percent"] for s in run["samples"])
            print(f"RAN HOT  {run['node']} held at least "
                  f"{run['cores'] * busy_fraction:.1f} of its "
                  f"{run['cores']:.0f} core(s) from {_oslo(first['at'])} to "
                  f"{_oslo(last['at'])} Oslo ({run['span_hours']:.1f}h, "
                  f"{len(run['samples'])} sample(s), peak "
                  f"{top / 100.0:.2f} core(s))", file=out)
            for comm, mean, peak_pct, count in burners(run):
                print(f"    {comm}  mean {mean / 100.0:.2f} core(s), peak "
                      f"{peak_pct / 100.0:.2f}, in {count} of "
                      f"{len(run['samples'])} sample(s)", file=out)
        return 2
    if silent:
        print(f"CANNOT JUDGE  {len(silent)} node(s) in the cluster wrote no "
              f"sample inside the window: {', '.join(silent)}. A node that "
              "has stopped reporting is an unread meter, never a quiet box.",
              file=out)
        return 1
    print(f"ok  no node held {busy_fraction * 100:.0f}% of its cores for "
          f"{min_span_hours:.1f}h or more in this window.", file=out)
    print("    NOT JUDGED  a spike shorter than that, and anything inside a "
          "BLIND window above. This reads the ledger only; it does not sweep "
          "the nodes itself.", file=out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER,
                    help="the CPU ledger host_memory_trend writes")
    ap.add_argument("--window-hours", type=float,
                    default=DEFAULT_WINDOW_HOURS,
                    help="how far back to look")
    ap.add_argument("--busy-fraction", type=float,
                    default=DEFAULT_BUSY_FRACTION,
                    help="fraction of a node's cores that counts as hot")
    ap.add_argument("--min-span-hours", type=float,
                    default=DEFAULT_MIN_SPAN_HOURS,
                    help="how long it has to stay there to be a finding")
    args = ap.parse_args(argv)
    return report(ledger=args.ledger, window_hours=args.window_hours,
                  busy_fraction=args.busy_fraction,
                  min_span_hours=args.min_span_hours)


if __name__ == "__main__":
    sys.exit(main())
