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

**Three verdicts, kept separate on purpose.** A container that died for a
reason (`OOMKilled`, `Error`), a Pod that is not Ready now, and a
Deployment with no available replica are three different problems with
three different fixes, and merging them into one red number is the failure
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
that it is not shown to seventy. **A Pod that
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
import subprocess
import sys
import zoneinfo

#: Margin over a workload's own termination grace, for image pull and
#: container start. Small next to the 2880s grace it is added to; it exists
#: so a Deployment is not called broken in the seconds between the old Pod
#: exiting and the new one passing its first probe.
START_MARGIN = datetime.timedelta(minutes=5)

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
            }
            (fresh if (not ready or recent) else old).append(row)
    return fresh, old


def not_ready(pods):
    """Pods that are meant to be serving and are not.

    `Succeeded` is a finished Job's Pod and is excluded. Everything else is
    judged on the container's waiting *reason* rather than on the Pod's
    phase, because `ImagePullBackOff` is `Pending` and permanent while
    `ContainerCreating` is `Pending` and over in seconds.

    A Pod that was never scheduled has no container statuses at all, so it
    cannot be judged that way; its own `PodScheduled` condition is read
    instead.
    """
    found = []
    for pod in pods:
        if pod["phase"] == "Succeeded":
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


def attribution(meminfo, pod_working_set):
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
    return lines


def largest_limit(pods):
    """The biggest single container memory limit anywhere, as (MiB, where)."""
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


def memory_headroom(meminfo, nodes, pods):
    """Can this host still start the largest container it is configured to run?

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
    host = next((name for name, mib in nodes.items() if abs(mib - total) < 1), None)
    if host is None:
        lines.append(
            "CANNOT ATTRIBUTE MEMORY — /proc/meminfo says "
            f"{total:.0f}Mi total, which matches no node's capacity "
            f"({', '.join(f'{n} {m:.0f}Mi' for n, m in sorted(nodes.items())) or 'none read'}). "
            "That means this is a container's view, not the host's, and every "
            "headroom number below would be about the wrong machine.")
        return lines, False, False

    biggest, where = largest_limit(pods)
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
    if biggest is None:
        lines.append(
            f"MEMORY  {host}: {available_mib:.0f}Mi of {total:.0f}Mi available. "
            "No container here sets a memory limit, so there is no configured "
            "size to judge that against.")
    elif available_mib < biggest:
        actionable = True
        lines.append(
            f"NODE OUT OF MEMORY — {host} has {available_mib:.0f}Mi available of "
            f"{total:.0f}Mi ({available_mib / total * 100:.1f}%), which is less than the "
            f"largest container limit configured on it ({biggest:.0f}Mi, {where}). "
            "The next time that workload rolls, the host cannot fit it.")
    else:
        lines.append(
            f"MEMORY  {host}: {available_mib:.0f}Mi of {total:.0f}Mi available "
            f"({available_mib / total * 100:.1f}%), above the largest configured "
            f"container limit ({biggest:.0f}Mi, {where}).")

    ceiling, limited, unlimited = declared_ceiling(pods)
    if limited == 0:
        lines.append(
            f"BUDGET  {host}: no running container sets a memory limit, so there "
            "is no declared budget to compare against the box.")
    else:
        share = ceiling / total * 100 if total else 0.0
        unbounded = (
            f" {unlimited} more running container(s) set no limit at all, so this "
            "sum is the least the host can be asked for, not the most."
            if unlimited else
            " Every running container sets one, so this sum is the whole of it.")
        if ceiling > total:
            actionable = True
            lines.append(
                f"MEMORY OVERCOMMITTED — {host} is {total:.0f}Mi and the memory limits "
                f"declared on it sum to {ceiling:.0f}Mi ({share:.0f}%) across {limited} "
                f"container(s). Nothing stops them all claiming it at once; the "
                f"scheduler only ever guarded the requests.{unbounded}")
        else:
            lines.append(
                f"BUDGET  {host}: declared limits sum to {ceiling:.0f}Mi of "
                f"{total:.0f}Mi ({share:.0f}%) across {limited} container(s).{unbounded}")

    swap_total = meminfo.get("SwapTotal", 0) / 1024
    swap_free = meminfo.get("SwapFree", 0) / 1024
    if swap_total <= 0:
        lines.append(f"SWAP    {host}: none configured, so there is no overflow to judge.")
    elif swap_free < swap_total * SWAP_NEARLY_GONE:
        actionable = True
        lines.append(
            f"SWAP EXHAUSTED — {host} has {swap_free:.0f}Mi free of {swap_total:.0f}Mi "
            f"({swap_free / swap_total * 100:.1f}%). Swap is the overflow that turns a "
            "memory spike into a slowdown; with it gone the next spike is a kill.")
    else:
        lines.append(
            f"SWAP    {host}: {swap_free:.0f}Mi free of {swap_total:.0f}Mi "
            f"({swap_free / swap_total * 100:.1f}%).")
    return lines, actionable, True


def report(pods, deployments, now, headroom=None):
    lines = []
    actionable = False

    deaths_found, old_deaths = deaths(pods, now)
    looping = [d for d in deaths_found if d["restarts"] >= REPEATING]
    once = [d for d in deaths_found if d["restarts"] < REPEATING]

    def _death_line(d):
        limit = f", memory limit {d['limit']}" if d["limit"] else ""
        state = "up again" if d["ready"] else "still down"
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
            f"{int(RECENT_DEATH.total_seconds() // 3600)}h ago and are up again. "
            "History, so it does not raise.")
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
    headroom = memory_headroom(meminfo, nodes, pods)
    # Deliberately after `headroom`: if the reading could not be attributed to
    # a host, every number below it is about the wrong machine and printing a
    # breakdown of it would be worse than printing nothing.
    if headroom[2]:
        working_set, _ = read_pod_working_set(runner)
        headroom = (headroom[0] + attribution(meminfo, working_set),
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
