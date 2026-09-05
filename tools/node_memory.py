"""Every node's own memory headroom, read off its kubelet rather than off /proc.

Issue #131 was rated 🔴 Immediately on one number: *"server1 has 487Mi of
7746Mi memory left and its 2GB of swap is completely full"*. Nothing in this
loop ever re-read that number. `tools.host_memory_trend` and
`tools.memory_headroom` both answer for whichever node the bridge pod happens
to stand on, and `tools.preflight` says so in a comment beside their labels:
*"these two need a different mechanism than a node argument"*. `tools.oom_rank`
reads the kill order and `tools.oom_history` reads kills that already happened;
neither says how much room is left before the next one. So the row's own
headline number went stale for days and the instrument that would have noticed
did not exist.

This is that mechanism. The kubelet's `/stats/summary` carries
`node.memory.availableBytes` and `node.swap` for **every** node over
`nodes/proxy`, which is the one source here that answers for a node this pod is
not standing on.

**The threshold is derived from the node, not chosen.** A node raises when its
available memory is smaller than the largest working set already running on it:
such a node cannot absorb its own biggest pod restarting, which is the ordinary
event that turns headroom into an OOM kill. Measured 2026-09-05 08:26 Oslo, that
line discriminates rather than being decorative -- server1 sits at 4249Mi
available against a 637Mi largest pod and passes, and the same rule applied to
the 487Mi in issue #131's title fails against the same pod.

Swap is printed and never raises. Its total is `swapAvailableBytes +
swapUsageBytes`, so a node reporting a total of zero has no swap configured at
all rather than full swap -- server2 is in that state today and server1 is not,
and reading a 0 as "completely full" is exactly the confusion this file exists
to stop.

**Headroom now and commitment are different questions and this answers both.**
Everything above is current state: how much room a node has *at this instant*.
It says nothing about how much the node has already promised. Measured
2026-09-05 08:42 Oslo, those two readings disagree sharply -- server2 has 4982Mi
free right now and the memory limits of the pods it has accepted add up to
7552Mi of its 7746Mi allocatable, which is 97%, on a node with no swap. A check
that reads only the first number calls that node healthy.

**The counted percentage is a floor, never a total, and the report says so
whenever it is one.** A container with no memory limit is in no sum: it may take
the whole node. Fifteen of them run on server2 today -- Traefik, the Tailscale
operator and all thirteen `ts-*` proxies, which are what serve every `.ts.net`
address here -- so server2's real commitment is *above* 97% by an amount nothing
can read off a manifest. Twelve more run on server1, including every ArgoCD
container, sealed-secrets and metrics-server. **They are printed and they do not
raise**: capping thirty containers is somebody's deliberate decision, and a
check that goes red on day one and stays red is off.

**The threshold is derived, not chosen.** A node raises when its counted limits
exceed `allocatable + swap total` -- the point at which the pods it has already
accepted are permitted, together, to use more memory than the machine can hand
out, so the kernel's OOM killer is the only arbiter left. Swap belongs in it
because it is real memory the kernel can give out, which is exactly the
difference between the two nodes here. It discriminates today rather than being
decorative: server1 sits at 4522Mi against 9792Mi and passes wide, and server2
at 7552Mi against 7746Mi passes by 194Mi -- 2.5% -- so the line is live.
"""

import argparse
import json
import subprocess

from tools import disk_health, oom_history, oom_rank


MIB = 1024.0**2

read_node_names = oom_history.read_node_names
read_summary = disk_health.read_summary


def node_memory(summary):
    """`(availableBytes, workingSetBytes)` for the node itself, or `None`."""
    memory = (summary.get("node") or {}).get("memory") or {}
    available = memory.get("availableBytes")
    if available is None:
        return None
    return available, memory.get("workingSetBytes") or 0


def node_swap(summary):
    """`(total, used)` swap bytes, or `None` when the kubelet reports no swap block.

    A missing block and a configured-but-empty swap file are different facts and
    the caller has to be able to tell them apart, so this does not fold the
    absent case into zero.
    """
    swap = (summary.get("node") or {}).get("swap")
    if not swap:
        return None
    free = swap.get("swapAvailableBytes") or 0
    used = swap.get("swapUsageBytes") or 0
    return free + used, used


def largest_pod(summary):
    """The biggest pod working set on this node, as `(bytes, name)`.

    Returns `None` when the kubelet reported no pod carrying a memory reading —
    a node with nothing on it has nothing to fail to absorb, and inventing a
    zero there would turn every such node into a pass that was guaranteed in
    advance.
    """
    biggest = None
    for pod in summary.get("pods") or []:
        working_set = (pod.get("memory") or {}).get("workingSetBytes")
        if working_set is None:
            continue
        ref = pod.get("podRef") or {}
        name = "%s/%s" % (ref.get("namespace", "?"), ref.get("name", "?"))
        if biggest is None or working_set > biggest[0]:
            biggest = (working_set, name)
    return biggest


def verdict(available, biggest):
    """`True` when this node cannot absorb its own largest pod restarting."""
    if biggest is None:
        return None
    return available < biggest[0]



def read_allocatable(runner=subprocess.run):
    """`{node: allocatable memory bytes}` from the API server.

    A node whose allocatable memory cannot be parsed is left out rather than
    given a zero: a zero would make every node look over-committed, which is a
    positive result guaranteed in advance.
    """
    done = runner(["kubectl", "get", "nodes", "-o", "json"],
                  capture_output=True, text=True)
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get nodes failed")
    sizes = {}
    for item in json.loads(done.stdout or "{}").get("items") or []:
        name = (item.get("metadata") or {}).get("name")
        raw = ((item.get("status") or {}).get("allocatable") or {}).get("memory")
        size = oom_rank.parse_quantity(raw)
        if name and size:
            sizes[name] = size
    return sizes


def read_pods(runner=subprocess.run):
    """Every pod the API server lists, across all namespaces."""
    done = runner(["kubectl", "get", "pods", "-A", "-o", "json"],
                  capture_output=True, text=True)
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get pods failed")
    return json.loads(done.stdout or "{}").get("items") or []


def pod_limits(pod):
    """`(counted bytes, [container names with no memory limit])` for one pod.

    This is the kubelet's own arithmetic and not a sum of every container in the
    spec. Ordinary init containers run *before* the app containers and release
    their memory first, so they commit nothing concurrent and are skipped; an
    init container with `restartPolicy: Always` is a sidecar, runs alongside,
    and is counted. Summing every init container additively inflates the total
    -- measured 2026-09-05, that mistake read server2 at 118% where kubectl and
    the kubelet both say 97%.
    """
    spec = pod.get("spec") or {}
    meta = pod.get("metadata") or {}
    where = "%s/%s" % (meta.get("namespace", "?"), meta.get("name", "?"))
    counted = 0
    uncounted = []
    concurrent = list(spec.get("containers") or [])
    concurrent += [c for c in spec.get("initContainers") or []
                   if c.get("restartPolicy") == "Always"]
    for container in concurrent:
        resources = container.get("resources") or {}
        limit = oom_rank.parse_quantity((resources.get("limits") or {}).get("memory"))
        if limit is None:
            uncounted.append("%s:%s" % (where, container.get("name", "?")))
        else:
            counted += limit
    return counted, uncounted


def node_commitment(pods, node):
    """`(counted bytes, [uncounted container names])` for everything on `node`.

    Succeeded and Failed pods hold no memory, so they are left out; an unbound
    pod has no node to be committed against yet.
    """
    counted = 0
    uncounted = []
    for pod in pods:
        if (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
            continue
        if ((pod.get("spec") or {}).get("nodeName")) != node:
            continue
        pod_counted, pod_uncounted = pod_limits(pod)
        counted += pod_counted
        uncounted += pod_uncounted
    return counted, uncounted


def commitment_verdict(counted, allocatable, swap_total):
    """`True` when the limits already accepted exceed what the machine has.

    `swap_total` is memory the kernel can genuinely hand out, so it belongs on
    the capacity side; `None` means the kubelet reported no swap block, which is
    treated as none rather than guessed at.
    """
    if not allocatable:
        return None
    return counted > allocatable + (swap_total or 0)


def report_commitment(node, counted, uncounted, allocatable, swap_total, out=print):
    """The commitment block for one node. Returns 1 when it is a finding."""
    if not allocatable:
        out("          CANNOT JUDGE commitment on %s — the API server reported "
            "no allocatable memory for it" % node)
        return None
    ceiling = allocatable + (swap_total or 0)
    over = commitment_verdict(counted, allocatable, swap_total)
    share = 100.0 * counted / allocatable
    label = "OVERCOMMITTED" if over else "committed"
    out("          %s: %s of limits accepted against %s allocatable (%.0f%%); "
        "the line is %s allocatable + swap"
        % (label, _mib(counted), _mib(allocatable), share, _mib(ceiling)))
    if uncounted:
        out("          %d container(s) on this node carry NO memory limit, so "
            "the figure above is a floor and not a total: %s"
            % (len(uncounted), ", ".join(sorted(uncounted))))
    return 1 if over else 0


def _mib(value):
    return "%dMi" % int(value / MIB)


def report(node, summary, out=print, commitment=None, allocatable=None):
    """One node's block. Returns 1 when it is a finding, else 0.

    `commitment` is `(counted, uncounted)` from `node_commitment` and
    `allocatable` the node's allocatable memory; both default to `None`, which
    prints the headroom half alone. That is deliberate rather than lazy -- a
    caller that could not read the pod list must not have its silence rendered
    as a node with nothing committed on it.
    """
    memory = node_memory(summary)
    if memory is None:
        out("  CANNOT READ %s — the kubelet reported no node memory block" % node)
        return None
    available, working_set = memory
    biggest = largest_pod(summary)
    tight = verdict(available, biggest)

    if tight is None:
        out("  ok      %s: %s available, %s in use — no pod on it to judge against"
            % (node, _mib(available), _mib(working_set)))
    elif tight:
        out("  LOW     %s: %s available, less than its largest pod %s at %s — "
            "this node cannot absorb that pod restarting"
            % (node, _mib(available), biggest[1], _mib(biggest[0])))
    else:
        out("  ok      %s: %s available, %s in use, largest pod %s at %s"
            % (node, _mib(available), _mib(working_set), biggest[1], _mib(biggest[0])))

    swap = node_swap(summary)
    if swap is None:
        out("          swap: the kubelet reports no swap block for this node")
    elif swap[0] == 0:
        out("          swap: none configured — a total of 0 is no swap file, "
            "not a full one; this node has no cushion and OOM-kills instead")
    else:
        out("          swap: %s of %s used" % (_mib(swap[1]), _mib(swap[0])))

    over = 0
    if commitment is not None:
        counted, uncounted = commitment
        judged = report_commitment(
            node, counted, uncounted, allocatable,
            None if swap is None else swap[0], out=out,
        )
        over = judged or 0
    return (1 if tight else 0) + over


def main(argv=None, runner=subprocess.run, out=print):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--node",
        action="append",
        help="only this node (repeatable); default is every node the API server lists",
    )
    args = parser.parse_args(argv)

    if args.node:
        nodes = list(args.node)
    else:
        try:
            nodes = read_node_names(runner=runner)
        except (OSError, ValueError) as exc:
            out("CANNOT READ the node list: %s" % exc)
            out("Nothing was swept, so this is not a clean result.")
            return 1

    pods = None
    allocatable = {}
    commitment_unreadable = None
    try:
        pods = read_pods(runner=runner)
        allocatable = read_allocatable(runner=runner)
    except (OSError, ValueError) as exc:
        commitment_unreadable = str(exc)

    findings = 0
    unreadable = []
    for node in nodes:
        try:
            summary = read_summary(node, runner=runner)
        except (OSError, ValueError) as exc:
            unreadable.append(node)
            out("  CANNOT READ %s — %s" % (node, exc))
            continue
        judged = report(
            node, summary, out=out,
            commitment=None if pods is None else node_commitment(pods, node),
            allocatable=allocatable.get(node),
        )
        if judged is None:
            unreadable.append(node)
        else:
            findings += judged

    out("Swept %d node(s): %s. Read from each node's own kubelet over "
        "nodes/proxy, not from this pod's /proc, so a node this pod is not "
        "standing on is judged the same as the one it is."
        % (len(nodes), ", ".join(nodes)))
    out("NOT JUDGED  whether a node will actually run out. The lines here are "
        "whether it could absorb its own largest pod restarting, and whether "
        "the limits it has already accepted exceed what it has; a node that "
        "passes both can still be killed by a workload that grows.")
    if commitment_unreadable is not None:
        out("CANNOT READ the pod list or node allocatable memory, so no node "
            "was judged on commitment at all: %s" % commitment_unreadable)
    if unreadable:
        out("%d node(s) could not be read, so this is not a clean sweep: %s"
            % (len(unreadable), ", ".join(unreadable)))
        return 1
    if commitment_unreadable is not None:
        return 1
    if findings:
        out("NODE MEMORY — %d finding(s): a node with less free memory than its "
            "own largest pod, or one whose accepted limits exceed its "
            "allocatable memory plus swap." % findings)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
