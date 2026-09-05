"""The kill order, and the one reading in it that can come back false."""

import pytest

from tools import oom_rank


def pod(name, qos, containers, node="server1", phase="Running", priority=None):
    return {
        "metadata": {"namespace": "ns", "name": name},
        "spec": {"nodeName": node, "priorityClassName": priority,
                 "containers": containers},
        "status": {"phase": phase, "qosClass": qos},
    }


def container(name, request=None, limit=None):
    resources = {}
    if request is not None:
        resources["requests"] = {"memory": request}
    if limit is not None:
        resources["limits"] = {"memory": limit}
    return {"name": name, "resources": resources}


CAPACITY = {"server1": 7746 * oom_rank.MIB}


def test_parse_quantity_reads_binary_and_decimal_suffixes():
    assert oom_rank.parse_quantity("1Gi") == 1024 ** 3
    assert oom_rank.parse_quantity("128Mi") == 128 * 1024 * 1024
    assert oom_rank.parse_quantity("1M") == 1000 ** 2
    assert oom_rank.parse_quantity("512") == 512
    assert oom_rank.parse_quantity(None) is None
    assert oom_rank.parse_quantity("not a number") is None


def test_besteffort_scores_worst_and_guaranteed_scores_best():
    assert oom_rank.oom_score_adj("BestEffort", None, CAPACITY["server1"]) == 1000
    assert oom_rank.oom_score_adj("Guaranteed", 1, CAPACITY["server1"]) == -997


def test_burstable_score_matches_the_kernel_reading_taken_on_this_pod():
    # Measured 2026-09-05 on agora-claude-bridge: request 1Gi of a 7746Mi node,
    # /proc/self/oom_score_adj reads 868. If this arithmetic drifts, the live
    # CONTROL line is the thing that catches it -- this pins the same number so
    # a refactor cannot move it silently.
    assert oom_rank.oom_score_adj(
        "Burstable", 1024 * oom_rank.MIB, 7746 * oom_rank.MIB) == 868


def test_burstable_with_no_request_is_clamped_below_besteffort():
    # The kubelet never lets a Burstable container tie with BestEffort, so a
    # request of nothing is 999 rather than 1000.
    assert oom_rank.oom_score_adj("Burstable", 0, CAPACITY["server1"]) == 999
    assert oom_rank.oom_score_adj("Burstable", None, CAPACITY["server1"]) == 999


def test_burstable_with_a_request_larger_than_the_node_is_clamped_up():
    assert oom_rank.oom_score_adj(
        "Burstable", 99 * CAPACITY["server1"], CAPACITY["server1"]) == 3


def test_unknown_qos_and_unknown_capacity_score_nothing_rather_than_zero():
    assert oom_rank.oom_score_adj("Something", 0, CAPACITY["server1"]) is None
    assert oom_rank.oom_score_adj("Burstable", 0, None) is None


def test_containers_skips_pods_that_are_not_running():
    pods = [pod("gone", "Burstable", [container("c", "64Mi")], phase="Succeeded")]
    assert oom_rank.containers(pods, CAPACITY) == []


def test_containers_scores_every_container_of_a_pod_separately():
    pods = [pod("two", "Burstable",
                [container("a", "64Mi", "128Mi"), container("b", "1Gi", "2Gi")])]
    records = oom_rank.containers(pods, CAPACITY)
    assert [r["score"] for r in records] == [992, 868]


def test_the_node_line_names_where_the_largest_limit_sits_in_the_order():
    pods = [
        pod("hungry", "Burstable", [container("hungry", "1Gi", "4Gi")]),
        pod("tiny", "BestEffort", [container("tiny")]),
    ]
    lines = oom_rank.judge_node("server1", oom_rank.containers(pods, CAPACITY))
    body = "\n".join(lines)
    assert "ns/hungry [hungry]" in body
    assert "largest limit on this node" in body
    assert "it is 2 of 2 in the kill order" in body
    assert "reaches 1 other container(s) before it" in body


def test_a_node_with_no_scorable_container_says_so_rather_than_printing_an_order():
    pods = [pod("odd", "Weird", [container("odd")])]
    lines = oom_rank.judge_node("server1", oom_rank.containers(pods, CAPACITY))
    assert lines == ["CANNOT SCORE  server1: no container on it carries a QoS "
                     "class this knows how to score."]


def test_a_matching_kernel_reading_is_the_only_thing_that_exits_zero():
    pods = [pod("bridge-abc", "Burstable", [container("bridge", "1Gi", "4Gi")])]
    records = oom_rank.containers(pods, CAPACITY)
    lines, code = oom_rank.report(records, "ns/bridge-abc", 868)
    assert code == 0
    assert any(line.startswith("CONTROL  the rule agrees") for line in lines)


def test_a_disagreeing_kernel_reading_raises_and_says_the_scores_are_unreliable():
    pods = [pod("bridge-abc", "Burstable", [container("bridge", "1Gi", "4Gi")])]
    records = oom_rank.containers(pods, CAPACITY)
    lines, code = oom_rank.report(records, "ns/bridge-abc", 42)
    assert code == 2
    assert any("CONTROL DISAGREES" in line for line in lines)


@pytest.mark.parametrize("hostname,own", [("ns/bridge-abc", None), (None, 868)])
def test_an_unverified_control_raises_rather_than_reading_as_confirmed(hostname, own):
    # A report nothing checked must not exit 0: that is the difference between
    # "the rule holds here" and "nobody asked".
    pods = [pod("bridge-abc", "Burstable", [container("bridge", "1Gi", "4Gi")])]
    records = oom_rank.containers(pods, CAPACITY)
    lines, code = oom_rank.report(records, hostname, own)
    assert code == 2
    assert any("CONTROL UNVERIFIED" in line for line in lines)


def test_a_critical_priority_class_is_named_as_unverified_not_scored_silently():
    pods = [pod("traefik", "BestEffort", [container("traefik")],
                priority="system-cluster-critical")]
    records = oom_rank.containers(pods, CAPACITY)
    lines, _ = oom_rank.report(records, "ns/traefik", 1000)
    body = "\n".join(lines)
    assert "score not verified from here" in body
    assert "NOT VERIFIED  1 container(s)" in body


def test_read_cluster_reports_why_rather_than_returning_an_empty_cluster():
    pods, capacities, why = oom_rank.read_cluster(run=lambda args, **kw: None)
    assert pods is None and capacities is None
    assert "kubectl could not read pods and nodes" in why
