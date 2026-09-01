"""Does a `GitHubService` claim still describe the service it ordered?

Cycle 654, working idea #158 -- the owner's ask for a self-service
platform. Four write-ups on that row say the same sentence about this
system and none of them ever measured it:

> a `GitHubService` orders two GitHub repos and writes the deployment YAML
> into the second one as a text blob, which ArgoCD applies later.
> Crossplane's involvement ends when the text lands in git, so change a
> field afterwards and the file keeps the old value silently.

That is an argument, not a number. This check turns it into one.

    python3 -m tools.claim_drift

**What it compares.** The `GitHubService` composition templates exactly
four claim fields into `manifest.yaml` in the paired `<name>-config`
repo: `publicPort` becomes the `PORT` env var, the container port and the
Service port; `internalPort` becomes `INTERNAL_PORT`; `metricsPort`
becomes `METRICS_PORT`; `persistenceSize` becomes the PVC's requested
storage. Every `RepositoryFile` in that composition carries
`managementPolicies: [Observe, Create, LateInitialize]` -- no `Update` --
so the file is seeded once and never reconciled again. This reads the
live claim from the cluster and the live `manifest.yaml` from GitHub and
says where the two disagree.

**The finding is not "go and edit the manifest."** The manifest is very
often the correct value and the claim is the stale one: a service whose
real port changed after it was seeded is working exactly as intended, and
the claim is simply no longer a description of it. Editing the file to
match the claim would break running services. The fix this reports
towards is step 4 of idea #158 -- making the composition compose real
Kubernetes objects rather than write text -- after which "ordered" and
"exists" cannot disagree, because they are the same object.

**Reading both sides from live sources is the point.** The claim comes
from the API server, not from `platform-config`, because an XR that was
edited by hand is still what Crossplane reconciles; the manifest comes
from GitHub's API, not from a local checkout, because the checkout may be
behind and because these `-config` repos are not cloned here at all.

**A field the claim does not set is not compared.** The XRD gives
`publicPort`, `internalPort`, `metricsPort` and `persistenceSize`
defaults, and Kubernetes writes those defaults into the stored object, so
in practice all four are always present -- but a claim missing one is
reported as not-compared rather than compared against a guess.

**What it cannot see.** It looks for a Deployment named after the service
and a PVC named `<service>-data`, which is what the template writes. A
manifest that grew a second workload, or renamed the first, does not
match, and that is reported as its own kind of drift rather than as
agreement -- the claim describes one Deployment and the repo holds
something else, which is the same finding wearing a different coat. It
does not read what is *running*; `tools.running_images` and
`tools.workload_health` own that question.

Exit 2 when a claim and its manifest disagree, 1 when something could not
be read (so a clean sweep is never confused with a blind one), 0 when
every claim still describes its service.
"""

import argparse
import base64
import json
import subprocess
import sys

import yaml

#: Claim field -> how it reaches `manifest.yaml`. The reader for each is
#: below; the label is what the report prints.
FIELDS = ("publicPort", "internalPort", "metricsPort", "persistenceSize")

ENV_FOR = {"publicPort": "PORT",
           "internalPort": "INTERNAL_PORT",
           "metricsPort": "METRICS_PORT"}


def _run(runner, args):
    """`(stdout, why)` for one shell read."""
    try:
        proc = runner(args, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "%s failed: %s" % (args[0], exc)
    if proc.returncode != 0:
        return None, "%s failed: %s" % (
            args[0], (proc.stderr or proc.stdout).strip())
    return proc.stdout, None


def read_claims(runner=subprocess.run):
    """Every live `GitHubService`, as `[{name, namespace, spec}]`."""
    body, why = _run(runner, ["kubectl", "get",
                              "githubservices.platform.sokratesai.io",
                              "-A", "-o", "json"])
    if why:
        return [], [why]
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        return [], ["kubectl returned something that is not JSON: %s" % exc]
    claims = []
    for item in parsed.get("items", []):
        meta = item.get("metadata", {})
        spec = item.get("spec", {})
        name = spec.get("serviceName") or meta.get("name")
        if not name:
            continue
        claims.append({"name": name,
                       "namespace": meta.get("namespace", ""),
                       "spec": spec})
    return claims, []


def read_manifest(service, runner=subprocess.run):
    """The parsed docs of `<service>-config/manifest.yaml`, or a reason."""
    body, why = _run(runner, ["gh", "api",
                              "repos/SokratesAI/%s-config/contents/manifest.yaml"
                              % service, "--jq", ".content"])
    if why:
        return None, why
    try:
        raw = base64.b64decode(body.replace("\n", ""))
    except (ValueError, TypeError) as exc:
        return None, "manifest.yaml did not decode: %s" % exc
    try:
        docs = [d for d in yaml.safe_load_all(raw) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        return None, "manifest.yaml is not YAML: %s" % exc
    return docs, None


def _deployment(docs, service):
    for doc in docs:
        if doc.get("kind") == "Deployment" and \
                doc.get("metadata", {}).get("name") == service:
            return doc
    return None


def _pvc(docs, service):
    want = "%s-data" % service
    for doc in docs:
        if doc.get("kind") == "PersistentVolumeClaim" and \
                doc.get("metadata", {}).get("name") == want:
            return doc
    return None


def _env_value(deployment, key):
    """The value of one env var on the deployment's own container."""
    spec = deployment.get("spec", {}).get("template", {}).get("spec", {})
    for container in spec.get("containers", []) or []:
        for env in container.get("env", []) or []:
            if env.get("name") == key:
                value = env.get("value")
                # An env var sourced from `valueFrom` carries no literal, and
                # `str(None)` would report the string "None" as the deployed
                # value. There is nothing to compare, so say absent.
                if value is None:
                    return None
                # Kubernetes env values are strings; the claim's ports are
                # integers. Compare as text so 8080 and '8080' agree.
                return str(value)
    return None


def _storage(pvc):
    requests = pvc.get("spec", {}).get("resources", {}).get("requests", {})
    value = requests.get("storage")
    return None if value is None else str(value)


def compare(claim, docs):
    """`[(field, ordered, deployed)]` for this claim, `deployed` may be None."""
    service = claim["name"]
    spec = claim["spec"]
    deployment = _deployment(docs, service)
    pvc = _pvc(docs, service)

    rows = []
    for field in FIELDS:
        if field not in spec:
            rows.append((field, None, None))
            continue
        ordered = str(spec[field])
        if field == "persistenceSize":
            deployed = None if pvc is None else _storage(pvc)
        else:
            deployed = (None if deployment is None
                        else _env_value(deployment, ENV_FOR[field]))
        rows.append((field, ordered, deployed))
    return rows, deployment is not None, pvc is not None


def is_drifted(rows, has_deployment, has_pvc):
    """Does this claim still describe its service?

    A field the claim sets and the manifest does not carry counts, not
    only a field the two spell differently: the composition templates an
    env var for every port, so an absent one means the manifest has moved
    away from the shape the claim ordered. A shape mismatch -- no
    Deployment under that name, no `<service>-data` PVC -- counts on its
    own. For a claim carrying the XRD's defaults the field rule already
    catches that, since every templated value then reads as absent; the
    guard is for a claim that sets none of the four, where the field rule
    has nothing to compare and would call a manifest of some other shape
    an agreement.
    """
    if not has_deployment or not has_pvc:
        return True
    return any(ordered is not None and ordered != deployed
               for _field, ordered, deployed in rows)


def format_report(results, problems):
    out = []
    drifted = [r for r in results if r["drift"]]
    agreed = [r for r in results if not r["drift"]]

    if drifted:
        out.append("CLAIM NO LONGER DESCRIBES THE SERVICE — %d of %d "
                   "GitHubService claim(s). The manifest is usually the "
                   "correct value and the claim the stale one; the fix is "
                   "idea #158 step 4 (compose objects, not text), never "
                   "editing manifest.yaml to match."
                   % (len(drifted), len(results)))
        for row in drifted:
            out.append("  %s (%s)" % (row["name"], row["namespace"]))
            if not row["has_deployment"]:
                out.append("      no Deployment named %s in manifest.yaml — "
                           "the claim describes one workload and the repo "
                           "holds something else" % row["name"])
            if not row["has_pvc"]:
                out.append("      no PersistentVolumeClaim named %s-data in "
                           "manifest.yaml" % row["name"])
            for field, ordered, deployed in row["rows"]:
                if ordered is None:
                    out.append("      %s: not set on the claim, not compared"
                               % field)
                elif deployed is None:
                    out.append("      %s: ordered %s, absent from the manifest"
                               % (field, ordered))
                elif ordered != deployed:
                    out.append("      %s: ordered %s, deployed %s"
                               % (field, ordered, deployed))

    for row in agreed:
        out.append("AGREES  %s — all %d templated field(s) still match"
                   % (row["name"], len(FIELDS)))

    for problem in problems:
        out.append("PROBLEM  %s" % problem)

    out.append("Read %d live GitHubService claim(s) from the API server and "
               "their manifest.yaml from GitHub. Only the four fields the "
               "composition templates are compared: %s. Nothing here says "
               "what is running — that is tools.running_images and "
               "tools.workload_health." % (len(results), ", ".join(FIELDS)))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    claims, problems = read_claims()
    results = []
    for claim in claims:
        docs, why = read_manifest(claim["name"])
        if why:
            problems.append("%s: %s" % (claim["name"], why))
            continue
        rows, has_deployment, has_pvc = compare(claim, docs)
        drift = is_drifted(rows, has_deployment, has_pvc)
        results.append({"name": claim["name"],
                        "namespace": claim["namespace"],
                        "rows": rows,
                        "has_deployment": has_deployment,
                        "has_pvc": has_pvc,
                        "drift": drift})

    print(format_report(results, problems))

    if any(r["drift"] for r in results):
        # A finding outranks an incomplete sweep: both are true and only
        # one is actionable. Same call `running_images` makes.
        return 2
    if problems:
        print("Something here was unreadable, so this run cannot claim the "
              "sweep was complete.")
        return 1
    if not results:
        # No claim read at all is no instrument, not a clean bill of health
        # on a cluster that demonstrably has GitHubService XRs.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
