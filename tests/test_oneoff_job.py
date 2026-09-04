"""What a one-off Job must be, so it never needs a pull request to clean up.

Each test here pins one property that, if it silently drifted, would send
one-off Jobs back through `platform-config` -- four billed private-repo CI
minutes each -- or would leave spent Jobs sitting against the `test`
namespace's 10-pod quota with nobody to delete them.
"""

import json

import pytest

from tools import oneoff_job


def test_the_job_deletes_itself_so_no_second_merge_is_needed():
    """The whole point. A spent Job in `test-jobs/` needed a merge to remove;
    this one is owned by nothing, so the TTL controller reaps it."""
    m = oneoff_job.build_manifest("probe", "echo hi")
    assert m["spec"]["ttlSecondsAfterFinished"] == oneoff_job.TTL_SECONDS
    assert m["spec"]["ttlSecondsAfterFinished"] > 0


def test_it_lands_in_the_test_namespace_and_nowhere_else():
    """The RBAC grant is scoped to `test`. A manifest that named any other
    namespace would be refused by the API server after the tool had already
    reported it applied, which reads as a broken cluster rather than a bug."""
    m = oneoff_job.build_manifest("probe", "echo hi")
    assert m["metadata"]["namespace"] == "test"
    assert m["metadata"]["name"].startswith("nova-oneoff-")


def test_it_does_not_retry():
    """A one-off Job asks a question once. `backoffLimit` above 0 turns one
    clean failure into several identical ones and a longer wait."""
    m = oneoff_job.build_manifest("probe", "false")
    assert m["spec"]["backoffLimit"] == 0
    assert m["spec"]["template"]["spec"]["restartPolicy"] == "Never"


def test_a_hostpath_mount_is_read_only():
    """Per-node questions are why hostPath is here at all. Writable would make
    a read-only probe able to damage the node it is measuring."""
    m = oneoff_job.build_manifest("disk", "df -h /host", hostpath="/var/lib/rancher")
    spec = m["spec"]["template"]["spec"]
    assert spec["volumes"][0]["hostPath"] == {
        "path": "/var/lib/rancher",
        "type": "Directory",
    }
    mount = spec["containers"][0]["volumeMounts"][0]
    assert mount["readOnly"] is True
    assert mount["mountPath"] == "/host"


def test_no_hostpath_means_no_volume_at_all():
    """A volumeMount naming a volume that is not there makes the pod
    unschedulable, and the failure surfaces as a timeout rather than an error."""
    spec = oneoff_job.build_manifest("probe", "echo hi")["spec"]["template"]["spec"]
    assert "volumes" not in spec
    assert "volumeMounts" not in spec["containers"][0]


def test_node_pinning_is_absent_unless_asked_for():
    """A per-node check that silently ran on whichever node the scheduler
    picked would answer a different question than the one asked -- and there
    are two nodes now."""
    spec = oneoff_job.build_manifest("probe", "echo hi")["spec"]["template"]["spec"]
    assert "nodeSelector" not in spec
    pinned = oneoff_job.build_manifest("probe", "echo hi", node="server2")
    assert pinned["spec"]["template"]["spec"]["nodeSelector"] == {
        "kubernetes.io/hostname": "server2"
    }


def test_an_empty_name_is_refused():
    with pytest.raises(ValueError):
        oneoff_job.build_manifest("", "echo hi")


class FakeKubectl:
    """Records the kubectl calls and replays canned results."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, stdin=None, timeout=None):
        self.calls.append((args, stdin))
        return self.results.pop(0)


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_logs_are_printed_even_when_the_job_fails(capsys):
    """The failure case is the one the Job was run to see. A tool that only
    printed logs on success would hide exactly the output that matters."""
    fake = FakeKubectl(
        [
            Result(stdout="job.batch/nova-oneoff-x created\n"),
            Result(returncode=1, stderr="timed out\n"),  # wait
            Result(stdout="the interesting error\n"),  # logs
        ]
    )
    manifest = oneoff_job.build_manifest("x", "false")
    code = oneoff_job.run(manifest, wait=5, kubectl=fake)
    assert code == 2
    assert "the interesting error" in capsys.readouterr().out


def test_a_failed_apply_stops_before_waiting(capsys):
    """Waiting on a Job that was never created burns the whole timeout and
    then reports a timeout, which names the wrong cause."""
    fake = FakeKubectl([Result(returncode=1, stderr="quota exceeded\n")])
    code = oneoff_job.run(oneoff_job.build_manifest("x", "true"), kubectl=fake)
    assert code == 1
    assert len(fake.calls) == 1


def test_the_manifest_is_what_gets_applied():
    """`kubectl apply -f -` reads stdin; sending anything but the manifest the
    caller inspected with --dry-run would make the dry run a lie."""
    fake = FakeKubectl([Result(), Result(), Result(stdout="hi\n")])
    manifest = oneoff_job.build_manifest("x", "echo hi")
    oneoff_job.run(manifest, kubectl=fake)
    args, stdin = fake.calls[0]
    assert args == ["apply", "-f", "-"]
    assert json.loads(stdin) == manifest
