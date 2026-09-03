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


def _runner(nodes=("server1",), summaries=None, node_list_rc=0):
    summaries = summaries or {}

    def run(argv, capture_output=False, text=False):
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
    """The threshold is derived from the cluster's behaviour, not invented here."""
    assert disk_health.MARGIN_PCT > 0
    for kind, evicts_at in disk_health.EVICTION_PCT.items():
        assert disk_health.raises_at(kind) == evicts_at + disk_health.MARGIN_PCT


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
