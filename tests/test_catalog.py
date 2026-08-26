"""The catalog's join, tested on parsed rows so no cluster is needed.

The two properties worth protecting are the ones the tool exists for: a
`GitHubService` match must never be counted as a running service composed by a
claim, and an unreadable source must never render as a coverage number.
"""

from agora_runner import catalog_build as catalog


def deployment(name, ns="agents", image="ghcr.io/sokratesai/thing:v1", ready=1, kind="Deployment"):
    return {
        "kind": kind,
        "metadata": {"name": name, "namespace": ns},
        "spec": {"template": {"spec": {"containers": [{"image": image}]}}},
        "status": {"readyReplicas": ready},
    }


def ingress(name, ns, backend, hostname="thing.tailc83eb3.ts.net", tls_host="thing"):
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {
            "rules": [{"http": {"paths": [{"backend": {"service": {"name": backend}}}]}}],
            "tls": [{"hosts": [tls_host]}],
        },
        "status": {"loadBalancer": {"ingress": [{"hostname": hostname}]}},
    }


def test_a_githubservice_orders_the_repo_not_the_workload():
    services = catalog.services_from([deployment("sokrates-docs", image="ghcr.io/sokratesai/sokrates-docs@sha256:ab")])
    catalog.attach_claims(services, [{"kind": "GitHubService", "metadata": {"name": "sokrates-docs"}}])

    assert services[0].repo_claim == "GitHubService"
    composed, repos, total = catalog.coverage(services)
    assert (composed, repos, total) == (0, 1, 1)


def test_a_claim_that_composes_objects_counts_as_composed():
    """The number has to be able to move, or it is a constant dressed as a measurement."""
    services = catalog.services_from([deployment("nova")])
    catalog.attach_claims(services, [{"kind": "TailscaleExposure", "metadata": {"name": "nova"}}])

    assert catalog.coverage(services) == (1, 1, 1)


def test_an_unreadable_source_suppresses_the_coverage_number():
    services = catalog.services_from([deployment("nova")])
    text = catalog.render(services, [], ["crossplane claims: Forbidden"])

    assert "Incomplete" in text
    assert "crossplane claims: Forbidden" in text
    assert "composed by a claim" not in text


def test_a_readable_run_states_the_coverage():
    services = catalog.services_from([deployment("nova")])
    text = catalog.render(services, [], [])

    assert "0 of 1 running services are composed by a claim" in text
    assert "Incomplete." not in text


def test_the_url_is_the_assigned_hostname_not_the_short_tls_name():
    services = catalog.services_from([deployment("nova")])
    orphans = catalog.attach_urls(services, [ingress("nova-tailscale", "agents", "nova", "nova.tailc83eb3.ts.net", "nova")])

    assert services[0].url == "https://nova.tailc83eb3.ts.net"
    assert orphans == []


def test_an_ingress_with_no_workload_behind_it_is_reported_as_a_door():
    services = catalog.services_from([deployment("nova")])
    orphans = catalog.attach_urls(services, [ingress("argocd-tailscale", "argocd", "argocd-server", "argocd.ts.net", "argocd")])

    assert orphans == ["argocd/argocd-tailscale -> argocd-server (argocd.ts.net)"]
    assert "Doors nothing in the catalog accounts for" in catalog.render(services, orphans, [])


def test_argocd_matches_a_config_repo_app_and_leaves_the_rest_blank():
    services = catalog.services_from(
        [
            deployment("nova-site", image="ghcr.io/sokratesai/agora-persona-runner@sha256:cd"),
            deployment("redis", image="redis:7-alpine"),
        ]
    )
    catalog.attach_argocd(
        services,
        [{"metadata": {"name": "agora-persona-runner-config"}}, {"metadata": {"name": "reloader"}}],
    )
    by_name = {s.name: s for s in services}

    assert by_name["nova-site"].argocd_app == "agora-persona-runner-config"
    assert by_name["redis"].argocd_app == ""


def test_a_workload_with_no_ready_replica_is_printed_as_down():
    services = catalog.services_from([deployment("ollama", ns="infra", ready=0)])

    assert "| NO |" in catalog.render(services, [], [])


def test_a_scaled_to_zero_workload_is_off_not_down():
    """`ollama` and `vault-bridge` are `replicas: 0` on purpose; printing NO reads as an outage."""
    services = catalog.services_from([dict(deployment("ollama", ns="infra", ready=0), spec={"replicas": 0, "template": {"spec": {"containers": [{"image": "x"}]}}})])

    assert "| off |" in catalog.render(services, [], [])


def test_a_healthy_workload_is_printed_up():
    assert "| yes |" in catalog.render(catalog.services_from([deployment("nova")]), [], [])


def test_a_second_ingress_for_one_service_does_not_clobber_the_first_url():
    """Two Ingresses can front one service. The first must win, and a hostless one
    must never blank a good URL -- the mutation to catch is assigning `url`
    unconditionally, which is what the original code did."""
    services = catalog.services_from([deployment("nova")])
    good = ingress("nova-tailscale", "agents", "nova", "nova.tailc83eb3.ts.net", "nova")
    second = ingress("nova-other", "agents", "nova", "other.tailc83eb3.ts.net", "other")
    bare = {"metadata": {"name": "nova-plain", "namespace": "agents"},
            "spec": {"rules": [{"http": {"paths": [{"backend": {"service": {"name": "nova"}}}]}}]}}
    catalog.attach_urls(services, [good, second, bare])

    assert services[0].url == "https://nova.tailc83eb3.ts.net"


SITEMAP = (
    "<?xml version='1.0'?><urlset>"
    "<loc>https://sokrates-docs.tailc83eb3.ts.net/explanation/nova</loc>"
    "<loc>https://sokrates-docs.tailc83eb3.ts.net/how-to/order-a-service</loc>"
    "</urlset>"
)


def _fake_docs(body=SITEMAP, fail=False):
    """`build` reads the docs site over HTTP, so every `build` test has to stub it
    or the suite reaches the network and the result depends on the cluster."""
    def fake(url=catalog.DOCS_SITEMAP):
        return catalog.Source(error="Connection refused") if fail else catalog.Source(
            rows=catalog.re.findall(r"<loc>([^<]+)</loc>", body)
        )
    return fake


def _fake_kubectl(failing):
    """Stand in for `_kubectl`, failing exactly the argv whose first `get` target is in `failing`."""
    def fake(args):
        target = args[args.index("get") + 1]
        if target in failing:
            return catalog.Source(error="Forbidden")
        if target == "xrd":
            return catalog.Source(rows=[{"spec": {"names": {"plural": "githubservices"}}}])
        if target == "deployments,statefulsets":
            return catalog.Source(rows=[deployment("nova", ns=args[args.index("-n") + 1])])
        if target == "ingress":
            return catalog.Source(rows=[ingress("argo", "argocd", "argo-server")])
        return catalog.Source(rows=[])
    return fake


def test_build_exits_1_and_suppresses_coverage_for_each_unreadable_source(monkeypatch):
    for target, label in (
        ("deployments,statefulsets", "workloads"),
        ("ingress", "ingresses"),
        ("applications", "argocd applications"),
        ("githubservices", "crossplane claims"),
    ):
        monkeypatch.setattr(catalog, "_kubectl", _fake_kubectl({target}))
        monkeypatch.setattr(catalog, "read_docs", _fake_docs())
        text, status = catalog.build()

        assert status == 1, f"{label} unreadable must exit 1"
        assert "composed by a claim" not in text, f"{label} unreadable must suppress the coverage number"
        assert label in text, f"{label} unreadable must be named in the report"


def test_build_exits_0_and_states_coverage_when_every_source_answers(monkeypatch):
    monkeypatch.setattr(catalog, "_kubectl", _fake_kubectl(set()))
    monkeypatch.setattr(catalog, "read_docs", _fake_docs())
    text, status = catalog.build()

    assert status == 0
    assert "composed by a claim" in text
    assert "Incomplete" not in text


def test_a_partial_workload_read_keeps_its_rows_and_hides_the_orphan_section(monkeypatch):
    """The failure the reviewer of runner#389 reproduced: one Forbidden namespace
    used to discard every row already read, and then every Ingress in the cluster
    printed as a door nothing accounts for."""
    monkeypatch.setattr(catalog, "_kubectl", _fake_kubectl({"ingress"}))
    workloads = catalog.read_workloads(("agents", "nope"))

    assert [s["metadata"]["name"] for s in workloads.rows] == ["nova", "nova"]

    monkeypatch.setattr(catalog, "_kubectl", _fake_kubectl({"applications"}))
    monkeypatch.setattr(catalog, "read_docs", _fake_docs())
    text, _ = catalog.build()
    assert "Doors nothing in the catalog accounts for" not in text


def test_the_composite_kinds_come_from_the_cluster_not_a_hardcoded_plural(monkeypatch):
    """The reviewer's finding: one hardcoded plural can only return one kind, so the
    headline number was 0 by construction. It has to be able to move."""
    def fake(args):
        target = args[args.index("get") + 1]
        if target == "xrd":
            return catalog.Source(rows=[{"spec": {"names": {"plural": "tailscaleexposures"}}}])
        if target == "tailscaleexposures":
            return catalog.Source(rows=[{"kind": "TailscaleExposure", "metadata": {"name": "nova"}}])
        if target == "deployments,statefulsets":
            return catalog.Source(rows=[deployment("nova", ns=args[args.index("-n") + 1])])
        return catalog.Source(rows=[])
    monkeypatch.setattr(catalog, "_kubectl", fake)
    monkeypatch.setattr(catalog, "read_docs", _fake_docs())
    text, status = catalog.build()

    assert status == 0
    assert "3 of 3 running services are composed by a claim" in text


# --- the document as it is stored, and publishing it (Cycle 451) -----------
#
# The failure these guard is not a wrong table. It is a correct table with a
# provenance line nobody updated: `catalog.md` said "Cycle 448" because a
# human typed it, so the page's freshness line was reporting when a cycle
# last remembered rather than when the cluster was last read.

from datetime import datetime, timedelta, timezone  # noqa: E402


def test_the_stored_document_carries_the_time_it_was_built():
    when = datetime(2026, 8, 26, 2, 15, tzinfo=timezone(timedelta(hours=2)))
    doc = catalog.document("# Service catalog\n", now=when)

    assert doc.startswith("---\n")
    assert "updated: 2026-08-26" in doc
    assert "Last regenerated 2026-08-26 02:15 Oslo." in doc
    assert doc.endswith("# Service catalog\n")


def test_the_stamp_is_oslo_not_the_pods_utc():
    """Rule 7: anything the owner reads is Oslo time, and the runner pod is UTC."""
    assert catalog._is_summer_time(datetime(2026, 8, 26, tzinfo=timezone.utc)) is True
    assert catalog._is_summer_time(datetime(2026, 1, 15, tzinfo=timezone.utc)) is False
    # The 2026 changeovers: 01:00 UTC on 29 March and on 25 October.
    assert catalog._is_summer_time(datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)) is False
    assert catalog._is_summer_time(datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)) is True
    assert catalog._is_summer_time(datetime(2026, 10, 25, 0, 59, tzinfo=timezone.utc)) is True
    assert catalog._is_summer_time(datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)) is False


def test_publish_writes_the_document_to_the_vault(monkeypatch):
    written = {}

    def fake_write(path, content, **kwargs):
        written["path"] = path
        written["content"] = content

    monkeypatch.setattr("agora_runner.vault.vault_write_path", fake_write)
    monkeypatch.setattr(catalog, "_kubectl", lambda args: catalog.Source(rows=[]))
    monkeypatch.setattr(catalog, "read_docs", _fake_docs())

    text, status = catalog.publish()

    assert status == 0
    assert written["path"] == "projects/sokrates/projects/agora/nova/catalog.md"
    assert written["content"].startswith("---\n")
    assert written["content"].endswith(text)


def test_publish_still_writes_when_a_source_was_forbidden(monkeypatch):
    """A partial read is not a reason to withhold the write. `render` already
    swaps the coverage number for a named refusal; withholding would leave the
    previous, confident-looking catalog on the page instead."""
    written = {}
    monkeypatch.setattr(
        "agora_runner.vault.vault_write_path",
        lambda path, content, **kw: written.update(content=content),
    )
    monkeypatch.setattr(
        catalog, "_kubectl", lambda args: catalog.Source(rows=[], error="Forbidden")
    )
    monkeypatch.setattr(catalog, "read_docs", _fake_docs())

    _text, status = catalog.publish()

    assert status == 1
    assert "**Incomplete" in written["content"]


def test_oslo_now_shifts_the_pods_utc_by_the_right_offset():
    """M6 in Cycle 451's mutation round survived without this: `_is_summer_time`
    was tested and the thing that *applies* it was not, so a stamp reading the
    pod's UTC would have passed every test in this file."""
    summer = catalog._oslo_now(datetime(2026, 8, 26, 0, 9, tzinfo=timezone.utc))
    winter = catalog._oslo_now(datetime(2026, 1, 15, 23, 30, tzinfo=timezone.utc))

    assert summer.strftime("%Y-%m-%d %H:%M") == "2026-08-26 02:09"
    assert winter.strftime("%Y-%m-%d %H:%M") == "2026-01-16 00:30"


def test_a_service_links_to_the_page_named_after_it():
    services = catalog.services_from([deployment("nova")])
    missing = catalog.attach_docs(services, ["https://docs.example/explanation/nova"])

    assert services[0].docs == "https://docs.example/explanation/nova"
    assert missing == []
    assert "[docs](https://docs.example/explanation/nova)" in catalog.render(services, [], [], missing)


def test_a_page_whose_slug_merely_contains_the_name_is_not_a_match():
    """The wrong link this join must not make: `/reference/agora-persona` is a page
    about the Persona resource, and `agora-persona-runner` is a different thing. A
    wrong link is worse than a blank, because the reader stops looking."""
    services = catalog.services_from([deployment("agora-persona-runner")])
    missing = catalog.attach_docs(services, ["https://docs.example/reference/agora-persona"])

    assert services[0].docs == ""
    assert missing == ["agents/agora-persona-runner"]


def test_the_image_repo_name_also_matches_when_the_workload_is_named_differently():
    services = catalog.services_from([deployment("web", image="ghcr.io/sokratesai/newspaper@sha256:ab")])
    catalog.attach_docs(services, ["https://docs.example/reference/newspaper"])

    assert services[0].docs == "https://docs.example/reference/newspaper"


def test_an_unreadable_docs_site_drops_the_column_rather_than_dashing_every_row(monkeypatch):
    """An unreachable site and a site with no service pages render identically as a
    column of dashes and mean opposite things, so the column has to go."""
    monkeypatch.setattr(catalog, "_kubectl", _fake_kubectl(set()))
    monkeypatch.setattr(catalog, "read_docs", _fake_docs(fail=True))
    text, status = catalog.build()

    assert status == 1
    assert "docs site: Connection refused" in text
    assert "| Docs |" not in text
    assert "no page named after them" not in text


def test_a_readable_docs_site_states_the_documented_count(monkeypatch):
    monkeypatch.setattr(catalog, "_kubectl", _fake_kubectl(set()))
    monkeypatch.setattr(catalog, "read_docs", _fake_docs())
    text, status = catalog.build()

    assert status == 0
    assert "3 of 3 services have a page named after them on the docs site." in text


def test_a_sitemap_with_no_pages_is_an_error_not_an_empty_catalog(monkeypatch):
    """The guaranteed-negative trap: something answering 200 with the wrong body
    would otherwise report every service as undocumented, confidently."""
    class Fake:
        def read(self): return b"<html>not a sitemap</html>"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(catalog.urllib.request, "urlopen", lambda url, timeout=0: Fake())
    got = catalog.read_docs("http://docs.example/sitemap.xml")

    assert not got.ok
    assert "not a sitemap" in got.error


def test_an_unreachable_docs_site_is_an_error_not_an_empty_page_list(monkeypatch):
    def boom(url, timeout=0):
        raise OSError("Connection refused")

    monkeypatch.setattr(catalog.urllib.request, "urlopen", boom)
    got = catalog.read_docs("http://docs.example/sitemap.xml")

    assert not got.ok
    assert got.rows == []
    assert "Connection refused" in got.error
