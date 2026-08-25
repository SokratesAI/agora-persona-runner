"""The service catalog: every service on this box, and who actually owns it.

Cycle 447, against the owner's capture *"Build our own IDP with self-service
options for capabilities/infrastructure, service discovery, and knowledge
base -- extending the Crossplane claim-as-API pattern from the 2026-08-25
sokrates-docs thread."*

An internal developer platform is four things: a way to order infrastructure
without writing YAML, a catalog that says what exists, a knowledge base, and
one front door over all of it. Three of those already have something behind
them here -- Crossplane XRDs order infrastructure, `sokrates-docs` is the
knowledge base, the Nova app is the front door. **Service discovery is the
one with nothing behind it at all**, and it is the piece the other three hang
off: you cannot promote a thing you cannot list.

So this is the catalog, and its most useful column is not the name. It is
`Source repo ordered by`, which answers the question the whole claim-as-API
pattern is about: was this ordered through an API, or hand-written and
applied? The first run, on 2026-08-26, printed the answer: **0 of 14 running
services are composed by a claim.** Four have a source repo that a
`GitHubService` XR ordered, and that XR writes the deployment YAML into the
repo as text rather than composing objects, so nothing running on this box is
a resource Crossplane owns. That number is what turns "we should build an
IDP" into a thing with a next step, and it is why the column distinguishes
the repo from the workload instead of scoring both as coverage.

    python3 -m tools.catalog                      # print the catalog
    python3 -m tools.catalog --write catalog.md   # and write it as markdown

**Exit codes.** 0 means every source answered. 1 means at least one source
was unreadable, and the coverage numbers are suppressed rather than computed
from a partial read -- the same contract `tools.security_alerts` keeps, and
for the same reason: an unreadable source and an empty one look identical on
the page and mean opposite things. The bridge service account can list
Deployments, Ingresses, Applications and `githubservices` today but is
`Forbidden` on `githubrepopolicies` and `tailscaleexposures`, so a partial
read is the normal case rather than the exceptional one.

The join is deliberately in pure functions that take parsed rows, so the
shape of the catalog is testable without a cluster; only `read_*` shells out.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field

# Namespaces holding things a person would call "a service". `kube-system`,
# `crossplane-system` and the rest run the platform itself and would drown the
# catalog in controllers nobody orders.
APP_NAMESPACES = ("agents", "infra", "obsidian")

# Kinds that order a *repo* rather than a running workload. A row matched by one
# of these is not self-service coverage of the thing that is running.
REPO_ONLY_KINDS = ("GitHubService", "GitHubRepoPolicy")


@dataclass
class Source:
    """One kubectl read: rows, or a reason there are none."""

    rows: list = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class Service:
    name: str
    namespace: str
    kind: str
    image: str
    ready: bool
    url: str = ""
    repo_claim: str = ""
    argocd_app: str = ""

    @property
    def repo(self) -> str:
        """`ghcr.io/sokratesai/agora@sha256:...` -> `agora`."""
        base = self.image.split("@")[0].split(":")[0]
        return base.rsplit("/", 1)[-1] if "/" in base else base


def _kubectl(args: list[str]) -> Source:
    try:
        out = subprocess.run(
            ["kubectl", *args, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Source(error=str(exc))
    if out.returncode != 0:
        return Source(error=out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "kubectl failed")
    try:
        return Source(rows=json.loads(out.stdout).get("items", []))
    except json.JSONDecodeError as exc:
        return Source(error=f"unparseable json: {exc}")


def read_workloads(namespaces=APP_NAMESPACES) -> Source:
    merged: list = []
    for ns in namespaces:
        got = _kubectl(["get", "deployments,statefulsets", "-n", ns])
        if not got.ok:
            return got
        merged.extend(got.rows)
    return Source(rows=merged)


def read_ingresses() -> Source:
    return _kubectl(["get", "ingress", "-A"])


def read_argocd_apps() -> Source:
    return _kubectl(["get", "applications", "-n", "argocd"])


def read_claims() -> Source:
    """Crossplane composite resources -- the things ordered through an API."""
    return _kubectl(["get", "githubservices", "-A"])


def services_from(workloads: list) -> list[Service]:
    out = []
    for item in workloads:
        meta = item.get("metadata", {})
        spec = item.get("spec", {})
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        out.append(
            Service(
                name=meta.get("name", ""),
                namespace=meta.get("namespace", ""),
                kind=item.get("kind", ""),
                image=containers[0].get("image", "") if containers else "",
                ready=bool(item.get("status", {}).get("readyReplicas")),
            )
        )
    return sorted(out, key=lambda s: (s.namespace, s.name))


def attach_urls(services: list[Service], ingresses: list) -> list[str]:
    """Set `url` on each service an Ingress points at. Returns the orphans.

    An orphan is an Ingress whose backend service matches no workload in the
    catalog. That is worth printing rather than dropping: it is a live door
    into this cluster that nothing in the catalog accounts for.
    """
    by_name = {(s.namespace, s.name): s for s in services}
    orphans = []
    for ing in ingresses:
        meta = ing.get("metadata", {})
        ns = meta.get("namespace", "")
        host = ""
        backend = ""
        for rule in ing.get("spec", {}).get("rules", []) or []:
            for path in rule.get("http", {}).get("paths", []) or []:
                backend = backend or path.get("backend", {}).get("service", {}).get("name", "")
        for tls in ing.get("spec", {}).get("tls", []) or []:
            for h in tls.get("hosts", []) or []:
                host = host or h
        # The operator writes the name that actually resolves into status; the
        # `tls.hosts` entry beside it is the short form and is not a URL.
        assigned = (ing.get("status", {}).get("loadBalancer", {}).get("ingress") or [{}])[0].get("hostname", "")
        host = assigned or host
        target = by_name.get((ns, backend))
        if target is not None:
            target.url = f"https://{host}" if host else ""
        else:
            orphans.append(f"{ns}/{meta.get('name', '')} -> {backend or '?'} ({host or 'no host'})")
    return sorted(orphans)


def attach_claims(services: list[Service], claims: list) -> None:
    """Name the XR that ordered a service's *source repo* -- not the service.

    This distinction is the finding, not a caveat. A `GitHubService` XR creates
    a GitHub repo and a paired `-config` repo, then writes the Deployment,
    Service, PVC and Ingress into the second one as the string body of a
    `RepositoryFile` (measured Cycle 416). ArgoCD applies that text later. So
    the running workload is not a resource Crossplane composes and there is no
    drift detection in either direction: change the XR and the file in git keeps
    the old value. Marking these rows "ordered as a claim" would read as
    self-service coverage this estate does not have.
    """
    ordered = {c.get("metadata", {}).get("name", ""): c.get("kind", "claim") for c in claims}
    for svc in services:
        svc.repo_claim = ordered.get(svc.name) or ordered.get(svc.repo) or ""


def attach_argocd(services: list[Service], apps: list) -> None:
    """Name the ArgoCD Application whose repo most plausibly owns a service.

    Matched on name, because an Application does not record which workloads it
    produced. A `-config` suffix is how this estate names an app's own config
    repo, so `nova-site` under `agora-persona-runner-config` is a real match
    only when the names line up; anything else is left blank rather than
    guessed at, and a blank here means "not matched", never "not deployed".
    """
    names = [a.get("metadata", {}).get("name", "") for a in apps]
    for svc in services:
        for app in names:
            stem = app[: -len("-config")] if app.endswith("-config") else app
            if stem and stem in (svc.name, svc.repo):
                svc.argocd_app = app
                break


def coverage(services: list[Service]) -> tuple[int, int, int]:
    """(workloads composed by a claim, source repos ordered by a claim, total).

    The first number is 0 today and the middle one is not, which is the whole
    point of returning both.
    """
    composed = sum(1 for s in services if s.repo_claim and s.repo_claim not in REPO_ONLY_KINDS)
    repos = sum(1 for s in services if s.repo_claim)
    return composed, repos, len(services)


def render(services: list[Service], orphans: list[str], unread: list[str]) -> str:
    lines = ["# Service catalog", ""]
    if unread:
        lines.append(f"**Incomplete.** {len(unread)} source(s) could not be read, so no coverage number is given below.")
        for u in unread:
            lines.append(f"- {u}")
        lines.append("")
    else:
        composed, repos, total = coverage(services)
        lines.append(
            f"**{composed} of {total} running services are composed by a claim.** "
            f"{repos} of them have a *source repo* that was ordered as one, but a `GitHubService` writes the "
            f"deployment YAML into that repo as text, so no workload here is an object Crossplane owns or "
            f"reconciles. Every row below is kept correct by somebody reading it."
        )
        lines.append("")
    lines.append("| Service | Namespace | Source repo ordered by | Deployed by | URL | Up |")
    lines.append("|---|---|---|---|---|---|")
    for s in services:
        url = f"[{s.url.removeprefix('https://')}]({s.url})" if s.url else "—"
        lines.append(
            f"| {s.name} | {s.namespace} | {s.repo_claim or '—'} | {s.argocd_app or '—'} | {url} | "
            f"{'yes' if s.ready else 'NO'} |"
        )
    lines.append("")
    if orphans:
        lines.append("## Doors nothing in the catalog accounts for")
        lines.append("")
        lines.append("Each of these is a live Ingress whose backend is not a workload above.")
        lines.append("")
        for o in orphans:
            lines.append(f"- {o}")
        lines.append("")
    return "\n".join(lines)


def build() -> tuple[str, int]:
    workloads = read_workloads()
    ingresses = read_ingresses()
    apps = read_argocd_apps()
    claims = read_claims()

    unread = [
        f"{label}: {src.error}"
        for label, src in (
            ("workloads", workloads),
            ("ingresses", ingresses),
            ("argocd applications", apps),
            ("crossplane claims", claims),
        )
        if not src.ok
    ]

    services = services_from(workloads.rows)
    orphans = attach_urls(services, ingresses.rows)
    attach_claims(services, claims.rows)
    attach_argocd(services, apps.rows)
    return render(services, orphans, unread), (1 if unread else 0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", metavar="PATH", help="also write the catalog as markdown to PATH")
    args = parser.parse_args(argv)

    text, status = build()
    print(text)
    if args.write:
        with open(args.write, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"written: {args.write}", file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
