"""What `tools.running_images` must get right about a live cluster."""

import json
import types

import pytest

from tools import running_images as ri


def _proc(payload):
    return types.SimpleNamespace(returncode=0, stdout=json.dumps(payload),
                                 stderr="")


def _runner(bodies):
    """A fake `subprocess.run` answering one payload per kubectl resource."""
    def run(args, **_kwargs):
        return _proc(bodies.get(args[2], {"items": []}))
    return run


def _workload(kind, name, namespace, image, init=None):
    spec = {"containers": [{"name": "c", "image": image}]}
    if init:
        spec["initContainers"] = [{"name": "i", "image": init}]
    meta = {"name": name, "namespace": namespace}
    if kind == "cronjobs":
        return {"metadata": meta,
                "spec": {"jobTemplate": {"spec": {"template": {"spec": spec}}}}}
    return {"metadata": meta, "spec": {"template": {"spec": spec}}}


@pytest.mark.parametrize("ref,verdict", [
    ("ghcr.io/x/y@sha256:" + "a" * 64, "digest"),
    ("ghcr.io/x/y:v1.2.0@sha256:" + "a" * 64, "digest"),
    ("couchdb:3.3", "version"),
    ("redis:7-alpine", "version"),
    ("tailscale/tailscale:v1.102.3", "version"),
    ("prom/prometheus:latest", "mutable"),
    ("ghcr.io/sokratesai/sokrates-agent-runtime:main", "mutable"),
    ("nginx:alpine", "mutable"),
    ("busybox", "mutable"),
    ("registry:5000/thing", "mutable"),
    ("registry:5000/thing:2.1", "version"),
])
def test_classify(ref, verdict):
    assert ri.classify(ref) == verdict


def test_registry_port_is_not_a_tag():
    assert ri.split_ref("registry:5000/thing:2.1") == ("registry:5000/thing",
                                                       "2.1", None)


def test_scaled_to_zero_workload_is_still_read():
    """The parked `whatsapp-bridge` is exactly what a Pod-only sweep misses."""
    runner = _runner({
        "deployments": {"items": [
            _workload("deployments", "whatsapp-bridge", "infra", "wa:latest")]},
        "pods": {"items": []},
    })
    images, problems = ri.read_workloads(runner)
    assert problems == []
    assert [i["ref"] for i in images] == ["wa:latest"]


def test_cronjob_template_is_read():
    """A CronJob has no Pod between firings and still names an image."""
    runner = _runner({"cronjobs": {"items": [
        _workload("cronjobs", "heartbeat-liveness", "agents", "rt:main")]}})
    images, _ = ri.read_workloads(runner)
    assert [(i["kind"], i["ref"]) for i in images] == [("cronjob", "rt:main")]


def test_init_containers_are_read():
    runner = _runner({"jobs": {"items": [
        _workload("jobs", "seed", "obsidian", "couchdb:3.3",
                  init="curlimages/curl:latest")]}})
    refs = [i["ref"] for i in ri.read_workloads(runner)[0]]
    assert refs == ["curlimages/curl:latest", "couchdb:3.3"]


def test_owned_pods_are_not_counted_twice():
    """A ReplicaSet's Pod is its Deployment's image asked a second time."""
    runner = _runner({"pods": {"items": [
        {"metadata": {"name": "d-abc", "namespace": "infra",
                      "ownerReferences": [{"kind": "ReplicaSet"}]},
         "spec": {"containers": [{"name": "c", "image": "g:latest"}]},
         "status": {}},
        {"metadata": {"name": "bare", "namespace": "infra"},
         "spec": {"containers": [{"name": "c", "image": "b:latest"}]},
         "status": {}},
    ]}})
    free, _resolved, problems = ri.read_pods(runner)
    assert problems == []
    assert [i["ref"] for i in free] == ["b:latest"]


def test_docker_hub_digest_joins_across_the_two_spellings():
    """The spec says `grafana/grafana:latest`, the status says `docker.io/...`.

    Without the normalisation the lookup misses and every Docker Hub image
    reports "no Pod is running this" while its Pod is running.
    """
    runner = _runner({"pods": {"items": [
        {"metadata": {"name": "g", "namespace": "infra",
                      "ownerReferences": [{"kind": "ReplicaSet"}]},
         "spec": {"containers": [{"name": "c",
                                  "image": "docker.io/grafana/grafana:latest"}]},
         "status": {"containerStatuses": [
             {"image": "docker.io/grafana/grafana:latest",
              "imageID": "docker.io/grafana/grafana@sha256:" + "b" * 64}]}},
    ]}})
    _free, resolved, _ = ri.read_pods(runner)
    images = [{"ref": "grafana/grafana:latest", "kind": "deployment",
               "name": "grafana", "namespace": "infra", "container": "c"}]
    report = ri.format_report(images, resolved, [])
    assert "running now: sha256:" + "b" * 64 in report
    assert "no Pod is running this" not in report


def test_workload_with_no_pod_says_so_rather_than_guessing():
    images = [{"ref": "rt:main", "kind": "cronjob", "name": "hb",
               "namespace": "agents", "container": "c"}]
    report = ri.format_report(images, {}, [])
    assert "no Pod is running this right now" in report


def test_a_mutable_image_raises_over_an_incomplete_sweep(monkeypatch, capsys):
    monkeypatch.setattr(ri, "read_workloads",
                        lambda: ([{"ref": "x:latest", "kind": "deployment",
                                   "name": "x", "namespace": "n",
                                   "container": "c"}], ["kubectl failed"]))
    monkeypatch.setattr(ri, "read_pods", lambda: ([], {}, []))
    assert ri.main([]) == 2
    assert "MUTABLE IMAGE" in capsys.readouterr().out


def test_unreadable_is_never_clean(monkeypatch, capsys):
    monkeypatch.setattr(ri, "read_workloads", lambda: ([], ["kubectl failed"]))
    monkeypatch.setattr(ri, "read_pods", lambda: ([], {}, []))
    assert ri.main([]) == 1
    assert "cannot claim the sweep was complete" in capsys.readouterr().out


def test_no_workloads_at_all_is_no_instrument(monkeypatch, capsys):
    monkeypatch.setattr(ri, "read_workloads", lambda: ([], []))
    monkeypatch.setattr(ri, "read_pods", lambda: ([], {}, []))
    assert ri.main([]) == 1
    assert "no instrument" in capsys.readouterr().out


def test_everything_pinned_exits_zero(monkeypatch):
    pinned = [{"ref": "ghcr.io/x/y@sha256:" + "a" * 64, "kind": "deployment",
               "name": "y", "namespace": "agents", "container": "c"},
              {"ref": "couchdb:3.3", "kind": "deployment", "name": "db",
               "namespace": "obsidian", "container": "c"}]
    monkeypatch.setattr(ri, "read_workloads", lambda: (pinned, []))
    monkeypatch.setattr(ri, "read_pods", lambda: ([], {}, []))
    assert ri.main([]) == 0


def test_one_image_in_two_places_is_one_finding():
    images = [{"ref": "rt:main", "kind": "deployment", "name": "a",
               "namespace": "agents", "container": "c"},
              {"ref": "rt:main", "kind": "cronjob", "name": "b",
               "namespace": "agents", "container": "c"}]
    report = ri.format_report(images, {}, [])
    assert "MUTABLE IMAGE — 1 image reference(s)" in report
    assert "agents/deployment a" in report
    assert "agents/cronjob b" in report
