"""Does every Helm repository this cluster pulls charts from still serve an index?

Cycle 484, on the outage Cycle 483 diagnosed. `bitnami-labs/sealed-secrets`
was transferred to `bitnami/sealed-secrets`, and GitHub Pages forwards
release downloads after a transfer but not the small static site a chart
repository publishes its `index.yaml` on. So the URL ArgoCD had pointed at
since April started answering 404, ArgoCD could not even build a diff for
the component that decrypts every credential in this cluster, and the only
place that fact appeared was an ArgoCD condition nothing reads.

It was found by a cycle tripping over it three hours later. That is the
same shape `tools.security_alerts` and `tools.cli_pin` were built for: a
fact that arrives only as a side effect of an unrelated command is not a
reported finding, it is an occasionally noticed one.

    python3 -m tools.helm_repo_health

**It reads the cluster, not git, and that is the whole design.** Cycle 483
merged the corrected URL into `platform-config` at 15:48 and the live
object still carried the dead one an hour later, because
`argocd/application.yaml` is excluded from what ArgoCD syncs. A check
built on the git file would have gone green the moment the PR merged and
said nothing about the outage that was still running. `kubectl get
applications -A` is what is actually being fetched.

**A 200 is not the measurement.** A GitHub Pages 404 is itself a 200-shaped
web page from a working server — the dead URL above returned 9,115 bytes of
HTML — and a generic host answers `/index.yaml` however it likes. So the
check parses the body as a Helm index and looks for the *named chart* in
its `entries` map. That is the one thing only a real chart repository for
that chart can produce, which is the difference between a positive result
that was guaranteed in advance and a measurement.

Exit status, matching `tools.security_alerts`, `tools.cli_pin` and
`tools.agentic_health` so a cycle can read it without parsing the text:
**2 means a repository a live Application depends on is not serving that
chart**, 1 means something was unreadable — which includes kubectl being
refused, and never reads as clean — and 0 means every Helm source
answered with its chart in the index, naming what it swept.

Scope it prints for itself: only sources with a `chart` field, i.e. Helm
chart repositories. A git `repoURL` is a different failure mode with a
different fix and ArgoCD reports it loudly on the Application itself.
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request

import yaml

USER_AGENT = "nova-helm-repo-health/1"


def read_applications(runner=subprocess.run):
    """Every Helm source on every live ArgoCD Application.

    Returns (sources, None) or (None, why). A source is a dict with
    `app`, `namespace`, `repo`, `chart` and `revision`. ArgoCD accepts
    either a single `spec.source` or a list under `spec.sources`; both
    shapes are read, because a multi-source Application is one edit away
    and a checker that silently skipped it would be worse than absent.
    """
    try:
        proc = runner(
            ["kubectl", "get", "applications", "-A", "-o", "json"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl failed: {exc}"
    if proc.returncode != 0:
        return None, f"kubectl failed: {proc.stderr.strip() or proc.stdout.strip()}"
    try:
        body = json.loads(proc.stdout)
    except ValueError as exc:
        return None, f"kubectl returned something that is not JSON: {exc}"

    sources = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        candidates = list(spec.get("sources") or [])
        if spec.get("source"):
            candidates.append(spec["source"])
        for source in candidates:
            chart = source.get("chart")
            if not chart:
                continue
            sources.append({
                "app": meta.get("name", "?"),
                "namespace": meta.get("namespace", "?"),
                "repo": (source.get("repoURL") or "").rstrip("/"),
                "chart": chart,
                "revision": source.get("targetRevision", ""),
            })
    return sources, None


def fetch_index(repo, opener=urllib.request.urlopen):
    """The chart names a repository's index.yaml advertises.

    Returns (set-of-chart-names, None) or (None, why). Anything that is
    not a Helm index — a 404 page, an HTML shell, a redirect to a
    marketing site — comes back as a reason rather than an empty set, so
    "serves nothing" and "serves something that is not a chart index"
    stay different sentences.
    """
    url = f"{repo}/index.yaml"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code} fetching {url}"
    except Exception as exc:  # noqa: BLE001 -- any network shape is "unreadable"
        return None, f"could not fetch {url}: {exc}"
    try:
        body = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, f"{url} is not YAML: {str(exc).splitlines()[0]}"
    if not isinstance(body, dict):
        return None, f"{url} is not a Helm index (no mapping at the top level)"
    entries = body.get("entries")
    if not isinstance(entries, dict):
        return None, f"{url} is not a Helm index (no `entries` map)"
    return set(entries), None


def check(runner=subprocess.run, opener=urllib.request.urlopen):
    """Return (exit_status, lines)."""
    sources, why = read_applications(runner)
    if sources is None:
        return 1, [f"COULD NOT READ: {why}",
                   "Being unable to check is not the same as nothing to check."]
    if not sources:
        return 0, ["No ArgoCD Application on this cluster pulls a Helm chart.",
                   "Nothing to check — every source is a git repository."]

    lines, indexes, broken = [], {}, []
    for source in sorted(sources, key=lambda s: (s["repo"], s["chart"])):
        repo = source["repo"]
        if repo not in indexes:
            indexes[repo] = fetch_index(repo, opener)
        charts, index_why = indexes[repo]
        where = f"{source['namespace']}/{source['app']}"
        if charts is None:
            lines.append(f"BROKEN  {where}: {index_why}")
            broken.append(where)
        elif source["chart"] not in charts:
            lines.append(
                f"BROKEN  {where}: {repo} serves an index, but it has no "
                f"chart named {source['chart']} ({len(charts)} chart(s) offered)")
            broken.append(where)
        else:
            lines.append(
                f"ok      {where}: {repo} serves {source['chart']} "
                f"(target {source['revision'] or 'unset'})")

    lines.append(f"Read {len(sources)} Helm source(s) on "
                 f"{len(indexes)} repositor(y/ies) from the live cluster, not from git.")
    if broken:
        lines.append("A repository a running Application depends on is not serving its chart.")
        lines.append("ArgoCD cannot diff or sync that Application until the repoURL is fixed.")
        return 2, lines
    return 0, lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    status, lines = check()
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
