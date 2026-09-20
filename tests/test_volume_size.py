"""Tests for `tools.volume_size`.

The pure half is the half that can be wrong quietly: a PV document this
misreads produces a Job that measures the wrong directory on the wrong node
and reports a confident number. So every parsing and grouping function is
tested against the real shape `kubectl get pv -o json` emits, and `measure` is
driven through a recording fake rather than a cluster.
"""

import json

import pytest

from tools import volume_size


def pv(name, path=None, node=None, namespace="agents", claim="a-claim"):
    spec = {"claimRef": {"namespace": namespace, "name": claim}}
    if path:
        spec["local"] = {"path": path}
    if node:
        spec["nodeAffinity"] = {
            "required": {
                "nodeSelectorTerms": [
                    {
                        "matchExpressions": [
                            {
                                "key": "kubernetes.io/hostname",
                                "operator": "In",
                                "values": [node],
                            }
                        ]
                    }
                ]
            }
        }
    return {"metadata": {"name": name}, "spec": spec}


def test_local_path_targets_reads_claim_node_and_path():
    payload = {"items": [pv("pv-1", "/srv/one", "server1", claim="post-data")]}
    assert volume_size.local_path_targets(payload) == [
        ("agents/post-data", "server1", "/srv/one")
    ]


def test_a_volume_that_is_not_local_path_is_skipped_not_guessed():
    payload = {"items": [pv("pv-nfs", node="server1")]}
    assert volume_size.local_path_targets(payload) == []


def test_a_local_path_volume_with_no_node_affinity_is_skipped():
    """A Job that cannot be pinned would measure an absent directory and
    report zero, which is the one wrong answer that looks right."""
    payload = {"items": [pv("pv-loose", "/srv/loose")]}
    assert volume_size.local_path_targets(payload) == []


def test_affinity_naming_two_nodes_is_not_a_pin():
    payload = {"items": [pv("pv-1", "/srv/one", "server1")]}
    expr = payload["items"][0]["spec"]["nodeAffinity"]["required"][
        "nodeSelectorTerms"
    ][0]["matchExpressions"][0]
    expr["values"] = ["server1", "server2"]
    assert volume_size.local_path_targets(payload) == []


def test_claim_filter_keeps_only_what_was_asked_for():
    payload = {
        "items": [
            pv("pv-1", "/srv/one", "server1", claim="wanted"),
            pv("pv-2", "/srv/two", "server1", claim="other"),
        ]
    }
    targets = volume_size.local_path_targets(payload, claims=["agents/wanted"])
    assert targets == [("agents/wanted", "server1", "/srv/one")]


def test_by_node_groups_and_deduplicates():
    targets = [
        ("agents/a", "server1", "/srv/a"),
        ("agents/b", "server2", "/srv/b"),
        ("agents/c", "server1", "/srv/a"),
    ]
    assert volume_size.by_node(targets) == {
        "server1": ["/srv/a"],
        "server2": ["/srv/b"],
    }


def test_size_command_mounts_the_path_under_the_host_mount():
    command = volume_size.size_command(["/srv/one"], mount="/host")
    assert 'du -xsk "/host/srv/one"' in command
    assert '"%s " "/srv/one"' in command or '"/srv/one"' in command


def test_parse_sizes_converts_kibibytes_to_bytes():
    assert volume_size.parse_sizes("/srv/one 8\n") == {"/srv/one": 8192}


def test_parse_sizes_keeps_missing_as_none_not_as_zero():
    """Absent and empty are opposite findings. A parser that dropped the
    MISSING line would turn a directory that is not on this node into a
    volume holding nothing, and nothing is exactly what an empty volume
    holds."""
    sizes = volume_size.parse_sizes("/srv/gone MISSING\n/srv/here 4\n")
    assert sizes == {"/srv/gone": None, "/srv/here": 4096}


def test_parse_sizes_ignores_noise_from_the_job():
    assert volume_size.parse_sizes("job.batch/x created\nnot a path 12\n") == {}


def test_human_never_calls_an_absent_directory_zero():
    assert volume_size.human(None) == "not on the node"
    assert volume_size.human(0) == "0 B"


def test_human_scales():
    assert volume_size.human(4096) == "4.1 KB"
    assert volume_size.human(2 * 1000 ** 3) == "2.0 GB"


def test_report_names_an_empty_volume_as_empty():
    lines = []
    status = volume_size.report(
        [("agents/post-data", "server1", "/srv/one")],
        {"/srv/one": 4096},
        out=lines.append,
    )
    assert status == 0
    assert "empty" in lines[0]
    assert "agents/post-data" in lines[0]


def test_report_does_not_call_a_volume_with_bytes_in_it_empty():
    lines = []
    volume_size.report(
        [("agents/couchdb", "server2", "/srv/db")],
        {"/srv/db": 300 * 1000 ** 2},
        out=lines.append,
    )
    assert "empty" not in lines[0]


def test_report_exits_2_when_a_volume_was_never_measured():
    lines = []
    status = volume_size.report(
        [("agents/a", "server1", "/srv/a")], {}, out=lines.append
    )
    assert status == 2
    assert "UNMEASURED" in lines[0]


def test_report_with_nothing_to_size_is_not_a_clean_run():
    lines = []
    assert volume_size.report([], {}, out=lines.append) == 1


class FakeKubectl:
    """Records every `kubectl` call and answers from a scripted log."""

    def __init__(self, logs):
        self.logs = logs
        self.calls = []
        self.applied = []

    def __call__(self, args, stdin=None, timeout=60):
        self.calls.append(list(args))
        if args[0] == "apply":
            self.applied.append(stdin)

        class Proc:
            returncode = 0
            stderr = ""

            def __init__(self, stdout=""):
                self.stdout = stdout

        if args[0] == "logs":
            name = args[1].split("/", 1)[1]
            return Proc(self.logs.get(name, ""))
        return Proc("")


def test_measure_runs_one_job_per_node_not_one_per_volume():
    targets = [
        ("agents/a", "server1", "/srv/a"),
        ("agents/b", "server1", "/srv/b"),
        ("agents/c", "server2", "/srv/c"),
    ]
    fake = FakeKubectl(
        {
            "nova-oneoff-volsize-server1": "/srv/a 4\n/srv/b 8\n",
            "nova-oneoff-volsize-server2": "/srv/c 12\n",
        }
    )
    sizes = volume_size.measure(targets, kubectl=fake)
    assert sizes == {"/srv/a": 4096, "/srv/b": 8192, "/srv/c": 12288}
    applies = [c for c in fake.calls if c[0] == "apply"]
    assert len(applies) == 2


def test_measure_pins_each_job_to_its_own_node():
    targets = [("agents/a", "server2", "/srv/a")]
    fake = FakeKubectl({"nova-oneoff-volsize-server2": "/srv/a 4\n"})
    volume_size.measure(targets, kubectl=fake)
    assert len(fake.applied) == 1
    manifest = json.loads(fake.applied[0])
    selector = manifest["spec"]["template"]["spec"]["nodeSelector"]
    assert selector == {"kubernetes.io/hostname": "server2"}


def test_measure_mounts_the_host_read_only():
    targets = [("agents/a", "server1", "/srv/a")]
    manifest = volume_size.oneoff_job.build_manifest(
        "volsize-server1",
        volume_size.size_command(["/srv/a"]),
        node="server1",
        hostpath="/",
        mount_path=volume_size.HOST_MOUNT,
    )
    mount = manifest["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][0]
    assert mount["readOnly"] is True
    assert mount["mountPath"] == "/host"


def test_a_node_whose_job_will_not_apply_does_not_lose_the_other_node():
    targets = [
        ("agents/a", "server1", "/srv/a"),
        ("agents/b", "server2", "/srv/b"),
    ]

    class HalfBroken(FakeKubectl):
        def __call__(self, args, stdin=None, timeout=60):
            proc = super().__call__(args, stdin=stdin, timeout=timeout)
            if args[0] == "apply" and "server1" in (stdin or ""):
                proc.returncode = 1
            return proc

    fake = HalfBroken({"nova-oneoff-volsize-server2": "/srv/b 8\n"})
    assert volume_size.measure(targets, kubectl=fake) == {"/srv/b": 8192}


def test_read_volumes_reports_an_unreadable_cluster_as_an_error():
    class Broken:
        def __call__(self, args, stdin=None, timeout=60):
            class Proc:
                returncode = 1
                stdout = ""
                stderr = "connection refused"

            return Proc()

    payload, error = volume_size.read_volumes(kubectl=Broken())
    assert payload is None
    assert "connection refused" in error


def test_main_dry_run_never_applies_anything():
    payload = {"items": [pv("pv-1", "/srv/one", "server1", claim="post-data")]}

    class Reader(FakeKubectl):
        def __call__(self, args, stdin=None, timeout=60):
            proc = super().__call__(args, stdin=stdin, timeout=timeout)
            if args[:2] == ["get", "pv"]:
                proc.stdout = json.dumps(payload)
            return proc

    fake = Reader({})
    lines = []
    assert volume_size.main(["--dry-run"], kubectl=fake, out=lines.append) == 0
    assert not [c for c in fake.calls if c[0] == "apply"]
    assert "du -xsk" in lines[0]
