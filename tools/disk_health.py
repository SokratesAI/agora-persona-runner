"""How much disk is left on each node, and is any PersistentVolumeClaim actually capped?

Cycle 865, on the owner's idea #233 — *"Marcus's data is on a 1Gi node-local
volume with no backup of any kind"*. The backup half is built and green
(`tools.marcus_backup_health`). The open half was the capacity question,
and measuring it gave an answer I was not expecting:

**the 1Gi on that claim is not enforced, and neither is any other one
here.** Every PersistentVolumeClaim in this cluster is `local-path`, which
provisions a plain directory on the node's disk with no quota on it. So
the kubelet reports the *node filesystem's* numbers for each volume — on
2026-09-04 all seven bound claims on server1 read 53.9GiB used of 74.8GiB,
identical to each other and identical to the node, while their requested
sizes are 1Gi, 1Gi, 1Gi, 1Gi, 5Gi, 5Gi and 10Gi. Marcus cannot fill "its"
1Gi. What it can do is fill server1's root disk, and the same disk is
under CouchDB (the vault), the Claude bridge, Agora, redis and
whatsapp-bridge — so the failure is not one workload losing its volume, it
is every stateful workload on the node stopping at the same moment.

Nothing here read that. All 42 preflight checks and both memory checks
watch memory, and `tools.host_memory_trend` watches swap; on 2026-09-04
server1 was at 76% of its disk with 17.8GiB left and no instrument
anywhere would have said so at 95%.

    python3 -m tools.disk_health
    python3 -m tools.disk_health --node server2

**The "no quota" verdict is measured, not a string match.** The obvious
implementation is `storageClassName == "local-path"`, and that is a table
of what we use today that goes stale the first time a real CSI driver
lands. Instead: a volume whose `capacityBytes` equals its node's
filesystem `capacityBytes` *is* the node filesystem, whatever provisioned
it, and its requested size is decoration. Those print under
`NOT CAPPED` and are deliberately **not** judged against their request —
judging them would print 5,600% full for every volume, every run, forever.
A volume that reports its own capacity is judged against its own capacity,
so a quota-backed claim added tomorrow is covered with no edit here.

**The threshold is the kubelet's, plus a margin.** The kubelet begins
evicting pods at `nodefs.available<10%` and garbage-collecting images at
`imagefs.available<15%` (k3s ships both defaults). Picking a number of my
own would be a number I invented; this raises `MARGIN_PCT` above the point
at which the cluster itself starts taking action, so the check fires while
there is still room to act rather than during the eviction.

Exit status, matching `tools.oom_history`, `tools.argocd_health` and the
rest of the preflight roster: **2 means a filesystem is inside the margin
above its own eviction threshold**, 1 means a node's kubelet or the node
list was unreadable — which never reads as clean — and 0 means everything
it could judge has room, naming what it swept and what it could not cap.

Two scopes it prints for itself. This is current state, not a trend: a disk
at 76% that is filling by 2GiB a day and one that has been flat for a month
read identically here, and only a stored series separates them. And it reads
each *node's* kubelet, so a Bound claim that no running Pod mounts appears in
no node's stats at all and is invisible to this — `kubectl get pvc -A` is the
list of what exists, this is the list of what is mounted.
"""

import argparse
import json
import subprocess
import sys

from tools import oom_history

#: The kubelet's own default eviction thresholds, as fractions of capacity
#: that must remain *available*. These are k3s/kubelet defaults, not a
#: judgement of mine: `nodefs.available<10%` evicts pods, and
#: `imagefs.available<15%` garbage-collects images.
EVICTION_PCT = {"nodefs": 10.0, "imagefs": 15.0}

#: How far above the cluster's own action point to raise, so a cycle sees
#: it while there is still room to act rather than during the eviction.
MARGIN_PCT = 5.0

GIB = 1024.0**3


#: `tools.oom_history` already owns this, and it is the function that had to be
#: fixed once when server2 joined and a hardcoded `server1` went silently wrong.
#: A second copy here is a second place to find that fix next time.
read_node_names = oom_history.read_node_names


def read_summary(node, runner=subprocess.run):
    """One node's own kubelet stats, over `nodes/proxy`.

    This is the only source here that answers for a node other than the one
    this pod happens to stand on — `/proc` and `df` in a container are always
    the local node, which is what made `memory_headroom` blind to server2.
    """
    done = runner(
        [
            "kubectl",
            "get",
            "--raw",
            "/api/v1/nodes/%s/proxy/stats/summary" % node,
        ],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get --raw failed")
    try:
        return json.loads(done.stdout)
    except ValueError as exc:
        raise OSError("the kubelet's stats summary did not parse: %s" % exc)


def node_filesystems(summary):
    """`{'nodefs': {...}, 'imagefs': {...}}` for the filesystems the node reports.

    A kubelet that publishes no block for one of them leaves it out entirely
    rather than reporting zero — an absent filesystem and a full one must not
    read the same.
    """
    node = summary.get("node") or {}
    found = {}
    nodefs = node.get("fs")
    if isinstance(nodefs, dict) and nodefs.get("capacityBytes"):
        found["nodefs"] = nodefs
    imagefs = (node.get("runtime") or {}).get("imageFs")
    if isinstance(imagefs, dict) and imagefs.get("capacityBytes"):
        found["imagefs"] = imagefs
    return found


def pvc_volumes(summary):
    """Every volume in this node's stats that is backed by a PersistentVolumeClaim."""
    volumes = []
    for pod in summary.get("pods") or []:
        ref = pod.get("podRef") or {}
        for volume in pod.get("volume") or []:
            claim = volume.get("pvcRef")
            if not claim:
                continue
            volumes.append(
                {
                    "namespace": claim.get("namespace") or ref.get("namespace") or "?",
                    "claim": claim.get("name") or "?",
                    "pod": ref.get("name") or "?",
                    "usedBytes": volume.get("usedBytes"),
                    "capacityBytes": volume.get("capacityBytes"),
                    "availableBytes": volume.get("availableBytes"),
                }
            )
    return volumes


def available_pct(filesystem):
    """Percent of capacity still free, or None when the kubelet did not say."""
    capacity = filesystem.get("capacityBytes")
    available = filesystem.get("availableBytes")
    if not capacity or available is None:
        return None
    return 100.0 * available / capacity


def raises_at(kind):
    """The available-percent below which `kind` is a finding."""
    return EVICTION_PCT[kind] + MARGIN_PCT


def is_capped(volume, filesystems):
    """Does this volume have a size of its own, or is it just the node's disk?

    True when the volume reports a capacity that is not any of the node's own
    filesystems. `local-path` hands out a directory with no quota, so the
    kubelet reports the whole node filesystem for it and the claim's requested
    size is enforced by nothing.
    """
    capacity = volume.get("capacityBytes")
    if not capacity:
        return None
    node_capacities = {
        fs.get("capacityBytes") for fs in filesystems.values() if fs.get("capacityBytes")
    }
    return capacity not in node_capacities


def _gib(value):
    return "?" if not value else "%.1fGiB" % (value / GIB)


def report(node, filesystems, volumes, out=print):
    """Print one node's verdict. Returns the number of findings on it."""
    findings = 0
    for kind in ("nodefs", "imagefs"):
        filesystem = filesystems.get(kind)
        if filesystem is None:
            out(
                "  NOT READ   %s %s — the kubelet published no block for it"
                % (node, kind)
            )
            continue
        free = available_pct(filesystem)
        if free is None:
            out(
                "  NOT READ   %s %s — the kubelet published no availableBytes"
                % (node, kind)
            )
            continue
        line = "%s %s: %s free of %s (%.1f%%), used %s" % (
            node,
            kind,
            _gib(filesystem.get("availableBytes")),
            _gib(filesystem.get("capacityBytes")),
            free,
            _gib(filesystem.get("usedBytes")),
        )
        if free < raises_at(kind):
            findings += 1
            out(
                "  FILLING    %s — under %.1f%% free, and the kubelet acts at %.1f%%"
                % (line, raises_at(kind), EVICTION_PCT[kind])
            )
        else:
            out("  ok         %s" % line)

    for volume in volumes:
        name = "%s/%s (%s)" % (volume["namespace"], volume["claim"], volume["pod"])
        capped = is_capped(volume, filesystems)
        if capped is None:
            out("  NOT READ   %s — the kubelet published no capacity for it" % name)
            continue
        if not capped:
            out(
                "  NOT CAPPED %s — reports the node filesystem (%s), so its requested size is enforced by nothing"
                % (name, _gib(volume.get("capacityBytes")))
            )
            continue
        free = available_pct(volume)
        if free is None:
            out("  NOT READ   %s — capped, but no availableBytes" % name)
            continue
        line = "%s: %s free of %s (%.1f%%)" % (
            name,
            _gib(volume.get("availableBytes")),
            _gib(volume.get("capacityBytes")),
            free,
        )
        # The kubelet publishes no eviction threshold for a volume's own fill
        # level, so this borrows nodefs's. That one IS a number I picked, and
        # nothing here today exercises it — every claim in this cluster is
        # uncapped.
        if free < raises_at("nodefs"):
            findings += 1
            out("  FILLING    %s" % line)
        else:
            out("  ok         %s" % line)
    return findings


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
    capped = 0
    uncapped = 0
    for node in nodes:
        out("== %s" % node)
        try:
            summary = read_summary(node, runner=runner)
        except (OSError, ValueError) as exc:
            unreadable.append(node)
            out("  CANNOT READ %s — %s" % (node, exc))
            continue
        filesystems = node_filesystems(summary)
        volumes = pvc_volumes(summary)
        for volume in volumes:
            state = is_capped(volume, filesystems)
            if state is True:
                capped += 1
            elif state is False:
                uncapped += 1
        findings += report(node, filesystems, volumes, out=out)

    out("")
    # The caveats go first and the sweep line last, because `tools.preflight`
    # collapses this to "the last line carrying a digit". The reviewer measured
    # the other order: the trend disclaimer is the same sentence on every run,
    # so the roster row could never vary with the result.
    out(
        "Raises when a filesystem has less than %.1f%% free — that is the kubelet's own eviction point plus %.1f%%."
        % (raises_at("nodefs"), MARGIN_PCT)
    )
    if uncapped:
        out(
            "NOT JUDGED  the requested size of the %d uncapped claim(s) above. There is no quota behind it, so the real limit is the node filesystem judged above."
            % uncapped
        )
    out(
        "NOT JUDGED  whether a disk is filling. This is current state; a flat disk and one that was ten points emptier yesterday read the same here."
    )
    out(
        "NOT JUDGED  a Bound claim that no running Pod mounts. This reads each node's kubelet, and an unmounted volume is in no node's stats at all."
    )
    if unreadable:
        out(
            "CANNOT READ %d of %d node(s), so the counts below cover only the rest."
            % (len(unreadable), len(nodes))
        )
    out(
        "Swept %d node(s): %s. %d claim(s) report a size of their own; %d report the node's disk instead."
        % (len(nodes), ", ".join(nodes), capped, uncapped)
    )
    if unreadable:
        out(
            "CANNOT READ %d node(s): %s — the sweep is partial, so a clean line above does not cover them."
            % (len(unreadable), ", ".join(unreadable))
        )
    # A real finding outranks a partial sweep, the same call `tools.oom_history`
    # makes. The reviewer caught the other order: a node at 2% free, read fine,
    # alongside one unreachable node collapsed to `UNREADABLE` in preflight's
    # roster — the word for "nothing could be judged" printed over something
    # that had been.
    if findings:
        return 2
    return 1 if unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
