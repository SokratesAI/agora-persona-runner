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

Two scopes it printed for itself. The first is closed as of Cycle 1013: the
kubelet is a single reading, so a disk at 76% filling by 2GiB a day and one
flat for a month read identically here — and only a stored series separates
them. Prometheus has one, `container_fs_usage_bytes{id="/"}` per node, so
each node now also gets a least-squares slope over the last 24 hours and,
where the fit spans at least six hours, how many days that slope puts
between it and the kubelet's own eviction point. **The slope is reported and
does not move the exit status**, which still turns on free space alone: a
projection is an extrapolation and the thing that evicts pods is the
percentage. Measured 2026-09-06 01:56 Oslo, which is why this is worth
having — server1 sits inside the margin and reads FILLING, and its disk has
been flat for the whole readable window while server2, which reads ok, is
the one growing. The second scope is unchanged: the per-node half reads each
*node's* kubelet, so it sees only what is mounted.

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

**And when it does raise, it now names what the disk is made of.** On
2026-09-05 server1 crossed the margin at 19.9% free, and this check said the
56.8GiB was 24.3GiB of container images, 0.9GiB of Pod ephemeral storage and
31.6GiB "neither -- local-path volume contents and whatever the host itself
stores, which these stats cannot separate". That last clause is true and it is
not an answer: finding out what those 31.6GiB actually were took five hand-run
one-off Jobs, and the answer was a 4.1GiB systemd journal sitting at journald's
own 4GiB default cap, 5.0GiB of build caches in `/root`, and 2.5GiB of this
loop's own restore-point archives in `/var/lib/nova-attic` for volume moves that
had already completed. None of that is visible to a kubelet at all, so no amount
of reading `stats/summary` harder was ever going to produce it.

`read_host_breakdown()` runs a read-only Job on the node and prints those
directories biggest first, **only on a node that raised.** The condition is the
point rather than an optimisation: a Job per node is tens of seconds, this check
runs inside `tools.preflight` every cycle, and on a normal morning it would be
answering a question nobody asked. The moment it is worth paying for is the
moment the check is about to say FILLING. A directory absent from the node is
named as absent rather than reported as zero -- `/home` is empty on a box with
no human users and missing on another, and those are different facts -- and a
read that fails prints `NOT READ` and deliberately does not move the verdict,
since the node has already raised and this is the detail line.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from tools import oneoff_job, oom_history

#: The kubelet's own default eviction thresholds, as fractions of capacity
#: that must remain *available*. These are k3s/kubelet defaults, not a
#: judgement of mine: `nodefs.available<10%` evicts pods, and
#: `imagefs.available<15%` garbage-collects images.
EVICTION_PCT = {"nodefs": 10.0, "imagefs": 15.0}

#: How far above the cluster's own action point to raise, so a cycle sees
#: it while there is still room to act rather than during the eviction.
MARGIN_PCT = 5.0

GIB = 1024.0**3

#: Prometheus keeps a stored series of every node's root filesystem, which is
#: the one thing a single kubelet reading cannot give: yesterday's number.
PROMETHEUS = "http://prometheus.infra.svc.cluster.local:9090"
PROM_TIMEOUT = 20

#: How far back to fit the slope, and how finely to sample it. The window is
#: a bound on one query, not a threshold — nothing is judged against it.
TREND_HOURS = 24.0
TREND_STEP_SECONDS = 1800

#: A fit shorter than this does not get turned into a projection. This estate
#: has real periodicity inside a day — hourly vault backups, six-hourly
#: newspaper jobs, an image pull on every merge — so a slope taken over one
#: hour extrapolates a single event into a trend, and a 1GiB layer pull reads
#: as +24GiB/day. Six hours is a quarter of that cycle. The slope itself is
#: still printed below this span, with its span beside it; only the
#: days-to-eviction arithmetic is withheld.
TREND_MIN_SPAN_HOURS = 6.0

#: Two points define a line and say nothing about whether it is one.
TREND_MIN_SAMPLES = 4


def _prom_json(url, opener=urllib.request.urlopen):
    with opener(url, timeout=PROM_TIMEOUT) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise ValueError("prometheus answered status=%r" % (payload.get("status"),))
    return payload["data"]


def root_devices(base=PROMETHEUS, opener=urllib.request.urlopen):
    """Which block device backs `/` on each node, per cAdvisor.

    A node publishes several filesystems at `id="/"` — `/dev/shm`, `/run`,
    tmpfs mounts — and only one of them is the disk the kubelet is reporting
    on. Picking the largest is not a guess: the caller checks the chosen
    device's capacity against the kubelet's own `capacityBytes` and refuses
    the trend rather than fitting a line through the wrong filesystem.
    """
    url = base + "/api/v1/query?" + urllib.parse.urlencode(
        {"query": 'container_fs_limit_bytes{id="/"}'}
    )
    data = _prom_json(url, opener=opener)
    best = {}
    for entry in data.get("result", []):
        metric = entry.get("metric") or {}
        node = metric.get("node")
        device = metric.get("device")
        if not node or not device:
            continue
        limit = float(entry["value"][1])
        if node not in best or limit > best[node][1]:
            best[node] = (device, limit)
    return best


def root_usage_series(base=PROMETHEUS, opener=urllib.request.urlopen,
                      hours=TREND_HOURS, now=None):
    """Every node's root-filesystem usage over the window, as (epoch, bytes)."""
    end = time.time() if now is None else now
    url = base + "/api/v1/query_range?" + urllib.parse.urlencode({
        "query": 'container_fs_usage_bytes{id="/"}',
        "start": "%d" % int(end - hours * 3600),
        "end": "%d" % int(end),
        "step": "%d" % TREND_STEP_SECONDS,
    })
    data = _prom_json(url, opener=opener)
    series = {}
    for entry in data.get("result", []):
        metric = entry.get("metric") or {}
        node = metric.get("node")
        device = metric.get("device")
        if not node or not device:
            continue
        points = [(float(t), float(v)) for t, v in entry.get("values", [])]
        if points:
            series[(node, device)] = points
    return series


def fit_slope(points):
    """Least-squares bytes-per-day through (epoch seconds, bytes).

    Returns None when there is nothing to fit — fewer than two distinct
    timestamps is not a slow trend, it is no trend.
    """
    if len(points) < 2:
        return None
    n = float(len(points))
    mean_t = sum(t for t, _ in points) / n
    mean_v = sum(v for _, v in points) / n
    denominator = sum((t - mean_t) ** 2 for t, _ in points)
    if denominator == 0:
        return None
    numerator = sum((t - mean_t) * (v - mean_v) for t, v in points)
    return (numerator / denominator) * 86400.0


def read_trends(base=PROMETHEUS, opener=urllib.request.urlopen, hours=TREND_HOURS,
                now=None):
    """Per node: the fitted slope of its root disk, its span and its sample count.

    Returns a dict of node -> dict, or raises OSError/ValueError if Prometheus
    could not be read at all. A node Prometheus has no series for is simply
    absent, which the report says out loud rather than treating as flat.
    """
    devices = root_devices(base=base, opener=opener)
    series = root_usage_series(base=base, opener=opener, hours=hours, now=now)
    trends = {}
    for node, (device, limit) in devices.items():
        points = series.get((node, device))
        if not points or len(points) < TREND_MIN_SAMPLES:
            continue
        slope = fit_slope(points)
        if slope is None:
            continue
        trends[node] = {
            "device": device,
            "capacity": limit,
            "slope_per_day": slope,
            "span_hours": (points[-1][0] - points[0][0]) / 3600.0,
            "samples": len(points),
            "first": points[0][1],
            "last": points[-1][1],
        }
    return trends


def days_to_eviction(filesystem, kind, slope_per_day):
    """How long until this filesystem reaches the kubelet's own action point.

    None when it is not heading there — a flat or shrinking disk has no date,
    and saying "never" would be a claim about the future rather than the fit.
    """
    if slope_per_day is None or slope_per_day <= 0:
        return None
    available = filesystem.get("availableBytes")
    capacity = filesystem.get("capacityBytes")
    if not available or not capacity:
        return None
    floor = capacity * EVICTION_PCT[kind] / 100.0
    headroom = available - floor
    if headroom <= 0:
        return 0.0
    return headroom / slope_per_day


def report_trend(node, kind, filesystem, trend, out=print):
    """One TREND line, or one line saying why there is not one."""
    if trend is None:
        out(
            "  NO TREND   %s %s — prometheus has no stored series for this node, so this is current state only"
            % (node, kind)
        )
        return
    capacity = filesystem.get("capacityBytes")
    if capacity and abs(trend["capacity"] - capacity) > capacity * 0.01:
        out(
            "  NO TREND   %s %s — prometheus's largest `/` filesystem is %s (%s) and the kubelet reports %s; not fitting a line through a different disk"
            % (node, kind, trend["device"], _gib(trend["capacity"]), _gib(capacity))
        )
        return
    per_day = trend["slope_per_day"] / GIB
    span = trend["span_hours"]
    shape = "growing by %+.2fGiB/day" % per_day if trend["slope_per_day"] > 0 else (
        "flat or shrinking (%+.2fGiB/day)" % per_day
    )
    tail = ""
    days = days_to_eviction(filesystem, kind, trend["slope_per_day"])
    if days is None:
        tail = " — not heading for the %.1f%%-free point the kubelet acts at" % (
            EVICTION_PCT[kind],
        )
    elif span < TREND_MIN_SPAN_HOURS:
        tail = (
            " — no projection from a span under %.1fh; that is short enough for one image pull to be the whole slope"
            % TREND_MIN_SPAN_HOURS
        )
    else:
        tail = " — %.1f day(s) to the %.1f%%-free point the kubelet acts at" % (
            days,
            EVICTION_PCT[kind],
        )
    out(
        "  TREND      %s %s: %s over the last %.1fh (%d samples, %s used at the start and %s at the end)%s"
        % (
            node,
            kind,
            shape,
            span,
            trend["samples"],
            _gib(trend["first"]),
            _gib(trend["last"]),
            tail,
        )
    )


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

#: Host directories the kubelet cannot attribute. Container images and each
#: Pod's ephemeral bytes already have their own line above; everything else on
#: the disk lands in the `neither` remainder, and until now the only way to see
#: inside that remainder was to hand-run a Job per node. `/var/lib/rancher/k3s/storage`
#: is where local-path puts every volume in this cluster, and the rest are the
#: host's own — the journal, the attic this loop archives volumes into before it
#: moves them, and the home directories nothing here provisions.
#:
#: They must stay disjoint: `report_host_breakdown` adds them up to say how much
#: of the remainder is still unnamed, and a directory inside another one here
#: would be counted twice and make that subtraction lie. `/usr` and `/swapfile`
#: were added Cycle 1010 after a live read of server1 found the named ones
#: covering 15.3GiB of a 30.3GiB remainder — those two are 4.9GiB of the rest,
#: and neither is under any other entry.
HOST_DIRS = (
    "/var/log/journal",
    "/var/lib/nova-attic",
    "/var/lib/rancher/k3s/storage",
    "/root",
    "/usr",
    "/swapfile",
    "/home",
    "/opt",
    "/var/cache",
    "/tmp",
    "/srv",
)

#: Long enough for `du` over the local-path storage tree -- the live read on
#: server1 finished well inside a minute -- and short enough that a node whose
#: disk is wedged does not hold the sweep open.
HOST_READ_SECONDS = 90

#: The budget for *all* of this run's host reads together, not per node.
#: `tools.preflight` kills a check at 240s and calls the result unreadable, so a
#: per-node timeout multiplies: three nodes raising at once would turn a real
#: FILLING finding (exit 2) into a hung check (exit 1) at exactly the moment the
#: finding matters. One shared deadline cannot do that however many nodes raise.
HOST_READ_BUDGET_SECONDS = 150


def host_breakdown_command(dirs=HOST_DIRS, mount="/host"):
    """The shell the read-only Job runs. Pure, so the parser has a fixed input.

    `du -xsk` and not `-m`: kibibytes are what busybox and GNU agree on, and
    rounding to whole mebibytes inside the container would throw away the only
    resolution this has. `-x` keeps it on the node's own filesystem, so a
    bind-mounted volume is not counted twice.
    """
    parts = []
    for directory in dirs:
        target = mount + directory
        parts.append(
            'printf "%s " "{d}"; du -xsk "{t}" 2>/dev/null | cut -f1 || true; echo'.format(
                d=directory, t=target
            )
        )
    return "; ".join(parts)


def parse_host_breakdown(logs):
    """`<path> <kibibytes>` lines into `{path: bytes}`, skipping what was absent.

    A directory that does not exist on this node prints its own name and no
    number. That is not a failure and it is not a zero — `/home` is empty on a
    box with no human users and missing on another — so it is left out rather
    than reported as nothing.
    """
    sizes = {}
    for line in (logs or "").splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        path, size = fields
        if not path.startswith("/") or not size.isdigit():
            continue
        sizes[path] = int(size) * 1024
    return sizes


def read_host_breakdown(node, runner=subprocess.run, wait=HOST_READ_SECONDS):
    """Run the read-only Job on `node` and return `{path: bytes}`.

    Raises `OSError` or `ValueError` if the Job could not be run or said
    nothing — the caller prints that rather than treating an empty answer as a
    disk with nothing on it.
    """
    manifest = oneoff_job.build_manifest(
        "disk-host-dirs-" + node,
        host_breakdown_command(),
        node=node,
        hostpath="/",
        mount_path="/host",
    )
    name = manifest["metadata"]["name"]
    namespace = manifest["metadata"]["namespace"]

    # A previous run of this same node's Job may still be inside its TTL, and
    # `kubectl apply` cannot change a Job's pod template. Delete first so the
    # answer is this sweep's, never the last one's.
    runner(
        ["kubectl", "delete", "job", name, "-n", namespace, "--ignore-not-found=true"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    applied = runner(
        ["kubectl", "apply", "-f", "-"],
        input=json.dumps(manifest),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if applied.returncode != 0:
        raise OSError((applied.stderr or "").strip() or "kubectl apply failed")
    runner(
        [
            "kubectl",
            "wait",
            "--for=condition=complete",
            "--timeout=%ds" % wait,
            "job/" + name,
            "-n",
            namespace,
        ],
        capture_output=True,
        text=True,
        timeout=wait + 30,
    )
    logs = runner(
        ["kubectl", "logs", "job/" + name, "-n", namespace, "--tail=100"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    sizes = parse_host_breakdown(logs.stdout)
    if not sizes:
        raise ValueError(
            (logs.stderr or "").strip() or "the Job printed no directory it could measure"
        )
    return sizes


def budgeted_host_reader(runner=subprocess.run, budget=HOST_READ_BUDGET_SECONDS,
                         clock=time.monotonic, read=None):
    """One reader whose whole run shares a single deadline.

    Returns a callable of `node`. The first node to raise may take the whole
    budget; a later one is refused rather than allowed to push the check past
    the point where `tools.preflight` stops calling it a check at all.
    """
    read = read or read_host_breakdown
    deadline = clock() + budget

    def reader(node):
        left = deadline - clock()
        if left < 10:
            raise OSError(
                "the %ds host-directory budget for this run is spent, so this node was not read"
                % budget
            )
        return read(node, runner=runner, wait=int(min(HOST_READ_SECONDS, left)))

    return reader


def report_host_breakdown(node, sizes, out=print, remainder=None):
    """Print what the unattributed remainder is actually made of, biggest first.

    `remainder` is the `neither` figure from the MADE OF line. Pass it and the
    last line says how much of that these directories actually account for --
    which is the question a cycle reading a FILLING node asks next, and which it
    otherwise has to do by hand off two numbers printed several lines apart. On
    the read that prompted this, the named directories covered 15.3GiB of a
    30.3GiB remainder and nothing said so, so the honest reading of the report
    was "half the disk is named" and the reading it invited was "this is all of
    it".
    """
    for path, size in sorted(sizes.items(), key=lambda kv: -kv[1]):
        out("  HOST DIR   %s %s — %s" % (node, path, _measured_gib(size)))
    missing = [d for d in HOST_DIRS if d not in sizes]
    if missing:
        out(
            "  HOST DIR   %s: %d of %d looked-for director(y/ies) do not exist on this node and are not zero: %s"
            % (node, len(missing), len(HOST_DIRS), ", ".join(missing))
        )
    if remainder is None or remainder <= 0:
        # No remainder to divide up: either the caller had none to give, or the
        # named parts already summed past the disk's used total and the MADE OF
        # line has already said that arithmetic is not a measurement.
        return
    named = sum(sizes.values())
    unnamed = remainder - named
    if unnamed <= 0:
        out(
            "  HOST DIR   %s: the directories above name %s, which is all of the %s the kubelet could not attribute — these three figures are sampled seconds apart, so the sum can run slightly past it."
            % (node, _measured_gib(named), _measured_gib(remainder))
        )
        return
    out(
        "  HOST DIR   %s: the directories above name %s of that %s; %s is in directories this does not look at."
        % (node, _measured_gib(named), _measured_gib(remainder), _measured_gib(unnamed))
    )


def report(node, filesystems, volumes, out=print, breakdown=None, host_reader=None,
           trend=None, trend_read=True):
    """Print one node's verdict. Returns the number of findings on it."""
    findings = 0
    filling = False
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
            filling = True
            out(
                "  FILLING    %s — under %.1f%% free, and the kubelet acts at %.1f%%"
                % (line, raises_at(kind), EVICTION_PCT[kind])
            )
        else:
            out("  ok         %s" % line)

    if trend_read:
        nodefs = filesystems.get("nodefs")
        if nodefs is not None and available_pct(nodefs) is not None:
            report_trend(node, "nodefs", nodefs, trend, out=out)
            if not shared and filesystems.get("imagefs") is not None:
                out(
                    "  NO TREND   %s imagefs — a separate disk from nodefs, and the stored series only covers `/`"
                    % node
                )

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

    # Only when this node actually raised. Naming the host directories costs a
    # Job on the node — tens of seconds — and on a normal morning it answers a
    # question nobody asked, which is the whole reason `preflight` is one call
    # rather than fifty. The moment it is worth paying for is the moment the
    # check is about to say FILLING and a cycle has to go and find out what of.
    if filling and host_reader is not None:
        try:
            sizes = host_reader(node)
        except (OSError, ValueError) as exc:
            # The node already raised, so the exit status is settled; this is
            # the detail line, and saying it could not be read is the honest
            # version of not printing it.
            out(
                "  NOT READ   %s host directories — %s. The remainder above stays unattributed."
                % (node, exc)
            )
        else:
            report_host_breakdown(
                node,
                sizes,
                out=out,
                remainder=breakdown["rest"] if breakdown else None,
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


def main(argv=None, runner=subprocess.run, out=print, host_reader=None,
         trend_reader=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--node",
        action="append",
        help="only this node (repeatable); default is every node the API server lists",
    )
    args = parser.parse_args(argv)

    if host_reader is None:
        host_reader = budgeted_host_reader(runner=runner)
    if trend_reader is None:
        trend_reader = read_trends

    trends = {}
    trend_read = True
    trend_error = None
    try:
        trends = trend_reader()
    except (OSError, ValueError, KeyError) as exc:
        trend_read = False
        trend_error = str(exc)

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
            host_reader=host_reader,
            trend=trends.get(node),
            trend_read=trend_read,
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
    if not trend_read:
        out(
            "NOT JUDGED  whether a disk is filling — prometheus was unreadable (%s), so this run is current state only, exactly as it was before the stored series was wired in."
            % trend_error
        )
    else:
        trended = sorted(n for n in nodes if n in trends)
        blind = sorted(n for n in nodes if n not in trends)
        out(
            "TREND READ  %d of %d node(s) got a fitted slope off prometheus's stored series (%s)%s. The slope is reported; it does not change the exit status, which still turns on free space alone."
            % (
                len(trended),
                len(nodes),
                ", ".join(trended) or "none",
                "" if not blind else "; no series for %s" % ", ".join(blind),
            )
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
