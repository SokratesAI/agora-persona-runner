"""Is every image Kubernetes actually runs pinned to bytes that cannot change?

Cycle 612, on the owner's idea #178 -- *"Nothing checks the images
Kubernetes actually runs — only the ones our Dockerfiles build."*

`tools.pin_drift` and `tools.eol_watch` both read files in GitHub repos:
a `FROM` line, an `ARG *_VERSION`, a `uses:` ref. Neither has ever looked
at an image that runs on this cluster without being built here, and there
are six of those -- four `:latest` tags, our own runtime on a branch tag
in two places, and the CouchDB holding the vault.

    python3 -m tools.running_images

**It reads the live cluster, never git.** That is the same call
`tools.helm_repo_health` makes and for the same reason: a manifest in git
that ArgoCD has not synced is not what is running, and a workload nobody
put in git still runs. The question in the row is what Kubernetes *runs*,
so the only honest place to ask it is the API server.

**It reads workloads, not only Pods.** `whatsapp-bridge` is parked at 0
replicas and the `heartbeat-liveness` CronJob has no Pod between firings,
so a Pod-only sweep is blind to exactly the two shapes idea #178 names.
Deployments, StatefulSets, DaemonSets, CronJobs and Jobs each carry a pod
template; bare Pods are swept too and any Pod with an owner is dropped,
because a ReplicaSet's Pod is its Deployment's image reported twice.

**The verdict is about mutability, not about being behind.** Three ways
to write an image reference and they are different questions:

- `image@sha256:...` pins the bytes. Nothing can change under it. Not raised.
- `image:3.3`, `image:v1.2.0` -- a version tag. It can technically be
  re-pushed, but it names a release, and whether that release is old is
  `pin_drift`'s and `eol_watch`'s question, not this one. Printed, not raised.
- `image:latest`, `image:main`, `image` with no tag at all -- the bytes
  change whenever the registry moves and nothing in the manifest changes
  with them. **That is the finding.** There is no diff to review, no
  ArgoCD change, and no way to say afterwards what was running.

A tag is read as a version when it starts with a digit, optionally after
a leading `v`. `main`, `latest`, `stable`, `alpine`, `nonroot` all name
no release. That rule is a judgement and it is printed in the report, so
a wrong call is visible rather than buried.

**A mutable tag prints the digest actually running under it**, taken from
`imageID` on any live Pod using it, because "what is running right now"
is the one thing a manifest cannot tell you and the cluster can. A
workload with no Pod -- scaled to zero, or a CronJob between firings --
prints that it has none instead, which is a true and useful difference.

**The boundary that follows from reading the cluster: a manifest with no
live object is invisible here.** Idea #178 names a `curlimages/curl:latest`
in the CouchDB init job, and this does not report it, because that Job has
run and been collected -- there is no API object left to read. Reading the
live cluster buys the truth about what runs and costs the ability to see
what would run. `tools.pin_drift` and `tools.eol_watch` read git and have
the opposite trade; neither of the three replaces another.

Exit status, matching `tools.eol_watch` and `tools.argocd_health`: 2 when
a running workload uses a mutable image reference, 1 when kubectl could
not be read -- which includes finding no workloads at all, since this
cluster demonstrably runs some, and never reads as clean -- and 0 when
every image swept is pinned to a digest or to a version tag.
"""

import argparse
import json
import re
import subprocess
import sys

#: Workload kinds that carry a pod template. `Pod` is handled separately
#: because it is the only one that can be owned by another of these.
TEMPLATED = (
    ("deployments", ("spec", "template", "spec")),
    ("statefulsets", ("spec", "template", "spec")),
    ("daemonsets", ("spec", "template", "spec")),
    ("jobs", ("spec", "template", "spec")),
    ("cronjobs", ("spec", "jobTemplate", "spec", "template", "spec")),
)

#: `v1.2.3`, `3.3`, `20-alpine` -- a leading digit, or a `v` and then one.
#: Everything else (`latest`, `main`, `stable`, `alpine`) names no release.
VERSION_TAG_RE = re.compile(r"\Av?\d")


def _run(runner, args):
    """`(body, why)` for one kubectl JSON read."""
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


def split_ref(ref):
    """`(name, tag, digest)` for an image reference.

    The colon in a registry port (`registry:5000/thing`) is not a tag
    separator, so the tag is only the part after the last colon when that
    part carries no slash.
    """
    digest = None
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    tag = None
    head, sep, tail = ref.rpartition(":")
    if sep and "/" not in tail:
        ref, tag = head, tail
    return ref, tag, digest


def normalise(ref):
    """One spelling for an image reference, so two sources can be joined.

    A pod spec says `prom/prometheus:latest` and the container status for
    the very same container says `docker.io/prom/prometheus:latest`, so a
    join on the raw string silently finds nothing and every Docker Hub
    image reports "no Pod is running this" while its Pod is running. That
    is a negative result guaranteed in advance, which is the failure
    `prompt.md` spends four paragraphs on -- and it is only visible
    because the fully-qualified `ghcr.io` images joined fine.
    """
    if ref.startswith("docker.io/"):
        ref = ref[len("docker.io/"):]
    if ref.startswith("library/"):
        ref = ref[len("library/"):]
    return ref


def classify(ref):
    """`"digest"`, `"version"` or `"mutable"` for one image reference."""
    _, tag, digest = split_ref(ref)
    if digest:
        return "digest"
    if tag and VERSION_TAG_RE.match(tag):
        return "version"
    return "mutable"


def _dig(obj, path):
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _containers(pod_spec):
    """Every container in a pod spec, init and ephemeral ones included.

    An init container pulls bytes and runs them as root often enough that
    leaving it out would be a partial answer reported as a whole one --
    and the `curlimages/curl:latest` in the CouchDB init job that idea
    #178 names is exactly one.
    """
    if not isinstance(pod_spec, dict):
        return []
    out = []
    for key in ("initContainers", "containers", "ephemeralContainers"):
        for container in pod_spec.get(key) or []:
            if isinstance(container, dict) and container.get("image"):
                out.append(container)
    return out


def read_workloads(runner=subprocess.run):
    """`(images, problems)` -- every image a workload on this cluster names.

    Each entry is one container: its image reference, the kind and name of
    the workload that asks for it, and its namespace.
    """
    images, problems = [], []
    for kind, path in TEMPLATED:
        body, why = _run(runner, ["kubectl", "get", kind, "-A", "-o", "json"])
        if why:
            problems.append(f"could not read {kind}: {why}")
            continue
        for item in body.get("items") or []:
            meta = item.get("metadata") or {}
            for container in _containers(_dig(item, path)):
                images.append({
                    "ref": container["image"],
                    "kind": kind[:-1],
                    "name": meta.get("name", "?"),
                    "namespace": meta.get("namespace", "?"),
                    "container": container.get("name", "?"),
                })
    return images, problems


def read_pods(runner=subprocess.run):
    """`(unowned_images, resolved, problems)` from the live Pods.

    `resolved` maps an image reference to the digests actually pulled for
    it, which is the half of the question a manifest cannot answer, and it
    is built from every Pod including the owned ones -- the digest running
    under a Deployment's tag comes from that Deployment's own Pod.

    Only Pods with **no** owner come back as images, because a ReplicaSet's
    Pod is its Deployment's image and reporting both is one question asked
    twice.
    """
    body, why = _run(runner, ["kubectl", "get", "pods", "-A", "-o", "json"])
    if why:
        return [], {}, [f"could not read pods: {why}"]
    free, resolved = [], {}
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        status_block = item.get("status") or {}
        statuses = (list(status_block.get("containerStatuses") or [])
                    + list(status_block.get("initContainerStatuses") or []))
        for status in statuses:
            image_id = (status or {}).get("imageID") or ""
            ref = (status or {}).get("image")
            if ref and "@sha256:" in image_id:
                resolved.setdefault(normalise(ref), set()).add(
                    image_id.split("@", 1)[1])
        if meta.get("ownerReferences"):
            continue
        for container in _containers(item.get("spec")):
            free.append({
                "ref": container["image"],
                "kind": "pod",
                "name": meta.get("name", "?"),
                "namespace": meta.get("namespace", "?"),
                "container": container.get("name", "?"),
            })
    return free, resolved, []


def group(images):
    """Collapse one image reference used in many places into one entry.

    `sokrates-agent-runtime:main` runs from a Deployment and from a
    CronJob; that is one decision, and `pin_drift._group`'s rule is that
    one question is reported once with its places listed underneath.
    """
    groups = {}
    for image in images:
        groups.setdefault(image["ref"], []).append(image)
    return groups


def _places(members):
    seen, out = set(), []
    for member in members:
        where = "%s/%s %s" % (member["namespace"], member["kind"], member["name"])
        if where not in seen:
            seen.add(where)
            out.append(where)
    return sorted(out)


def format_report(images, resolved, problems):
    out = []
    by_verdict = {"mutable": [], "version": [], "digest": []}
    for image in images:
        by_verdict[classify(image["ref"])].append(image)

    mutable = group(by_verdict["mutable"])
    if mutable:
        out.append("MUTABLE IMAGE — %d image reference(s) can change under a "
                   "running workload with nothing in git to show it."
                   % len(mutable))
        for ref, members in sorted(mutable.items()):
            out.append("  %s" % ref)
            digests = sorted(resolved.get(normalise(ref)) or [])
            if digests:
                out.append("      running now: %s" % ", ".join(digests))
            else:
                out.append("      no Pod is running this right now, so there "
                           "is no digest to report")
            for place in _places(members):
                out.append("      %s" % place)

    versioned = group(by_verdict["version"])
    if versioned:
        out.append("PINNED BY VERSION — %d reference(s). Whether the version "
                   "is old is tools.pin_drift's and tools.eol_watch's "
                   "question, not this one:" % len(versioned))
        for ref, members in sorted(versioned.items()):
            out.append("  %s — %s" % (ref, ", ".join(_places(members))))

    digested = group(by_verdict["digest"])
    if digested:
        out.append("PINNED BY DIGEST — %d reference(s), which is the shape "
                   "this check is looking for:" % len(digested))
        for ref, members in sorted(digested.items()):
            name = ref.split("@", 1)[0]
            out.append("  %s — %s" % (name, ", ".join(_places(members))))

    for problem in problems:
        out.append("PROBLEM  %s" % problem)

    out.append("Read %d container image reference(s) across %d distinct "
               "reference(s) from the live cluster, not from git. A tag is "
               "read as a version when it starts with a digit, optionally "
               "after a `v`; `latest`, `main` and `alpine` name no release "
               "and are mutable." % (len(images), len(group(images))))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    images, problems = read_workloads()
    free, resolved, pod_problems = read_pods()
    images += free
    problems += pod_problems

    print(format_report(images, resolved, problems))

    if any(classify(i["ref"]) == "mutable" for i in images):
        # A finding outranks an incomplete sweep, the same call
        # `eol_watch` makes: both are true and only one is actionable.
        return 2
    if problems:
        print("Something here was unreadable, so this run cannot claim the "
              "sweep was complete.")
        return 1
    if not images:
        # An empty answer from a working kubectl is not a clean bill of
        # health on a cluster that demonstrably runs workloads.
        print("No workload image was read at all, which is no instrument "
              "rather than no finding.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
