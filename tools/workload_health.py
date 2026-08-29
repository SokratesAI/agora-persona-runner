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

#: Margin over a workload's own termination grace, for image pull and
#: container start. Small next to the 2880s grace it is added to; it exists
#: so a Deployment is not called broken in the seconds between the old Pod
#: exiting and the new one passing its first probe.
START_MARGIN = datetime.timedelta(minutes=5)

#: A container that exits 0 because its work is done is not a failure.
BENIGN_TERMINATION = {"Completed"}

#: How recently a container must have died for its death to still be news,
#: given it has come back up since. Three cycles at the 20-minute cadence.
#: A container that is down *now* raises at any age; this bounds only the
#: recovered ones, because `lastState` holds them for the life of the Pod.
RECENT_DEATH = datetime.timedelta(hours=1)


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
            "limits": limits,
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
            for where in ("lastState", "state"):
                term = (container.get(where) or {}).get("terminated") or {}
                reason = term.get("reason")
                if not reason or reason in BENIGN_TERMINATION:
                    continue
                ready = bool(container.get("ready"))
                at = _parse(term.get("finishedAt"))
                recent = at is None or (now - at) <= window
                row = {
                    "namespace": pod["namespace"],
                    "pod": pod["name"],
                    "container": container.get("name") or "?",
                    "reason": reason,
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

    `Succeeded` is a finished Job's Pod and `Pending` on a Pod with no
    container statuses yet is a scheduler snapshot, not a failure — both
    are excluded so this does not fire on the normal life of a CronJob.
    """
    found = []
    for pod in pods:
        if pod["phase"] in ("Succeeded", "Pending"):
            continue
        for container in pod["containers"]:
            if container.get("ready"):
                continue
            waiting = (container.get("state") or {}).get("waiting") or {}
            found.append({
                "namespace": pod["namespace"],
                "pod": pod["name"],
                "container": container.get("name") or "?",
                "phase": pod["phase"],
                "reason": waiting.get("reason") or "not ready",
                "message": (waiting.get("message") or "").strip(),
                "restarts": container.get("restartCount") or 0,
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
        (rolling if row["down"] <= budget else past).append(row)
    return past, rolling


def report(pods, deployments, now):
    lines = []
    actionable = False

    deaths_found, old_deaths = deaths(pods, now)
    if deaths_found:
        actionable = True
        lines.append(
            f"CONTAINER DIED — {len(deaths_found)} container(s) ended on something "
            "other than a clean exit and the record is still on the Pod.")
        for d in sorted(deaths_found, key=lambda d: (d["namespace"], d["pod"])):
            limit = f", memory limit {d['limit']}" if d["limit"] else ""
            state = "up again" if d["ready"] else "still down"
            lines.append(
                f"  {d['namespace']}/{d['pod']} [{d['container']}] — {d['reason']}"
                f" (exit {d['exit_code']}) at {d['at']} UTC, {d['restarts']} restart(s),"
                f" {state}{limit}")

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
                f" (exit {d['exit_code']}) at {d['at']} UTC, {d['restarts']} restart(s)")

    healed = healed_restarts(pods, deaths_found + old_deaths)
    if healed:
        lines.append(
            f"RESTARTED AND HEALED — {len(healed)} container(s) carry restarts with no "
            "cause still on the Pod. History, so it does not raise.")
        for h in sorted(healed, key=lambda h: -h["restarts"]):
            lines.append(
                f"  {h['namespace']}/{h['pod']} [{h['container']}] — {h['restarts']} restart(s)")

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

    lines, status = report(
        pods, deployments, now or datetime.datetime.now(datetime.timezone.utc))
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
