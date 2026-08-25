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

    assert "Incomplete." in text
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
