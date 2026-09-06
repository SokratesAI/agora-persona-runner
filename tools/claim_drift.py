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

**"ordered" used to be a word this check had not earned.** The XRD gives
`publicPort`, `internalPort`, `metricsPort` and `persistenceSize`
defaults, and the API server writes those defaults into the stored
object, so every one of the four is always present on the live XR
whatever anybody asked for. Reading the live XR alone therefore cannot
tell a value somebody chose from a value the schema supplied, and until
Cycle 1025 this printed `ordered 8080` about all of them. Measured
2026-09-06: not one of the four claim files in `platform-config` sets any
of the four fields, so every number this check had ever called an order
was an XRD default.

So the source of the *order* is the claim as written in git --
`crossplane/service-<name>.yaml` in `SokratesAI/platform-config` -- while
the value compared is still the live XR's, because that is what
Crossplane reconciles. A field the claim file does not set is reported as
defaulted rather than ordered, and it still counts as drift: the stored
XR says 8080 and the service runs 8090 either way, so the claim does not
describe the service. What changes is the sentence, not the verdict --
"nobody ordered this and the default disagrees" is a different problem
from "the order went stale", and only one of them is fixed by step 4.
A claim file this cannot find is reported as such and its fields are
labelled unknown, never assumed ordered.

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


def read_ordered_fields(service, runner=subprocess.run):
    """Which of `FIELDS` the claim file in git actually sets, or a reason.

    The live XR carries all four whatever was asked for, so the only
    honest source for "was this ordered" is the YAML somebody wrote. It
    lives at a known path per service; a claim written to a differently
    named file is not found, and that is returned as a reason rather than
    read as "ordered nothing", which would print a confident sentence
    about a file this never opened.
    """
    body, why = _run(runner, ["gh", "api",
                              "repos/SokratesAI/platform-config/contents/"
                              "crossplane/service-%s.yaml" % service,
                              "--jq", ".content"])
    if why:
        return None, why
    try:
        raw = base64.b64decode(body.replace("\n", ""))
    except (ValueError, TypeError) as exc:
        return None, "the claim file did not decode: %s" % exc
    try:
        docs = [d for d in yaml.safe_load_all(raw) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        return None, "the claim file is not YAML: %s" % exc
    for doc in docs:
        if doc.get("kind") != "GitHubService":
            continue
        spec = doc.get("spec", {}) or {}
        if (spec.get("serviceName") or
                doc.get("metadata", {}).get("name")) != service:
            continue
        return {f for f in FIELDS if f in spec}, None
    return None, ("crossplane/service-%s.yaml holds no GitHubService named "
                  "%s" % (service, service))


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


def compare(claim, docs, ordered_fields=None):
    """`[(field, value, deployed, source)]`, `deployed` may be None.

    `value` is the live XR's, which is what Crossplane reconciles.
    `source` is `"ordered"` when the claim file in git sets the field,
    `"default"` when it does not, and `"unknown"` when the claim file
    could not be read -- `ordered_fields` is `None` in that case and this
    never guesses.
    """
    service = claim["name"]
    spec = claim["spec"]
    deployment = _deployment(docs, service)
    pvc = _pvc(docs, service)

    rows = []
    for field in FIELDS:
        if field not in spec:
            rows.append((field, None, None, "unknown"))
            continue
        value = str(spec[field])
        if field == "persistenceSize":
            deployed = None if pvc is None else _storage(pvc)
        else:
            deployed = (None if deployment is None
                        else _env_value(deployment, ENV_FOR[field]))
        if ordered_fields is None:
            source = "unknown"
        else:
            source = "ordered" if field in ordered_fields else "default"
        rows.append((field, value, deployed, source))
    return rows, deployment is not None, pvc is not None


def is_drifted(rows, has_deployment, has_pvc):
    """Does this claim still describe its service?

    A field the claim sets and the manifest does not carry counts, not
    only a field the two spell differently: the composition templates an
    env var for every port, so an absent one means the manifest has moved
    away from the shape the claim ordered. A shape mismatch -- no
    Deployment under that name, no `<service>-data` PVC -- counts on its
    own, and it is the only rule left for a claim that sets none of the
    four, where the field rule has nothing to compare and would call a
    manifest of some other shape an agreement.

    Whether a value was ordered or defaulted does not enter into it: the
    stored XR says 8080 either way and the service runs 8090 either way,
    so the claim fails to describe it either way. That distinction is the
    report's job, not the verdict's -- making the sentence honest must
    not make the alarm quieter.
    """
    if not has_deployment or not has_pvc:
        return True
    return any(value is not None and value != deployed
               for _field, value, deployed, _source in rows)


def format_report(results, problems):
    out = []
    drifted = [r for r in results if r["drift"]]
    agreed = [r for r in results if not r["drift"]]

    if drifted:
        out.append("CLAIM NO LONGER DESCRIBES THE SERVICE — %d of %d "
                   "GitHubService claim(s). The manifest is the correct "
                   "value on every one measured so far, so the fix is never "
                   "editing manifest.yaml to match; it is idea #158 step 4 "
                   "(compose objects, not text) for a field somebody "
                   "ordered, and ordering the field at all for one the XRD "
                   "merely defaulted."
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
            for field, value, deployed, source in row["rows"]:
                if value is None:
                    out.append("      %s: not set on the claim, not compared"
                               % field)
                    continue
                said = {"ordered": "ordered %s" % value,
                        "default": "never ordered, XRD default %s" % value,
                        "unknown": "%s on the XR, claim file unread" % value}[
                            source]
                if deployed is None:
                    out.append("      %s: %s, absent from the manifest"
                               % (field, said))
                elif value != deployed:
                    out.append("      %s: %s, deployed %s"
                               % (field, said, deployed))

    for row in agreed:
        out.append("AGREES  %s — all %d templated field(s) still match"
                   % (row["name"], len(FIELDS)))

    unordered = [r["name"] for r in results
                 if all(source == "default"
                        for _f, _v, _d, source in r["rows"])]
    if unordered:
        out.append("NOT SELF-SERVICE — %d of %d claim(s) order none of the "
                   "four fields, so every value above is the XRD's default "
                   "rather than anybody's order: %s. That is a different "
                   "problem from a stale order and step 4 does not fix it: "
                   "these services were never ordered through the claim, "
                   "they were described by it afterwards."
                   % (len(unordered), len(results), ", ".join(unordered)))

    for problem in problems:
        out.append("PROBLEM  %s" % problem)

    out.append("Read %d live GitHubService claim(s) from the API server, "
               "their manifest.yaml from GitHub, and each claim's own YAML in "
               "platform-config to tell an ordered field from an XRD default. "
               "Only the four fields the composition templates are compared: "
               "%s. Nothing here says what is running — that is "
               "tools.running_images and tools.workload_health."
               % (len(results), ", ".join(FIELDS)))
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
        ordered_fields, why = read_ordered_fields(claim["name"])
        if why:
            problems.append("%s: %s" % (claim["name"], why))
        rows, has_deployment, has_pvc = compare(claim, docs, ordered_fields)
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
