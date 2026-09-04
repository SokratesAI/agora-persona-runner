"""Which workloads would actually restart if their Secret or ConfigMap changed?

Sokrates, in a capture on 2026-09-04: *"Confirmed Reloader IS correctly
auto-restarting telegram-bridge on ConfigMap changes ... The 'Reloader is broken
cluster-wide' note dated 2026-07-29 in agora-persona-runner-config's manifest
may be stale -- worth a check if anyone's still avoiding it on that
assumption."*

He is right, and I measured it myself before writing this: Reloader v1.3.0 is
Running in `infra`, watching every namespace, and it performed eight reloads on
2026-09-04 alone (hub, telegram-bridge, prometheus, grafana). It is not broken.

What I found while checking is the thing worth an instrument. **`Reloader` only
restarts a workload that asked to be restarted, and most of ours never asked.**
`couchdb-credentials` is read by five workloads in this cluster and only three
of them carry an annotation, so rotating that password would leave the other two
running on the old one -- silently, because a pod with stale credentials keeps
its old connection open and fails at the next reconnect, hours later, somewhere
else. Nothing anywhere reported that, and it is exactly the shape a status
readout is for: the fact is one `kubectl get` away and nobody was taking it.

**A missing annotation is invisible in both directions, which is why this reads
the workload rather than Reloader's log.** Reloader logs a line when it reloads
something; it logs nothing at all for a workload it was never told to watch, so
its log looks identical whether coverage is complete or absent. The declaration
lives on the Deployment, so that is what gets read.

**Scope is declared, not inferred.** `OWNED` names the three namespaces whose
manifests live in repos this loop can open a pull request against. Everything
else in the cluster -- argocd, crossplane-system, tailscale, kube-system -- is an
upstream chart or an operator's own output, where an annotation I added would be
reverted on the next sync. Those are counted and named in the report and they do
not raise, because raising forever on something nobody here can fix is how a
check gets ignored.

**One annotation this cannot judge, and it says so rather than guessing.**
`reloader.stakater.com/search: "true"` reloads on any ConfigMap or Secret
carrying `reloader.stakater.com/match: "true"`, so the answer lives on the other
object. Nothing in this cluster uses it today; if something starts to, the
workload is reported as `not judged` rather than as covered.

Exit 1 -- Reloader is absent, unready, or `kubectl` failed, so no annotation in
the cluster means anything and no coverage claim here can be trusted.
Exit 2 -- an owned workload reads a Secret or ConfigMap that no annotation
covers.
Exit 0 -- every owned workload is covered.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

#: Namespaces whose manifests live in a repo this loop can send a pull request
#: to. Anything outside this raises nothing -- see the module docstring.
OWNED = ("agents", "infra", "obsidian")

#: Kubernetes projects the cluster CA into every pod as a ConfigMap. It is not
#: a thing anyone rotates by hand and no workload declares it, so counting it
#: would make every single workload permanently uncovered and the report
#: worthless.
IGNORED_REFS = frozenset({"cm:kube-root-ca.crt"})

RELOADER_NAMESPACE = "infra"
RELOADER_DEPLOYMENT = "reloader-reloader"

_AUTO = "reloader.stakater.com/auto"
_SEARCH = "reloader.stakater.com/search"
_CM_RELOAD = "configmap.reloader.stakater.com/reload"
_SEC_RELOAD = "secret.reloader.stakater.com/reload"


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


def references(spec: dict) -> set[str]:
    """Every ConfigMap and Secret a pod spec reads, as `cm:name` / `sec:name`.

    All four ways a pod can name one are collected -- a projected volume, a
    plain volume, `envFrom`, and a single `env` entry -- because Reloader's
    `auto` covers all four and a coverage report that read only volumes would
    call the runner uncovered when it is not.
    """
    found: set[str] = set()

    def _volume(vol: dict) -> None:
        if vol.get("configMap"):
            found.add("cm:" + str(vol["configMap"].get("name")))
        if vol.get("secret"):
            found.add("sec:" + str(vol["secret"].get("secretName")))
        for source in (vol.get("projected") or {}).get("sources") or []:
            _volume(source)

    for vol in spec.get("volumes") or []:
        _volume(vol)

    for container in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        for entry in container.get("envFrom") or []:
            if entry.get("configMapRef"):
                found.add("cm:" + str(entry["configMapRef"].get("name")))
            if entry.get("secretRef"):
                found.add("sec:" + str(entry["secretRef"].get("name")))
        for entry in container.get("env") or []:
            source = entry.get("valueFrom") or {}
            if source.get("configMapKeyRef"):
                found.add("cm:" + str(source["configMapKeyRef"].get("name")))
            if source.get("secretKeyRef"):
                found.add("sec:" + str(source["secretKeyRef"].get("name")))

    return found - IGNORED_REFS


def _names(value) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def covered(annotations: dict, refs: set[str]) -> tuple[set[str], bool]:
    """`(the refs Reloader would restart on, whether the answer is judgeable)`.

    The second value is False only for `search`, whose answer lives on the
    ConfigMap rather than here.
    """
    annotations = annotations or {}
    if str(annotations.get(_SEARCH, "")).lower() == "true":
        return set(), False
    if str(annotations.get(_AUTO, "")).lower() == "true":
        return set(refs), True
    named = {"cm:" + n for n in _names(annotations.get(_CM_RELOAD))}
    named |= {"sec:" + n for n in _names(annotations.get(_SEC_RELOAD))}
    return refs & named, True


def read_reloader(runner=subprocess.run):
    """`(ready_replicas, None)` for the Reloader Deployment, or `(None, why)`."""
    body, why = _run(
        runner,
        ["kubectl", "get", "deploy", RELOADER_DEPLOYMENT,
         "-n", RELOADER_NAMESPACE, "-o", "json"],
    )
    if why:
        return None, why
    ready = ((body.get("status") or {}).get("readyReplicas")) or 0
    return int(ready), None


def read_workloads(runner=subprocess.run):
    """Every Deployment and StatefulSet, as a list of plain dicts, or `(None, why)`."""
    body, why = _run(
        runner, ["kubectl", "get", "deploy,statefulset", "-A", "-o", "json"]
    )
    if why:
        return None, why

    out = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        spec = ((item.get("spec") or {}).get("template") or {}).get("spec") or {}
        refs = references(spec)
        seen, judged = covered(meta.get("annotations") or {}, refs)
        out.append({
            "kind": item.get("kind") or "?",
            "namespace": meta.get("namespace") or "?",
            "name": meta.get("name") or "?",
            "refs": refs,
            "covered": seen,
            "judged": judged,
        })
    return out, None


def report(ready, workloads):
    """`(exit_code, lines)`. Pure, so the tests do not need a cluster."""
    lines = []
    if ready is None or ready < 1:
        lines.append(
            "CANNOT JUDGE — the Reloader Deployment "
            f"{RELOADER_NAMESPACE}/{RELOADER_DEPLOYMENT} has {ready if ready is not None else 'no'} "
            "ready replica(s), so no annotation in this cluster does anything."
        )
        return 1, lines

    owned = [w for w in workloads if w["namespace"] in OWNED]
    gaps = []
    for w in owned:
        if not w["refs"]:
            continue
        if not w["judged"]:
            lines.append(
                f"not judged  {w['namespace']}/{w['name']} — uses "
                f"`{_SEARCH}`, whose answer lives on the ConfigMap"
            )
            continue
        missing = sorted(w["refs"] - w["covered"])
        if missing:
            gaps.append((w, missing))

    for w, missing in sorted(gaps, key=lambda g: (g[0]["namespace"], g[0]["name"])):
        lines.append(
            f"NO RESTART  {w['namespace']}/{w['name']} — a change to "
            + ", ".join(missing)
            + " reaches this pod only on the next restart"
        )

    fine = [w for w in owned if w["refs"] and w["judged"] and not (w["refs"] - w["covered"])]
    for w in sorted(fine, key=lambda w: (w["namespace"], w["name"])):
        lines.append(
            f"ok          {w['namespace']}/{w['name']} — "
            f"{len(w['refs'])} reference(s), all covered"
        )

    # A Secret several workloads read is the case that actually bites: the
    # rotation looks like it worked because the pods that do restart come back
    # healthy, and the ones that did not are the ones nobody looks at.
    shared: dict[str, list] = {}
    for w in owned:
        for ref in w["refs"]:
            shared.setdefault(ref, []).append(w)
    for ref, users in sorted(shared.items()):
        if len(users) < 2:
            continue
        stale = sorted(
            f"{u['namespace']}/{u['name']}"
            for u in users
            if u["judged"] and ref not in u["covered"]
        )
        if stale:
            lines.append(
                f"SHARED      {ref} is read by {len(users)} workload(s); rotating it "
                f"leaves {len(stale)} of them on the old value: " + ", ".join(stale)
            )

    outside = [w for w in workloads if w["namespace"] not in OWNED and w["refs"]]
    lines.append(
        f"Read {len(workloads)} workload(s) across the cluster and judged the "
        f"{len(owned)} in {', '.join(OWNED)}. {len(outside)} more read a ConfigMap or "
        "Secret and are left alone: their manifests come from an upstream chart or "
        "an operator, so an annotation added here would be reverted on the next sync."
    )
    return (2 if gaps else 0), lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    ready, why = read_reloader()
    if why:
        print(f"CANNOT JUDGE — {why}")
        return 1
    workloads, why = read_workloads()
    if why:
        print(f"CANNOT JUDGE — {why}")
        return 1

    status, lines = report(ready, workloads)
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
