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
    python3 -m tools.catalog --publish            # and put it in the vault

**This module lives in `agora_runner/` rather than in `tools/` because the
runner pod is the only place that can refresh the catalog unattended.** The
image copies `agora_runner/` and nothing else, so a builder that lived in
`tools/` could only ever be run by hand from a checkout -- which is exactly
what made the page a screenshot: `catalog.md` was regenerated when a cycle
happened to remember, and the roadmap's step 3 asks for it to happen without
one. `catalog_refresh` runs `publish()` on a timer inside the runner; `tools
/catalog.py` is now a thin CLI over the same functions, so there is still one
implementation and the tests still import it through that name.

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

import http.client
import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field

# Namespaces holding things a person would call "a service". `kube-system`,
# `crossplane-system` and the rest run the platform itself and would drown the
# catalog in controllers nobody orders.
APP_NAMESPACES = ("agents", "infra", "obsidian")

# Kinds that order a *repo* rather than a running workload. A row matched by one
# of these is not self-service coverage of the thing that is running.
REPO_ONLY_KINDS = ("GitHubService", "GitHubRepoPolicy")

# The knowledge base, read from the service itself rather than from its git repo.
# The sitemap lists the pages that are *built and serving*, which is what a link
# on the catalog has to point at -- a `.md` file on the default branch may not be
# on the site yet, and the site is what the reader clicks. Cluster-internal, so no
# token and no Tailscale hop; the `<loc>` entries it returns are the public URLs.
DOCS_SITEMAP = "http://sokrates-docs.agents.svc.cluster.local:8080/sitemap.xml"


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
    wanted: int = 1
    url: str = ""
    repo_claim: str = ""
    argocd_app: str = ""
    docs: str = ""

    @property
    def repo(self) -> str:
        """`ghcr.io/sokratesai/agora@sha256:...` -> `agora`."""
        return self.image.split("@")[0].split(":")[0].rsplit("/", 1)[-1]


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
    """Rows from the namespaces that answered, plus the first error if any did not.

    A `Source` carries both on purpose. Returning early on the first error threw
    away every row already collected, and the catalog then printed every Ingress
    in the cluster under "doors nothing accounts for" -- a confident,
    security-shaped, false claim built out of a partial read. Keeping the rows
    and the error together lets the table show what is known while `ok` stays
    false, so the coverage number and the orphan section both stay suppressed.
    """
    merged: list = []
    errors = []
    for ns in namespaces:
        got = _kubectl(["get", "deployments,statefulsets", "-n", ns])
        if not got.ok:
            errors.append(f"{ns}: {got.error}")
            continue
        merged.extend(got.rows)
    return Source(rows=merged, error="; ".join(errors))


def read_ingresses() -> Source:
    return _kubectl(["get", "ingress", "-A"])


def read_argocd_apps() -> Source:
    return _kubectl(["get", "applications", "-n", "argocd"])


def read_xr_kinds() -> Source:
    """Every composite kind this cluster offers, read off the XRDs.

    Hardcoding `githubservices` here is the bug the reviewer of runner#389
    found: a query for one plural can only ever return one kind, so
    `coverage`'s first number was 0 by construction rather than by
    measurement, and would have stayed 0 after somebody shipped a claim that
    genuinely composes a workload. The list has to come from the cluster.
    """
    got = _kubectl(["get", "xrd"])
    if not got.ok:
        return got
    return Source(rows=[x.get("spec", {}).get("names", {}).get("plural", "") for x in got.rows if x])


def read_claims() -> Source:
    """Every composite resource live on this cluster, of every offered kind."""
    kinds = read_xr_kinds()
    if not kinds.ok:
        return kinds
    merged: list = []
    errors = []
    for plural in kinds.rows:
        if not plural:
            continue
        got = _kubectl(["get", plural, "-A"])
        if not got.ok:
            # Same partial-read contract as read_workloads: the bridge account is
            # Forbidden on two of the three kinds this cluster offers today, so a
            # kind that answers still gets to fill in its column, and every kind
            # that did not is named rather than just the first.
            errors.append(f"{plural}: {got.error.split(': ', 1)[-1]}")
            continue
        merged.extend(got.rows)
    return Source(rows=merged, error="; ".join(errors))


def read_docs(url: str = DOCS_SITEMAP) -> Source:
    """Every page the docs site publishes, read off its own sitemap.

    The one source here that is not `kubectl`, and it keeps the same contract:
    an unreachable site is an `error`, never an empty page list. Those two look
    identical in the rendered table -- every row with a dash in the Docs column
    -- and they mean opposite things, so `render` drops the column entirely
    rather than filling it with dashes it cannot stand behind.
    """
    try:
        with urllib.request.urlopen(url, timeout=30) as fh:
            body = fh.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as exc:
        return Source(error=f"{url}: {exc}")
    pages = re.findall(r"<loc>([^<]+)</loc>", body)
    if not pages:
        return Source(error=f"{url}: no <loc> entries, so this is not a sitemap")
    return Source(rows=pages)


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
                wanted=spec.get("replicas", 1),
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
            if host and not target.url:
                target.url = f"https://{host}"
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
    ordered = {c.get("metadata", {}).get("name", ""): c.get("kind", "") for c in claims}
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


def attach_docs(services: list[Service], pages: list) -> list[str]:
    """Link each service to its own page on the docs site. Returns the undocumented.

    This is the join that makes the catalog an IDP rather than an inventory: a
    row that says what a thing is, where it runs, and where it is written up.

    Matched when the last segment of a page URL is *exactly* the service name or
    its image repo name -- the same rule as `attach_argocd` above and for the
    same reason. A substring match would link `agora-persona-runner` to
    `/reference/agora-persona`, which is a page about the Persona resource and
    not about the runner, and a wrong link in a catalog is worse than no link,
    because the reader stops looking. So a blank here means "no page named after
    this service", never "this service is undocumented" -- a README is still a
    README and this join cannot see one.
    """
    by_slug = {}
    for url in pages:
        by_slug.setdefault(url.rstrip("/").rsplit("/", 1)[-1], url)
    missing = []
    for svc in services:
        svc.docs = by_slug.get(svc.name) or by_slug.get(svc.repo) or ""
        if not svc.docs:
            missing.append(f"{svc.namespace}/{svc.name}")
    return sorted(missing)


def coverage(services: list[Service]) -> tuple[int, int, int]:
    """(workloads composed by a claim, source repos ordered by a claim, total).

    The first number is 0 today and the middle one is not, which is the whole
    point of returning both.
    """
    composed = sum(1 for s in services if s.repo_claim and s.repo_claim not in REPO_ONLY_KINDS)
    repos = sum(1 for s in services if s.repo_claim)
    return composed, repos, len(services)


def render(services: list[Service], orphans: list[str], unread: list[str], undocumented=None) -> str:
    """`undocumented` is `None` when the docs site could not be read, and a list
    (possibly empty) when it could. The Docs column and its section appear only
    in the second case -- see `read_docs`."""
    lines = ["# Service catalog", ""]
    if unread:
        lines.append(
            f"**Incomplete — do not read the table below as a full picture.** {len(unread)} source(s) could not "
            "be read. No coverage number is given, and the sections that depend on a source that failed are "
            "omitted rather than rendered from what did answer."
        )
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
        if undocumented is not None:
            have = len(services) - len(undocumented)
            verb, pronoun = ("has", "it") if have == 1 else ("have", "them")
            lines.append(
                f"**{have} of {len(services)} services {verb} a page named after {pronoun} on the docs site.** "
                "Matched on the name, so this counts pages written *about a service*; a service missing from "
                "the count may still have a README this join cannot see."
            )
            lines.append("")
    docs_col = " Docs |" if undocumented is not None else ""
    docs_rule = "---|" if undocumented is not None else ""
    lines.append("| Service | Namespace | Source repo ordered by | Deployed by | URL |" + docs_col + " Up |")
    lines.append("|---|---|---|---|---|" + docs_rule + "---|")
    for s in services:
        url = f"[{s.url.removeprefix('https://')}]({s.url})" if s.url else "—"
        docs = "" if undocumented is None else f" {f'[docs]({s.docs})' if s.docs else '—'} |"
        lines.append(
            f"| {s.name} | {s.namespace} | {s.repo_claim or '—'} | {s.argocd_app or '—'} | {url} |" + docs + " "
            f"{'yes' if s.ready else ('off' if s.wanted == 0 else 'NO')} |"
        )
    lines.append("")
    if undocumented and not unread:
        lines.append("## Services with no page named after them on the docs site")
        lines.append("")
        lines.append(
            "The knowledge base is written by topic, so these are gaps in *service* documentation "
            "specifically. Naming a page after one of these is what takes it off this list."
        )
        lines.append("")
        for u in undocumented:
            lines.append(f"- {u}")
        lines.append("")
    # Suppressed on a partial read for the same reason the number is: if the
    # workload list is short because a namespace was Forbidden, every Ingress in
    # the cluster looks like an unaccounted-for door. That is a confident,
    # security-shaped, false claim -- the worst thing this page could print.
    if orphans and not unread:
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
    docs = read_docs()

    unread = [
        f"{label}: {src.error}"
        for label, src in (
            ("workloads", workloads),
            ("ingresses", ingresses),
            ("argocd applications", apps),
            ("crossplane claims", claims),
            ("docs site", docs),
        )
        if not src.ok
    ]

    services = services_from(workloads.rows)
    orphans = attach_urls(services, ingresses.rows)
    attach_claims(services, claims.rows)
    attach_argocd(services, apps.rows)
    undocumented = attach_docs(services, docs.rows) if docs.ok else None
    return render(services, orphans, unread, undocumented), (1 if unread else 0)


# The frontmatter `catalog.md` carries in the vault. It was hand-written by
# whichever cycle last ran the tool, which is why the provenance line said
# "Cycle 448" -- and a provenance line a human types is one that goes stale
# in exactly the same way the table under it does. The builder writes it now.
FRONT_MATTER = """---
type: catalog
tags: [agora, platform, idp, catalog]
status: built
updated: {date}
maintenance: Generated from the live cluster -- by `agora_runner.catalog_refresh` on a timer in the runner pod, or by `python3 -m tools.catalog --publish` by hand. Do not hand-edit; the next run overwrites it. Last regenerated {stamp}.
---
"""

VAULT_PATH = "projects/sokrates/projects/agora/nova/catalog.md"


def _oslo_now(utc_now=None):
    """`datetime.now()` in Oslo. Written out rather than taken from the
    process timezone because the runner pod is UTC and rule 7 is that
    anything the owner reads is Oslo time."""
    from datetime import datetime, timedelta, timezone

    # Europe/Oslo is UTC+1, +2 on summer time. `zoneinfo` needs tzdata,
    # which this image does not install (`python:3.12-slim`, and the
    # Dockerfile's note that the runtime is stdlib-only is about wheels,
    # not about apt). Rather than add a package for one timestamp, ask
    # the one library that is already here: `time.localtime` is UTC in
    # the pod, so that is no help -- but the offset is derivable from the
    # date, and getting it wrong by an hour on the changeover weekend is
    # a cosmetic error in a freshness line, not a wrong catalog.
    now = utc_now or datetime.now(timezone.utc)
    summer = _is_summer_time(now)
    return now.astimezone(timezone(timedelta(hours=2 if summer else 1)))


def _is_summer_time(moment) -> bool:
    """EU summer time: 01:00 UTC on the last Sunday of March until 01:00
    UTC on the last Sunday of October."""
    from datetime import datetime, timedelta, timezone

    def last_sunday(year, month):
        # The 1st of the next month, walked back to the Sunday before it.
        first_next = datetime(year + (month == 12), (month % 12) + 1, 1, 1, tzinfo=timezone.utc)
        return first_next - timedelta(days=(first_next.weekday() + 1) % 7 or 7)

    year = moment.year
    return last_sunday(year, 3) <= moment < last_sunday(year, 10)


def document(text: str, now=None) -> str:
    """The catalog as it is stored: frontmatter, then `render`'s markdown."""
    stamp = now or _oslo_now()
    return FRONT_MATTER.format(
        date=stamp.strftime("%Y-%m-%d"), stamp=stamp.strftime("%Y-%m-%d %H:%M Oslo")
    ) + text


def publish() -> tuple[str, int]:
    """Build the catalog and write it to the vault. Returns (text, status).

    The status is `build`'s: 1 if any source was unreadable. **It still
    publishes on a partial read**, because `render` already replaces the
    coverage number with a named refusal and omits the sections that
    depend on a source that failed -- so the honest page is the one that
    says which source was Forbidden, and withholding the write would
    leave the *previous*, confident-looking catalog on screen instead.
    """
    from agora_runner.vault import vault_write_path

    text, status = build()
    vault_write_path(VAULT_PATH, document(text))
    return text, status
