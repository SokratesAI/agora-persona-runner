"""Does every Crossplane claim in `platform-config` still fit the live schema?

Cycle 687, closing the last open half of idea #138 -- "keep this" promotes a
demo to UAT in one tap. The tap is a merge: `tools.demo promote` opens a pull
request adding a `GitHubService` claim, the owner taps Merge in a meeting, and
five minutes later there is meant to be a repo, an image and a tailnet
hostname. Nothing between the render and the merge asks whether the claim the
button will apply is a shape the cluster accepts.

    python3 -m tools.claim_schema

**This has already gone wrong once, in this repo.** `platform-config#577` is
titled *"Unstick sokratesai-infra: a claim cannot carry a field the live CRD
lacks"* -- a claim carrying a field the API server did not know about, merged,
and the ArgoCD Application stopped syncing for everything else in it too. The
failure is quiet in the worst way: the YAML is well-formed, the review reads
fine, the merge succeeds, and the damage lands minutes later in a sync status
nobody is watching. That is the exact shape a one-tap promotion turns into a
routine act.

**The live schema is readable, and the CRD is not.** `kubectl get crd` is
Forbidden to both of my service accounts -- measured this cycle from the bridge
pod and from the runner pod, same answer -- which is why no earlier check ever
compared a claim against anything but the copy of the XRD in git. But a
Crossplane `CompositeResourceDefinition` is an ordinary custom resource, and
`kubectl get xrd` answers. The XRD is what generates the CRD, so its
`openAPIV3Schema` is the same schema the API server validates against, read
from the live cluster rather than from a file that may not have synced.
Reading git's copy would defeat the point: the drift this looks for is exactly
the case where the two disagree.

**What it validates.** For every YAML document under `crossplane/` in the
`platform-config` checkout whose `apiVersion` group and `kind` match an XRD
offered by the cluster, the document's `spec` is checked against that XRD's
schema: unknown properties, missing required ones, wrong types, `pattern`,
`enum`, `minimum`/`maximum` and `minProperties`. Then the claim text
`tools.demo promote` would render is validated the same way, without opening
anything, so the promotion path is judged and not just the claims that already
merged.

**What it does not validate, and says so on every run.** A claim that fits the
schema can still fail to reconcile -- a name already taken on GitHub, a
composition that errors, a provider without credentials. This judges the claim
text against the schema and nothing further downstream; `tools.crossplane_health`
is what reads whether a live claim actually became something.

Exit 0 when every claim it could read fits. Exit 2 when one does not. Exit 1
when it could not read the XRDs or the checkout at all -- a check that could
not run must never read as a check that came back clean.
"""

import json
import os
import re
import subprocess
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is present on both pods
    yaml = None


#: The repo whose `crossplane/` directory holds every claim.
CHECKOUT = "platform-config"

#: Directory inside it. Claims sit at the top of it and under `claims/`.
CLAIM_DIR = "crossplane"

#: The name `promotion_claim` is rendered under when this validates the
#: promotion path. It is never written anywhere -- a legal service name that
#: exercises the pattern is all that is wanted.
SAMPLE_DEMO = "demo-promotion-probe"


def workspace():
    return os.environ.get("NOVA_WORKSPACE") or "/data/workspace"


def checkout_dir():
    return os.path.join(workspace(), CHECKOUT)


def read_live_xrds(run=None):
    """`{(group, kind): (served version name, spec schema)}` from the cluster.

    Returns `None` when the cluster could not be asked, which is a different
    answer from an empty dict and is reported as UNREADABLE rather than as a
    clean sweep.
    """
    run = run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, timeout=60))
    try:
        done = run(["kubectl", "get", "xrd", "-o", "json"])
    except Exception:
        return None
    if done.returncode != 0:
        return None
    try:
        listing = json.loads(done.stdout)
    except ValueError:
        return None
    return schemas_from_xrds(listing.get("items") or [])


def schemas_from_xrds(items):
    """Pull `{(group, kind): (version, spec schema)}` out of XRD objects.

    A claim names the *claim* kind when an XRD offers one (`claimNames`) and
    the composite kind otherwise. Both are registered here under the same
    schema, because both are validated by it.
    """
    out = {}
    for item in items:
        spec = item.get("spec") or {}
        group = spec.get("group")
        names = spec.get("names") or {}
        claim_names = spec.get("claimNames") or {}
        kinds = [k for k in (names.get("kind"), claim_names.get("kind")) if k]
        if not group or not kinds:
            continue
        for version in spec.get("versions") or []:
            if not version.get("served", True):
                continue
            schema = (((version.get("schema") or {})
                       .get("openAPIV3Schema") or {})
                      .get("properties") or {}).get("spec")
            if not schema:
                continue
            for kind in kinds:
                out[(group, kind)] = (version.get("name"), schema)
    return out


def _type_ok(value, expected):
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True


def validate(spec, schema, path="spec"):
    """`(problems, caveats)` for `spec` against an XRD `openAPIV3Schema`.

    Deliberately narrow: it implements the keywords these XRDs actually use
    (`type`, `pattern`, `enum`, `minimum`, `maximum`, `minProperties`,
    `additionalProperties`, `required`) and nothing else. A keyword it does
    not know is not silently treated as satisfied -- it comes back as a
    caveat naming the keyword, because a validator that quietly ignores half
    a schema is the guard-that-guards-nothing shape this loop keeps paying
    for. A caveat is not a finding: `x-kubernetes-validations` holds CEL
    rules the API server evaluates and I cannot, and a claim that carries
    one is not thereby wrong.
    """
    known = {
        "type", "pattern", "enum", "minimum", "maximum", "minProperties",
        "additionalProperties", "required", "properties", "default",
        "description", "items", "format", "x-kubernetes-preserve-unknown-fields",
    }
    problems = []
    caveats = []
    if not isinstance(spec, dict):
        return [f"{path} is {type(spec).__name__}, not a mapping"], caveats

    unknown_keywords = sorted(set(schema) - known)
    if unknown_keywords:
        caveats.append(
            f"{path} carries {', '.join(unknown_keywords)}, which this "
            "validator does not implement, so that part is unjudged")

    properties = schema.get("properties") or {}
    for missing in sorted(set(schema.get("required") or []) - set(spec)):
        problems.append(f"{path}.{missing} is required and is not set")
    for key in sorted(set(spec) - set(properties)):
        problems.append(
            f"{path}.{key} is not a field the live schema has -- "
            "a claim carrying it is refused on apply")

    for key in sorted(set(spec) & set(properties)):
        sub_problems, sub_caveats = _check_value(
            spec[key], properties[key], f"{path}.{key}")
        problems.extend(sub_problems)
        caveats.extend(sub_caveats)
    return problems, caveats


def _check_value(value, rules, path):
    problems = []
    caveats = []
    expected = rules.get("type")
    if expected and not _type_ok(value, expected):
        problems.append(
            f"{path} is {type(value).__name__}, and the live schema says {expected}")
        return problems, caveats
    if "enum" in rules and value not in rules["enum"]:
        problems.append(
            f"{path} is {value!r}; the live schema allows "
            f"{', '.join(repr(v) for v in rules['enum'])}")
    if "pattern" in rules and isinstance(value, str):
        if not re.search(rules["pattern"], value):
            problems.append(
                f"{path} is {value!r}, which does not match the live "
                f"schema's pattern {rules['pattern']}")
    if "minimum" in rules and isinstance(value, (int, float)):
        if value < rules["minimum"]:
            problems.append(f"{path} is {value}, below the minimum {rules['minimum']}")
    if "maximum" in rules and isinstance(value, (int, float)):
        if value > rules["maximum"]:
            problems.append(f"{path} is {value}, above the maximum {rules['maximum']}")
    if "minProperties" in rules and isinstance(value, dict):
        if len(value) < rules["minProperties"]:
            problems.append(
                f"{path} has {len(value)} entr(y/ies), below the "
                f"minimum {rules['minProperties']}")
    if rules.get("properties") or rules.get("required"):
        sub_problems, sub_caveats = validate(value, rules, path)
        problems.extend(sub_problems)
        caveats.extend(sub_caveats)
    return problems, caveats


def claim_documents(directory):
    """`(relative path, document)` for every YAML document under `directory`."""
    found = []
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if not name.endswith((".yaml", ".yml")):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, directory)
            try:
                with open(full) as fh:
                    docs = list(yaml.safe_load_all(fh))
            except Exception as exc:  # a composition embeds templates; skip loudly
                found.append((rel, {"__unreadable__": str(exc)[:200]}))
                continue
            for doc in docs:
                if isinstance(doc, dict):
                    found.append((rel, doc))
    return found


def judge(documents, schemas):
    """`(findings, judged, skipped_kinds, caveats)` for a set of documents."""
    findings = []
    judged = 0
    skipped = set()
    caveats = set()
    for rel, doc in documents:
        if "__unreadable__" in doc:
            findings.append((rel, [f"could not parse: {doc['__unreadable__']}"]))
            continue
        api = doc.get("apiVersion") or ""
        kind = doc.get("kind")
        group = api.split("/")[0] if "/" in api else ""
        if not kind:
            continue
        key = (group, kind)
        if key not in schemas:
            if group.endswith("sokratesai.io"):
                skipped.add(f"{kind}.{group}")
            continue
        _version, schema = schemas[key]
        judged += 1
        problems, doc_caveats = validate(doc.get("spec") or {}, schema)
        for caveat in doc_caveats:
            caveats.add(f"{kind} {caveat}")
        if problems:
            name = (doc.get("metadata") or {}).get("name", "?")
            findings.append((f"{rel} ({kind}/{name})", problems))
    return findings, judged, skipped, caveats


def promotion_document():
    """The claim `tools.demo promote` would render, as a parsed document."""
    from agora_runner.nova_demos import promotion_claim

    text = promotion_claim(
        SAMPLE_DEMO, "A probe, never written anywhere.",
        "https://example.invalid/demo/probe/", "/data/workspace/demos/probe",
        "2026-01-01")
    return yaml.safe_load(text)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if yaml is None:
        print("UNREADABLE — no yaml module on this pod, so no claim was judged.")
        return 1

    schemas = read_live_xrds()
    if schemas is None:
        print("UNREADABLE — `kubectl get xrd` could not be read, so no claim "
              "was judged against the live schema. This is not a clean sweep.")
        return 1
    if not schemas:
        print("UNREADABLE — the cluster offers no XRD, so there is nothing to "
              "validate a claim against. This is not a clean sweep.")
        return 1

    directory = os.path.join(checkout_dir(), CLAIM_DIR)
    if not os.path.isdir(directory):
        print(f"UNREADABLE — no {CHECKOUT} checkout at {checkout_dir()}, so no "
              "claim in git was judged. This is not a clean sweep.")
        return 1

    documents = claim_documents(directory)
    findings, judged, skipped, caveats = judge(documents, schemas)

    try:
        promo = promotion_document()
    except Exception as exc:
        findings.append(("tools.demo promote (rendered claim)",
                         [f"could not render: {str(exc)[:200]}"]))
        promo = None
    if promo is not None:
        promo_findings, promo_judged, _, promo_caveats = judge(
            [("tools.demo promote (rendered claim)", promo)], schemas)
        findings.extend(promo_findings)
        judged += promo_judged
        caveats |= promo_caveats
        if not promo_judged:
            findings.append(
                ("tools.demo promote (rendered claim)",
                 [f"renders {promo.get('apiVersion')} {promo.get('kind')}, "
                  "which no XRD in this cluster offers -- the promotion PR "
                  "would merge and never reconcile"]))

    kinds = sorted({f"{k}.{g}" for (g, k) in schemas})
    for where, problems in findings:
        print(f"CLAIM DOES NOT FIT THE LIVE SCHEMA — {where}")
        for problem in problems:
            print(f"    {problem}")
    print(f"Judged {judged} claim(s) against {len(kinds)} live XRD schema(s) "
          f"read from the cluster, not from git: {', '.join(kinds)}. The "
          "promotion claim `tools.demo promote` renders is judged too, without "
          "opening anything.")
    print("    NOT JUDGED  whether a claim that fits actually reconciles — a "
          "taken repository name, a failing composition or a provider without "
          "credentials all pass this and fail later; tools.crossplane_health "
          "reads that half.")
    for caveat in sorted(caveats):
        print(f"    NOT JUDGED  {caveat}.")
    if skipped:
        print(f"    NOT JUDGED  {', '.join(sorted(skipped))} — a platform kind "
              "in git that no live XRD offers, so there is no schema to judge "
              "it against.")
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
