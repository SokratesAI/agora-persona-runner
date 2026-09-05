"""move_fit answers a hypothetical, so every test hands it a state that does not exist."""

import pytest

from tools import move_fit


def _pod(namespace, name, node, requests=None, limits=None, containers=1,
         phase="Running"):
    return {
        "namespace": namespace,
        "name": name,
        "node": node,
        "phase": phase,
        "requests": requests or {},
        "limits": limits or {},
        "container_count": containers,
    }


def _summary(available_mib, swap=None, pods=()):
    node = {"memory": {"availableBytes": available_mib * move_fit.MIB,
                       "workingSetBytes": 0}}
    if swap is not None:
        node["swap"] = {"swapAvailableBytes": swap[0] * move_fit.MIB,
                        "swapUsageBytes": swap[1] * move_fit.MIB}
    return {"node": node, "pods": list(pods)}


def _pod_stat(namespace, name, working_set_mib):
    return {"podRef": {"namespace": namespace, "name": name},
            "memory": {"workingSetBytes": working_set_mib * move_fit.MIB}}


def _lines(pod, target, pods, allocatable, summary, working_set):
    out = []
    findings = move_fit.report(pod, target, pods, allocatable, summary,
                               working_set, out=out.append)
    return findings, "\n".join(out)


def test_placement_refuses_when_the_target_has_no_request_room_left():
    """The scheduler compares requests against allocatable, so this is the hard no."""
    mover = _pod("agents", "big", "server1", requests={"c": "4Gi"})
    sitting = _pod("infra", "incumbent", "server2", requests={"c": "6Gi"})
    findings, text = _lines(mover, "server2", [mover, sitting], 7746,
                            _summary(7000, swap=(2048, 0)), 100)
    assert "WOULD NOT PLACE" in text
    assert findings == 1


def test_placement_passes_on_requests_even_when_the_limit_is_larger_than_the_node():
    """A limit bigger than the whole box does not stop placement — only requests do.

    This is the case the bridge is actually in, and reading the limit here
    would have reported a move as impossible that the scheduler performs.
    """
    mover = _pod("agents", "bridge", "server1",
                 requests={"c": "1Gi"}, limits={"c": "4Gi"})
    findings, text = _lines(mover, "server2", [mover], 7746,
                            _summary(4800), 339)
    assert "ok  PLACEMENT" in text
    assert findings == 0
    assert "COMMITMENT" in text


def test_live_refuses_when_the_pod_is_bigger_than_what_the_target_has_free():
    mover = _pod("agents", "fat", "server1", requests={"c": "100Mi"})
    findings, text = _lines(mover, "server2", [mover], 7746,
                            _summary(500), 900)
    assert "WOULD NOT FIT" in text
    assert findings == 1


def test_an_unmeasured_working_set_is_not_a_pass():
    """A Pod the kubelet has no reading for must not read as using nothing."""
    mover = _pod("agents", "quiet", "server1", requests={"c": "10Mi"})
    findings, text = _lines(mover, "server2", [mover], 7746, _summary(7000), None)
    assert "CANNOT JUDGE LIVE" in text
    assert findings == 1


def test_a_target_with_no_node_memory_block_is_not_a_pass():
    mover = _pod("agents", "quiet", "server1", requests={"c": "10Mi"})
    findings, text = _lines(mover, "server2", [mover], 7746,
                            {"node": {}, "pods": []}, 50)
    assert "CANNOT JUDGE LIVE" in text
    assert findings == 1


def test_commitment_is_reported_and_never_raises():
    """Overcommit past 100% must not turn an otherwise-fine move into a refusal."""
    mover = _pod("agents", "bridge", "server1",
                 requests={"c": "1Gi"}, limits={"c": "4Gi"})
    sitting = _pod("infra", "incumbent", "server2",
                   requests={"c": "100Mi"}, limits={"c": "7Gi"})
    findings, text = _lines(mover, "server2", [mover, sitting], 7746,
                            _summary(4800), 339)
    assert findings == 0
    # 7168Mi already there plus the mover's 4096Mi over a 7746Mi node.
    assert "from 7168Mi (93%) to 11264Mi (145%)" in text


def test_no_swap_on_the_target_is_said_out_loud():
    mover = _pod("agents", "bridge", "server1", requests={"c": "1Gi"})
    _, text = _lines(mover, "server2", [mover], 7746, _summary(4800), 339)
    assert "no swap on server2" in text


def test_swap_on_the_target_is_named_as_a_cushion():
    mover = _pod("agents", "bridge", "server2", requests={"c": "1Gi"})
    _, text = _lines(mover, "server1", [mover], 7746,
                     _summary(4800, swap=(1848, 200)), 339)
    assert "2048Mi of swap on server1" in text


def test_a_pod_already_on_the_target_is_not_counted_against_itself():
    """The mover must be excluded from the incumbents, or it pays its own rent twice."""
    mover = _pod("agents", "bridge", "server1", requests={"c": "4Gi"})
    stale = _pod("agents", "bridge", "server2", requests={"c": "4Gi"})
    findings, text = _lines(mover, "server2", [mover, stale], 7746,
                            _summary(4800), 339)
    assert "ok  PLACEMENT" in text
    assert findings == 0


def test_a_pod_already_on_the_target_node_is_nothing_to_judge():
    mover = _pod("agents", "bridge", "server2", requests={"c": "1Gi"})
    findings, text = _lines(mover, "server2", [mover], 7746, _summary(4800), 339)
    assert "ALREADY THERE" in text
    assert findings == 0


def test_succeeded_pods_do_not_hold_a_reservation():
    """A finished Job Pod would otherwise fence off request room nobody holds."""
    mover = _pod("agents", "mover", "server1", requests={"c": "4Gi"})
    done = _pod("infra", "job", "server2", requests={"c": "6Gi"}, phase="Succeeded")
    findings, text = _lines(mover, "server2", [mover, done], 7746,
                            _summary(4800), 100)
    assert "ok  PLACEMENT" in text
    assert findings == 0


def test_find_pod_refuses_an_ambiguous_prefix():
    pods = [_pod("agents", "bridge-a", "server1"), _pod("agents", "bridge-b", "server1")]
    pod, why = move_fit.find_pod(pods, "agents/bridge")
    assert pod is None
    assert "2 Pods match" in why


def test_find_pod_matches_a_generated_suffix():
    pods = [_pod("agents", "bridge-7647d6b859-49pbw", "server1"),
            _pod("agents", "other", "server1")]
    pod, why = move_fit.find_pod(pods, "agents/bridge")
    assert why is None
    assert pod["name"] == "bridge-7647d6b859-49pbw"


def test_find_pod_refuses_a_name_without_a_namespace():
    pod, why = move_fit.find_pod([], "bridge")
    assert pod is None
    assert "expected <namespace>/<name>" in why


def test_allocatable_is_read_rather_than_capacity():
    """Capacity is the whole machine; only allocatable is placeable."""
    body = {"items": [{"metadata": {"name": "server2"},
                       "status": {"capacity": {"memory": "8000Mi"},
                                  "allocatable": {"memory": "7746Mi"}}}]}

    def runner(args, **kwargs):
        import json
        import types
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

    nodes, why = move_fit.read_node_allocatable(runner=runner)
    assert why is None
    assert nodes == {"server2": 7746.0}


def test_unlimited_containers_on_the_mover_are_named():
    mover = _pod("agents", "loose", "server1", requests={"c": "1Gi"},
                 limits={"c": "1Gi"}, containers=3)
    _, text = _lines(mover, "server2", [mover], 7746, _summary(4800), 100)
    assert "2 of its container(s) declare no limit" in text


@pytest.mark.parametrize("target", ["server3", ""])
def test_main_refuses_an_unknown_node(target, monkeypatch):
    monkeypatch.setattr(move_fit.workload_health, "read_pods",
                        lambda runner=None: ([], None))
    monkeypatch.setattr(move_fit, "read_node_allocatable",
                        lambda runner=None: ({"server1": 7746.0}, None))
    out = []
    assert move_fit.main(["--pod", "agents/x", "--to", target],
                         runner=None, out=out.append) == 1
    assert "the API server lists no such node" in "\n".join(out)
