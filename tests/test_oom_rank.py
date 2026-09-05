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


def test_node_critical_beats_besteffort_because_the_kubelet_checks_it_first():
    """Measured on server1 Cycle 946: local-path-provisioner reads -997.

    The precondition is the whole point — the same Pod without the class
    scores 1000, so this asserts the class did the work rather than the
    QoS class quietly agreeing.
    """
    assert oom_rank.oom_score_adj("BestEffort", None, CAPACITY["server1"]) == 1000
    assert oom_rank.oom_score_adj(
        "BestEffort", None, CAPACITY["server1"],
        "system-node-critical") == oom_rank.GUARANTEED_ADJ


def test_node_critical_overrides_burstable_too_not_only_besteffort():
    """Measured on server1 Cycle 946: metrics-server is Burstable and reads -997.

    Its precondition is the discriminating half — the same Pod without the
    class scores 991, so a rule that only covered BestEffort would fail here.
    """
    request = 70 * oom_rank.MIB
    assert oom_rank.oom_score_adj("Burstable", request, CAPACITY["server1"]) == 991
    assert oom_rank.oom_score_adj(
        "Burstable", request, CAPACITY["server1"],
        "system-node-critical") == oom_rank.GUARANTEED_ADJ


def test_cluster_critical_leaves_a_burstable_score_alone():
    """Measured on server1 Cycle 946: coredns carries it and reads 991."""
    request = 70 * oom_rank.MIB
    assert oom_rank.oom_score_adj(
        "Burstable", request, CAPACITY["server1"],
        "system-cluster-critical") == 991


def test_cluster_critical_buys_nothing_in_the_kill_order():
    """Measured on server1 Cycle 946: traefik carries it and reads 1000."""
    assert oom_rank.oom_score_adj(
        "BestEffort", None, CAPACITY["server1"],
        "system-cluster-critical") == oom_rank.BESTEFFORT_ADJ


def test_node_critical_outranks_a_burstable_pod_in_the_printed_order():
    """The correction that matters to the reader, not just to the number.

    Before this, local-path-provisioner printed first in server1's kill
    order and the bridge pod printed last. It is the other way round.
    """
    pods = [
        pod("local-path", "BestEffort", [container("local-path-provisioner")],
            priority="system-node-critical"),
        pod("bridge", "Burstable", [container("bridge", "1Gi", "4Gi")]),
        pod("argocd", "BestEffort", [container("argocd")]),
    ]
    records = oom_rank.containers(pods, CAPACITY)
    lines, _ = oom_rank.report(records, "ns/bridge", 868)
    order = [line for line in lines if line.strip().startswith(("1.", "2.", "3."))]
    assert "ns/argocd" in order[0]
    assert "ns/bridge" in order[1]
    assert "ns/local-path" in order[2]


def test_a_cluster_critical_pod_is_marked_as_not_protecting_it():
    pods = [pod("traefik", "BestEffort", [container("traefik")],
                priority="system-cluster-critical")]
    records = oom_rank.containers(pods, CAPACITY)
    lines, _ = oom_rank.report(records, "ns/traefik", 1000)
    body = "\n".join(lines)
    assert "which the kernel's kill order does not read" in body
    assert "NOT VERIFIED" not in body


def test_a_priority_class_never_read_is_still_flagged_as_unverified():
    """The two measured classes must not make an unknown third read as clean."""
    pods = [pod("thing", "BestEffort", [container("thing")],
                priority="some-other-class")]
    records = oom_rank.containers(pods, CAPACITY)
    lines, _ = oom_rank.report(records, "ns/thing", 1000)
    body = "\n".join(lines)
    assert "NOT VERIFIED  1 priority class(es)" in body
    assert "some-other-class" in body


def test_read_cluster_reports_why_rather_than_returning_an_empty_cluster():
    pods, capacities, why = oom_rank.read_cluster(run=lambda args, **kw: None)
    assert pods is None and capacities is None
    assert "kubectl could not read pods and nodes" in why
