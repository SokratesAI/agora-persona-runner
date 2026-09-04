"""Nothing watched the node disk, and a local-path claim's size is enforced by nothing.

The failure these pin is the one the tool was built around: every claim in
this cluster reports the *node filesystem's* used/capacity, so a reader that
judged a volume against its requested 1Gi would print thousands of percent
for every volume forever, and a reader that judged it against the reported
capacity would call a 72%-full node disk a healthy 1Gi volume.
"""

import json
import subprocess

from tools import disk_health


NODE_CAPACITY = 80307429376
NODE_AVAILABLE = 19111919616


def _summary(volumes=(), nodefs=True, imagefs=True, node_available=NODE_AVAILABLE):
    node = {"nodeName": "server1"}
    if nodefs:
        node["fs"] = {
            "capacityBytes": NODE_CAPACITY,
            "availableBytes": node_available,
            "usedBytes": NODE_CAPACITY - node_available,
        }
    if imagefs:
        node["runtime"] = {
            "imageFs": {
                "capacityBytes": NODE_CAPACITY,
                "availableBytes": node_available,
                "usedBytes": NODE_CAPACITY - node_available,
            }
        }
    pods = []
    for namespace, claim, pod, capacity, available in volumes:
        pods.append(
            {
                "podRef": {"namespace": namespace, "name": pod},
                "volume": [
                    {
                        "pvcRef": {"namespace": namespace, "name": claim},
                        "capacityBytes": capacity,
                        "availableBytes": available,
                        "usedBytes": capacity - available,
                    }
                ],
            }
        )
    return {"node": node, "pods": pods}


def _runner(nodes=("server1",), summaries=None, node_list_rc=0, claims=(), pods=()):
    summaries = summaries or {}

    def run(argv, capture_output=False, text=False):
        if argv[:3] == ["kubectl", "get", "pvc"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"items": list(claims)}), ""
            )
        if argv[:3] == ["kubectl", "get", "pods"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"items": list(pods)}), ""
            )
        if argv[:3] == ["kubectl", "get", "nodes"]:
            body = {"items": [{"metadata": {"name": name}} for name in nodes]}
            return subprocess.CompletedProcess(
                argv, node_list_rc, json.dumps(body), "no route to the API server"
            )
        assert argv[:3] == ["kubectl", "get", "--raw"], argv
        node = argv[3].split("/")[4]
        answer = summaries.get(node)
        if answer is None:
            return subprocess.CompletedProcess(argv, 1, "", "Error from server: 502")
        return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")

    return run


def _lines(argv=None, **kwargs):
    printed = []
    code = disk_health.main(argv or [], runner=_runner(**kwargs), out=printed.append)
    return code, "\n".join(printed)


def test_a_local_path_claim_is_not_judged_against_its_reported_capacity():
    """The volume reports the node's disk, so it must print NOT CAPPED, not `ok`.

    A reader that took `capacityBytes` at face value would call marcus-data a
    74.8GiB volume with 17.8GiB free — which is the node, not the volume.
    """
    summary = _summary(
        volumes=[("agents", "marcus-data", "marcus-1", NODE_CAPACITY, NODE_AVAILABLE)]
    )
    code, text = _lines(summaries={"server1": summary})
    assert "NOT CAPPED agents/marcus-data" in text
    assert "enforced by nothing" in text
    assert "0 claim(s) report a size of their own; 1 report the node's disk" in text
    assert code == 0


def test_a_volume_with_a_capacity_of_its_own_is_judged_against_it():
    """The uncapped branch must not swallow a real quota-backed volume.

    Precondition this test depends on: the node itself is healthy here
    (23.8% free), so the only thing that can raise is the volume.
    """
    summary = _summary(
        volumes=[("agents", "real-data", "pod-1", 1073741824, 10737418)]  # 1GiB, 1% free
    )
    assert disk_health.available_pct(summary["node"]["fs"]) > disk_health.raises_at(
        "nodefs"
    )
    code, text = _lines(summaries={"server1": summary})
    assert "FILLING    agents/real-data" in text
    assert "NOT CAPPED" not in text
    assert code == 2


def test_a_node_inside_the_margin_raises_and_names_the_kubelets_own_point():
    """Free space between the eviction threshold and the margin is the finding.

    12% free is above the kubelet's 10% nodefs eviction point and below this
    check's 15%, so it is exactly the window the margin exists to catch.
    """
    twelve_pct = int(NODE_CAPACITY * 0.12)
    summary = _summary(node_available=twelve_pct)
    code, text = _lines(summaries={"server1": summary})
    assert "FILLING    server1 nodefs" in text
    assert "the kubelet acts at 10.0%" in text
    assert code == 2


def test_a_node_above_the_margin_does_not_raise():
    code, text = _lines(summaries={"server1": _summary()})
    assert "ok         server1 nodefs" in text
    assert "FILLING" not in text
    assert code == 0


def test_an_unreadable_kubelet_exits_one_and_is_named():
    """A node the sweep could not reach must never read as a clean node."""
    code, text = _lines(
        nodes=("server1", "server2"), summaries={"server1": _summary()}
    )
    assert "CANNOT READ server2" in text
    assert "the sweep is partial" in text
    assert code == 1


def test_an_unreadable_node_list_exits_one_and_sweeps_nothing():
    code, text = _lines(node_list_rc=1)
    assert "CANNOT READ the node list" in text
    assert "not a clean result" in text
    assert "== server1" not in text
    assert code == 1


def test_a_missing_filesystem_block_reads_as_unread_not_as_full():
    """An absent imagefs and a full one must not print the same thing.

    The *reason* is asserted, not just the NOT READ prefix: "the kubelet
    published no block for it" and "published no availableBytes" are two
    different states of the kubelet and the first mutation I ran survived on
    a test that only checked the prefix they share.
    """
    code, text = _lines(summaries={"server1": _summary(imagefs=False)})
    assert "NOT READ   server1 imagefs — the kubelet published no block for it" in text
    assert "FILLING    server1 imagefs" not in text
    assert code == 0


def test_a_filesystem_block_with_no_available_bytes_says_which_half_is_missing():
    """A published block missing availableBytes is a different state again."""
    summary = _summary()
    del summary["node"]["fs"]["availableBytes"]
    code, text = _lines(summaries={"server1": summary})
    assert "NOT READ   server1 nodefs — the kubelet published no availableBytes" in text
    assert "FILLING    server1 nodefs" not in text
    assert code == 0


def test_the_margin_sits_above_the_kubelets_own_eviction_point():
    """The threshold is the kubelet's number plus a margin, checked against
    the literal values rather than against `raises_at`'s own arithmetic —
    the first version of this test restated the implementation line and could
    not fail.
    """
    assert disk_health.EVICTION_PCT == {"nodefs": 10.0, "imagefs": 15.0}
    assert disk_health.raises_at("nodefs") == 15.0
    assert disk_health.raises_at("imagefs") == 20.0


def test_a_real_finding_outranks_a_node_the_sweep_could_not_read():
    """Exit 2, not 1, when one node is critical and another is unreachable.

    preflight collapses 1 to `UNREADABLE`, the word for "nothing could be
    judged" — printing it over a node that WAS judged and is at 2% free is the
    less urgent answer winning.
    """
    two_pct = int(NODE_CAPACITY * 0.02)
    code, text = _lines(
        nodes=("server1", "server2"),
        summaries={"server1": _summary(node_available=two_pct)},
    )
    assert "FILLING    server1 nodefs" in text
    assert "CANNOT READ server2" in text
    assert code == 2


def test_the_last_line_carrying_a_digit_varies_with_the_result():
    """preflight's roster row is the last digit-bearing line, so it must move.

    The first version ended on a fixed disclaimer sentence, which meant the
    collapsed row said the same thing whatever the sweep found.
    """
    def last_number_line(text):
        return [ln for ln in text.splitlines() if any(c.isdigit() for c in ln)][-1]

    _, one = _lines(summaries={"server1": _summary()})
    _, two = _lines(
        nodes=("server1", "server2"),
        summaries={
            "server1": _summary(
                volumes=[("agents", "m", "p", NODE_CAPACITY, NODE_AVAILABLE)]
            ),
            "server2": _summary(),
        },
    )
    assert last_number_line(one) != last_number_line(two)
    assert last_number_line(two).startswith("Swept 2 node(s)")


def test_is_capped_compares_against_every_filesystem_the_node_reports():
    """imagefs and nodefs can differ; a volume matching either one is the disk."""
    filesystems = {
        "nodefs": {"capacityBytes": NODE_CAPACITY},
        "imagefs": {"capacityBytes": 12345},
    }
    assert disk_health.is_capped({"capacityBytes": NODE_CAPACITY}, filesystems) is False
    assert disk_health.is_capped({"capacityBytes": 12345}, filesystems) is False
    assert disk_health.is_capped({"capacityBytes": 999}, filesystems) is True
    assert disk_health.is_capped({"capacityBytes": None}, filesystems) is None


def test_a_volume_with_no_pvcref_is_not_a_claim():
    """emptyDir and configMap volumes appear in the same list and are not claims."""
    summary = _summary()
    summary["pods"] = [
        {
            "podRef": {"namespace": "agents", "name": "pod-1"},
            "volume": [
                {"name": "scratch", "capacityBytes": 100, "availableBytes": 50},
                {
                    "pvcRef": {"namespace": "agents", "name": "real"},
                    "capacityBytes": 100,
                    "availableBytes": 50,
                },
            ],
        }
    ]
    volumes = disk_health.pvc_volumes(summary)
    assert [v["claim"] for v in volumes] == ["real"]


def _done(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["kubectl"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _claim(namespace, name, tracking=None, phase="Bound", storage="10Gi"):
    metadata = {
        "namespace": namespace,
        "name": name,
        "creationTimestamp": "2026-02-23T12:54:28Z",
    }
    if tracking:
        metadata["annotations"] = {"argocd.argoproj.io/tracking-id": tracking}
    return {
        "metadata": metadata,
        "spec": {"resources": {"requests": {"storage": storage}}},
        "status": {"phase": phase},
    }


def test_read_mounted_claims_reads_pod_volumes_not_workload_templates():
    pods = {
        "items": [
            {
                "metadata": {"namespace": "agents"},
                "spec": {
                    "volumes": [
                        {"name": "tmp", "emptyDir": {}},
                        {
                            "name": "data",
                            "persistentVolumeClaim": {"claimName": "marcus-data"},
                        },
                    ]
                },
            }
        ]
    }

    def runner(argv, **kwargs):
        assert argv[:3] == ["kubectl", "get", "pods"]
        return _done(json.dumps(pods))

    assert disk_health.read_mounted_claims(runner=runner) == {("agents", "marcus-data")}


def test_unmounted_claims_separates_owned_from_orphaned():
    claims = [
        _claim("agents", "marcus-data"),
        _claim("infra", "ollama-models", tracking="sokratesai-deployments:/x"),
        _claim("infra", "nats-js-nats-0"),
    ]
    found = disk_health.unmounted_claims(claims, {("agents", "marcus-data")})
    assert [(c["metadata"]["name"], owner) for c, owner in found] == [
        ("ollama-models", "sokratesai-deployments"),
        ("nats-js-nats-0", None),
    ]


def test_only_an_untracked_unmounted_claim_raises():
    lines = []
    findings = disk_health.report_unmounted(
        [
            (_claim("infra", "ollama-models"), "sokratesai-deployments"),
            (_claim("infra", "nats-js-nats-0"), None),
        ],
        out=lines.append,
    )
    assert findings == 1
    parked = [line for line in lines if "PARKED" in line]
    orphaned = [line for line in lines if "ORPHANED" in line]
    assert len(parked) == 1 and "ollama-models" in parked[0]
    assert len(orphaned) == 1 and "nats-js-nats-0" in orphaned[0]
    # The parked line must say who owns it — that is the whole reason it does
    # not raise, and a reader has to be able to check the claim.
    assert "sokratesai-deployments" in parked[0]


def test_an_unreadable_claim_list_is_never_clean():
    def runner(argv, **kwargs):
        if argv[:3] == ["kubectl", "get", "pvc"]:
            return _done("", returncode=1, stderr="Forbidden")
        return _done(json.dumps({"items": []}))

    lines = []
    status = disk_health.main(["--node", "server1"], runner=runner, out=lines.append)
    assert status == 1
    assert any("CANNOT READ the claim list" in line for line in lines)


GIB = 1024**3


def _breakdown_summary(images=None, ephemeral=(), image_capacity=NODE_CAPACITY):
    """A node whose image store is on the root disk unless `image_capacity` differs."""
    used = NODE_CAPACITY - NODE_AVAILABLE
    node = {
        "nodeName": "server1",
        "fs": {
            "capacityBytes": NODE_CAPACITY,
            "availableBytes": NODE_AVAILABLE,
            "usedBytes": used,
        },
        "runtime": {
            "imageFs": {
                "capacityBytes": image_capacity,
                "availableBytes": NODE_AVAILABLE,
                "usedBytes": used if images is None else images,
            }
        },
    }
    pods = [{"podRef": {"namespace": "agents", "name": "p%d" % i},
             "ephemeral-storage": {"usedBytes": size}}
            for i, size in enumerate(ephemeral)]
    return {"node": node, "pods": pods}


def test_image_store_on_the_root_disk_is_named_as_the_same_disk():
    summary = _breakdown_summary(images=10 * GIB)
    assert disk_health.shares_one_filesystem(disk_health.node_filesystems(summary))


def test_a_separate_image_disk_is_not_read_as_the_same_disk():
    summary = _breakdown_summary(images=10 * GIB, image_capacity=NODE_CAPACITY // 2)
    assert not disk_health.shares_one_filesystem(disk_health.node_filesystems(summary))


def test_a_node_publishing_no_image_store_shares_nothing():
    summary = _summary(imagefs=False)
    assert not disk_health.shares_one_filesystem(disk_health.node_filesystems(summary))


def test_breakdown_splits_used_into_images_ephemeral_and_the_rest():
    summary = _breakdown_summary(images=10 * GIB, ephemeral=(GIB, GIB // 2))
    got = disk_health.usage_breakdown(summary, disk_health.node_filesystems(summary))
    assert got["images"] == 10 * GIB
    assert got["ephemeral"] == GIB + GIB // 2
    assert got["used"] == NODE_CAPACITY - NODE_AVAILABLE
    # The remainder is what the kubelet cannot attribute, and it must be the
    # arithmetic complement rather than a second measurement.
    assert got["rest"] == got["used"] - got["images"] - got["ephemeral"]


def test_breakdown_refuses_when_the_image_store_is_a_different_disk():
    """Its bytes are not part of this disk's used total, so subtracting them lies."""
    summary = _breakdown_summary(images=10 * GIB, image_capacity=NODE_CAPACITY // 2)
    assert disk_health.usage_breakdown(summary, disk_health.node_filesystems(summary)) is None


def test_breakdown_refuses_when_the_node_published_no_used_bytes():
    summary = _breakdown_summary(images=10 * GIB)
    summary["node"]["fs"].pop("usedBytes")
    assert disk_health.usage_breakdown(summary, disk_health.node_filesystems(summary)) is None


def test_report_prints_the_breakdown_and_it_does_not_raise():
    summary = _breakdown_summary(images=10 * GIB, ephemeral=(GIB,))
    filesystems = disk_health.node_filesystems(summary)
    lines = []
    findings = disk_health.report(
        "server1",
        filesystems,
        [],
        out=lines.append,
        breakdown=disk_health.usage_breakdown(summary, filesystems),
    )
    made = [line for line in lines if "MADE OF" in line]
    assert len(made) == 1
    assert "10.0GiB of container images" in made[0]
    assert "1.0GiB of Pod ephemeral storage" in made[0]
    assert findings == 0


def test_the_second_filesystem_line_says_it_is_not_a_second_disk():
    summary = _breakdown_summary(images=10 * GIB)
    lines = []
    disk_health.report("server1", disk_health.node_filesystems(summary), [], out=lines.append)
    imagefs = [line for line in lines if "imagefs" in line]
    assert len(imagefs) == 1
    assert "the same disk as nodefs" in imagefs[0]
    nodefs = [line for line in lines if "nodefs:" in line]
    assert "the same disk" not in nodefs[0]
