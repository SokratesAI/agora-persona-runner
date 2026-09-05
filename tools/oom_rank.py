"""Which container does the kernel kill first when a node runs out?

Cycle 940, on the owner's issue #131 (server1 out of memory, 🔴
Immediately). Cycle 937 closed the false alarm on that row and left one
sentence open: the bridge pod's 4 GiB *limit* is larger than server1 has
free, so "a runaway lands as a host OOM kill of some other victim".
Nothing here could name the victim. Every memory instrument in this repo
reports bytes — `host_memory_trend` the node's, `memory_headroom` this
container's cgroup, `workload_health` the declared budget — and none of
them reads the one number that decides who dies: `oom_score_adj`.

The kernel picks by score, and the kubelet writes that score from the
Pod's QoS class alone:

- **Guaranteed** (every container has request == limit) -> -997.
- **BestEffort** (no requests and no limits anywhere in the Pod) -> 1000,
  the worst possible score. First to die, every time.
- **Burstable** -> `1000 - 1000 * memoryRequest / nodeCapacity`, clamped
  into `[3, 999]`. A small request is a high score.

So the score is set by the *request*, and the limit — the thing that
decides how large a runaway can get — does not enter into it at all. A
container that asks for little and may take a lot is therefore ranked
*safer* than one that asks for nothing and takes nothing.

That is the shape on server1 today and it is why this is a file rather
than a paragraph. `agora-claude-bridge` requests 1 GiB of the node's 7746
MiB and may grow to 4 GiB, which scores it 868 — 22nd of 22 in server1's
kill order. Ten containers on the same node are BestEffort at 1000: all
seven of ArgoCD, `traefik` (the cluster's ingress), `local-path-provisioner`
(every volume mount on the box), `sealed-secrets` and `reloader`. If my own
pod runs away, the kernel works down from 1000 and takes those before it
reaches me.

**This raises nothing on the ranking, and that is deliberate.** "The
container with the largest limit is ranked safest" is exactly how
Kubernetes is designed to behave — a larger request buys a lower score —
so an alarm on it would fire on the normal case forever, and any number
of ranks or megabytes at which it became bad would be one I invented
rather than measured. `tools.disk_health` prints its image breakdown on
the same terms. The one thing here that can come back false is the
control below, and that is the only thing that exits 2.

**The control that makes this a measurement rather than arithmetic.**
This runs inside one of the containers it ranks, so it reads
`/proc/self/oom_score_adj` and compares it against what the formula
predicts for this pod. Measured on the bridge pod the cycle this was
written: predicted 868, kernel 868. If the kubelet's rule ever changes,
or this reading of it is wrong, that line disagrees and says so instead
of the whole report being confidently wrong — `prompt.md`'s "a positive
result can be guaranteed in advance" applies to a formula as much as to
an HTTP 200.

**What it deliberately does not judge.** A Pod carrying
`system-node-critical` or `system-cluster-critical` is named and its
score is printed as unverified: those priority classes protect a Pod from
*eviction and preemption*, which is the kubelet's own accounting, and
that is a different mechanism from the kernel's `oom_score_adj`. I have
only ever read one container's real score — my own, which carries no
priority class — so extending the confirmed formula onto a critical Pod
would be a claim wider than the check I took. Two of the ten BestEffort
containers on server1 are in that set: `traefik` and
`local-path-provisioner`.

It also says nothing about *whether* a node will run out. That is
`tools.host_memory_trend` and `tools.workload_health`. This one answers
only the conditional: if it does, in what order.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MIB = 1024 * 1024

#: The kubelet's constants, from its QoS-to-OOM-score rule.
GUARANTEED_ADJ = -997
BESTEFFORT_ADJ = 1000
#: Burstable is clamped into this range: never below a Guaranteed pod's
#: neighbourhood, and never equal to BestEffort.
BURSTABLE_FLOOR = 1000 + GUARANTEED_ADJ
BURSTABLE_CEILING = BESTEFFORT_ADJ - 1

#: Priority classes that protect a Pod from eviction, which is not the same
#: mechanism as the kernel's kill order. Named, not scored.
CRITICAL_PRIORITY = ("system-node-critical", "system-cluster-critical")

SUFFIX = {"Ki": 1024, "Mi": MIB, "Gi": 1024 ** 3, "Ti": 1024 ** 4,
          "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4}


def parse_quantity(raw):
    """Bytes from a Kubernetes quantity string, or `None` if it is absent."""
    if raw is None:
        return None
    text = str(raw).strip()
    for suffix, factor in SUFFIX.items():
        if text.endswith(suffix):
            head = text[: -len(suffix)]
            try:
                return int(float(head) * factor)
            except ValueError:
                return None
    try:
        return int(float(text))
    except ValueError:
        return None


def oom_score_adj(qos_class, request_bytes, node_capacity_bytes):
    """The score the kubelet writes for a container, or `None` if unknowable.

    `request_bytes` is that container's own memory request — 0 when it sets
    none, which is what makes a Burstable container with no request score
    999 rather than being left out.
    """
    if qos_class == "Guaranteed":
        return GUARANTEED_ADJ
    if qos_class == "BestEffort":
        return BESTEFFORT_ADJ
    if qos_class != "Burstable":
        return None
    if not node_capacity_bytes:
        return None
    adj = 1000 - (1000 * (request_bytes or 0)) // node_capacity_bytes
    if adj < BURSTABLE_FLOOR:
        return BURSTABLE_FLOOR
    if adj >= BESTEFFORT_ADJ:
        return BURSTABLE_CEILING
    return adj


def containers(pods, capacities):
    """One record per running container, scored. Newest kubectl JSON in."""
    out = []
    for pod in pods:
        status = pod.get("status") or {}
        if status.get("phase") != "Running":
            continue
        spec = pod.get("spec") or {}
        node = spec.get("nodeName")
        qos = status.get("qosClass")
        meta = pod.get("metadata") or {}
        for container in spec.get("containers") or []:
            resources = container.get("resources") or {}
            request = parse_quantity((resources.get("requests") or {}).get("memory"))
            limit = parse_quantity((resources.get("limits") or {}).get("memory"))
            out.append({
                "node": node,
                "name": f"{meta.get('namespace')}/{meta.get('name')} [{container.get('name')}]",
                "qos": qos,
                "request": request,
                "limit": limit,
                "priority": spec.get("priorityClassName"),
                "score": oom_score_adj(qos, request, capacities.get(node)),
            })
    return out


def judge_node(node, records):
    """`(lines, None)` — one node's kill order. This raises nothing.

    There is no threshold here on purpose. "The container with the largest
    limit is ranked safest" is how Kubernetes is *designed* to behave — a
    larger request buys a lower score — so alarming on it would be alarming
    on the normal case, and picking a number of ranks or megabytes at which
    it becomes bad would be a number I invented rather than measured
    (`personality.md`: a limit needs a danger, and I have to have measured
    the danger). What this prints is the order. The judgement is Edvard's
    and mine, on the page, with the numbers in front of us.
    """
    scored = [r for r in records if r["score"] is not None]
    if not scored:
        return [f"CANNOT SCORE  {node}: no container on it carries a QoS class "
                f"this knows how to score."]

    order = sorted(scored, key=lambda r: (-r["score"], r["name"]))
    lines = [f"{node} — {len(order)} container(s), highest score dies first:"]
    biggest = max((r for r in scored if r["limit"]), key=lambda r: r["limit"],
                  default=None)
    for position, record in enumerate(order, start=1):
        marks = []
        if record["priority"] in CRITICAL_PRIORITY:
            marks.append(f"{record['priority']}, score not verified from here")
        if biggest is not None and record is biggest:
            marks.append("largest limit on this node")
        note = ("  — " + "; ".join(marks)) if marks else ""
        limit = (f"limit {record['limit'] / MIB:.0f} MiB"
                 if record["limit"] else "no limit")
        lines.append(
            f"  {position:>2}. {record['score']:>5}  {record['name']}  "
            f"({record['qos']}, request {(record['request'] or 0) / MIB:.0f} MiB, "
            f"{limit}){note}")
    if biggest is not None:
        rank = order.index(biggest) + 1
        lines.append(
            f"  The container that can grow the most here is {biggest['name']} "
            f"({biggest['limit'] / MIB:.0f} MiB), and it is {rank} of "
            f"{len(order)} in the kill order — the kernel reaches "
            f"{rank - 1} other container(s) before it.")
    return lines


def read_own_score(path="/proc/self/oom_score_adj"):
    try:
        return int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def control_line(records, hostname, own_score):
    """`(line, agreed)` — the reading that would disagree if the rule were wrong.

    `agreed` is True only when a real kernel value was read AND matched. An
    unreadable `/proc/self/oom_score_adj`, or a sweep this pod is not in,
    leaves the whole report unverified, and an unverified report must not
    read as a confirmed one — so both of those are False.
    """
    if own_score is None:
        return ("CONTROL UNVERIFIED — /proc/self/oom_score_adj could not be "
                "read, so nothing above was checked against a live kernel."), False
    mine = [r for r in records if hostname and hostname in r["name"]]
    if not mine:
        return (f"CONTROL UNVERIFIED — the kernel gives this process "
                f"oom_score_adj {own_score}, but its own Pod is not in the "
                f"sweep, so there is nothing to compare it against."), False
    predicted = mine[0]["score"]
    if predicted == own_score:
        return (f"CONTROL  the rule agrees with the kernel on this pod: "
                f"predicted {predicted}, /proc/self/oom_score_adj reads "
                f"{own_score} ({mine[0]['name']})."), True
    return (f"CONTROL DISAGREES — predicted {predicted} for {mine[0]['name']} "
            f"and /proc/self/oom_score_adj reads {own_score}. The rule this "
            f"check applies is wrong or has changed; treat every score above "
            f"as unreliable."), False


def _kubectl(args, timeout=60):
    binary = shutil.which("kubectl")
    if binary is None:
        return None
    try:
        done = subprocess.run([binary, *args], capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def read_cluster(run=_kubectl):
    """`(pods, capacities, None)` from the live cluster, or `(None, None, why)`."""
    raw_pods = run(["get", "pods", "-A", "-o", "json"])
    raw_nodes = run(["get", "nodes", "-o", "json"])
    if raw_pods is None or raw_nodes is None:
        return None, None, ("kubectl could not read pods and nodes, so no kill "
                            "order could be built. This check reads the live "
                            "cluster and has no fallback.")
    try:
        pods = json.loads(raw_pods).get("items") or []
        nodes = json.loads(raw_nodes).get("items") or []
    except json.JSONDecodeError as exc:
        return None, None, f"kubectl returned something that is not JSON: {exc}"
    capacities = {}
    for node in nodes:
        name = (node.get("metadata") or {}).get("name")
        capacity = ((node.get("status") or {}).get("capacity") or {}).get("memory")
        capacities[name] = parse_quantity(capacity)
    return pods, capacities, None


def report(records, hostname, own_score):
    """`(lines, exit_code)`. Exit 2 means the CONTROL disagreed, nothing else.

    A ranking is a readout, not an alarm, so the only thing here that can
    raise is the one check that can actually come back false: the formula
    against a real kernel.
    """
    lines = []
    nodes = sorted({r["node"] for r in records if r["node"]})
    for node in nodes:
        lines.extend(judge_node(node, [r for r in records if r["node"] == node]))
    control, agreed = control_line(records, hostname, own_score)
    lines.append(control)
    unscored = [r for r in records if r["score"] is None]
    if unscored:
        lines.append(f"  NOT JUDGED  {len(unscored)} container(s) whose Pod "
                     f"carries no QoS class this knows how to score.")
    critical = [r for r in records if r["priority"] in CRITICAL_PRIORITY]
    if critical:
        lines.append(
            f"  NOT VERIFIED  {len(critical)} container(s) sit in a Pod with a "
            f"critical priority class. Those classes govern eviction and "
            f"preemption, which is the kubelet's accounting rather than the "
            f"kernel's, and I have only ever read one container's real "
            f"oom_score_adj — my own, which carries no priority class. Their "
            f"scores above are the formula, not a reading.")
    lines.append(
        f"  NOT JUDGED  whether any node will actually run out. This is the "
        f"kill ORDER only — tools.host_memory_trend and tools.workload_health "
        f"own that half, and nothing here invents a threshold on the ranking.")
    lines.append(
        f"Judged {len(records)} running container(s) across {len(nodes)} node(s), "
        f"read from the live cluster, not from git.")
    return lines, (0 if agreed else 2)


def main(argv=None):
    import os
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    print("OOM KILL ORDER")
    pods, capacities, why = read_cluster()
    if pods is None:
        print(f"COULD NOT READ: {why}")
        return 1
    records = containers(pods, capacities)
    lines, code = report(records, os.environ.get("HOSTNAME"), read_own_score())
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
