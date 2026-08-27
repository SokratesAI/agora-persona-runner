"""Is every Crossplane resource in this cluster still reconciling — and if not, why?

Cycle 526, out of the owner's issue #109. Crossplane spent an afternoon failing
to make `sokrates-docs` public: the composition asked GitHub to switch on
secret scanning in the same atomic `PATCH` that flipped visibility, and GitHub
validates that block against the repo's *current* visibility, so every
reconcile came back `422 Secret scanning is not available for this
repository`. The fix is merged and the repo is public. **The half that was
never fixed is that nothing noticed.**

    python3 -m tools.crossplane_health

The reason it went unnoticed is structural rather than bad luck, and it is
the whole argument for this tool. A Crossplane claim aggregates the health of
what it composes *optimistically*: `GitHubService/sokrates-docs` read
`SYNCED=True READY=True` with `spec.visibility: public` on it, all day, while
the `Repository` one layer down carried the 422. So the object a person would
think to look at is exactly the object that cannot show the failure.
`tools.argocd_health` does not close this either — ArgoCD's job ends when the
manifest is applied, and a claim that applied cleanly and then failed at the
provider is `Synced, Healthy` to ArgoCD forever.

**It reads the live cluster, never git**, for the same reason
`helm_repo_health` and `argocd_health` do: the failure is a statement about
what a provider did with a manifest, and the manifest was fine.

**`ReconcilePaused` is not a failure here, and this checks the annotation
rather than the reason string.** 28 of the 37 managed resources in this
cluster are paused right now and every one of them is meant to be: the
`repositoryfile-lockdown` WatchOperation in `platform-config` pauses each
`RepositoryFile` once it reaches `Ready=True`, so a template file is seeded
once and then left alone. A check that raised on those would be red on 76% of
the cluster from the day it was written, which is the same as being off. But
`crossplane.io/paused: "true"` is what *makes* it deliberate, so that is what
gets read — a resource reporting `ReconcilePaused` without the annotation is
reported, because then the pause came from somewhere nobody wrote down.

**A kind this account cannot list is a blind spot, and the blind spot is
measured rather than assumed.** `kubectl get managed` is refused on 73 of the
GitHub provider's kinds here and answers for the rest, so the sweep is
partial by construction and a tool that ignored that would be reporting
"clean" over a hole. It is not guesswork which part of the hole matters: a
composite lists everything it composes in `spec.crossplane.resourceRefs`, so
anything referenced there and missing from the sweep is a resource that
really exists and that I really cannot see. Today that is the two
`ActionsSecret` objects on each `GitHubService`. Those raise; a refused kind
nothing references does not, because red on day one and forever is the same
as off.

**Synced and Ready are separate verdicts and this never merges them**, the
same call `argocd_health` makes on sync-versus-health one layer up.
`Synced=False` means Crossplane could not get the desired state to the
provider — that is the 422 case, and the provider's own message is the
finding. `Ready=False` means the provider accepted it and the external
resource is not usable yet. They have different causes and different fixes.

Every failing managed resource is printed **with the composite that owns it
and that composite's own verdict**, because "the claim says True and the
thing under it says False" is the specific shape of the incident this exists
for, and a report that showed only the leaf would leave a cycle to rediscover
that the claim above it lies.

Exit status, matching `tools.security_alerts`, `tools.cli_pin`,
`tools.agentic_health`, `tools.heartbeat_health`, `tools.helm_repo_health`
and `tools.argocd_health`: **2 means a Crossplane resource is failing to
reconcile for a reason a cycle could act on**, 1 means something was
unreadable — which includes zero resources, since this cluster demonstrably
runs Crossplane and an empty answer is a query that looked in the wrong
place, and includes a composed resource this account cannot list — and 0
means everything answered, naming what it swept either way.
"""

import argparse
import datetime
import json
import re
import subprocess
import sys

PAUSED_ANNOTATION = "crossplane.io/paused"


# `... is forbidden: User "x" cannot list resource "actionssecrets" in API
# group "actions.github.m.upbound.io" ...` -- the kind is the plural resource
# name, which is what the refusal names and what an RBAC rule would name back.
FORBIDDEN = re.compile(
    r'cannot list resource "(?P<resource>[^"]+)" in API group "(?P<group>[^"]*)"')


def _run(runner, args):
    """(body, refused, why). A partial answer is an answer.

    `kubectl get managed` asks the API server for every kind in the category
    and reports each refusal on stderr while still returning the kinds it
    could read -- and it exits 1 having done so. Treating that exit as a
    failure would throw away 37 real resources over 73 kinds nothing here
    composes, so the refusals are parsed out and carried instead.
    """
    try:
        proc = runner(args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, [], f"kubectl failed: {exc}"

    refused = sorted({
        f"{m.group('resource')}.{m.group('group')}" if m.group("group")
        else m.group("resource")
        for m in FORBIDDEN.finditer(proc.stderr or "")
    })
    try:
        body = json.loads(proc.stdout)
    except ValueError as exc:
        if proc.returncode != 0:
            return None, refused, (
                f"kubectl failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return None, refused, f"kubectl returned something that is not JSON: {exc}"
    if not isinstance(body, dict):
        return None, refused, "kubectl returned JSON that is not an object"
    return body, refused, None


def _conditions(item):
    return {
        c.get("type"): c
        for c in ((item.get("status") or {}).get("conditions") or [])
        if isinstance(c, dict)
    }


def _read(runner, category):
    """Every resource in a Crossplane kubectl category, as (list, None) or (None, why).

    `managed` and `composite` are categories Crossplane stamps onto the CRDs
    it installs, so one query covers every provider and every XRD without
    this tool holding a list of kinds that would go stale the first time a
    new provider lands.
    """
    body, refused, why = _run(runner, ["kubectl", "get", category, "-A", "-o", "json"])
    if why:
        return None, refused, why

    rows = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        conds = _conditions(item)
        synced = conds.get("Synced") or {}
        ready = conds.get("Ready") or {}
        owners = [
            (o.get("kind") or "?", o.get("name") or "?")
            for o in meta.get("ownerReferences") or []
            if o.get("controller")
        ]
        rows.append({
            "kind": item.get("kind") or "?",
            "name": meta.get("name", "?"),
            "namespace": meta.get("namespace") or "",
            "paused": (meta.get("annotations") or {}).get(PAUSED_ANNOTATION) == "true",
            "synced": synced.get("status") or "Unknown",
            "synced_reason": synced.get("reason") or "",
            # The provider's own error text. It is the only place the 422
            # ever appeared, so it is quoted rather than summarised.
            "message": (synced.get("message") or ready.get("message") or "").strip(),
            "since": synced.get("lastTransitionTime") or ready.get("lastTransitionTime") or "",
            "ready": ready.get("status") or "Unknown",
            "ready_reason": ready.get("reason") or "",
            "owners": owners,
            # Everything this resource composes, when it is a composite.
            # Crossplane writes it on the spec, not the status.
            "composes": [
                (r.get("kind") or "?", r.get("name") or "?",
                 (r.get("apiVersion") or "").split("/")[0])
                for r in (((item.get("spec") or {}).get("crossplane") or {})
                          .get("resourceRefs") or [])
            ],
        })
    return rows, refused, None


def read_managed(runner=subprocess.run):
    return _read(runner, "managed")


def read_composites(runner=subprocess.run):
    return _read(runner, "composite")


def unreadable_children(managed, composites):
    """Resources a composite says it composes that the sweep never returned.

    This is the only honest way to size the RBAC hole from inside the cluster.
    A refused kind that nothing composes costs nothing and is not worth a red
    status forever; a refused kind that a live composite *names* is a real
    object, in a real state, that this account cannot see. Returned as a
    sorted list of (kind, name, group, owner) so the report can say which
    claim is the one with a hole under it.
    """
    seen = {(row["kind"], row["name"]) for row in managed}
    missing = set()
    for composite in composites:
        for kind, name, group in composite["composes"]:
            if (kind, name) not in seen:
                missing.add((kind, name, group,
                             f"{composite['kind']}/{composite['name']}"))
    return sorted(missing)


def _age(since, now):
    """"3d" from an RFC3339 stamp, or "" when it cannot be read.

    Never raises: an unparseable timestamp must cost the age, not the verdict
    it sits beside.
    """
    if not since:
        return ""
    try:
        at = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        return ""
    seconds = (now - at).total_seconds()
    if seconds < 0:
        return ""
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _label(row):
    where = f"{row['namespace']}/" if row["namespace"] else ""
    return f"{row['kind']}/{where}{row['name']}"


def classify(row):
    """One of "ok", "paused", "unexplained-pause", "not-synced", "not-ready".

    Order matters. A paused resource reports `Synced=False` by construction,
    so the pause has to be read before the sync verdict or every paused
    resource is a finding.
    """
    if row["synced_reason"] == "ReconcilePaused":
        return "paused" if row["paused"] else "unexplained-pause"
    if row["synced"] != "True":
        return "not-synced"
    if row["ready"] != "True":
        return "not-ready"
    return "ok"


def report(managed, composites, blind, refused, now):
    """The printed lines and the exit status, as (lines, status)."""
    lines = []
    actionable = False
    paused = 0

    # A composite's verdict, so a failing leaf can be printed next to what the
    # claim above it is claiming. This is the point of the tool.
    composite_verdict = {
        (row["kind"], row["name"]): f"{row['synced']}/{row['ready']}"
        for row in composites
    }

    for row in sorted(managed + composites, key=lambda r: (r["kind"], r["name"])):
        verdict = classify(row)
        age = _age(row["since"], now)
        aged = f", {age}" if age else ""

        if verdict == "ok":
            continue
        if verdict == "paused":
            paused += 1
            continue

        actionable = True
        if verdict == "unexplained-pause":
            lines.append(
                f"PAUSED WITH NO ANNOTATION  {_label(row)}: reconciliation is "
                f"paused{aged} but nothing set {PAUSED_ANNOTATION}")
        elif verdict == "not-synced":
            lines.append(
                f"NOT SYNCED  {_label(row)}: Crossplane cannot get the desired "
                f"state to the provider ({row['synced_reason'] or 'no reason given'}"
                f"{aged})")
        else:
            lines.append(
                f"NOT READY  {_label(row)}: the provider accepted it and the "
                f"external resource is not usable ({row['ready_reason'] or 'no reason given'}"
                f"{aged})")

        if row["message"]:
            for part in row["message"].splitlines():
                lines.append(f"           {part.strip()}")
        for kind, name in row["owners"]:
            claim = composite_verdict.get((kind, name))
            if claim is None:
                lines.append(f"           owned by {kind}/{name}")
            else:
                lines.append(
                    f"           owned by {kind}/{name}, which reads "
                    f"Synced/Ready = {claim} — a composite aggregates "
                    f"optimistically, so that is not a second opinion")

    for kind, name, group, owner in blind:
        lines.append(
            f"CANNOT SEE  {kind}/{name} ({group}): {owner} composes it and this "
            f"account cannot list that kind, so its state is unknown — not clean")

    lines.append(
        f"Read {len(managed)} managed resource(s) and {len(composites)} composite(s) "
        f"from the live cluster, not from git.")
    if refused:
        lines.append(
            f"{len(refused)} kind(s) were refused to this account; "
            f"{len(blind)} live resource(s) sit in them. A refused kind nothing "
            f"composes is not raised.")
    if paused:
        lines.append(
            f"{paused} managed resource(s) are paused on purpose "
            f"({PAUSED_ANNOTATION}) and are not raised — that is what "
            f"repositoryfile-lockdown does once a file is seeded.")
    lines.append(
        "A claim can read Synced=True while the resource it composes is failing; "
        "that is why this reads the leaves.")
    if blind:
        return lines, 2 if actionable else 1
    return lines, (2 if actionable else 0)


def main(argv=None, runner=subprocess.run, now=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    managed, refused, why = read_managed(runner)
    if why:
        print(f"COULD NOT READ  {why}")
        return 1
    if not managed:
        # An empty list from a working kubectl is not a clean bill of health,
        # it is no instrument: this cluster runs Crossplane with a live
        # provider, so zero managed resources means the query looked in the
        # wrong place.
        print("COULD NOT READ  kubectl returned no managed resources at all")
        return 1

    composites, refused_composites, why = read_composites(runner)
    if why:
        print(f"COULD NOT READ  {why}")
        return 1

    lines, status = report(
        managed, composites,
        unreadable_children(managed, composites),
        sorted(set(refused) | set(refused_composites)),
        now or datetime.datetime.now(datetime.timezone.utc))
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
