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
"""

import argparse
import subprocess

from tools import disk_health, oom_history


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


def _mib(value):
    return "%dMi" % int(value / MIB)


def report(node, summary, out=print):
    """One node's block. Returns 1 when it is a finding, else 0."""
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
    return 1 if tight else 0


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

    findings = 0
    unreadable = []
    for node in nodes:
        try:
            summary = read_summary(node, runner=runner)
        except (OSError, ValueError) as exc:
            unreadable.append(node)
            out("  CANNOT READ %s — %s" % (node, exc))
            continue
        judged = report(node, summary, out=out)
        if judged is None:
            unreadable.append(node)
        else:
            findings += judged

    out("Swept %d node(s): %s. Read from each node's own kubelet over "
        "nodes/proxy, not from this pod's /proc, so a node this pod is not "
        "standing on is judged the same as the one it is."
        % (len(nodes), ", ".join(nodes)))
    out("NOT JUDGED  whether a node will actually run out. The line here is "
        "whether it could absorb its own largest pod restarting; a node that "
        "passes can still be killed by a workload that grows.")
    if unreadable:
        out("%d node(s) could not be read, so this is not a clean sweep: %s"
            % (len(unreadable), ", ".join(unreadable)))
        return 1
    if findings:
        out("NODE MEMORY LOW — %d node(s) have less free memory than their own "
            "largest pod." % findings)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
