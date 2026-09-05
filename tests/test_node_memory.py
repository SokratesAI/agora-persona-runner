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


def _pod(node, name, limits=(), init=(), phase="Running", ns="agents"):
    """One pod spec. `limits`/`init` are `(container name, limit or None)` pairs.

    `init` entries take an optional third element: `True` marks the sidecar form
    (`restartPolicy: Always`), which runs alongside the app containers.
    """
    def container(entry):
        cname, limit = entry[0], entry[1]
        spec = {"name": cname, "resources": {}}
        if limit is not None:
            spec["resources"]["limits"] = {"memory": limit}
        return spec

    inits = []
    for entry in init:
        spec = container(entry)
        if len(entry) > 2 and entry[2]:
            spec["restartPolicy"] = "Always"
        inits.append(spec)
    return {
        "metadata": {"namespace": ns, "name": name},
        "status": {"phase": phase},
        "spec": {
            "nodeName": node,
            "containers": [container(e) for e in limits],
            "initContainers": inits,
        },
    }


def _runner(summaries, nodes=None, pods=None, allocatable=None, pods_fail=False):
    names = list(nodes if nodes is not None else summaries)
    allocatable = allocatable or {}

    def run(argv, **kwargs):
        if argv[:3] == ["kubectl", "get", "pods"]:
            if pods_fail:
                return subprocess.CompletedProcess(argv, 1, "", "Forbidden")
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"items": list(pods or [])}), ""
            )
        if argv[:3] == ["kubectl", "get", "nodes"]:
            body = {"items": [
                {"metadata": {"name": n},
                 "status": {"allocatable": {"memory": allocatable[n]}}
                 if n in allocatable else {}}
                for n in names
            ]}
            return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
        raw = argv[-1]
        for name, summary in summaries.items():
            if "/nodes/%s/proxy" % name in raw:
                if summary is None:
                    return subprocess.CompletedProcess(argv, 1, "", "Forbidden")
                return subprocess.CompletedProcess(argv, 0, json.dumps(summary), "")
        raise AssertionError("unexpected call: %r" % (argv,))

    return run


def _run(summaries, nodes=None, argv=None, pods=None, allocatable=None,
         pods_fail=False):
    lines = []
    code = node_memory.main(
        argv or [],
        runner=_runner(summaries, nodes, pods, allocatable, pods_fail),
        out=lines.append,
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


def test_accepted_limits_over_the_machine_raise():
    """The question headroom cannot answer: what has this node already promised?

    server2 has 4982Mi free right now and its accepted limits are 97% of
    allocatable. The free-memory half calls that healthy, so the node here is
    given plenty of room and an over-subscribed set of limits.
    """
    code, out = _run(
        {"server2": _summary("server2", 5 * GiB, pods=[("infra", "grafana", GiB // 4)],
                             swap={"swapAvailableBytes": 0, "swapUsageBytes": 0})},
        allocatable={"server2": "8Gi"},
        pods=[_pod("server2", "hog", limits=[("app", "9Gi")])],
    )
    assert code == 2, out
    assert "OVERCOMMITTED" in out
    assert "ok      server2" in out, "the headroom half must still read healthy"


def test_swap_is_capacity_the_kernel_can_hand_out():
    """Two nodes with identical limits, and only the one with no swap raises.

    This is the whole difference between server1 and server2 today, so a
    verdict that ignored swap would report them the same.
    """
    limits = [_pod("server1", "a", limits=[("app", "9Gi")]),
              _pod("server2", "b", limits=[("app", "9Gi")])]
    code, out = _run(
        {"server1": _summary("server1", 4 * GiB, pods=[("agents", "bridge", GiB // 2)],
                             swap={"swapAvailableBytes": 2 * GiB, "swapUsageBytes": 0}),
         "server2": _summary("server2", 4 * GiB, pods=[("agents", "bridge", GiB // 2)],
                             swap={"swapAvailableBytes": 0, "swapUsageBytes": 0})},
        allocatable={"server1": "8Gi", "server2": "8Gi"},
        pods=limits,
    )
    assert code == 2, out
    assert "committed: 9216Mi of limits accepted against 8192Mi allocatable" in out
    assert "OVERCOMMITTED: 9216Mi" in out
    assert out.count("OVERCOMMITTED") == 1, "server1's swap covers the same limits"


def test_an_ordinary_init_container_commits_nothing_concurrent():
    """The 118%-versus-97% bug, pinned.

    Summing every init container additively is what my own first reading did.
    An ordinary init container has exited by the time the app container runs;
    a sidecar (`restartPolicy: Always`) has not.
    """
    code, out = _run(
        {"server1": _summary("server1", 4 * GiB, pods=[("agents", "bridge", GiB // 2)])},
        allocatable={"server1": "8Gi"},
        pods=[_pod("server1", "p", limits=[("app", "1Gi")],
                   init=[("setup", "4Gi"), ("proxy", "1Gi", True)])],
    )
    assert code == 0, out
    assert "committed: 2048Mi of limits accepted" in out, \
        "1Gi app + 1Gi sidecar; the 4Gi init container has exited"


def test_a_container_with_no_limit_is_named_and_does_not_raise():
    """Thirty of these run here today, so raising on them is red forever.

    But a sum that silently omits them reads as a total when it is a floor.
    """
    code, out = _run(
        {"server2": _summary("server2", 5 * GiB, pods=[("infra", "grafana", GiB // 4)])},
        allocatable={"server2": "8Gi"},
        pods=[_pod("server2", "ts-nova-0", limits=[("tailscale", None)], ns="tailscale")],
    )
    assert code == 0, out
    assert "OVERCOMMITTED" not in out
    assert "1 container(s) on this node carry NO memory limit" in out
    assert "tailscale/ts-nova-0:tailscale" in out
    assert "floor and not a total" in out


def test_terminal_pods_hold_no_memory():
    """A finished Job's limits are not a commitment; 15 CronJob pods sit here."""
    code, out = _run(
        {"server1": _summary("server1", 4 * GiB, pods=[("agents", "bridge", GiB // 2)])},
        allocatable={"server1": "8Gi"},
        pods=[_pod("server1", "backup-1", limits=[("app", "9Gi")], phase="Succeeded"),
              _pod("server1", "live", limits=[("app", "1Gi")])],
    )
    assert code == 0, out
    assert "committed: 1024Mi of limits accepted" in out


def test_an_unreadable_pod_list_is_not_a_node_with_nothing_on_it():
    """Silence must not render as 0% committed — that is a clean bill of health
    handed out by a blind instrument."""
    code, out = _run(
        {"server1": _summary("server1", 4 * GiB, pods=[("agents", "bridge", GiB // 2)])},
        allocatable={"server1": "8Gi"},
        pods_fail=True,
    )
    assert code == 1, out
    assert "committed:" not in out
    assert "no node was judged on commitment at all" in out


def test_a_node_with_no_allocatable_reading_is_not_judged():
    """Guessing a capacity would make every node look over-committed."""
    code, out = _run(
        {"server1": _summary("server1", 4 * GiB, pods=[("agents", "bridge", GiB // 2)])},
        pods=[_pod("server1", "p", limits=[("app", "9Gi")])],
    )
    assert code == 0, out
    assert "CANNOT JUDGE commitment on server1" in out
