"""The catalog's join, tested on parsed rows so no cluster is needed.

The two properties worth protecting are the ones the tool exists for: a
`GitHubService` match must never be counted as a running service composed by a
claim, and an unreadable source must never render as a coverage number.
"""

from tools import catalog


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
        text, status = catalog.build()

        assert status == 1, f"{label} unreadable must exit 1"
        assert "composed by a claim" not in text, f"{label} unreadable must suppress the coverage number"
        assert label in text, f"{label} unreadable must be named in the report"


def test_build_exits_0_and_states_coverage_when_every_source_answers(monkeypatch):
    monkeypatch.setattr(catalog, "_kubectl", _fake_kubectl(set()))
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
    text, status = catalog.build()

    assert status == 0
    assert "3 of 3 running services are composed by a claim" in text
