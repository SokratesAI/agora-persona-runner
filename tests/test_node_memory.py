"""Issue #131's headline number went stale for days and nothing re-read it.

The failures these pin are the two that made that possible: a memory reading
that only ever answers for the node this pod stands on, and a swap total of
zero read as "completely full" when it means there is no swap file at all.
"""

import json
import subprocess

from tools import node_memory


def _summary(node_name, available, working_set=0, pods=(), swap=None, memory=True):
    node = {"nodeName": node_name}
    if memory:
        node["memory"] = {
            "availableBytes": available,
            "workingSetBytes": working_set,
        }
    if swap is not None:
        node["swap"] = swap
    return {
        "node": node,
        "pods": [
            {
                "podRef": {"namespace": ns, "name": name},
                "memory": {"workingSetBytes": ws},
            }
            for ns, name, ws in pods
        ],
    }


def _config(behavior):
    """One node's kubelet configz. `behavior=None` means the key is absent."""
    memory_swap = {} if behavior is None else {"swapBehavior": behavior}
    return {"kubeletconfig": {"failSwapOn": False, "memorySwap": memory_swap}}


def _runner(summaries, nodes=None, configs=None):
    names = list(nodes if nodes is not None else summaries)
    configs = configs or {}

    def run(argv, **kwargs):
        if argv[:3] == ["kubectl", "get", "nodes"]:
            body = {"items": [{"metadata": {"name": n}} for n in names]}
            return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
        raw = argv[-1]
        for name, summary in summaries.items():
            if "/nodes/%s/proxy/configz" % name in raw:
                config = configs.get(name, _config(None))
                if config is None:
                    return subprocess.CompletedProcess(argv, 1, "", "Forbidden")
                return subprocess.CompletedProcess(argv, 0, json.dumps(config), "")
            if "/nodes/%s/proxy" % name in raw:
                if summary is None:
                    return subprocess.CompletedProcess(argv, 1, "", "Forbidden")
                return subprocess.CompletedProcess(argv, 0, json.dumps(summary), "")
        raise AssertionError("unexpected call: %r" % (argv,))

    return run


def _run(summaries, nodes=None, argv=None, configs=None):
    lines = []
    code = node_memory.main(
        argv or [], runner=_runner(summaries, nodes, configs), out=lines.append
    )
    return code, "\n".join(lines)


GiB = 1024**3


def test_a_second_node_is_judged_even_though_this_pod_is_not_on_it():
    """The whole point: /proc in a container is always the local node.

    server1 is healthy and server2 is not. A reader built on this pod's own
    /proc — which is what host_memory_trend and memory_headroom do — reports
    server1 and returns clean, which is the shape of the miss.
    """
    code, out = _run(
        {
            "server1": _summary("server1", 4 * GiB, pods=[("agents", "bridge", GiB // 2)]),
            "server2": _summary("server2", 100 * 1024**2, pods=[("infra", "grafana", GiB)]),
        }
    )
    assert code == 2, out
    assert "ok      server1" in out
    assert "LOW     server2" in out
    assert "infra/grafana" in out


def test_the_line_is_the_largest_pod_and_it_discriminates():
    """Same node, same free memory, two different largest pods, two verdicts.

    A threshold that passed regardless of the pods on the node would be a
    number I invented rather than one the node handed me.
    """
    tight, _ = _run({"n": _summary("n", GiB, pods=[("a", "big", 2 * GiB)])})
    loose, _ = _run({"n": _summary("n", GiB, pods=[("a", "small", GiB // 4)])})
    assert (tight, loose) == (2, 0)


def test_swap_total_of_zero_is_no_swap_not_full_swap():
    """Issue #131 says "its 2GB of swap is completely full"; server2 reports 0.

    Those are opposite facts and the kubelet renders them with the same free
    figure, so the total is what separates them.
    """
    _, none_at_all = _run(
        {"n": _summary("n", GiB, pods=[("a", "p", 1)],
                       swap={"swapAvailableBytes": 0, "swapUsageBytes": 0})}
    )
    _, quite_full = _run(
        {"n": _summary("n", GiB, pods=[("a", "p", 1)],
                       swap={"swapAvailableBytes": 0, "swapUsageBytes": 2 * GiB})}
    )
    assert "none configured" in none_at_all
    assert "none configured" not in quite_full
    assert "2048Mi of 2048Mi used" in quite_full


def test_a_missing_swap_block_is_not_reported_as_no_swap_file():
    _, out = _run({"n": _summary("n", GiB, pods=[("a", "p", 1)])})
    assert "no swap block" in out
    assert "none configured" not in out


def test_a_node_with_no_pods_is_not_a_pass_that_was_guaranteed():
    """Judging an empty node against a zero would make every empty node pass."""
    code, out = _run({"n": _summary("n", 1, pods=[])})
    assert code == 0
    assert "no pod on it to judge against" in out


def test_an_unreadable_node_never_reads_as_clean():
    code, out = _run({"server1": _summary("server1", 4 * GiB,
                                          pods=[("a", "p", 1)]),
                      "server2": None})
    assert code == 1, out
    assert "CANNOT READ server2" in out
    assert "not a clean sweep" in out


def test_a_finding_on_one_node_survives_an_unreadable_other_node():
    """Unreadable wins the exit code, and the finding still has to be printed."""
    code, out = _run({"server1": _summary("server1", 1, pods=[("a", "big", GiB)]),
                      "server2": None})
    assert code == 1
    assert "LOW     server1" in out


def test_a_node_with_no_memory_block_is_unreadable_rather_than_zero():
    code, out = _run({"n": _summary("n", 0, memory=False)})
    assert code == 1
    assert "no node memory block" in out


def test_no_node_list_is_not_a_clean_sweep():
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "Forbidden")

    lines = []
    assert node_memory.main([], runner=run, out=lines.append) == 1
    assert "Nothing was swept" in "\n".join(lines)


def _with_swap(swap_bytes=2 * GiB):
    return _summary("n", GiB, pods=[("a", "p", 1)],
                    swap={"swapAvailableBytes": swap_bytes, "swapUsageBytes": 0})


def test_swap_a_pod_cannot_reach_is_not_reported_as_a_cushion():
    """The claim that cost three cycles: server1 has 2GB of swap, so a pod there
    is cushioned. It is not — both kubelets run swapBehavior NoSwap, so every
    container cgroup is capped at memory.swap.max=0 and a pod hitting its own
    limit dies identically on a node with swap and a node without.

    Same node, same swap file, two kubelet settings, two opposite sentences —
    so this discriminates rather than restating the swap numbers.
    """
    _, no_swap_for_pods = _run({"n": _with_swap()},
                               configs={"n": _config("NoSwap")})
    _, limited = _run({"n": _with_swap()},
                      configs={"n": _config("LimitedSwap")})

    assert "2048Mi used" in no_swap_for_pods and "2048Mi used" in limited
    assert "does NOT cushion a pod" in no_swap_for_pods
    assert "memory.swap.max=0" in no_swap_for_pods
    assert "does NOT cushion a pod" not in limited
    assert "reaches pods" in limited


def test_an_absent_swap_behavior_is_named_as_the_default_not_as_unknown():
    """`memorySwap: {}` is what both nodes actually report, and it means NoSwap.

    Reading the absent key as "cannot tell" would put the node back in the state
    this whole change exists to end.
    """
    _, out = _run({"n": _with_swap()}, configs={"n": _config(None)})
    assert "does NOT cushion a pod" in out
    assert "unset, which the kubelet defaults to NoSwap" in out


def test_an_unreadable_kubelet_config_is_not_reported_as_either_answer():
    """A configz this account cannot read is unmeasured, not NoSwap.

    The precondition is that the swap numbers themselves still print — an
    unreadable configz must not take the node's memory verdict with it.
    """
    code, out = _run({"n": _with_swap()}, configs={"n": None})
    assert "2048Mi used" in out
    assert "unmeasured" in out
    assert "does NOT cushion a pod" not in out
    assert "reaches pods" not in out
    assert code == 0, out


def test_a_node_with_no_swap_file_still_says_what_a_pod_would_get():
    """server2's line. A zero total and a NoSwap kubelet are two separate facts
    and the second is the one that says whether the first matters."""
    _, out = _run(
        {"n": _summary("n", GiB, pods=[("a", "p", 1)],
                       swap={"swapAvailableBytes": 0, "swapUsageBytes": 0})},
        configs={"n": _config("NoSwap")},
    )
    assert "none configured" in out
    assert "does NOT cushion a pod" in out
    assert "no cushion and OOM-kills instead" not in out
