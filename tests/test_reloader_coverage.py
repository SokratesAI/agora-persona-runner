"""A workload that reads a Secret nothing will restart it for is the finding."""

from __future__ import annotations

from tools import reloader_coverage as rc


def _workload(namespace, name, refs, annotations=None, kind="Deployment"):
    seen, judged = rc.covered(annotations or {}, set(refs))
    return {
        "kind": kind,
        "namespace": namespace,
        "name": name,
        "refs": set(refs),
        "covered": seen,
        "judged": judged,
    }


class TestReferences:
    def test_finds_all_four_ways_a_pod_names_one(self):
        spec = {
            "volumes": [
                {"name": "a", "configMap": {"name": "app-config"}},
                {"name": "b", "secret": {"secretName": "tls"}},
            ],
            "containers": [
                {
                    "name": "c",
                    "envFrom": [
                        {"configMapRef": {"name": "env-config"}},
                        {"secretRef": {"name": "env-secret"}},
                    ],
                    "env": [
                        {"name": "P", "valueFrom": {"secretKeyRef": {"name": "db"}}},
                        {"name": "Q", "valueFrom": {"configMapKeyRef": {"name": "tune"}}},
                        {"name": "R", "value": "literal"},
                    ],
                }
            ],
        }
        assert rc.references(spec) == {
            "cm:app-config", "sec:tls", "cm:env-config",
            "sec:env-secret", "sec:db", "cm:tune",
        }

    def test_a_secret_named_only_by_an_init_container_still_counts(self):
        spec = {"initContainers": [
            {"name": "i", "envFrom": [{"secretRef": {"name": "bootstrap"}}]}
        ]}
        assert rc.references(spec) == {"sec:bootstrap"}

    def test_the_projected_cluster_ca_is_not_a_reference(self):
        # Kubernetes mounts it into every pod, so counting it would make every
        # workload permanently uncovered and the whole report meaningless.
        spec = {"volumes": [
            {"name": "kube-api-access", "projected": {"sources": [
                {"configMap": {"name": "kube-root-ca.crt"}},
                {"secret": {"secretName": "real-one"}},
            ]}}
        ]}
        assert rc.references(spec) == {"sec:real-one"}


class TestCovered:
    def test_auto_covers_every_reference(self):
        seen, judged = rc.covered({rc._AUTO: "true"}, {"sec:a", "cm:b"})
        assert judged and seen == {"sec:a", "cm:b"}

    def test_a_named_configmap_does_not_cover_a_secret(self):
        # telegram-bridge's real shape: its ConfigMap is named and its bot-token
        # Secret is not, so a token rotation reaches nothing.
        annotations = {rc._CM_RELOAD: "telegram-bridge-app"}
        seen, judged = rc.covered(annotations, {"cm:telegram-bridge-app", "sec:owner"})
        assert judged
        assert seen == {"cm:telegram-bridge-app"}

    def test_a_comma_list_covers_each_name(self):
        seen, _ = rc.covered({rc._CM_RELOAD: "a, b ,c"}, {"cm:a", "cm:b", "cm:c", "cm:d"})
        assert seen == {"cm:a", "cm:b", "cm:c"}

    def test_search_is_not_judgeable_here(self):
        _, judged = rc.covered({rc._SEARCH: "true"}, {"sec:a"})
        assert judged is False

    def test_no_annotation_covers_nothing(self):
        seen, judged = rc.covered({}, {"sec:a"})
        assert judged and seen == set()


class TestReport:
    def test_an_uncovered_owned_workload_raises(self):
        code, lines = rc.report(1, [_workload("agents", "agora", {"sec:token"})])
        assert code == 2
        assert any("NO RESTART" in line and "agents/agora" in line for line in lines)

    def test_a_covered_workload_is_clean(self):
        code, lines = rc.report(
            1, [_workload("agents", "nova-site", {"sec:token"}, {rc._AUTO: "true"})]
        )
        assert code == 0
        assert any(line.startswith("ok") and "nova-site" in line for line in lines)

    def test_a_workload_outside_the_owned_namespaces_never_raises(self):
        outside = _workload("argocd", "argocd-server", {"sec:argocd-redis"})
        # The precondition this guard depends on: it really is uncovered, so a
        # zero exit here is the scope rule and not an accidentally clean input.
        assert outside["refs"] - outside["covered"]
        code, lines = rc.report(1, [outside])
        assert code == 0
        assert not any("NO RESTART" in line for line in lines)

    def test_a_shared_secret_names_the_workloads_left_behind(self):
        code, lines = rc.report(1, [
            _workload("agents", "runner", {"sec:couchdb-credentials"}, {rc._AUTO: "true"}),
            _workload("obsidian", "vault-bridge", {"sec:couchdb-credentials"}),
        ])
        assert code == 2
        shared = [line for line in lines if line.startswith("SHARED")]
        assert len(shared) == 1
        assert "obsidian/vault-bridge" in shared[0]
        assert "agents/runner" not in shared[0]

    def test_a_secret_only_one_workload_reads_gets_no_shared_line(self):
        _, lines = rc.report(1, [_workload("agents", "marcus", {"sec:marcus-secrets"})])
        assert not any(line.startswith("SHARED") for line in lines)

    def test_no_ready_reloader_cannot_judge_anything(self):
        # Every annotation in the cluster is decorative when nothing is watching,
        # so a clean-looking workload must not certify the cluster.
        clean = _workload("agents", "nova-site", {"sec:token"}, {rc._AUTO: "true"})
        assert not (clean["refs"] - clean["covered"])
        code, lines = rc.report(0, [clean])
        assert code == 1
        assert any("CANNOT JUDGE" in line for line in lines)

    def test_a_search_annotated_workload_is_reported_not_guessed(self):
        code, lines = rc.report(
            1, [_workload("agents", "thing", {"sec:a"}, {rc._SEARCH: "true"})]
        )
        assert code == 0
        assert any("not judged" in line and "agents/thing" in line for line in lines)
