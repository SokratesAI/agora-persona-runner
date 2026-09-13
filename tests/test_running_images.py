"""What `tools.running_images` must get right about a live cluster."""

import json
import types

import pytest

from tools import running_images as ri


#: Captured before the autouse fixture below replaces it, so the tests
#: that mean to exercise the real fetch still can.
REAL_FETCH = ri.fetch_manifests


@pytest.fixture(autouse=True)
def _no_github(monkeypatch):
    """No test in this file may reach GitHub by accident.

    `main` fetches `platform-config` now, so three tests written about the
    live-cluster exit contract started making a real `gh api` call the
    moment that landed. A test that quietly goes to the network is a test
    whose result depends on the network; the tests that mean to exercise
    the manifest half override this in their own body.
    """
    monkeypatch.setattr(ri, "fetch_manifests", lambda: ({}, None))


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


def test_two_digests_under_one_name_are_told_apart():
    """My reviewer's finding on runner#514.

    `ghcr.io/sokratesai/vault-bridge` runs one digest in `agents` and a
    different one in `obsidian`. Printing the name alone rendered those two
    correct groups as the same string twice, which reads as a duplicated
    line rather than as "two consumers are on unreconciled pins".
    """
    ref = "ghcr.io/sokratesai/vault-bridge@sha256:"
    images = [{"ref": ref + "63b9c5db" + "0" * 56, "kind": "deployment",
               "name": "newspaper", "namespace": "agents", "container": "c"},
              {"ref": ref + "fcf9610d" + "1" * 56, "kind": "deployment",
               "name": "vault-bridge", "namespace": "obsidian",
               "container": "c"}]
    report = ri.format_report(images, {}, [])
    printed = [l for l in report.splitlines() if "vault-bridge@" in l]
    assert len(printed) == 2
    assert printed[0] != printed[1]
    assert "63b9c5db" in report and "fcf9610d" in report


def test_a_reference_with_no_digest_is_printed_whole():
    assert ri._short_digest("prom/prometheus:latest") == "prom/prometheus:latest"


# --- the manifest half: what git declares that the cluster cannot show ----

def _tarball(files):
    """A gzipped tarball shaped like `gh api .../tarball` returns one."""
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            blob = text.encode("utf-8")
            info = tarfile.TarInfo("SokratesAI-platform-config-abc123/" + name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


def _gh(payload, returncode=0, stderr=b""):
    def run(args, **_kwargs):
        assert args[0] == "gh", args
        return types.SimpleNamespace(returncode=returncode, stdout=payload,
                                     stderr=stderr)
    return run


DEPLOYMENT = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: thing
spec:
  template:
    spec:
      initContainers:
        - name: wait
          image: curlimages/curl:latest
      containers:
        - name: app
          image: ghcr.io/sokratesai/thing@sha256:abc
"""


def test_a_manifest_is_read_out_of_the_tarball_not_a_local_clone():
    files, why = REAL_FETCH(runner=_gh(_tarball(
        {"deployments/thing.yaml": DEPLOYMENT, "README.md": "not yaml"})))
    assert why is None
    assert sorted(files) == ["deployments/thing.yaml"]
    assert "curlimages/curl:latest" in files["deployments/thing.yaml"]


def test_an_init_container_in_a_manifest_is_a_container():
    """The `curlimages/curl:latest` idea #178 names is an init container.

    Reporting only `containers` would answer the row's own example with
    silence.
    """
    images, problems = ri.images_in_manifests(
        {"deployments/thing.yaml": DEPLOYMENT})
    assert problems == []
    assert sorted(i["ref"] for i in images) == [
        "curlimages/curl:latest", "ghcr.io/sokratesai/thing@sha256:abc"]
    assert images[0]["path"] == "deployments/thing.yaml"


def test_a_cronjob_template_is_reached_without_naming_its_path():
    """A CronJob buries the pod spec two levels deeper than a Deployment."""
    cronjob = """
apiVersion: batch/v1
kind: CronJob
metadata:
  name: tick
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: run
              image: ghcr.io/sokratesai/runtime:main
"""
    images, _ = ri.images_in_manifests({"cronjobs/tick.yaml": cronjob})
    assert [i["ref"] for i in images] == ["ghcr.io/sokratesai/runtime:main"]


def test_a_manifest_that_does_not_parse_is_a_problem_not_a_silence():
    images, problems = ri.images_in_manifests({"broken.yaml": "a: [1\nb: }"})
    assert images == []
    assert len(problems) == 1 and "broken.yaml" in problems[0]


def test_a_declared_mutable_image_with_no_live_object_is_the_finding():
    manifest = [{"ref": "curlimages/curl:latest", "path": "deployments/db.yaml"}]
    assert ri.declared_not_running(manifest, []) == {
        "curlimages/curl:latest": ["deployments/db.yaml"]}


def test_a_declared_mutable_image_that_is_running_is_not_reported_twice():
    """The live sweep already prints it, with the digest running under it."""
    manifest = [{"ref": "ghcr.io/sokratesai/runtime:main", "path": "a.yaml"}]
    live = [{"ref": "ghcr.io/sokratesai/runtime:main", "kind": "cronjob",
             "name": "tick", "namespace": "agents", "container": "c"}]
    assert ri.declared_not_running(manifest, live) == {}


def test_the_join_survives_the_docker_io_prefix():
    """`normalise` exists because the cluster qualifies Docker Hub and git does not.

    Without it every Docker Hub image in a manifest reads as undeployed,
    which is a finding guaranteed in advance rather than a measurement.
    """
    manifest = [{"ref": "prom/prometheus:latest", "path": "a.yaml"}]
    live = [{"ref": "docker.io/prom/prometheus:latest", "kind": "deployment",
             "name": "prometheus", "namespace": "infra", "container": "c"}]
    assert ri.declared_not_running(manifest, live) == {}


def test_a_declared_pinned_image_with_no_live_object_is_not_a_finding():
    """This check judges mutability, not whether ArgoCD has synced."""
    manifest = [{"ref": "ghcr.io/sokratesai/thing@sha256:abc", "path": "a.yaml"},
                {"ref": "couchdb:3.3", "path": "b.yaml"}]
    assert ri.declared_not_running(manifest, []) == {}


def test_one_reference_declared_in_two_files_names_both_once():
    manifest = [{"ref": "x:latest", "path": "a.yaml"},
                {"ref": "x:latest", "path": "b.yaml"},
                {"ref": "x:latest", "path": "a.yaml"}]
    assert ri.declared_not_running(manifest, []) == {"x:latest": ["a.yaml", "b.yaml"]}


def test_gh_failing_is_reported_rather_than_read_as_nothing_declared():
    files, why = REAL_FETCH(
        runner=_gh(b"", returncode=1, stderr=b"gh: HTTP 404\n"))
    assert files is None
    assert "HTTP 404" in why


def test_a_tarball_that_is_not_a_tarball_is_reported():
    files, why = REAL_FETCH(runner=_gh(b"this is not gzip"))
    assert files is None
    assert "tarball" in why


def test_an_undeployed_manifest_image_raises_the_exit_code(monkeypatch):
    monkeypatch.setattr(ri, "read_workloads", lambda: ([], []))
    monkeypatch.setattr(ri, "read_pods", lambda: (
        [{"ref": "ghcr.io/sokratesai/thing@sha256:abc", "kind": "pod",
          "name": "p", "namespace": "agents", "container": "c"}], {}, []))
    monkeypatch.setattr(ri, "fetch_manifests",
                        lambda: ({"a.yaml": DEPLOYMENT}, None))
    assert ri.main([]) == 2


def test_no_manifests_answers_from_the_live_cluster_alone(monkeypatch):
    """The flag has to actually skip the read, not just hide the section."""
    called = []
    monkeypatch.setattr(ri, "read_workloads", lambda: ([], []))
    monkeypatch.setattr(ri, "read_pods", lambda: (
        [{"ref": "ghcr.io/sokratesai/thing@sha256:abc", "kind": "pod",
          "name": "p", "namespace": "agents", "container": "c"}], {}, []))
    monkeypatch.setattr(ri, "fetch_manifests",
                        lambda: called.append(1) or ({}, None))
    assert ri.main(["--no-manifests"]) == 0
    assert called == []


def test_github_being_unreadable_never_reads_as_clean(monkeypatch):
    monkeypatch.setattr(ri, "read_workloads", lambda: ([], []))
    monkeypatch.setattr(ri, "read_pods", lambda: (
        [{"ref": "ghcr.io/sokratesai/thing@sha256:abc", "kind": "pod",
          "name": "p", "namespace": "agents", "container": "c"}], {}, []))
    monkeypatch.setattr(ri, "fetch_manifests", lambda: (None, "gh: HTTP 404"))
    assert ri.main([]) == 1


def test_the_report_names_the_file_that_declares_an_undeployed_image():
    report = ri.format_report(
        [], {}, [], {"curlimages/curl:latest": ["deployments/couchdb/init.yaml"]}, 29)
    assert "DECLARED BUT NOT RUNNING — 1 mutable reference(s)" in report
    assert "deployments/couchdb/init.yaml" in report
    assert "Read 29 container image reference(s) from" in report
