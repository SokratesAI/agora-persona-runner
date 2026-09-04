"""Has a workload here been killed, restarted, or left with nobody serving?

Cycle 615, on my own unboarded capture from this morning. The persona
runner was **OOMKilled** at 06:42 Oslo — a 256Mi limit against 34Mi
idle — and its Deployment then reported
`Available: False, MinimumReplicasUnavailable` for well over half an
hour, so Agora answered no persona at all. Nothing said so. I found it
by accident, because a conversation I had created to test something
else never got a reply.

    python3 -m tools.workload_health

**None of the eighteen checks step 1a already runs would have seen it.**
`argocd_health` reads Application health, `running_images` reads image
references, `crossplane_health` reads managed resources, `helm_repo_health`
reads chart indexes. Not one of them reads `restartCount`,
`lastState.terminated.reason`, or a Deployment's `Available` condition —
the three fields that say a container died and that nobody is serving.
`kubectl get pods -A` is in the delegated read brief, which is a summary
written by a subagent, and Cycle 592 already measured that one of those
came back falsely clean while a pod crash-looped.

**Cycle 616 added the layer under all of that: the host's own memory.**
Everything above reads what a *container* did — a death, a restart, a
Deployment with nobody serving. None of it can see the machine those
containers are competing for. On 2026-08-29 server1 had **487Mi of 7746Mi
available and 0Mi of 2048Mi swap free**, the k3s control plane restarted at
07:52 Oslo, ten pods across every namespace went with it, and `crossplane`
and `crossplane-rbac-manager` exited 1 on leader-election lease timeouts
against an API server that could not answer. All I could have reported was
the wreckage. So it reads `/proc/meminfo` — not `kubectl top node`, which
counts reclaimable page cache as used and cannot see swap at all — and
asks one question: **can this host still start the largest container it is
configured to run?** That threshold is read off the cluster rather than
chosen, the same call `read_deployments` makes on the drain budget; a
number I picked would be a number nobody could argue with.

**And it checks that the reading is the host's before it judges it.**
`/proc/meminfo` in a pod is the host's only because nothing here mounts
lxcfs — measured, MemTotal 7931600 kB against server1's capacity of
7931600Ki. If that stops being true the file starts describing a
container and every percentage would be about the wrong machine while
looking perfectly reasonable, so the equality is asserted against the
node's own capacity and an unmatched reading exits 1 rather than 0.

**Cycle 706 added the budget beside the headroom, because every
per-container question can pass while the node is over its own ceiling.**
The check above asks whether the largest single container can still start.
On 2026-08-31 it could -- 2048Mi against 1889Mi available was the one red,
and the largest limit was the only limit anyone had compared to anything.
Meanwhile the memory limits declared across the node summed to 7978Mi on a
7746Mi box, 103%, and 27 further running containers declared no limit at
all, so that sum is a floor rather than a ceiling. Nothing stops them all
claiming what they are allowed at once; the scheduler only ever guarded
*requests*, which came to 2519Mi, a third of the box. That is why server1
can look comfortably scheduled and still OOMKill the persona runner
(issue #130), and it is the half of issue #131 that needs no host shell.

**It reads the live cluster, never git** — the same call `helm_repo_health`
and `argocd_health` make, and for the same reason: a manifest ArgoCD has
not synced is not what is running.

**Four verdicts, kept separate on purpose.** A container that died for a
reason (`OOMKilled`, `Error`), a Pod that is not Ready now, a Deployment
with no available replica, and a Pod still Terminating past the instant
Kubernetes promised it would be gone are four different problems with four
different fixes, and merging them into one red number is the failure
`agentic_health` had to learn one layer down.

**The one judgement it makes, and it is the whole reason this is usable.**
`agora-persona-runner` is `Recreate` with a 2880-second termination grace
so a running cycle can finish, which means every ordinary rollout of it
reports `Available: False` for up to 48 minutes **by design**. A check that
called that an outage would be red on every deploy, including its own. So
an unavailable Deployment is judged against **its own drain budget**, read
off the object — the longest `terminationGracePeriodSeconds` in its pod
template, plus a five-minute margin for pull and start. Inside that budget
it prints as `ROLLING` and deliberately does not raise; past it, the
rollout is not a rollout any more and it raises. Same shape as
`schedule_health` deriving its window from the cron it is judging rather
than from a constant someone picked.

**And the boundary this buys it, printed rather than hidden.** A restart
that already healed is history, and a check that goes red on history is red
forever — `cycle_postmortem` says so in its own footer. `lastState` keeps a
container's last death for the whole life of the Pod, which here is weeks,
so a death alone cannot be the trigger. What raises is a container that is
**down now**, at any age, or one that died **inside the recent window** and
has since come back; an older death on a container that is Ready again
prints under `DIED AND RECOVERED` and does not raise. The window is one
hour, which is three cycles at the current 20-minute cadence — long enough
that a death is shown to a cycle that can still act on it, short enough
that it is not shown to seventy. **"Down now" is read off a Pod that is
still running**, because a Pod in a terminal phase — a Job's, after its
run — has every container `ready: false` forever, and reading that as an
outage is how one `marcus-backup` run that failed at 13:20 was still being
reported as a live incident fifteen hours and three successful runs later
(Cycle 882). **A Pod that
was replaced outright carries no trace at all**: `Recreate` makes a new
Pod, so the OOMKill that started this loses its `lastState` the moment the
replacement starts, and `restartCount` on the new Pod is 0. That case is
covered by the availability verdict and not by this one, which is exactly
why the two are separate.

**Readiness is judged on the waiting reason, never on the Pod phase.** My
first version skipped `Pending` wholesale, to avoid firing on a Pod that
was merely a few seconds old — and that silently swallowed
`ImagePullBackOff`, which is `Pending`, permanent, and exactly the kind of
thing this exists to catch. `ContainerCreating` and `PodInitializing` are
the transient reasons; everything else raises whatever phase it is in. A
Pod that cannot be scheduled at all has no container statuses to read, so
that one case is judged on the Pod's own `PodScheduled` condition.

A workload scaled to 0 replicas on purpose — `whatsapp-bridge` is parked
there until 1 September — has nothing unavailable about it and is not
raised.

Exit contract, the same as every other step-1a check: **2 means something
is broken now**, 1 means the cluster could not be read — which includes
finding no Pods at all, since this cluster demonstrably runs some, and
never reads as clean — 0 means everything swept is up.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
import zoneinfo

#: Margin over a workload's own termination grace, for image pull and
#: container start. Small next to the 2880s grace it is added to; it exists
#: so a Deployment is not called broken in the seconds between the old Pod
#: exiting and the new one passing its first probe.
START_MARGIN = datetime.timedelta(minutes=5)

#: Margin past a Pod's own deletion deadline before it is called stuck.
#: `metadata.deletionTimestamp` is the *deadline*, not the moment the
#: delete was asked for -- the API server sets it to now plus the grace
#: period -- so once it is in the past the kubelet has already sent
#: SIGKILL and the only thing still owed is the status write. This bounds
#: that write, so it has to exceed the kubelet's own housekeeping period
#: (`--sync-frequency`, 1 minute by default) rather than be a number I
#: liked the look of. Two of those.
KILL_MARGIN = datetime.timedelta(minutes=2)

#: A container that exits 0 because its work is done is not a failure.
BENIGN_TERMINATION = {"Completed"}

#: Rule 7: anything a person reads is Oslo, never raw UTC. Kubernetes
#: stamps everything in UTC and this report exists so a person can
#: reconstruct an outage timeline -- "OOMKilled at 06:42 Oslo" is the
#: sentence it has to support, and handing over the UTC value makes the
#: reader do the conversion that got Cycle 446's report two hours wrong.
OSLO = zoneinfo.ZoneInfo("Europe/Oslo")

#: A container that died once and came back had a bad moment. One that has
#: died many times and died again just now is in a loop, and the two want
#: different sentences -- merging them is the failure `agentic_health` had
#: to learn one layer down, where a streak counter read as a diagnosis.
#: The threshold is 2 because a single OOMKill leaves restartCount at 1.
REPEATING = 2

#: Waiting reasons that mean "give it a moment", not "this is broken".
#: Everything else — ImagePullBackOff, CrashLoopBackOff, ErrImagePull,
#: CreateContainerConfigError — is a state a Pod stays in until somebody
#: acts, so it raises regardless of the phase it wears.
TRANSIENT_WAITING = {"ContainerCreating", "PodInitializing"}

# A Pod in one of these phases is finished. Every container in it is
# `ready: false` and will stay that way forever, so readiness there says
# nothing about whether anything is down right now -- it is the normal
# resting state of a Job Pod that has already run. Three separate places
# below read `ready` as "is this serving", and on a terminal Pod all three
# were wrong in the same way: measured 2026-09-04, one `marcus-backup` run
# that failed at 13:20 the previous day was still being reported as a live
# outage fifteen hours and three successful runs later.
TERMINAL_PHASES = {"Succeeded", "Failed"}

#: How recently a container must have died for its death to still be news,
#: given it has come back up since. Three cycles at the 20-minute cadence.
#: A container that is down *now* raises at any age; this bounds only the
#: recovered ones, because `lastState` holds them for the life of the Pod.
RECENT_DEATH = datetime.timedelta(hours=1)


#: Below this share of swap left, the kernel has no overflow to spend: the
#: next spike is an OOM kill rather than a slowdown. Swap is the shock
#: absorber, so "nearly full" is the finding and "full" is too late --
#: measured on server1 on 2026-08-29, swap was 224kB of 2GiB free while the
#: control plane was falling over.
SWAP_NEARLY_GONE = 0.10

#: Where the host's memory is read from. This is `/proc/meminfo` rather than
#: `kubectl top node` on purpose: `top` reports the root cgroup's working set,
#: which counts reclaimable page cache as used and cannot see swap at all.
MEMINFO = "/proc/meminfo"

#: The cgroup every Pod on the node lives under, on a systemd-cgroup runtime.
#: `read_pod_working_set` already counts what is inside it, so the breakdown
#: in `host_cgroup_shares` treats it as one block and names only what is not.
KUBEPODS = "/kubepods.slice"


def _run(runner, args):
    try:
        proc = runner(args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl failed: {exc}"
    if proc.returncode != 0:
        return None, f"kubectl failed: {proc.stderr.strip() or proc.stdout.strip()}"
    try:
        body = json.loads(proc.stdout)
    except ValueError as exc:
        return None, f"kubectl returned something that is not JSON: {exc}"
    if not isinstance(body, dict):
        return None, "kubectl returned JSON that is not an object"
    return body, None


def read_pods(runner=subprocess.run):
    """Every Pod in the cluster, as (list, None) or (None, why)."""
    body, why = _run(runner, ["kubectl", "get", "pods", "-A", "-o", "json"])
    if why:
        return None, why

    pods = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        limits = {}
        for container in spec.get("containers") or []:
            mem = ((container.get("resources") or {}).get("limits") or {}).get("memory")
            if mem:
                limits[container.get("name") or "?"] = mem
        pods.append({
            "namespace": meta.get("namespace") or "",
            "name": meta.get("name") or "?",
            "phase": status.get("phase") or "Unknown",
            "containers": status.get("containerStatuses") or [],
            "conditions": status.get("conditions") or [],
            "limits": limits,
            # How many containers the Pod declares, not how many set a limit.
            # `limits` only records the ones that did, so the two counts
            # together are what say a limit is missing rather than zero.
            "container_count": len(spec.get("containers") or []),
            # Which node it is actually on. Empty for a Pod the scheduler has
            # not placed yet, and that is a real state rather than a gap --
            # an unscheduled Pod is on nobody's budget.
            "node": spec.get("nodeName") or "",
            # Set only while the Pod is Terminating, and it is a deadline
            # rather than a start time -- see KILL_MARGIN.
            "deletion": meta.get("deletionTimestamp") or "",
        })
    return pods, None


def read_deployments(runner=subprocess.run):
    """Every Deployment, with the two things that decide availability.

    `grace` is the longest `terminationGracePeriodSeconds` in the pod
    template, because that is how long the *old* Pod may legitimately keep
    the new one out under a `Recreate` strategy. It is read off the object
    rather than tabulated here: a table of "what we know is slow" is a
    second copy of the truth that goes stale the way the value it mirrors
    does.
    """
    body, why = _run(runner, ["kubectl", "get", "deployments", "-A", "-o", "json"])
    if why:
        return None, why

    deployments = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        template_spec = ((spec.get("template") or {}).get("spec")) or {}
        available = None
        since = ""
        reason = ""
        for cond in status.get("conditions") or []:
            if cond.get("type") == "Available":
                available = cond.get("status") == "True"
                since = cond.get("lastTransitionTime") or ""
                reason = cond.get("reason") or ""
        deployments.append({
            "namespace": meta.get("namespace") or "",
            "name": meta.get("name") or "?",
            "replicas": spec.get("replicas"),
            "strategy": ((spec.get("strategy") or {}).get("type")) or "RollingUpdate",
            "grace": template_spec.get("terminationGracePeriodSeconds"),
            "available": available,
            "since": since,
            "reason": reason,
        })
    return deployments, None


def _parse(stamp):
    if not stamp:
        return None
    try:
        return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _oslo(stamp):
    """A UTC stamp from Kubernetes, as Oslo local time for a person to read."""
    at = _parse(stamp)
    if at is None:
        return stamp or "an unrecorded time"
    return f"{at.astimezone(OSLO):%Y-%m-%d %H:%M} Oslo"


def _duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def deaths(pods, now, window=RECENT_DEATH):
    """Containers whose most recent termination was not a clean exit.

    Reads `lastState.terminated` (the container restarted in place) and
    `state.terminated` (it is down right now). A Pod replaced outright
    carries neither — see the module docstring.
    """
    fresh, old = [], []
    for pod in pods:
        for container in pod["containers"]:
            # A container that died, restarted and died again carries BOTH
            # `state.terminated` and `lastState.terminated`. It is one
            # container and must be one row -- reporting it twice makes the
            # heading's count a count of deaths under the word "containers".
            # `state` is the newer of the two, so it wins.
            current = (container.get("state") or {}).get("terminated") or {}
            previous = (container.get("lastState") or {}).get("terminated") or {}
            term = current if current.get("reason") else previous
            reason = term.get("reason")
            if not reason or reason in BENIGN_TERMINATION:
                continue
            also = previous.get("reason") if current.get("reason") else None
            if also in BENIGN_TERMINATION or also == reason:
                also = None
            ready = bool(container.get("ready"))
            # `not ready` is what keeps a death loud after the window closes,
            # and it is only a live signal on a Pod that is still running.
            terminal = pod["phase"] in TERMINAL_PHASES
            at = _parse(term.get("finishedAt"))
            recent = at is None or (now - at) <= window
            row = {
                "namespace": pod["namespace"],
                "pod": pod["name"],
                "container": container.get("name") or "?",
                "reason": reason,
                "also": also,
                "exit_code": term.get("exitCode"),
                "at": term.get("finishedAt") or "",
                "restarts": container.get("restartCount") or 0,
                "limit": pod["limits"].get(container.get("name") or "?"),
                "ready": ready,
                "terminal": terminal,
            }
            (fresh if ((not ready and not terminal) or recent) else old).append(row)
    return fresh, old


def not_ready(pods):
    """Pods that are meant to be serving and are not.

    A Pod in a terminal phase is excluded -- `Succeeded` is a finished
    Job's Pod, and `Failed` is one whose run ended non-zero. Neither is
    going to serve again, and the death itself is already reported by
    `deaths`; listing it here says an outage is happening now, which is a
    different claim and a false one. A Deployment left with nobody serving
    is caught by `unavailable`, which reads the Deployment rather than the
    corpse of a Pod. Everything else is
    judged on the container's waiting *reason* rather than on the Pod's
    phase, because `ImagePullBackOff` is `Pending` and permanent while
    `ContainerCreating` is `Pending` and over in seconds.

    A Pod that was never scheduled has no container statuses at all, so it
    cannot be judged that way; its own `PodScheduled` condition is read
    instead.
    """
    found = []
    for pod in pods:
        if pod["phase"] in TERMINAL_PHASES:
            continue
        for container in pod["containers"]:
            if container.get("ready"):
                continue
            waiting = (container.get("state") or {}).get("waiting") or {}
            reason = waiting.get("reason") or ""
            if reason in TRANSIENT_WAITING:
                continue
            found.append({
                "namespace": pod["namespace"],
                "pod": pod["name"],
                "container": container.get("name") or "?",
                "phase": pod["phase"],
                "reason": reason or "not ready",
                "message": (waiting.get("message") or "").strip(),
                "restarts": container.get("restartCount") or 0,
            })
        if pod["containers"]:
            continue
        for cond in pod["conditions"]:
            if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
                found.append({
                    "namespace": pod["namespace"],
                    "pod": pod["name"],
                    "container": "-",
                    "phase": pod["phase"],
                    "reason": cond.get("reason") or "not scheduled",
                    "message": (cond.get("message") or "").strip(),
                    "restarts": 0,
                })
    return found


def healed_restarts(pods, deaths_found):
    """Ready containers with a nonzero restart count and no visible cause.

    Context, never a finding: the container is up now, and the reason it
    went down has already aged out of `lastState`.
    """
    named = {(d["namespace"], d["pod"], d["container"]) for d in deaths_found}
    found = []
    for pod in pods:
        for container in pod["containers"]:
            count = container.get("restartCount") or 0
            key = (pod["namespace"], pod["name"], container.get("name") or "?")
            if count and container.get("ready") and key not in named:
                found.append({
                    "namespace": pod["namespace"],
                    "pod": pod["name"],
                    "container": key[2],
                    "restarts": count,
                })
    return found


def unavailable(deployments, now):
    """Deployments with no available replica, split by their own budget.

    Returns (past_budget, rolling). A Deployment asked for 0 replicas has
    nothing unavailable about it and appears in neither.
    """
    past, rolling = [], []
    for dep in deployments:
        if dep["available"] is not False:
            continue
        if not dep["replicas"]:
            continue
        grace = dep["grace"] if isinstance(dep["grace"], int) else 30
        budget = datetime.timedelta(seconds=grace) + START_MARGIN
        since = _parse(dep["since"])
        row = dict(dep, budget=budget, down=None)
        if since is None:
            # No transition time is not permission to assume it just
            # started: an unavailable Deployment with no clock on it is
            # the loud case, not the quiet one.
            past.append(row)
            continue
        row["down"] = now - since
        # A stamp in the future is clock skew, not a rollout that started
        # later than now. Treating it as inside the budget would make an
        # outage read as a healthy deploy, which is the quiet direction.
        if row["down"] < datetime.timedelta(0):
            past.append(row)
            continue
        (rolling if row["down"] <= budget else past).append(row)
    return past, rolling


def terminating(pods, now):
    """Pods being deleted, split by whether their own deadline has passed.

    Returns (past_deadline, draining). `deletionTimestamp` is the instant
    Kubernetes promised the Pod would be gone, so this needs no table of
    which workloads are slow: `agora-persona-runner` drains a running cycle
    for 48 minutes and is quiet for all of them, and a `coredns` Pod on a
    30-second grace is loud two and a half minutes in. That split is the
    whole point -- Sokrates reported a runner Pod "stuck in Terminating for
    ~20min, not responding to SIGTERM" on 2026-09-03, and that Pod was
    working exactly as designed with 28 minutes still to go.

    Past the deadline the kubelet has already sent SIGKILL, so what is left
    is not a slow shutdown: it is a kubelet that cannot be reached, a
    finalizer nobody will clear, or a node that is gone.
    """
    past, draining = [], []
    for pod in pods:
        if not pod.get("deletion"):
            continue
        deadline = _parse(pod["deletion"])
        row = dict(pod, deadline=deadline, over=None, left=None)
        if deadline is None:
            # A Pod that is demonstrably being deleted and carries no
            # readable deadline is the loud case: there is no clock that
            # would let it be called normal.
            past.append(row)
            continue
        if now > deadline + KILL_MARGIN:
            row["over"] = now - deadline
            past.append(row)
        else:
            row["left"] = deadline - now
            draining.append(row)
    return past, draining


def _mib(quantity):
    """A Kubernetes memory quantity as MiB, or None if it is not one."""
    if not quantity:
        return None
    text = str(quantity).strip()
    for suffix, factor in (("Ki", 1 / 1024), ("Mi", 1), ("Gi", 1024), ("Ti", 1024 * 1024),
                           ("K", 1000 / 1048576), ("M", 1000 ** 2 / 1048576),
                           ("G", 1000 ** 3 / 1048576), ("T", 1000 ** 4 / 1048576)):
        if text.endswith(suffix):
            try:
                return float(text[: -len(suffix)]) * factor
            except ValueError:
                return None
    try:
        return float(text) / 1048576
    except ValueError:
        return None


def read_meminfo(path=MEMINFO):
    """`/proc/meminfo` as {name: kB}, or (None, why)."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"could not read {path}: {exc}"
    fields = {}
    for line in raw.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            fields[name.strip()] = float(parts[0])
        except ValueError:
            continue
    if "MemTotal" not in fields:
        return None, f"{path} carries no MemTotal"
    return fields, None


def read_node_capacity(runner=subprocess.run):
    """{node name: memory capacity in kB}, or (None, why)."""
    body, why = _run(runner, ["kubectl", "get", "nodes", "-o", "json"])
    if why:
        return None, why
    nodes = {}
    for item in body.get("items") or []:
        name = (item.get("metadata") or {}).get("name") or "?"
        capacity = ((item.get("status") or {}).get("capacity") or {}).get("memory")
        mib = _mib(capacity)
        if mib is not None:
            nodes[name] = mib
    return nodes, None


def read_pod_working_set(runner=subprocess.run):
    """Sum of every Pod's memory working set in MiB, or (None, why).

    `kubectl top` rather than the Pod spec on purpose: requests and limits say
    what a Pod is *allowed*, and the question here is what it is *using*.
    """
    try:
        proc = runner(["kubectl", "top", "pods", "-A", "--no-headers"],
                      capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl top failed: {exc}"
    if proc.returncode != 0:
        return None, f"kubectl top failed: {proc.stderr.strip() or proc.stdout.strip()}"
    total, counted = 0.0, 0
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        mib = _mib(parts[3])
        if mib is None:
            continue
        total += mib
        counted += 1
    if not counted:
        # A working kubectl that returned no rows is no instrument, not zero.
        return None, "kubectl top returned no Pod rows"
    return (total, counted), None


#: The one cAdvisor series that answers "which cgroup is holding it".
#: `container_memory_working_set_bytes` carries a line per cgroup on the node,
#: not just per Pod, so the machine-level slices -- `/`, `/kubepods.slice`,
#: `/system.slice/<unit>.service` -- are all in it. The Pod lines carry a
#: non-empty `pod` label and are dropped here: `read_pod_working_set` already
#: counts those, and counting them twice is how a breakdown stops adding up.
CADVISOR_SERIES = "container_memory_working_set_bytes"

#: Working set is not demand. It counts the active page cache a cgroup holds,
#: which the kernel reclaims before it kills anything -- the same trap
#: `memory.peak` set on Cycle 711, one level up. `container_memory_rss` is the
#: anonymous part the kernel cannot hand back, and `container_memory_swap` is
#: the part it already pushed to disk. Issue #131's headline is that this box's
#: 2GB of swap is full, and only the swap series can say whose it is.
CADVISOR_RSS_SERIES = "container_memory_rss"
CADVISOR_SWAP_SERIES = "container_memory_swap"

#: All three are read in one pass over one fetch on purpose. cAdvisor samples
#: each cgroup at its own instant, so a second fetch would be a second sample
#: and the parts would disagree with each other for a reason that has nothing
#: to do with the machine -- which is the exact failure the negative-remainder
#: branch below already exists to catch.
CADVISOR_ALL_SERIES = (CADVISOR_SERIES, CADVISOR_RSS_SERIES, CADVISOR_SWAP_SERIES)

_CADVISOR_LINE = re.compile(
    r"^(?P<series>[a-z_]+)\{(?P<labels>[^}]*)\}\s+(?P<value>[^\s]+)")
_CADVISOR_LABEL = re.compile(r'(?P<key>[A-Za-z_][A-Za-z0-9_]*)="(?P<value>[^"]*)"')


#: The kubelet's own summary of the node it runs on. `/proc/meminfo` answers
#: the same questions and only ever for the node *this* pod stands on, which
#: is what left every other node's headroom and swap unjudged when server2
#: joined on 2026-09-03. This endpoint carries `availableBytes` -- the
#: kubelet's MemAvailable, the one field that says what can still be
#: allocated -- and a swap block, per node, for every node the API server
#: lists. Same `nodes/proxy` grant `read_node_cgroup_series` already uses.
NODE_STATS_PATH = "/api/v1/nodes/{node}/proxy/stats/summary"


def read_node_memory_stats(node, runner=subprocess.run):
    """{available_mib, swap_total_mib, swap_free_mib} for `node`, or (None, why).

    Swap total is not published: the kubelet reports what is left and what is
    used, so the total is their sum. Measured 2026-09-03 against server1 --
    1616375808 + 531103744 bytes, which is 2048Mi, the same SwapTotal
    `/proc/meminfo` reports on that host. server2 answers 0 and 0, which is a
    node with no swap at all rather than a node whose swap is full.

    A node that answers without `availableBytes` is unreadable rather than
    zero, for the same reason a missing `MemAvailable` is: the caller must be
    able to tell "nothing left" from "no instrument", and they have opposite
    fixes.
    """
    path = NODE_STATS_PATH.format(node=node)
    try:
        proc = runner(["kubectl", "get", "--raw", path],
                      capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl get --raw {path} failed: {exc}"
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        return None, f"kubectl get --raw {path} failed: {detail}"
    try:
        body = json.loads(proc.stdout)
    except ValueError as exc:
        return None, f"{path} did not answer JSON: {exc}"

    memory = (body.get("node") or {}).get("memory") or {}
    available = memory.get("availableBytes")
    if available is None:
        return None, f"{path} carries no availableBytes for {node}"
    swap = (body.get("node") or {}).get("swap") or {}
    swap_free = swap.get("swapAvailableBytes")
    swap_used = swap.get("swapUsageBytes")
    if swap_free is None or swap_used is None:
        # A kubelet built without swap accounting says nothing here. That is
        # not the same as a node with no swap, so it is carried as None and
        # `swap_line` prints a caveat instead of "none configured".
        swap_free = swap_total = None
    else:
        swap_total = (swap_free + swap_used) / 1024 / 1024
        swap_free = swap_free / 1024 / 1024
    return ({"available_mib": available / 1024 / 1024,
             "swap_total_mib": swap_total,
             "swap_free_mib": swap_free}, None)


def matching_host(meminfo, nodes):
    """The node whose capacity equals this `/proc/meminfo`, or None.

    Neither pod this loop runs in mounts lxcfs, so `/proc/meminfo` is the
    host's, and that equality is the proof rather than the assumption. Pulled
    out of `memory_headroom` in Cycle 713 because `main` needs the same answer
    to know which node to ask cAdvisor about, and re-deriving the rule in two
    places is how the two drift apart.
    """
    total = meminfo.get("MemTotal", 0) / 1024
    return next((name for name, mib in nodes.items() if abs(mib - total) < 1), None)


def read_node_cgroup_series(host, runner=subprocess.run):
    """{series: {cgroup: MiB}} for every machine-level cgroup on `host`, or (None, why).

    This is the instrument issue #131 was missing. `attribution` below can say
    how much anonymous memory sits outside every Pod cgroup -- ~1,900Mi on
    2026-08-31 -- and could not say *whose* it was, so three cycles running
    reported a number with no name attached to it. The kubelet's cAdvisor
    endpoint carries the name, and this loop could not read it until
    2026-08-31, when `nodes/proxy` was granted read-only to both of its
    service accounts (`monitoring/node-proxy-rbac.yaml`, Cycle 712).

    Ask for it in the subresource form or the answer is about a node *named*
    proxy: `kubectl auth can-i get --subresource=proxy nodes`.

    A failure here is a missing instrument, never a clean reading -- the
    caller prints why and stops, rather than falling back to a number without
    a name and calling that an attribution. A series the endpoint does not
    carry comes back as a missing key rather than an empty dict, so a caller
    cannot mistake "cAdvisor publishes no swap here" for "nothing is swapped".
    """
    path = f"/api/v1/nodes/{host}/proxy/metrics/cadvisor"
    try:
        proc = runner(["kubectl", "get", "--raw", path],
                      capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl get --raw {path} failed: {exc}"
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        return None, f"kubectl get --raw {path} failed: {detail}"

    series = {}
    for line in proc.stdout.splitlines():
        match = _CADVISOR_LINE.match(line)
        if not match:
            continue
        name = match.group("series")
        if name not in CADVISOR_ALL_SERIES:
            continue
        labels = dict(
            (m.group("key"), m.group("value"))
            for m in _CADVISOR_LABEL.finditer(match.group("labels")))
        if labels.get("pod"):
            continue
        cgroup = labels.get("id")
        if not cgroup:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        series.setdefault(name, {})[cgroup] = value / 1024 / 1024
    if not series.get(CADVISOR_SERIES):
        # A 200 that carried no such series is no instrument, not an empty
        # node: this endpoint is 3.4MB of metrics and always carries the root.
        return None, f"{path} returned no {CADVISOR_SERIES} series for any cgroup"
    return series, None


def read_node_cgroups(host, runner=subprocess.run):
    """Working set in MiB per machine-level cgroup on `host`, or (None, why).

    The working-set half of `read_node_cgroup_series`, kept as its own name
    because that is what every caller of the size breakdown wants.
    """
    series, why = read_node_cgroup_series(host, runner)
    if series is None:
        return None, why
    return series[CADVISOR_SERIES], None


def host_cgroup_shares(cgroups):
    """(root, kubepods, [(cgroup, mib), ...], unnamed) in MiB, from a cAdvisor read.

    The third element is every machine-level cgroup outside `/kubepods.slice`,
    largest first, with descendants of another listed cgroup dropped so the
    parts do not double-count their own children. `unnamed` is what the root
    holds that none of them accounts for -- the honest remainder, printed
    rather than absorbed into the largest slice.
    """
    root = cgroups.get("/")
    # The kubepods slice itself, never the sum of its children: cAdvisor
    # instruments the parent *and* the besteffort/burstable slices under it,
    # so adding them up counts every Pod on the node twice.
    kubepods = cgroups.get(KUBEPODS, 0.0)
    outside = {cgroup: mib for cgroup, mib in cgroups.items()
               if cgroup != "/" and not (cgroup == KUBEPODS
                                         or cgroup.startswith(KUBEPODS + "/"))}
    # Keep only the outermost of each chain: /system.slice and
    # /system.slice/k3s.service both appear when both are instrumented, and
    # adding them together counts k3s twice.
    tops = [(cgroup, mib) for cgroup, mib in outside.items()
            if not any(cgroup != other and cgroup.startswith(other + "/")
                       for other in outside)]
    tops.sort(key=lambda pair: pair[1], reverse=True)
    unnamed = None
    if root is not None:
        unnamed = root - kubepods - sum(mib for _, mib in tops)
    return root, kubepods, tops, unnamed


def attribution(meminfo, pod_working_set, node_cgroups=None, cgroups_why=None,
                node_swap=None, node_rss=None):
    """Where the host's memory went, as lines. Context, never a verdict.

    `memory_headroom` above says the host is full and stops there, which
    points a reader at the Pods -- the only thing every other check in this
    file can see. On 2026-08-29 that was the wrong direction: pods were using
    2,086Mi of a 7,745Mi host and `AnonPages` was 6,842Mi, so ~4,756Mi of
    anonymous process memory belonged to something outside every Pod cgroup.
    Three cycles re-derived that split by hand before it was written down.

    It does not raise. There is no pull request that fixes "the host is using
    memory", and a line that goes red on a normal machine is one nobody reads
    -- the same call `security_alerts` makes on an already-fixed advisory.
    """
    anon = meminfo.get("AnonPages")
    if anon is None:
        return ["MEMORY  cannot attribute: /proc/meminfo carries no AnonPages."]
    anon_mib = anon / 1024
    cache_mib = (meminfo.get("Cached", 0) + meminfo.get("Buffers", 0)) / 1024
    slab_mib = (meminfo.get("SUnreclaim", 0) + meminfo.get("KernelStack", 0)
                + meminfo.get("PageTables", 0)) / 1024
    lines = [
        f"MEMORY WENT  {anon_mib:.0f}Mi anonymous process memory, "
        f"{cache_mib:.0f}Mi page cache (reclaimable), "
        f"{slab_mib:.0f}Mi unreclaimable kernel (slab, stacks, page tables)."]
    if pod_working_set is None:
        lines.append(
            "  Could not split that by Pod: kubectl top was unreadable, so "
            "whether this is the workloads or the host is unmeasured here.")
        return lines
    pods_mib, counted = pod_working_set
    outside = anon_mib - pods_mib
    if outside <= 0:
        # The two numbers come from different instruments and overlap: a Pod's
        # working set counts page cache its cgroup holds, which is not anonymous.
        # So they can cross, and "-120Mi outside every Pod cgroup" is a sentence
        # with no meaning -- say what was measured instead of subtracting anyway.
        lines.append(
            f"  {counted} Pod(s) account for {pods_mib:.0f}Mi of working set, which is "
            "at or above the anonymous total — the Pods are holding page cache as "
            "well, so this host has no measurable memory outside its Pod cgroups.")
        return lines
    lines.append(
        f"  {counted} Pod(s) account for {pods_mib:.0f}Mi of working set, leaving "
        f"~{outside:.0f}Mi of anonymous memory outside every Pod cgroup — "
        "processes on the host itself (k3s, containerd, anything hand-run). "
        "A Pod's working set counts some page cache too, so that figure is a "
        "lower bound on the host's own share, not an exact one.")
    lines.extend(name_the_host_share(node_cgroups, cgroups_why, node_rss))
    lines.extend(name_the_swap(node_swap))
    return lines


def _resident(cgroup, node_rss):
    """" (N Mi of it resident)" for a named cgroup, or "" when rss is unread.

    A working set counts the active page cache a cgroup holds, and the kernel
    reclaims that before it kills anything -- so "k3s 2369Mi" on its own does
    not say whether k3s actually needs 2.4GB or is merely sitting on files it
    read. `container_memory_rss` is the anonymous part, and on server1 it is
    2118Mi of that 2359Mi, which is what makes it a real claim on the box
    rather than a reclaimable one. Same lesson as `memory.peak` on Cycle 711:
    a high number built from cache is not a finding.
    """
    if not node_rss:
        return ""
    rss = node_rss.get(cgroup)
    if rss is None:
        return ""
    return f" ({rss:.0f}Mi of it resident anonymous)"


def name_the_host_share(node_cgroups, why, node_rss=None):
    """Which cgroup holds the memory outside every Pod, as lines.

    The line above says how much and cannot say whose; this says whose. It is
    still context and still never raises -- k3s using 2.3GB is what k3s does
    on this box, and the value of naming it is that the next cycle argues
    about a named process instead of re-deriving an anonymous number.
    """
    if node_cgroups is None:
        return ["  CANNOT NAME THAT SHARE — " + (why or "cAdvisor was not read")
                + ". The size above stands; the owner of it does not."]
    root, kubepods, tops, unnamed = host_cgroup_shares(node_cgroups)
    if not tops:
        return ["  CANNOT NAME THAT SHARE — cAdvisor carries no machine-level "
                "cgroup outside " + KUBEPODS + " on this node."]
    named = ", ".join(f"{cgroup} {mib:.0f}Mi{_resident(cgroup, node_rss)}"
                      for cgroup, mib in tops)
    line = f"  Named on the node by cAdvisor: {named}."
    if root is not None:
        line += (f" Its root cgroup reads {root:.0f}Mi and {KUBEPODS} {kubepods:.0f}Mi, "
                 f"leaving {unnamed:.0f}Mi in no cgroup it breaks out.")
        if unnamed < 0:
            # Each series is sampled at its own instant, so the parts can
            # briefly exceed the root they came from. "-50Mi in no cgroup" is
            # a sentence with no meaning; say the parts do not add up instead.
            line += (" That remainder is negative, which means these readings "
                     "were taken at different instants and do not add up — "
                     "read the parts, not the difference.")
    return [line]


def name_the_swap(node_swap):
    """Which cgroup filled the swap, as lines. Context, never a verdict.

    Issue #131's own title is "its 2GB of swap is completely full", and until
    this cycle every line I printed about that was a total with nobody's name
    on it. `report` already says how much swap is gone; this says whose it is.

    The subtraction is deliberate and it is the whole point. cAdvisor charges
    swap to the cgroup that owns the pages, so `/` minus `/kubepods.slice`
    minus the named machine slices is swap held by processes in none of them --
    host daemons outside k3s, or anything hand-run. Measured on server1
    2026-08-31: 1662Mi swapped at the root, 169Mi of it k3s and 1Mi of it every
    Pod on the box together, so ~1.5GB of the full swap is neither.

    Returns nothing at all when the series is absent rather than guessing: a
    kernel built without swap accounting publishes no such series, and "0Mi
    swapped" is a very different sentence from "I could not read it".
    """
    if not node_swap:
        return []
    root = node_swap.get("/")
    if root is None:
        return ["  CANNOT NAME THE SWAP — cAdvisor publishes "
                + CADVISOR_SWAP_SERIES + " but not for the root cgroup."]
    if root <= 0:
        return [f"  Nothing on this node is swapped out: the root cgroup's "
                f"{CADVISOR_SWAP_SERIES} reads 0Mi."]
    _, kubepods, tops, unnamed = host_cgroup_shares(node_swap)
    named = ", ".join(f"{cgroup} {mib:.0f}Mi" for cgroup, mib in tops if mib >= 1) \
        or "no machine slice holds a whole MiB of it"
    line = (f"  Of the {root:.0f}Mi swapped out on this node, {KUBEPODS} holds "
            f"{kubepods:.0f}Mi and {named}")
    if unnamed is not None and unnamed >= 1:
        line += (f", leaving {unnamed:.0f}Mi swapped by processes in no cgroup "
                 "cAdvisor breaks out — host daemons outside k3s, not the workloads.")
    else:
        # Below a whole MiB, or negative because each series is sampled at its
        # own instant. Neither is a quantity worth printing, so name nobody
        # rather than name a remainder that is an artefact of the sampling.
        line += "."
    return [line]


def pods_on(pods, host):
    """The Pods the scheduler actually placed on `host`.

    Every caller below asks a question about one box -- what is the largest
    container it must be able to start, what have its containers collectively
    been promised -- and `read_pods` returns the whole cluster. That was the
    same list until 2026-09-03, when server2 joined: from then on the sums
    below counted workloads on the other node against this node's capacity,
    and the number stayed plausible while being wrong. Measured Cycle 860,
    the morning after the join: limits summed to 9002Mi cluster-wide against
    server1's 7746Mi (116%), where server1's own share was 7850Mi (101%) and
    server2 held the other 1152Mi.

    A Pod with no `node` is on nobody's list on purpose -- the scheduler has
    not placed it, so no node has promised it anything yet.
    """
    return [pod for pod in pods if pod.get("node") == host]


def largest_limit(pods):
    """The biggest single container memory limit among the Pods given, as (MiB, where).

    Scope is the caller's: hand it `pods_on(pods, host)` to ask what this
    node must be able to start, which is the only question it is asked here.
    """
    biggest, where = None, ""
    for pod in pods:
        for container, quantity in (pod.get("limits") or {}).items():
            mib = _mib(quantity)
            if mib is None:
                continue
            if biggest is None or mib > biggest:
                biggest = mib
                where = f"{pod['namespace']}/{pod['name']} [{container}]"
    return biggest, where


def declared_ceiling(pods):
    """What the pods on this host are collectively allowed to take.

    `largest_limit` asks whether the biggest single container can still
    start. That is a real question and it is not this one, and the case that
    separates them is eight 1Gi containers on this box: every one of them
    fits in what is available, so the per-container check is quiet, and
    together they are over the machine. On 2026-08-31 the limits declared
    across server1 summed to 7978Mi against a 7746Mi box while the scheduler
    saw 2519Mi of requests and packed happily.

    Returns (total MiB, containers that set a limit, containers that do not).
    Only Running and Pending Pods are counted -- a Succeeded Job pod holds no
    memory and would inflate the sum with workloads that finished days ago.

    The third number is why the first is a floor rather than a ceiling. A
    container with no memory limit can take the whole machine, so a sum over
    only the ones that declared is the *least* this node can be asked for,
    never the most.
    """
    total = 0.0
    with_limit = without_limit = 0
    for pod in pods:
        if pod.get("phase") not in ("Running", "Pending"):
            continue
        limits = pod.get("limits") or {}
        for quantity in limits.values():
            mib = _mib(quantity)
            if mib is None:
                continue
            total += mib
            with_limit += 1
        declared = pod.get("container_count")
        if declared is None:
            continue
        without_limit += max(0, declared - len(limits))
    return total, with_limit, without_limit


def budget_line(host, ceiling, limited, unlimited, total):
    """One node's declared memory budget against its own capacity.

    Returns (line, actionable). Shared by the node this pod stands on and
    every other node, because the question and the arithmetic are identical
    -- only where the capacity came from differs, and that is the caller's.
    """
    if limited == 0:
        return (f"BUDGET  {host}: no running container sets a memory limit, so there "
                "is no declared budget to compare against the box."), False
    share = ceiling / total * 100 if total else 0.0
    unbounded = (
        f" {unlimited} more running container(s) set no limit at all, so this "
        "sum is the least the host can be asked for, not the most."
        if unlimited else
        " Every running container sets one, so this sum is the whole of it.")
    if ceiling > total:
        return (f"MEMORY OVERCOMMITTED — {host} is {total:.0f}Mi and the memory limits "
                f"declared on it sum to {ceiling:.0f}Mi ({share:.0f}%) across {limited} "
                f"container(s). Nothing stops them all claiming it at once; the "
                f"scheduler only ever guarded the requests.{unbounded}"), True
    return (f"BUDGET  {host}: declared limits sum to {ceiling:.0f}Mi of "
            f"{total:.0f}Mi ({share:.0f}%) across {limited} container(s).{unbounded}"), False


def available_line(node, available_mib, total_mib, biggest, where):
    """"Can this node still start the largest container configured on it?"

    Pulled out of `memory_headroom` in Cycle 861. Until then this judgement
    existed only for the node the bridge pod happened to be standing on,
    because `/proc/meminfo` is the only host it can see -- so the same
    question about server2 was answered "not judged" rather than answered.
    The kubelet publishes `availableBytes` for every node, so the rule
    travels; only the reading did not.

    Returns (line, actionable).
    """
    if biggest is None:
        return (f"MEMORY  {node}: {available_mib:.0f}Mi of {total_mib:.0f}Mi available. "
                "No container here sets a memory limit, so there is no configured "
                "size to judge that against."), False
    if available_mib < biggest:
        return (f"NODE OUT OF MEMORY — {node} has {available_mib:.0f}Mi available of "
                f"{total_mib:.0f}Mi ({available_mib / total_mib * 100:.1f}%), which is "
                f"less than the largest container limit configured on it "
                f"({biggest:.0f}Mi, {where}). "
                "The next time that workload rolls, the host cannot fit it."), True
    return (f"MEMORY  {node}: {available_mib:.0f}Mi of {total_mib:.0f}Mi available "
            f"({available_mib / total_mib * 100:.1f}%), above the largest configured "
            f"container limit ({biggest:.0f}Mi, {where})."), False


def swap_line(node, swap_total_mib, swap_free_mib):
    """What is left of `node`'s overflow, and what it means when there is none.

    A node with no swap at all is not a finding -- it is how Hetzner ships a
    box, and a check that goes permanently red on an ordinary configuration
    stops being read. It is worth *saying*, though, and it was not said until
    Cycle 861: server1 has 2GB of swap and server2 has none, so an identical
    memory spike is a slowdown on one box and a kill on the other, and the
    report named neither.

    `swap_total_mib` of None means the kubelet published no swap block at
    all, which is a missing instrument rather than a node without swap.

    Returns (line, actionable).
    """
    if swap_total_mib is None:
        return (f"SWAP    {node}: not judged — the kubelet publishes no swap "
                "figures for this node, which is not the same as none configured."), False
    if swap_total_mib <= 0:
        return (f"SWAP    {node}: none configured, so there is no overflow to judge — "
                "a memory spike here is a kill rather than a slowdown."), False
    if swap_free_mib < swap_total_mib * SWAP_NEARLY_GONE:
        return (f"SWAP EXHAUSTED — {node} has {swap_free_mib:.0f}Mi free of "
                f"{swap_total_mib:.0f}Mi ({swap_free_mib / swap_total_mib * 100:.1f}%). "
                "Swap is the overflow that turns a memory spike into a slowdown; "
                "with it gone the next spike is a kill."), True
    return (f"SWAP    {node}: {swap_free_mib:.0f}Mi free of {swap_total_mib:.0f}Mi "
            f"({swap_free_mib / swap_total_mib * 100:.1f}%)."), False


def other_node_budgets(nodes, host, pods, stats=None):
    """Every node this pod is not standing on, judged the same way as the one it is.

    Cycle 860 gave each of these a budget line -- capacity against the limits
    declared on the Pods placed there -- and stopped, because the other half
    of `memory_headroom` read `/proc/meminfo`, which in a pod is only ever the
    node that pod is scheduled on. So the report said server2's real headroom
    and its swap were "not judged", which is honest and is not an answer, and
    "is there room on server2" -- the question the owner's note actually asks --
    could not be answered from this loop at all.

    The kubelet answers it, for every node, over the `nodes/proxy` grant this
    module already uses for cAdvisor. `stats` is that reader, injected so a
    test can hand this a node's figures without a cluster; the default is the
    real one, resolved here rather than in the signature, because a default
    argument binds the function object at import and a monkeypatch of the
    module attribute would then never be seen.

    Returns (lines, actionable, judged). A node whose kubelet could not be
    read is `judged=False` -- a partial sweep must never read as a clean one,
    which is the same contract every check in `preflight` keeps.
    """
    read_stats = stats or read_node_memory_stats
    lines, actionable, judged = [], False, True
    for node in sorted(n for n in nodes if n != host):
        ceiling, limited, unlimited = declared_ceiling(pods_on(pods, node))
        line, raised = budget_line(node, ceiling, limited, unlimited, nodes[node])
        actionable = actionable or raised
        lines.append(line)

        figures, why = read_stats(node)
        if figures is None:
            judged = False
            lines.append(
                f"  CANNOT JUDGE {node}'s headroom — {why}. Its declared budget "
                "above still stands; what is actually allocatable there, and its "
                "swap, were not read, which is no instrument rather than clean.")
            continue
        biggest, where = largest_limit(pods_on(pods, node))
        line, raised = available_line(
            node, figures["available_mib"], nodes[node], biggest, where)
        actionable = actionable or raised
        lines.append("  " + line)
        line, raised = swap_line(
            node, figures["swap_total_mib"], figures["swap_free_mib"])
        actionable = actionable or raised
        lines.append("  " + line)
    stray = [pod["name"] for pod in pods
             if pod.get("phase") in ("Running", "Pending") and not pod.get("node")]
    if stray:
        lines.append(
            f"  {len(stray)} Pod(s) are on no node yet ({', '.join(sorted(stray)[:3])}"
            f"{', ...' if len(stray) > 3 else ''}), so they count against nobody's "
            "budget above. That is the scheduler's state, not a gap in this read.")
    return lines, actionable, judged


def memory_headroom(meminfo, nodes, pods, stats=None):
    """Can this host still start the largest container it is configured to run?

    `pods` is the whole cluster and it is scoped here, not by the caller:
    every sum below is about one box. See `pods_on` for what that was
    getting wrong from 2026-09-03 onward. Every node the API server lists
    gets a budget line, so a one-node reading can never read as a sweep.

    Returns (lines, actionable, judged). `judged` is False either when the
    reading cannot be attributed to a host or when the host matched and
    `MemAvailable` is absent. Both are "no instrument", not a clean bill of
    health, and `main` turns either into an exit 1 -- but they print
    different sentences, because a reading about the wrong machine and a
    missing field on the right one have different fixes.
    """
    total = meminfo.get("MemTotal", 0) / 1024
    available = meminfo.get("MemAvailable")
    lines = []

    # Neither pod this loop runs in mounts lxcfs, so `/proc/meminfo` is the
    # host's -- measured 2026-08-29, MemTotal 7931600 kB against server1's
    # capacity of 7931600Ki. That equality is the whole proof, so it is
    # checked rather than remembered: if some future runtime starts faking
    # meminfo per container, this reads a container's memory and calls it a
    # node's, and every number below would be wrong while looking right.
    host = matching_host(meminfo, nodes)
    if host is None:
        lines.append(
            "CANNOT ATTRIBUTE MEMORY — /proc/meminfo says "
            f"{total:.0f}Mi total, which matches no node's capacity "
            f"({', '.join(f'{n} {m:.0f}Mi' for n, m in sorted(nodes.items())) or 'none read'}). "
            "That means this is a container's view, not the host's, and every "
            "headroom number below would be about the wrong machine.")
        return lines, False, False

    here = pods_on(pods, host)
    biggest, where = largest_limit(here)
    if available is None:
        # A different failure from an unattributable reading, and it does not
        # get to borrow that sentence: the host matched, one field is absent.
        lines.append(
            f"CANNOT JUDGE MEMORY — {host} matched, but {MEMINFO} carries no "
            "MemAvailable, which is the only field that says what can still be "
            "allocated.")
        return lines, False, False
    available_mib = available / 1024

    actionable = False
    line, raised = available_line(host, available_mib, total, biggest, where)
    actionable = actionable or raised
    lines.append(line)

    ceiling, limited, unlimited = declared_ceiling(here)
    line, raised = budget_line(host, ceiling, limited, unlimited, total)
    actionable = actionable or raised
    lines.append(line)

    line, raised = swap_line(host, meminfo.get("SwapTotal", 0) / 1024,
                             meminfo.get("SwapFree", 0) / 1024)
    actionable = actionable or raised
    lines.append(line)

    other_lines, other_actionable, other_judged = other_node_budgets(
        nodes, host, pods, stats=stats)
    lines.extend(other_lines)
    actionable = actionable or other_actionable
    return lines, actionable, other_judged


def report(pods, deployments, now, headroom=None):
    lines = []
    actionable = False

    deaths_found, old_deaths = deaths(pods, now)
    looping = [d for d in deaths_found if d["restarts"] >= REPEATING]
    once = [d for d in deaths_found if d["restarts"] < REPEATING]

    def _death_line(d):
        limit = f", memory limit {d['limit']}" if d["limit"] else ""
        if d["ready"]:
            state = "up again"
        elif d.get("terminal"):
            state = "Pod finished"
        else:
            state = "still down"
        also = f", previously {d['also']}" if d.get("also") else ""
        return (f"  {d['namespace']}/{d['pod']} [{d['container']}] — {d['reason']}"
                f" (exit {d['exit_code']}) at {_oslo(d['at'])}, {d['restarts']} restart(s),"
                f" {state}{limit}{also}")

    if looping:
        actionable = True
        lines.append(
            f"CRASH LOOPING — {len(looping)} container(s) have died repeatedly and died "
            "again just now. This is a state, not an event, and it stays loud until "
            "somebody fixes it — there is deliberately no way to mute it here.")
        for d in sorted(looping, key=lambda d: -d["restarts"]):
            lines.append(_death_line(d))

    if once:
        actionable = True
        lines.append(
            f"CONTAINER DIED — {len(once)} container(s) ended on something "
            "other than a clean exit and the record is still on the Pod.")
        for d in sorted(once, key=lambda d: (d["namespace"], d["pod"])):
            lines.append(_death_line(d))

    stuck = not_ready(pods)
    if stuck:
        actionable = True
        lines.append(
            f"NOT READY — {len(stuck)} container(s) are not serving right now.")
        for s in sorted(stuck, key=lambda s: (s["namespace"], s["pod"])):
            detail = f" — {s['message']}" if s["message"] else ""
            lines.append(
                f"  {s['namespace']}/{s['pod']} [{s['container']}] — {s['phase']},"
                f" {s['reason']}, {s['restarts']} restart(s){detail}")

    past, rolling = unavailable(deployments, now)
    if past:
        actionable = True
        lines.append(
            f"NOBODY SERVING — {len(past)} Deployment(s) have had no available "
            "replica for longer than their own drain budget.")
        for d in sorted(past, key=lambda d: (d["namespace"], d["name"])):
            down = _duration(d["down"].total_seconds()) if d["down"] else "an unknown time"
            lines.append(
                f"  {d['namespace']}/{d['name']} — {d['reason'] or 'Available=False'} for {down},"
                f" budget {_duration(d['budget'].total_seconds())}"
                f" ({d['strategy']}, grace {d['grace']}s)")

    if rolling:
        lines.append(
            f"ROLLING — {len(rolling)} Deployment(s) are unavailable inside their own "
            "drain budget. That is what a Recreate rollout looks like, so it does not raise.")
        for d in sorted(rolling, key=lambda d: (d["namespace"], d["name"])):
            down = _duration(d["down"].total_seconds()) if d["down"] else "?"
            lines.append(
                f"  {d['namespace']}/{d['name']} — down {down} of"
                f" {_duration(d['budget'].total_seconds())} ({d['strategy']})")

    if old_deaths:
        lines.append(
            f"DIED AND RECOVERED — {len(old_deaths)} container(s) died longer than "
            f"{int(RECENT_DEATH.total_seconds() // 3600)}h ago and nothing is down "
            "now: either the container came back, or its Pod has finished and "
            "will not run again. History, so it does not raise.")
        for d in sorted(old_deaths, key=lambda d: (d["namespace"], d["pod"])):
            lines.append(
                f"  {d['namespace']}/{d['pod']} [{d['container']}] — {d['reason']}"
                f" (exit {d['exit_code']}) at {_oslo(d['at'])}, {d['restarts']} restart(s)")

    healed = healed_restarts(pods, deaths_found + old_deaths)
    if healed:
        lines.append(
            f"RESTARTED AND HEALED — {len(healed)} container(s) carry restarts with no "
            "cause still on the Pod. History, so it does not raise.")
        for h in sorted(healed, key=lambda h: -h["restarts"]):
            lines.append(
                f"  {h['namespace']}/{h['pod']} [{h['container']}] — {h['restarts']} restart(s)")

    overdue, draining = terminating(pods, now)
    if overdue:
        actionable = True
        lines.append(
            f"TERMINATING PAST ITS OWN DEADLINE — {len(overdue)} Pod(s) are still here "
            "after the instant Kubernetes promised they would be gone. The kubelet has "
            "already sent SIGKILL by then, so this is a kubelet that cannot be reached, "
            "a finalizer, or a dead node — not a slow shutdown.")
        for t in sorted(overdue, key=lambda t: -(t["over"].total_seconds() if t["over"] else 0)):
            over = f"{_duration(t['over'].total_seconds())} past" if t["over"] else "no readable deadline"
            lines.append(f"  {t['namespace']}/{t['name']} on {t['node'] or 'no node'} — {over}")
    if draining:
        lines.append(
            f"DRAINING — {len(draining)} Pod(s) are Terminating inside their own grace. "
            "Deliberately not raised: a cycle takes up to 48 minutes to finish and the "
            "old Pod is meant to stay until it does.")
        for t in sorted(draining, key=lambda t: -(t["left"].total_seconds())):
            lines.append(
                f"  {t['namespace']}/{t['name']} on {t['node'] or 'no node'} — "
                f"{_duration(t['left'].total_seconds())} left")

    if headroom is not None:
        head_lines, head_actionable, judged = headroom
        lines.extend(head_lines)
        actionable = actionable or head_actionable
        if not judged:
            # Unattributable is not clean. Raising here would merge it with a
            # real finding, so it is left to main to turn into an exit 1.
            pass

    lines.append(
        f"Read {len(pods)} Pod(s) and {len(deployments)} Deployment(s) from the live "
        "cluster, not from git.")
    lines.append(
        "An unavailable Deployment is judged against its own terminationGracePeriodSeconds "
        f"plus {int(START_MARGIN.total_seconds() // 60)}m, because a Recreate rollout is "
        "unavailable by design for exactly that long.")
    lines.append(
        "A Terminating Pod is judged against its own deletionTimestamp, which is the "
        f"deadline rather than the start, plus {int(KILL_MARGIN.total_seconds() // 60)}m "
        "for the status write.")
    return lines, (2 if actionable else 0)


def main(argv=None, runner=subprocess.run, now=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    pods, why = read_pods(runner)
    if why:
        print(f"COULD NOT READ  {why}")
        return 1
    if not pods:
        # An empty list from a working kubectl is no instrument, not a
        # clean bill of health — this cluster demonstrably runs Pods.
        print("COULD NOT READ  kubectl returned no Pods at all")
        return 1

    deployments, why = read_deployments(runner)
    if why:
        print(f"COULD NOT READ  {why}")
        return 1

    meminfo, why = read_meminfo()
    if why:
        print(f"COULD NOT READ  {why}")
        return 1
    nodes, why = read_node_capacity(runner)
    if why:
        print(f"COULD NOT READ  {why}")
        return 1
    # The stats reader takes `main`'s runner, not its own default: a test that
    # injects a fake kubectl here must not have one call escape to the real
    # cluster, which is exactly what happened the first time this was wired.
    headroom = memory_headroom(
        meminfo, nodes, pods,
        stats=lambda node: read_node_memory_stats(node, runner=runner))
    # Deliberately after `headroom`: if the reading could not be attributed to
    # a host, every number below it is about the wrong machine and printing a
    # breakdown of it would be worse than printing nothing.
    #
    # The gate is the host's own attribution, not `headroom[2]`. Since Cycle
    # 861 that third value also carries whether *every other* node could be
    # read, and an unreadable kubelet on server2 says nothing about whether
    # this host's /proc/meminfo breakdown is sound -- suppressing the
    # breakdown for it would be one failure hiding a working instrument.
    if matching_host(meminfo, nodes) is not None:
        working_set, _ = read_pod_working_set(runner)
        series, cgroups_why = read_node_cgroup_series(
            matching_host(meminfo, nodes), runner)
        cgroups = series[CADVISOR_SERIES] if series else None
        node_swap = series.get(CADVISOR_SWAP_SERIES) if series else None
        node_rss = series.get(CADVISOR_RSS_SERIES) if series else None
        headroom = (headroom[0] + attribution(meminfo, working_set, cgroups,
                                              cgroups_why, node_swap, node_rss),
                    headroom[1], headroom[2])

    lines, status = report(
        pods, deployments, now or datetime.datetime.now(datetime.timezone.utc),
        headroom=headroom)
    for line in lines:
        print(line)
    # A reading that could not be attributed to a host never reads as clean.
    if not headroom[2] and status == 0:
        return 1
    return status


if __name__ == "__main__":
    sys.exit(main())
