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
read identically here, and only a stored series separates them. And the
per-node half reads each *node's* kubelet, so it sees only what is mounted.

**That second gap is now closed from the other side, and closing it found
two.** Cycle 871 went looking for a workload to move onto server2 and
walked into `infra/nats-js-nats-0` — a 10Gi claim created 2026-02-23,
pinned to server1, carrying no `argocd.argoproj.io/tracking-id`, for a
`nats` StatefulSet that does not exist in the cluster — and
`agents/ollama-models`, Pending since 2026-07-09 and likewise tracked by
nothing. Both were found by hand, by listing claims and reading them one
at a time, and the tool that is supposed to answer "what is on server1's
disk" said in words that it could not see them.

So `unmounted_claims()` asks the API server instead of the kubelet: every
claim that exists, minus every claim a Pod mounts. **The verdict is
deliberately two-sided, because an unmounted claim is usually fine.**
`infra/ollama-models` is unmounted because its Deployment is parked at
`replicas: 0` — that is a decision somebody made, ArgoCD tracks the claim,
and a check that reddens on it every morning is one nobody reads. Only a
claim that is mounted by nothing **and** tracked by no ArgoCD Application
raises: no workload uses it and no repository would recreate it, so it is
an object that exists purely by accident and holds a directory on a node's
disk. That is a closeable finding rather than a standing one.
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


def shares_one_filesystem(filesystems):
    """Are nodefs and imagefs the same disk?

    k3s puts the image store on the root filesystem, so the kubelet publishes
    two blocks describing one disk. Their free percentages are then identical
    and reporting them as two verdicts says the same thing twice. Compared on
    capacity AND availability rather than on a path, because the kubelet
    publishes no mount point for either.
    """
    nodefs = filesystems.get("nodefs")
    imagefs = filesystems.get("imagefs")
    if not nodefs or not imagefs:
        return False
    for key in ("capacityBytes", "availableBytes"):
        if nodefs.get(key) is None or nodefs.get(key) != imagefs.get(key):
            return False
    return True


def usage_breakdown(summary, filesystems):
    """What a node's used bytes are made of, for the parts the kubelet names.

    Only the image store and each Pod's ephemeral storage are attributable
    from these stats. What is left is named rather than dropped, and it is not
    "the host": a `local-path` volume's contents live on the node filesystem
    and appear in no per-Pod counter, so they are inside the remainder too.
    Measuring those needs a hostPath Job (Cycle 869 ran one).

    Returns None when there is nothing to decompose — no nodefs usedBytes, or
    an image store on a different disk, where its bytes are not part of this
    disk's used total at all.
    """
    nodefs = filesystems.get("nodefs")
    if not nodefs or not nodefs.get("usedBytes"):
        return None
    if not shares_one_filesystem(filesystems):
        return None
    used = nodefs["usedBytes"]
    images = (filesystems.get("imagefs") or {}).get("usedBytes") or 0
    ephemeral = 0
    for pod in summary.get("pods") or []:
        pod_used = (pod.get("ephemeral-storage") or {}).get("usedBytes")
        if pod_used:
            ephemeral += pod_used
    return {
        "used": used,
        "images": images,
        "ephemeral": ephemeral,
        "rest": used - images - ephemeral,
    }


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


def _measured_gib(value):
    """Like `_gib`, but 0 is an answer rather than a missing field.

    `_gib`'s "?" means the kubelet published nothing. Every figure in the
    breakdown is computed, so 0 there means measured-and-zero — an idle node
    reporting no ephemeral storage is not a node that failed to report.
    """
    return "%.1fGiB" % (value / GIB)


def read_claims(runner=subprocess.run):
    """Every PersistentVolumeClaim the API server knows about."""
    done = runner(
        ["kubectl", "get", "pvc", "--all-namespaces", "-o", "json"],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get pvc failed")
    try:
        return json.loads(done.stdout).get("items") or []
    except ValueError as exc:
        raise OSError("the claim list did not parse: %s" % exc)


def read_mounted_claims(runner=subprocess.run):
    """`{(namespace, claim)}` for every claim some Pod currently mounts.

    Pods rather than workloads on purpose: a Deployment parked at
    `replicas: 0` still names its claim in a template that nothing is
    running, and the question here is what is actually in use.
    """
    done = runner(
        ["kubectl", "get", "pods", "--all-namespaces", "-o", "json"],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get pods failed")
    try:
        items = json.loads(done.stdout).get("items") or []
    except ValueError as exc:
        raise OSError("the pod list did not parse: %s" % exc)
    mounted = set()
    for pod in items:
        namespace = (pod.get("metadata") or {}).get("namespace")
        for volume in (pod.get("spec") or {}).get("volumes") or []:
            claim = (volume.get("persistentVolumeClaim") or {}).get("claimName")
            if claim:
                mounted.add((namespace, claim))
    return mounted


def tracked_by(claim):
    """The ArgoCD Application that owns this claim, or None if nothing does.

    ArgoCD stamps `argocd.argoproj.io/tracking-id` on everything it applies,
    so its absence is the difference between "a repository would recreate
    this" and "deleting it deletes it".
    """
    annotations = (claim.get("metadata") or {}).get("annotations") or {}
    tracking = annotations.get("argocd.argoproj.io/tracking-id")
    if not tracking:
        return None
    return tracking.split(":", 1)[0] or None


def unmounted_claims(claims, mounted):
    """The claims no Pod mounts, newest-agnostic, in `kubectl` order.

    Each is `(claim, owner)` where `owner` is the ArgoCD Application that
    tracks it or None. The caller decides which of those raises.
    """
    found = []
    for claim in claims:
        metadata = claim.get("metadata") or {}
        key = (metadata.get("namespace"), metadata.get("name"))
        if key in mounted:
            continue
        found.append((claim, tracked_by(claim)))
    return found


def report_unmounted(unmounted, out=print):
    """Print the unmounted claims. Returns the number that raise."""
    findings = 0
    for claim, owner in unmounted:
        metadata = claim.get("metadata") or {}
        name = "%s/%s" % (metadata.get("namespace"), metadata.get("name"))
        requested = (
            ((claim.get("spec") or {}).get("resources") or {}).get("requests") or {}
        ).get("storage", "?")
        phase = (claim.get("status") or {}).get("phase", "?")
        created = metadata.get("creationTimestamp", "?")
        if owner:
            out(
                "  PARKED     %s (%s, %s) — no Pod mounts it, but %s tracks it, so this is a decision rather than litter"
                % (name, requested, phase, owner)
            )
            continue
        findings += 1
        out(
            "  ORPHANED   %s (%s, %s, created %s) — no Pod mounts it and no ArgoCD Application tracks it, so nothing uses it and nothing would recreate it"
            % (name, requested, phase, created)
        )
    return findings

def report(node, filesystems, volumes, out=print, breakdown=None):
    """Print one node's verdict. Returns the number of findings on it."""
    findings = 0
    shared = shares_one_filesystem(filesystems)
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
        same = " — the same disk as nodefs, not a second one" if (
            shared and kind == "imagefs"
        ) else ""
        line = "%s %s: %s free of %s (%.1f%%), used %s" % (
            node,
            kind,
            _gib(filesystem.get("availableBytes")),
            _gib(filesystem.get("capacityBytes")),
            free,
            _gib(filesystem.get("usedBytes")),
        ) + same
        if free < raises_at(kind):
            findings += 1
            out(
                "  FILLING    %s — under %.1f%% free, and the kubelet acts at %.1f%%"
                % (line, raises_at(kind), EVICTION_PCT[kind])
            )
        else:
            out("  ok         %s" % line)

    if breakdown:
        head = "  MADE OF    %s: %s of container images, %s of Pod ephemeral storage" % (
            node,
            _measured_gib(breakdown["images"]),
            _measured_gib(breakdown["ephemeral"]),
        )
        if breakdown["rest"] < 0:
            # The kubelet samples node.fs, runtime.imageFs and each Pod's
            # ephemeral-storage seconds apart, and the writable-layer bytes it
            # counts under imageFs can overlap a Pod's own. So the two named
            # parts can sum past the disk's used total, and the remainder is
            # then arithmetic rather than a measurement. Printing "-3.0GiB of
            # something" would be a disk figure that is simply not true.
            out(
                "%s — which is %s MORE than the %s this disk reports used, so there is no remainder to name. These three figures are sampled seconds apart and the image store's writable layers can be counted twice."
                % (head, _measured_gib(-breakdown["rest"]), _measured_gib(breakdown["used"]))
            )
        else:
            out(
                "%s, %s neither — local-path volume contents and whatever the host itself stores, which these stats cannot separate. Of %s used."
                % (head, _measured_gib(breakdown["rest"]), _measured_gib(breakdown["used"]))
            )

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
        findings += report(
            node,
            filesystems,
            volumes,
            out=out,
            breakdown=usage_breakdown(summary, filesystems),
        )

    out("== claims no Pod mounts")
    orphaned = 0
    claims_read = None
    try:
        claims = read_claims(runner=runner)
        mounted = read_mounted_claims(runner=runner)
    except (OSError, ValueError) as exc:
        unreadable.append("the claim list")
        out("  CANNOT READ the claim list — %s" % exc)
    else:
        claims_read = len(claims)
        unmounted = unmounted_claims(claims, mounted)
        if not unmounted:
            out("  ok         every claim that exists is mounted by a running Pod")
        else:
            orphaned = report_unmounted(unmounted, out=out)
        findings += orphaned

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
        "NOT JUDGED  how much disk an unmounted claim actually holds. It is in no node's kubelet stats at all, so the section above says that it exists and not what it costs."
    )
    if unreadable:
        out(
            "CANNOT READ %d of %d node(s), so the counts below cover only the rest."
            % (len(unreadable), len(nodes))
        )
    out(
        "Swept %d node(s): %s. %d claim(s) report a size of their own; %d report the node's disk instead. %s claim(s) exist cluster-wide, %d of them mounted by nothing and owned by nothing."
        % (
            len(nodes),
            ", ".join(nodes),
            capped,
            uncapped,
            "?" if claims_read is None else claims_read,
            orphaned,
        )
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
