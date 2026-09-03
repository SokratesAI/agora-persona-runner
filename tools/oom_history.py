"""Which OOM kills happened on this node, and which pod did each one hit?

Cycle 814, on my own issue #27. The container running my Claude Code
session died at 2026-09-02T09:08:51Z with exit 137 and reason `Error`,
taking two cycles with it, and every instrument here came back blank:
the Deployment has no livenessProbe, so the kubelet did not do it;
containerd said `Error` and not `OOMKilled`, so it did not look like
memory; and `memory.events` on the replacement container reads zero
because that counter is born with the container. Cycle 813 wrote "the
cause is not known" and named the two records that might survive a
restart. One of them answers: the node's own `kern.log`, readable
through `nodes/proxy`, holds the kernel's account of every OOM kill
with the victim's pid, name, rss and cgroup path.

    python3 -m tools.oom_history            # every node, last 24h
    python3 -m tools.oom_history --hours 72
    python3 -m tools.oom_history --node server2

**`Error` and `OOMKilled` are not the two answers.** The 09:08 event
reads `oom_memcg=/kubepods.slice/.../kubepods-burstable-pod<uid>.slice`
and `task_memcg=<that same path>/cri-containerd-<id>.scope` — the limit
that was hit belongs to the **pod** cgroup and the process lived one
level down in the container scope. containerd watches the container
scope for OOM events, so a kill triggered by the parent never reaches
the reason field, and Kubernetes reports the SIGKILL as a plain `Error`.
That is why five checks that all read Kubernetes objects agreed there
was no memory event: they were all reading the same blind instrument.

**The raise is narrow on purpose.** A `CONSTRAINT_NONE` kill is the
whole box running out — that is issue #131 and idea #179, it is boarded, it is
waiting on a decision that is not mine, and there were sixteen of them on
this node in the last day, so raising on it means red every morning forever, which is
the same as off. A `CONSTRAINT_MEMCG` kill is one workload asking for
more than its own declared limit, which a cycle can act on today by
raising the limit or by fixing what leaked. Both are printed; only the
second one raises.

Exit status, matching `tools.helm_repo_health`, `tools.argocd_health`
and the rest of the preflight roster: **2 means a cgroup-limit OOM kill
happened inside the window**, 1 means the kernel log or the pod list was
unreadable — which never reads as clean — and 0 means the window held no
cgroup-limit kill, naming what it swept.

**It reads every node, and it did not until Cycle 857.** The node was
`--node server1` by default, which was the whole cluster until server2
joined on 2026-09-03 — after that a kill on server2 produced a report
identical to no kill at all, from a check whose summary line said it had
swept. The node list comes off the API server rather than a constant here,
because a constant is the same failure again on the third node. A node
whose `kern.log` cannot be read exits 1 and is named in the summary, so a
partial sweep can never be read as a clean one, and a cgroup-limit kill on
any node still outranks that.

Scope it prints for itself: the node keeps `kern.log` for as long as
logrotate keeps it and this reads the current file only, so a window
longer than that rotation silently holds fewer days than it asks for —
the report says which timestamps it actually saw. A pod deleted since
its kill cannot be named, only its uid.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

OSLO = ZoneInfo("Europe/Oslo")

STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?[+-]\d\d:\d\d)\s")
EVENT = re.compile(r"oom-kill:constraint=(?P<constraint>\w+),")
POD_UID = re.compile(r"pod([0-9a-fA-F]{8}(?:[_-][0-9a-fA-F]{4}){3}[_-][0-9a-fA-F]{12})\.slice")
VICTIM = re.compile(
    r"Killed process (?P<pid>\d+) \((?P<name>.*?)\) total-vm:(?P<vm>\d+)kB, anon-rss:(?P<rss>\d+)kB"
)
INVOKED = re.compile(r"invoked oom-killer:")


def read_kern_log(node, runner=subprocess.run):
    """The node's current kernel log, through the kubelet's log endpoint."""
    done = runner(
        ["kubectl", "get", "--raw", "/api/v1/nodes/%s/proxy/logs/kern.log" % node],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get --raw failed")
    return done.stdout


def read_node_names(runner=subprocess.run):
    """Every node in the cluster, in the order the API server lists them.

    The default used to be the literal string `server1`, which was correct
    while server1 was the only node and silently wrong from 2026-09-03, when
    server2 joined: a kill on the new node produced the same clean report as
    no kill at all.
    """
    done = runner(
        ["kubectl", "get", "nodes", "-o", "json"], capture_output=True, text=True
    )
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get nodes failed")
    names = [
        item.get("metadata", {}).get("name")
        for item in json.loads(done.stdout).get("items", [])
    ]
    names = [name for name in names if name]
    if not names:
        raise OSError("the API server listed no nodes, which is not a cluster")
    return names


def read_pod_names(runner=subprocess.run):
    """uid -> 'namespace/name' for every pod alive right now."""
    done = runner(
        ["kubectl", "get", "pods", "-A", "-o", "json"], capture_output=True, text=True
    )
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get pods failed")
    names = {}
    for item in json.loads(done.stdout).get("items", []):
        meta = item.get("metadata", {})
        if meta.get("uid"):
            names[meta["uid"]] = "%s/%s" % (meta.get("namespace", "?"), meta.get("name", "?"))
    return names


def _stamp(line):
    found = STAMP.match(line)
    if not found:
        return None
    return datetime.fromisoformat(found.group(1))


def parse_events(text):
    """Every OOM kill in the log, each with the victims the kernel named under it.

    The kernel prints the `oom-kill:constraint=` summary once and then one
    `Killed process` line per victim, and it repeats some of those lines
    verbatim — the same pid twice in one event is one kill, so victims are
    deduplicated by pid rather than counted.
    """
    events = []
    current = None
    for line in text.splitlines():
        when = _stamp(line)
        found = EVENT.search(line)
        if found:
            uid = POD_UID.search(line)
            current = {
                "when": when,
                "constraint": found.group("constraint"),
                "pod_uid": uid.group(1).replace("_", "-") if uid else None,
                "victims": [],
            }
            events.append(current)
            continue
        if INVOKED.search(line):
            # A trigger with no constraint line of its own is a kill the
            # kernel decided not to make; do not attach later victims to
            # whatever event happened to come before it.
            current = None
            continue
        victim = VICTIM.search(line)
        if victim and current is not None:
            pid = int(victim.group("pid"))
            if any(seen["pid"] == pid for seen in current["victims"]):
                continue
            current["victims"].append(
                {
                    "pid": pid,
                    "name": victim.group("name"),
                    "rss_kb": int(victim.group("rss")),
                    "vm_kb": int(victim.group("vm")),
                }
            )
    return events


def within(events, hours, now=None):
    now = now or datetime.now(timezone.utc)
    floor = now - timedelta(hours=hours)
    return [e for e in events if e["when"] is not None and e["when"] >= floor]


def _oslo(when):
    return when.astimezone(OSLO).strftime("%Y-%m-%d %H:%M:%S Oslo")


def report(node, events, hours, pod_names, seen_from, out=print):
    """Print one node's window and return the exit status it earns."""
    limit = [e for e in events if e["constraint"] == "CONSTRAINT_MEMCG"]
    node_wide = [e for e in events if e["constraint"] != "CONSTRAINT_MEMCG"]

    if limit:
        out(
            "CGROUP LIMIT OOM on %s -- %d kill(s) in the last %dh where a workload asked "
            "for more than its own declared limit. Kubernetes reports these as exit 137 "
            "with reason `Error` when the limit that was hit is the pod cgroup's, because "
            "containerd only watches the container scope."
            % (node, len(limit), hours)
        )
        for event in limit:
            out("  %s  %s" % (_oslo(event["when"]), _pod_label(event, pod_names)))
            for victim in event["victims"]:
                out(
                    "      killed %s (pid %d), %dMi resident of %dMi virtual"
                    % (
                        victim["name"],
                        victim["pid"],
                        victim["rss_kb"] // 1024,
                        victim["vm_kb"] // 1024,
                    )
                )
    if node_wide:
        out(
            "%s RAN OUT -- %d global kill(s) in the last %dh. Deliberately not raised: "
            "this is the node being oversubscribed, which is issue #131 and idea #179, "
            "boarded and waiting on the owner. Raising on it would be red every morning."
            % (node, len(node_wide), hours)
        )
        for event in node_wide:
            names = ", ".join(v["name"] for v in event["victims"]) or "no victim named"
            out("  %s  %s -- %s" % (_oslo(event["when"]), _pod_label(event, pod_names), names))

    out(
        "%s: read that node's own kern.log through nodes/proxy, not a Kubernetes object. "
        "%s Window %dh; a pod deleted since its kill can only be named by uid."
        % (node, seen_from, hours)
    )
    if not limit and not node_wide:
        out("  no OOM kill on %s in the window." % node)
    return 2 if limit else 0


def _pod_label(event, pod_names):
    uid = event["pod_uid"]
    if uid is None:
        return "outside every pod cgroup (a host process)"
    name = pod_names.get(uid)
    return name if name else "pod %s (gone since, cannot be named)" % uid


def sweep_one(node, hours, pod_names, runner=subprocess.run, out=print, now=None):
    """One node's verdict. 1 means it could not be read, which is never clean."""
    try:
        text = read_kern_log(node, runner=runner)
    except (OSError, ValueError) as problem:
        out("UNREADABLE -- %s: %s. A node nothing could be read from is not a clean one."
            % (node, problem))
        return 1

    events = parse_events(text)
    stamps = [e["when"] for e in events if e["when"] is not None]
    if events and not stamps:
        out("UNREADABLE -- %s carried OOM events with no parsable timestamp." % node)
        return 1
    seen_from = (
        "The current kern.log holds %d OOM event(s), oldest %s."
        % (len(events), _oslo(min(stamps)))
        if stamps
        else "The current kern.log holds no OOM event at all."
    )
    return report(node, within(events, hours, now=now), hours, pod_names, seen_from, out=out)


def main(argv=None, runner=subprocess.run, out=print, now=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--node", default=None,
                        help="one node instead of every node in the cluster")
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args(argv)

    try:
        nodes = [args.node] if args.node else read_node_names(runner=runner)
        pod_names = read_pod_names(runner=runner)
    except (OSError, ValueError) as problem:
        out("UNREADABLE -- %s. A window nothing could be read from is not a clean one." % problem)
        return 1

    statuses = [
        sweep_one(node, args.hours, pod_names, runner=runner, out=out, now=now)
        for node in nodes
    ]
    unread = [node for node, status in zip(nodes, statuses) if status == 1]
    out("Swept %d node(s): %s.%s"
        % (len(nodes), ", ".join(nodes),
           "" if not unread else " Could not read %s, so this sweep is partial."
                                 % ", ".join(unread)))
    if 2 in statuses:
        return 2
    return 1 if unread else 0


if __name__ == "__main__":
    sys.exit(main())
