"""Whether one workload would fit on another node — asked before the move, not after.

Issue #131 has been open on the same shape of work for a week: move something
off server1. Five volumes have moved so far and every one of them was judged by
reading two separate budget lines and forming an opinion. Nothing here answers
the question the move actually asks, which is not *"how is server2 doing"* but
*"how would server2 be doing with this pod on it"*.

That gap is not academic. Measured 2026-09-05 18:44 Oslo, `tools.node_memory`
says server1 has 4277Mi available and server2 has 4850Mi, so server2 reads as
the roomier box and the move reads as obviously safe. `tools.workload_health`
says server1's declared limits sum to 75% of the node and server2's to 91%, so
server2 is the *tighter* box and the same move reads as obviously unsafe. Both
lines are correct and neither is about the move.

So this composes the two readers rather than adding a third. `tools.node_memory`
owns what a node has left, `tools.workload_health` owns what a node has been
promised, and Cycle 952 already built the first of those a second time in the
second file and threw it away unmerged (runner#748). Every number below comes
out of one of those two modules; this file contributes the arithmetic of adding
one pod to one node and nothing else.

**Three answers, kept apart, because they fail for different reasons.**

`PLACEMENT` is the only one the cluster enforces at move time. The scheduler
compares memory *requests* against the target's allocatable memory and never
looks at a limit, so a target whose requests are already full leaves the moved
Pod Pending — a visible, immediate failure. This one raises.

`LIVE` is what the pod is using right now against what the target has free. A
pod whose working set is larger than the target's available memory is an OOM
kill on arrival rather than a scheduling refusal, and on a node with no swap it
is a kill rather than a slowdown. This one raises too.

`COMMITMENT` is the declared-limit sum after the move. It deliberately does
**not** raise. Overcommit is normal here — server2 was at 91% before anything
moved and is running fine — and a threshold on it would be a number I picked
rather than one I measured. It is printed because it is the figure that says how
bad the *worst* case is, and the worst case is what a limit exists to bound.

**What this does not judge.** Whether the pod grows after the move: the live
figure is one instant, and a workload whose peak is three times its resting set
passes here and dies later — `tools.limit_headroom` owns peaks. Whether the
volume can follow: every volume in this cluster is local-path and bolted to a
node's disk, so a Pod that fits on memory may still be unmovable, and that is
`tools.disk_health`'s half. And CPU: this is a memory answer only, said in the
name of every line it prints.
"""

import argparse
import subprocess

from tools import node_memory, workload_health

MIB = 1024.0**2


def read_node_allocatable(runner=subprocess.run):
    """{node name: allocatable memory in MiB}, or (None, why).

    Deliberately allocatable rather than the capacity `workload_health` reads.
    Capacity is the whole machine; allocatable is capacity minus what the
    kubelet reserves for itself and the system, and it is the only one of the
    two the scheduler compares a request against. Reading capacity here would
    hand back headroom that no Pod can ever be placed into.
    """
    body, why = workload_health._run(runner, ["kubectl", "get", "nodes", "-o", "json"])
    if why:
        return None, why
    nodes = {}
    for item in body.get("items") or []:
        name = (item.get("metadata") or {}).get("name") or "?"
        mib = workload_health._mib(
            ((item.get("status") or {}).get("allocatable") or {}).get("memory"))
        if mib is not None:
            nodes[name] = mib
    return nodes, None


def find_pod(pods, wanted):
    """The one Pod matching `namespace/name-prefix`, or `(None, why)`.

    A Deployment's Pod carries a generated suffix that changes on every roll,
    so naming it exactly means reading it off the cluster first and the answer
    going stale between the read and the call. A prefix match is what a person
    actually has. Matching more than one Pod is refused rather than resolved:
    picking the first would silently answer about a different workload.
    """
    if "/" not in wanted:
        return None, "expected <namespace>/<name>, got %r" % wanted
    namespace, prefix = wanted.split("/", 1)
    hits = [p for p in pods
            if p["namespace"] == namespace and p["name"].startswith(prefix)]
    if not hits:
        return None, "no Pod in %s whose name starts with %r" % (namespace, prefix)
    if len(hits) > 1:
        return None, "%d Pods match %r: %s" % (
            len(hits), wanted, ", ".join(sorted(p["name"] for p in hits)))
    return hits[0], None


def sum_requests(pods):
    """Total declared memory requests over these Pods, in MiB.

    Running and Pending only, matching `workload_health.declared_ceiling`: a
    Succeeded Job Pod holds no memory and the scheduler has already released
    its reservation.
    """
    total = 0.0
    for pod in pods:
        if pod.get("phase") not in ("Running", "Pending"):
            continue
        for quantity in (pod.get("requests") or {}).values():
            mib = workload_health._mib(quantity)
            if mib is not None:
                total += mib
    return total


def pod_requests(pod):
    """This Pod's own declared memory requests, in MiB."""
    return sum_requests([dict(pod, phase="Running")])


def pod_limits(pod):
    """This Pod's own declared memory limits in MiB, and how many containers set none."""
    total = 0.0
    for quantity in (pod.get("limits") or {}).values():
        mib = workload_health._mib(quantity)
        if mib is not None:
            total += mib
    declared = pod.get("container_count") or 0
    return total, max(0, declared - len(pod.get("limits") or {}))


def pod_working_set(summary, namespace, name):
    """This Pod's working set in MiB off its node's kubelet, or `None`.

    `None` is a real answer and not a zero: a Pod the kubelet has no stats for
    has not been measured, and treating that as nought would turn the live
    check into a pass that was guaranteed in advance.
    """
    for pod in summary.get("pods") or []:
        ref = pod.get("podRef") or {}
        if ref.get("namespace") == namespace and ref.get("name") == name:
            working_set = (pod.get("memory") or {}).get("workingSetBytes")
            if working_set is None:
                return None
            return working_set / MIB
    return None


def _mi(value):
    return "%dMi" % int(value)


def report(pod, target, pods, allocatable, target_summary, source_working_set,
           out=print):
    """The three lines, most enforceable first. Returns the count of findings."""
    source = pod.get("node") or ""
    where = "%s/%s" % (pod["namespace"], pod["name"])
    findings = 0

    if source == target:
        out("  ALREADY THERE  %s is on %s — nothing to judge." % (where, target))
        return 0

    others = [p for p in workload_health.pods_on(pods, target)
              if p["name"] != pod["name"] or p["namespace"] != pod["namespace"]]

    # PLACEMENT — the only one the scheduler enforces.
    asked = pod_requests(pod)
    committed = sum_requests(others)
    room = allocatable - committed
    if asked > room:
        findings += 1
        out("  WOULD NOT PLACE  %s requests %s and %s has %s of its %s allocatable "
            "left once the %d Pod(s) already on it are counted — the scheduler "
            "would leave the moved Pod Pending."
            % (where, _mi(asked), target, _mi(room), _mi(allocatable),
               len(others), ))
    else:
        out("  ok  PLACEMENT   %s requests %s and %s has %s of its %s allocatable "
            "free — the scheduler places on requests and never reads a limit."
            % (where, _mi(asked), target, _mi(room), _mi(allocatable)))

    # LIVE — what it is using now against what the target has free.
    memory = node_memory.node_memory(target_summary)
    available = None if memory is None else memory[0] / MIB
    swap = node_memory.node_swap(target_summary)
    cushion = ("no swap on %s, so an overshoot there is a kill rather than a "
               "slowdown" % target) if (swap is None or swap[0] == 0) else (
        "%s of swap on %s to absorb an overshoot" % (_mi(swap[0] / MIB), target))
    if source_working_set is None:
        out("  CANNOT JUDGE LIVE  %s's kubelet reports no memory reading for %s, "
            "so what it is using was not measured — this is not a pass." % (source, where))
        findings += 1
    elif available is None:
        out("  CANNOT JUDGE LIVE  %s's kubelet reports no node memory block, so "
            "what it has free was not measured — this is not a pass." % target)
        findings += 1
    elif source_working_set > available:
        findings += 1
        out("  WOULD NOT FIT    %s is using %s and %s has %s available — %s."
            % (where, _mi(source_working_set), target, _mi(available), cushion))
    else:
        out("  ok  LIVE        %s is using %s and %s has %s available — %s."
            % (where, _mi(source_working_set), target, _mi(available), cushion))

    # COMMITMENT — reported, never raised.
    before, _, unlimited_before = workload_health.declared_ceiling(others)
    mine, mine_unlimited = pod_limits(pod)
    after = before + mine
    clause = ""
    if mine_unlimited:
        clause = (" %d of its container(s) declare no limit at all, so that sum "
                  "is the least it can be asked for." % mine_unlimited)
    out("  COMMITMENT      %s's declared limits go from %s (%d%%) to %s (%d%%) of "
        "its %s allocatable once %s's %s is added.%s This is reported and never "
        "raised: overcommit is normal here and a threshold on it would be a "
        "number I picked."
        % (target, _mi(before), round(100 * before / allocatable), _mi(after),
           round(100 * after / allocatable), _mi(allocatable), where, _mi(mine),
           clause))
    return findings


def main(argv=None, runner=subprocess.run, out=print):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pod", required=True,
                        help="<namespace>/<name prefix> of the Pod to move")
    parser.add_argument("--to", required=True, dest="target", help="the node to move it to")
    args = parser.parse_args(argv)

    pods, why = workload_health.read_pods(runner=runner)
    if why:
        out("CANNOT READ the Pod list: %s" % why)
        out("Nothing was judged, so this is not a clean result.")
        return 1
    allocatable, why = read_node_allocatable(runner=runner)
    if why:
        out("CANNOT READ the node list: %s" % why)
        out("Nothing was judged, so this is not a clean result.")
        return 1
    if args.target not in allocatable:
        out("CANNOT READ %s — the API server lists no such node. Known: %s"
            % (args.target, ", ".join(sorted(allocatable))))
        return 1

    pod, why = find_pod(pods, args.pod)
    if why:
        out("CANNOT READ the Pod: %s" % why)
        return 1

    try:
        target_summary = node_memory.read_summary(args.target, runner=runner)
    except (OSError, ValueError) as exc:
        out("CANNOT READ %s's kubelet — %s" % (args.target, exc))
        return 1

    working_set = None
    source = pod.get("node") or ""
    if source:
        try:
            working_set = pod_working_set(
                node_memory.read_summary(source, runner=runner),
                pod["namespace"], pod["name"])
        except (OSError, ValueError) as exc:
            out("CANNOT READ %s's kubelet — %s" % (source, exc))
            return 1

    out("Would %s/%s fit on %s? Memory only — CPU, disk and the volume it is "
        "bolted to are not judged here."
        % (pod["namespace"], pod["name"], args.target))
    findings = report(pod, args.target, pods, allocatable[args.target],
                      target_summary, working_set, out=out)
    out("NOT JUDGED  whether the workload grows after the move. LIVE above is one "
        "instant; tools.limit_headroom reads peaks.")
    out("NOT JUDGED  whether its volume can follow. Every volume here is "
        "local-path and bolted to one node's disk — tools.disk_health owns that half.")
    if findings:
        out("WOULD NOT MOVE — %d of the enforceable check(s) above say no." % findings)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
