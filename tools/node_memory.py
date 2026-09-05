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

**Commitment is a different question and this file does not own it.**
Everything here is current state: how much room a node has *at this instant*.
How much a node has already *promised* -- the memory limits of the pods placed
on it, against its capacity -- is `tools.workload_health`, whose
`declared_ceiling` sums them, `budget_line` raises `MEMORY OVERCOMMITTED`, and
`other_node_budgets` already runs both for every node rather than only the one
this pod stands on. Measured 2026-09-05 08:51 Oslo it prints *"server2 is
7746Mi and the memory limits declared on it sum to 8064Mi (104%) across 19
container(s)"* and exits 2.

That paragraph is here because Cycle 952 built the whole of it a second time,
in this file, and threw it away unmerged (runner#748). The docstring above
frames *"the one source here that answers for a node this pod is not standing
on"* as a gap this module closed; `other_node_budgets` closed it at Cycle 860.
Nothing pointed from here to there -- `oom_rank` names `workload_health` as the
owner of the declared budget and this file did not -- so writing the second
implementation was a reasonable thing to start doing. **If the question is what
a node has been promised rather than what it has left, it is answered already
and it is not answered here.**

Swap is printed and never raises. Its total is `swapAvailableBytes` +
`swapUsageBytes`, so a node reporting a total of zero has no swap configured at
all rather than full swap -- server2 is in that state today and server1 is not,
and reading a 0 as "completely full" is exactly the confusion this file exists
to stop.

**A node having swap is not the same as a pod being able to use it, and this
file used to say it was.** The zero-swap line read *"this node has no cushion
and OOM-kills instead"*, which three cycles then carried into the handoff and
onto issue #131 as a reason not to move a workload to server2. Measured
2026-09-05 from a container on server1, the node *with* the 2GB swap file:
`memory.swap.max` is **0** and `memory.swap.current` is **0**. Both kubelets run
`failSwapOn: false` with `memorySwap` unset, and unset defaults to `NoSwap`, so
every container cgroup on both nodes is forbidden swap entirely. server1's swap
cushions host processes -- k3s, containerd, the stray `claude.exe` children
the owner reaped on 08-29 -- and does nothing whatsoever for a pod that reaches its
own memory limit. So the behaviour is read off each kubelet's own `configz` and
printed beside the numbers rather than asserted here, because a sentence in this
docstring is exactly the thing that went stale last time.
"""

import argparse
import json
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


def read_swap_behavior(node, runner=subprocess.run):
    """What the kubelet on `node` lets a container do with the node's swap.

    Returns `memorySwap.swapBehavior`, or `""` when the key is absent. Absent is
    a real answer rather than a missing one -- the kubelet defaults it to
    `NoSwap` -- and it is kept distinct from the explicit spelling so a node that
    was configured on purpose can be told from one that inherited the default.
    """
    done = runner(
        [
            "kubectl",
            "get",
            "--raw",
            "/api/v1/nodes/%s/proxy/configz" % node,
        ],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get --raw failed")
    config = (json.loads(done.stdout) or {}).get("kubeletconfig") or {}
    return ((config.get("memorySwap") or {}).get("swapBehavior")) or ""


def swap_reaches_pods(behavior):
    """`True` only when this kubelet lets a container use the node's swap.

    `LimitedSwap` is the one setting that gives a Burstable pod any swap at all.
    `NoSwap` and the unset default both cap every container cgroup at
    `memory.swap.max=0`, which is why they must not be reported differently from
    a node with no swap file.
    """
    return behavior == "LimitedSwap"


def swap_clause(behavior):
    """The sentence that says whether the swap numbers above mean anything for a pod."""
    if behavior is None:
        return ("could not read this kubelet's swapBehavior, so whether a pod on "
                "this node can reach that swap at all is unmeasured")
    if swap_reaches_pods(behavior):
        return ("reaches pods: kubelet swapBehavior is LimitedSwap, so a Burstable "
                "pod here may use it")
    named = behavior or "unset, which the kubelet defaults to NoSwap"
    return ("does NOT cushion a pod: kubelet swapBehavior is %s, so every container "
            "cgroup on this node is capped at memory.swap.max=0 and a pod that hits "
            "its own memory limit is OOM-killed exactly as it would be with no swap "
            "file at all. Host processes only." % named)


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


def report(node, summary, behavior=None, out=print):
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
    else:
        if swap[0] == 0:
            out("          swap: none configured — a total of 0 is no swap file, "
                "not a full one")
        else:
            out("          swap: %s of %s used" % (_mib(swap[1]), _mib(swap[0])))
        out("                %s" % swap_clause(behavior))
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
        try:
            behavior = read_swap_behavior(node, runner=runner)
        except (OSError, ValueError):
            behavior = None
        judged = report(node, summary, behavior=behavior, out=out)
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
