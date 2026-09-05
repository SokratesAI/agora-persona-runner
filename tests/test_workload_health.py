"""Cycle 615. The runner was OOMKilled and left nobody serving, and none of
the eighteen step-1a checks read the three fields that say so."""
import datetime
import json
import pytest
from unittest import mock

from tools import workload_health as wh

NOW = datetime.datetime(2026, 8, 29, 8, 0, tzinfo=datetime.timezone.utc)


def _pod(namespace="agents", name="p", phase="Running", containers=(), limits=None,
         conditions=(), node="server1", deletion="", requests=None):
    return {
        "namespace": namespace, "name": name, "phase": phase,
        "containers": list(containers), "conditions": list(conditions),
        "limits": dict(limits or {}),
        # What the scheduler reserves, which is a different question from
        # the limit and is what placement is decided on. Omitted by most
        # tests here on purpose: a Pod record with no requests at all is a
        # real state and `available_line` must say so rather than fall back
        # on the limit.
        "requests": dict(requests or {}),
        # Empty unless the test is about a Terminating Pod. It is a
        # deadline, not a start time -- see wh.KILL_MARGIN.
        "deletion": deletion,
        # Defaults to the node NODES declares, because every headroom test
        # below is about the box this pod stands on. Pass another name to
        # put a Pod on a different node, or "" to leave it unscheduled.
        "node": node,
    }


def _container(name="c", ready=True, restarts=0, last=None, state=None):
    out = {"name": name, "ready": ready, "restartCount": restarts,
           "state": state or {"running": {}}}
    if last:
        out["lastState"] = last
    return out


def _terminated(reason, exit_code=1, at="2026-08-29T07:45:00Z"):
    return {"terminated": {"reason": reason, "exitCode": exit_code, "finishedAt": at}}


def _deployment(name="d", available=False, since="2026-08-29T07:55:00Z",
                grace=30, replicas=1, strategy="Recreate", reason="MinimumReplicasUnavailable"):
    return {"namespace": "agents", "name": name, "replicas": replicas,
            "strategy": strategy, "grace": grace, "available": available,
            "since": since, "reason": reason}


def test_oomkill_on_a_container_that_is_down_raises():
    pods = [_pod(containers=[_container(ready=False, restarts=3,
                                        state=_terminated("OOMKilled", 137, "2026-08-29T07:59:00Z"))],
                 limits={"c": "256Mi"})]
    lines, status = wh.report(pods, [], NOW)
    assert status == 2
    body = "\n".join(lines)
    assert "CRASH LOOPING" in body
    assert "OOMKilled" in body and "memory limit 256Mi" in body


def test_a_death_inside_the_window_raises_even_though_the_container_recovered():
    pods = [_pod(containers=[_container(ready=True, restarts=1,
                                        last=_terminated("OOMKilled", 137, "2026-08-29T07:45:00Z"))])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 2
    assert "CONTAINER DIED" in "\n".join(lines)


def test_an_old_death_on_a_recovered_container_is_history_and_does_not_raise():
    pods = [_pod(containers=[_container(ready=True, restarts=1,
                                        last=_terminated("Error", 1, "2026-08-28T10:00:00Z"))])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 0
    body = "\n".join(lines)
    assert "DIED AND RECOVERED" in body
    assert "CONTAINER DIED" not in body


def test_an_old_death_still_raises_when_the_container_is_down_now():
    pods = [_pod(containers=[_container(ready=False, restarts=1,
                                        last=_terminated("Error", 1, "2026-08-20T10:00:00Z"))])]
    _, status = wh.report(pods, [], NOW)
    assert status == 2


def test_a_clean_exit_is_not_a_death():
    pods = [_pod(phase="Succeeded",
                 containers=[_container(ready=False, state=_terminated("Completed", 0))])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 0
    assert "CONTAINER DIED" not in "\n".join(lines)


def test_one_container_that_died_twice_is_one_row_not_two():
    # Died, restarted, died again: both `state` and `lastState` are terminated.
    pods = [_pod(containers=[{
        "name": "c", "ready": False, "restartCount": 2,
        "lastState": _terminated("OOMKilled", 137, "2026-08-29T07:50:00Z"),
        "state": _terminated("Error", 1, "2026-08-29T07:58:00Z"),
    }])]
    fresh, old = wh.deaths(pods, NOW)
    assert len(fresh) + len(old) == 1
    lines, status = wh.report(pods, [], NOW)
    assert status == 2
    body = "\n".join(lines)
    assert "1 container(s) have died repeatedly" in body
    # The newer death is the verdict; the older one is context beside it.
    assert "Error" in body and "previously OOMKilled" in body


def test_imagepullbackoff_raises_even_though_the_pod_is_pending():
    pods = [_pod(phase="Pending", containers=[{
        "name": "c", "ready": False, "restartCount": 0,
        "state": {"waiting": {"reason": "ImagePullBackOff",
                              "message": "Back-off pulling image"}}}])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 2
    assert "ImagePullBackOff" in "\n".join(lines)


def test_containercreating_is_transient_and_stays_quiet():
    pods = [_pod(phase="Pending", containers=[{
        "name": "c", "ready": False, "restartCount": 0,
        "state": {"waiting": {"reason": "ContainerCreating"}}}])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 0
    assert "NOT READY" not in "\n".join(lines)


def test_a_pod_that_cannot_be_scheduled_raises_with_no_container_statuses():
    pods = [_pod(phase="Pending", containers=[], conditions=[
        {"type": "PodScheduled", "status": "False", "reason": "Unschedulable",
         "message": "0/1 nodes are available: 1 Insufficient memory."}])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 2
    body = "\n".join(lines)
    assert "Unschedulable" in body and "Insufficient memory" in body


def test_a_transition_time_in_the_future_is_not_read_as_a_rollout():
    deps = [_deployment(grace=2880, since="2026-08-29T09:00:00Z")]
    lines, status = wh.report([_pod()], deps, NOW)
    assert status == 2
    assert "NOBODY SERVING" in "\n".join(lines)


def test_a_crashlooping_container_is_not_ready_and_raises():
    pods = [_pod(containers=[_container(
        ready=False, restarts=414,
        state={"waiting": {"reason": "CrashLoopBackOff", "message": "back-off 5m"}})])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 2
    body = "\n".join(lines)
    assert "NOT READY" in body and "CrashLoopBackOff" in body


def test_unavailable_inside_the_drain_budget_is_a_rollout_and_does_not_raise():
    # The runner's real shape: Recreate, 2880s grace, five minutes into a roll.
    deps = [_deployment(name="agora-persona-runner", grace=2880,
                        since="2026-08-29T07:55:00Z")]
    lines, status = wh.report([_pod()], deps, NOW)
    assert status == 0
    body = "\n".join(lines)
    assert "ROLLING" in body and "NOBODY SERVING" not in body


def test_unavailable_past_the_drain_budget_raises():
    deps = [_deployment(name="agora-persona-runner", grace=2880,
                        since="2026-08-29T07:00:00Z")]
    lines, status = wh.report([_pod()], deps, NOW)
    assert status == 2
    body = "\n".join(lines)
    assert "NOBODY SERVING" in body and "agora-persona-runner" in body


def test_the_budget_comes_off_the_object_not_a_constant():
    # Same 12 minutes down. A 30s grace is past budget; a 2880s grace is not.
    since = "2026-08-29T07:48:00Z"
    _, tight = wh.report([_pod()], [_deployment(grace=30, since=since)], NOW)
    _, loose = wh.report([_pod()], [_deployment(grace=2880, since=since)], NOW)
    assert tight == 2
    assert loose == 0


def test_a_workload_parked_at_zero_replicas_is_not_unavailable():
    # On the live cluster a settled scaled-to-0 Deployment reports
    # Available=True, so `available is not False` already excludes it. The
    # case the replicas guard actually exists for is the transition: a
    # Deployment scaled to 0 that has not yet flipped its condition back,
    # which would otherwise read as an outage that can never end.
    deps = [_deployment(name="whatsapp-bridge", replicas=0, available=False,
                        since="2026-08-01T00:00:00Z", grace=30)]
    lines, status = wh.report([_pod()], deps, NOW)
    assert status == 0
    assert "whatsapp-bridge" not in "\n".join(lines)


def test_a_settled_zero_replica_workload_reports_available_and_is_ignored():
    deps = [_deployment(name="ollama", replicas=0, available=True, since="", grace=30)]
    lines, status = wh.report([_pod()], deps, NOW)
    assert status == 0
    assert "ollama" not in "\n".join(lines)


def test_a_single_death_is_not_called_a_crash_loop():
    pods = [_pod(containers=[_container(ready=True, restarts=1,
                                        last=_terminated("OOMKilled", 137, "2026-08-29T07:45:00Z"))])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 2
    body = "\n".join(lines)
    assert "CONTAINER DIED" in body and "CRASH LOOPING" not in body


def test_timestamps_a_person_reads_are_oslo_not_utc():
    # 07:45 UTC on 2026-08-29 is 09:45 in Oslo (CEST, UTC+2).
    pods = [_pod(containers=[_container(ready=True, restarts=1,
                                        last=_terminated("OOMKilled", 137, "2026-08-29T07:45:00Z"))])]
    lines, _ = wh.report(pods, [], NOW)
    body = "\n".join(lines)
    assert "09:45 Oslo" in body
    assert "07:45" not in body


def test_unavailable_with_no_transition_time_is_the_loud_case():
    deps = [_deployment(since="")]
    _, status = wh.report([_pod()], deps, NOW)
    assert status == 2


def test_a_healed_restart_is_context_and_does_not_raise():
    pods = [_pod(containers=[_container(ready=True, restarts=17)])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 0
    assert "RESTARTED AND HEALED" in "\n".join(lines)


def test_no_pods_is_no_instrument_not_a_clean_bill(capsys):
    def runner(args, **kwargs):
        class P:
            returncode = 0
            stdout = json.dumps({"items": []})
            stderr = ""
        return P()
    assert wh.main([], runner=runner) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


def test_kubectl_failure_never_reads_as_clean(capsys):
    def runner(args, **kwargs):
        class P:
            returncode = 1
            stdout = ""
            stderr = "Error from server (Forbidden)"
        return P()
    assert wh.main([], runner=runner) == 1
    assert "Forbidden" in capsys.readouterr().out


def test_read_deployments_takes_the_grace_off_the_pod_template():
    body = {"items": [{
        "metadata": {"namespace": "agents", "name": "agora-persona-runner"},
        "spec": {"replicas": 1, "strategy": {"type": "Recreate"},
                 "template": {"spec": {"terminationGracePeriodSeconds": 2880}}},
        "status": {"conditions": [
            {"type": "Available", "status": "False",
             "reason": "MinimumReplicasUnavailable",
             "lastTransitionTime": "2026-08-29T05:02:00Z"}]},
    }]}

    def runner(args, **kwargs):
        class P:
            returncode = 0
            stdout = json.dumps(body)
            stderr = ""
        return P()

    deps, why = wh.read_deployments(runner)
    assert why is None
    assert deps[0]["grace"] == 2880
    assert deps[0]["available"] is False
    assert deps[0]["strategy"] == "Recreate"


# --- Cycle 616: does this host still have room to start what it runs? -----
#
# server1 was at 487Mi of 7746Mi available with swap 0.0% free while the
# control plane fell over, and none of the checks read either number.
#
# MEMINFO below is a real /proc/meminfo read off that host, but it is the
# *baseline* one: MemAvailable 451752 kB is 441Mi and SwapFree is the
# healthy value, so nothing here is the incident until a test says so.
# Each test overrides the one field it is about -- SwapFree=224 for the
# exhausted case -- because a fixture that is already red cannot show that
# a verdict fired for the reason it names.

MEMINFO = """MemTotal:        7931600 kB
MemFree:          178804 kB
MemAvailable:     451752 kB
Cached:           402464 kB
SwapTotal:       2097148 kB
SwapFree:        1500000 kB
"""

NODES = {"server1": 7931600 / 1024}


def _stats(available_mib=4096, swap_total_mib=2048, swap_free_mib=1024, why=None):
    """A stand-in for `read_node_memory_stats`, so a test needs no cluster."""
    def read(node):
        if why:
            return None, why
        return ({"available_mib": available_mib,
                 "swap_total_mib": swap_total_mib,
                 "swap_free_mib": swap_free_mib}, None)
    return read


def _meminfo(**overrides):
    fields = {}
    for line in MEMINFO.splitlines():
        name, _, rest = line.partition(":")
        fields[name.strip()] = float(rest.split()[0])
    fields.update(overrides)
    return fields


def _pod_with_limit(mib, name="big", request_mib=None):
    return _pod(name=name, limits={"c": f"{mib}Mi"},
                requests=({"c": f"{request_mib}Mi"} if request_mib else None))


def _budget_pod(name, limits=None, containers=1, phase="Running", node="server1",
                requests=None):
    pod = _pod(name=name, phase=phase, limits=limits or {}, node=node,
               requests=requests)
    pod["container_count"] = containers
    return pod


def test_declared_ceiling_sums_the_limits_of_running_pods_only():
    total, limited, unlimited = wh.declared_ceiling([
        _budget_pod("a", {"c": "512Mi"}),
        _budget_pod("b", {"c": "1Gi"}),
        _budget_pod("finished", {"c": "4Gi"}, phase="Succeeded"),
    ])
    assert total == 1536
    assert limited == 2
    assert unlimited == 0


def test_declared_ceiling_counts_containers_that_set_no_limit():
    total, limited, unlimited = wh.declared_ceiling([
        _budget_pod("two", {"c": "256Mi"}, containers=2),
        _budget_pod("none", containers=3),
    ])
    assert total == 256
    assert limited == 1
    assert unlimited == 4


def test_the_sum_of_limits_over_the_box_raises_while_every_single_one_fits():
    """The case largest_limit cannot see, and the reason this check exists.

    Eight 1Gi containers on a 7746Mi node: the biggest is 1024Mi against
    1889Mi available, so the per-container question passes. The node is
    still over its own budget by 446Mi.
    """
    pods = [_budget_pod(f"p{i}", {"c": "1Gi"}) for i in range(8)]
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), NODES, pods)
    assert judged is True
    assert actionable is True
    text = " ".join(lines)
    assert "NODE OUT OF MEMORY" not in text, "the per-container check must pass here"
    assert "MEMORY OVERCOMMITTED" in text
    assert "8192Mi (106%)" in text
    assert "8 container(s)" in text


def test_limits_inside_the_box_are_reported_without_raising():
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), NODES,
        [_budget_pod("p", {"c": "1Gi"})])
    assert actionable is False
    assert any(line.startswith("BUDGET ") and "1024Mi of 7746Mi (13%)" in line
               for line in lines)


def test_the_sum_says_it_is_a_floor_when_some_container_declares_nothing():
    lines, _, _ = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), NODES,
        [_budget_pod("p", {"c": "1Gi"}, containers=3)])
    text = " ".join(lines)
    assert "2 more running container(s) set no limit" in text
    assert "not the most" in text


def test_the_sum_says_it_is_the_whole_of_it_when_every_container_declares_one():
    lines, _, _ = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), NODES,
        [_budget_pod("p", {"c": "1Gi"}, containers=1)])
    assert "Every running container sets one" in " ".join(lines)


def test_mib_reads_every_suffix_kubernetes_writes():
    assert wh._mib("1Gi") == 1024
    assert wh._mib("512Mi") == 512
    assert wh._mib("1048576Ki") == 1024
    assert wh._mib("1048576") == 1  # bare bytes
    assert wh._mib(None) is None
    assert wh._mib("not-a-size") is None


def test_read_meminfo_parses_the_file(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO)
    fields, why = wh.read_meminfo(str(path))
    assert why is None
    assert fields["MemAvailable"] == 451752
    assert fields["SwapTotal"] == 2097148


def test_read_meminfo_without_memtotal_is_unreadable(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("Cached: 1 kB\n")
    fields, why = wh.read_meminfo(str(path))
    assert fields is None
    assert "MemTotal" in why


def test_a_meminfo_that_matches_no_node_is_not_judged():
    # If a runtime ever fakes /proc/meminfo per container, this reads a
    # container's memory and would call it the host's.
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemTotal=2097152), NODES, [_pod_with_limit(256)])
    assert judged is False
    assert actionable is False
    assert "CANNOT ATTRIBUTE MEMORY" in lines[0]


def test_available_below_the_largest_configured_request_raises():
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemAvailable=451752), NODES,
        [_pod_with_limit(2048, request_mib=2048)])
    assert judged is True
    assert actionable is True
    assert any("NODE OUT OF MEMORY" in line for line in lines)
    assert any("largest container memory request" in line for line in lines)


def test_a_limit_over_the_headroom_does_not_raise_when_the_request_fits():
    """The live server1 case on 2026-09-05, and the reason this rule changed.

    3663Mi free, `agora-claude-bridge` limit 4Gi, request 1Gi. The old rule
    compared the limit and shouted that the host could not fit the workload
    on its next roll. The scheduler never reads the limit, so the Pod would
    have been admitted -- and that 4Gi was raised deliberately, on the owner's
    own ask, so the alarm could only ever be silenced by undoing a decision.
    """
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemAvailable=3751000), NODES,
        [_pod_with_limit(4096, request_mib=1024)])
    text = " ".join(lines)
    assert judged is True
    assert actionable is False
    assert "NODE OUT OF MEMORY" not in text
    # The overhang is still stated -- it says how a runaway would land.
    assert "The largest limit configured here is 4096Mi" in text
    assert "host OOM killer" in text


def test_no_request_anywhere_is_not_judged_rather_than_judged_on_the_limit():
    """The failure this whole change exists to stop, asserted directly."""
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemAvailable=451752), NODES, [_pod_with_limit(2048)])
    text = " ".join(lines)
    assert judged is True
    assert actionable is False
    assert "NODE OUT OF MEMORY" not in text
    assert "Not judged for scheduling" in text


def test_available_above_the_largest_configured_request_is_quiet():
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=451752), NODES,
        [_pod_with_limit(64, request_mib=64)])
    assert actionable is False
    assert any(line.startswith("MEMORY ") for line in lines)


def test_the_threshold_is_the_biggest_request_not_the_first_one():
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=451752), NODES,
        [_pod_with_limit(64, name="small", request_mib=64),
         _pod_with_limit(2048, name="big", request_mib=2048)])
    assert actionable is True
    assert "agents/big [c]" in " ".join(lines)


def test_no_container_sets_a_limit_so_there_is_nothing_to_judge_against():
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemAvailable=1), NODES, [_pod()])
    assert judged is True
    assert actionable is False
    assert "no configured size" in " ".join(lines).lower() or "No container here sets" in " ".join(lines)


def test_swap_nearly_gone_raises_on_its_own():
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(SwapFree=224), NODES, [_pod_with_limit(64)])
    assert actionable is True
    assert any("SWAP EXHAUSTED" in line for line in lines)


def test_swap_with_room_left_does_not_raise():
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(SwapFree=1500000), NODES, [_pod_with_limit(64)])
    assert actionable is False
    assert any(line.startswith("SWAP ") for line in lines)


def test_a_host_with_no_swap_at_all_is_not_a_finding():
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(SwapTotal=0, SwapFree=0), NODES, [_pod_with_limit(64)])
    assert actionable is False
    assert "none configured" in " ".join(lines)


def test_missing_memavailable_is_not_judged():
    fields = _meminfo()
    del fields["MemAvailable"]
    _, actionable, judged = wh.memory_headroom(fields, NODES, [_pod_with_limit(64)])
    assert judged is False
    assert actionable is False


def test_report_without_headroom_is_unchanged():
    # 21 call sites above pass report() three positional arguments. This
    # pins that adding a fourth kept them working; it deliberately says
    # nothing about the memory verdict, which the tests above cover.
    lines, status = wh.report([_pod()], [], NOW)
    assert status == 0
    assert not any("MEMORY" in line for line in lines)


def test_a_missing_memavailable_says_so_rather_than_blaming_attribution():
    # A host we matched, missing one field, is not the same failure as a
    # reading that belongs to some other machine, and it must not borrow
    # that sentence.
    fields = _meminfo()
    del fields["MemAvailable"]
    lines, _, judged = wh.memory_headroom(fields, NODES, [_pod_with_limit(64)])
    assert judged is False
    assert "CANNOT ATTRIBUTE MEMORY" not in " ".join(lines)
    assert "MemAvailable" in " ".join(lines)


def test_a_meminfo_that_is_not_text_is_unreadable_not_a_crash(tmp_path):
    path = tmp_path / "meminfo"
    path.write_bytes(b"MemTotal: 1 kB\n\xff\xfe not utf-8\n")
    fields, why = wh.read_meminfo(str(path))
    assert fields is None
    assert "could not read" in why


def test_an_unattributable_reading_makes_main_exit_1(capsys, monkeypatch):
    monkeypatch.setattr(wh, "read_meminfo", lambda *a, **k: (_meminfo(MemTotal=2097152), None))

    def runner(args, **kwargs):
        if "pods" in args:
            body = {"items": [{"metadata": {"namespace": "agents", "name": "p"},
                               "spec": {"containers": []}, "status": {"phase": "Running"}}]}
        elif "nodes" in args:
            body = {"items": [{"metadata": {"name": "server1"},
                               "status": {"capacity": {"memory": "7931600Ki"}}}]}
        else:
            body = {"items": []}
        return type("P", (), {"returncode": 0, "stdout": json.dumps(body), "stderr": ""})()

    assert wh.main([], runner=runner, now=NOW) == 1
    assert "CANNOT ATTRIBUTE MEMORY" in capsys.readouterr().out


def test_an_unreadable_meminfo_never_reads_as_clean(capsys, monkeypatch):
    monkeypatch.setattr(wh, "read_meminfo", lambda *a, **k: (None, "could not read /proc/meminfo"))

    def runner(args, **kwargs):
        body = {"items": [{"metadata": {"namespace": "agents", "name": "p"},
                           "spec": {"containers": []}, "status": {"phase": "Running"}}]}
        return type("P", (), {"returncode": 0, "stdout": json.dumps(body), "stderr": ""})()

    assert wh.main([], runner=runner, now=NOW) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


# --- attribution: where the host's memory went -------------------------------

MEMINFO_FULL_HOST = {
    "MemTotal": 7931600.0, "MemAvailable": 246232.0,
    "AnonPages": 7006024.0, "Cached": 180084.0, "Buffers": 1480.0,
    "SUnreclaim": 188596.0, "KernelStack": 26800.0, "PageTables": 63716.0,
    "SwapTotal": 2097148.0, "SwapFree": 132.0,
}


def _top(rows, returncode=0, stderr=""):
    def runner(args, **kwargs):
        assert args[:3] == ["kubectl", "top", "pods"]
        return type("P", (), {"returncode": returncode,
                              "stdout": "\n".join(rows), "stderr": stderr})()
    return runner


def test_pod_working_set_sums_the_memory_column():
    rows = ["agents  a  10m  252Mi", "infra  b  1m  192Mi"]
    (total, counted), why = wh.read_pod_working_set(runner=_top(rows))
    assert why is None
    assert counted == 2
    assert total == pytest.approx(444.0)


def test_no_pod_rows_is_no_instrument_not_zero():
    total, why = wh.read_pod_working_set(runner=_top([]))
    assert total is None
    assert "no Pod rows" in why


def test_attribution_names_the_memory_outside_every_pod():
    lines = wh.attribution(MEMINFO_FULL_HOST, (2086.0, 42))
    joined = "\n".join(lines)
    # 7006024kB anon = 6841.8Mi; minus 2086Mi of Pods leaves ~4756Mi.
    assert "6842Mi anonymous" in joined
    assert "~4756Mi of anonymous memory outside every Pod cgroup" in joined
    assert "42 Pod(s) account for 2086Mi" in joined
    assert "lower bound" in joined


def test_attribution_says_so_when_the_pod_split_is_unreadable():
    lines = wh.attribution(MEMINFO_FULL_HOST, None)
    joined = "\n".join(lines)
    assert "6842Mi anonymous" in joined
    assert "unmeasured" in joined
    assert "outside every Pod cgroup" not in joined


def test_attribution_never_raises_on_its_own(capsys):
    """A host using memory is not a finding, and there is no PR that fixes it."""
    meminfo = dict(MEMINFO_FULL_HOST, MemAvailable=6000000.0, AnonPages=900000.0,
                   Cached=800000.0, SwapFree=2000000.0)

    def runner(args, **kwargs):
        if args[:3] == ["kubectl", "top", "pods"]:
            return type("P", (), {"returncode": 0, "stdout": "a b 1m 100Mi", "stderr": ""})()
        if "pods" in args:
            body = {"items": [{"metadata": {"namespace": "agents", "name": "p"},
                               "spec": {"containers": []}, "status": {"phase": "Running"}}]}
        elif "nodes" in args:
            body = {"items": [{"metadata": {"name": "server1"},
                               "status": {"capacity": {"memory": "7931600Ki"}}}]}
        else:
            body = {"items": []}
        return type("P", (), {"returncode": 0, "stdout": json.dumps(body), "stderr": ""})()

    with mock.patch.object(wh, "read_meminfo", lambda *a, **k: (meminfo, None)):
        assert wh.main([], runner=runner, now=NOW) == 0
    assert "outside every Pod cgroup" in capsys.readouterr().out


def test_a_reading_off_the_wrong_machine_is_never_broken_down(capsys):
    """If meminfo is a container's view, a breakdown of it is worse than none."""
    def runner(args, **kwargs):
        assert args[:3] != ["kubectl", "top", "pods"], "must not attribute an unattributed reading"
        assert args[:3] != ["kubectl", "get", "--raw"], "must not name cgroups on the wrong machine"
        if "pods" in args:
            body = {"items": [{"metadata": {"namespace": "agents", "name": "p"},
                               "spec": {"containers": []}, "status": {"phase": "Running"}}]}
        elif "nodes" in args:
            body = {"items": [{"metadata": {"name": "server1"},
                               "status": {"capacity": {"memory": "999Ki"}}}]}
        else:
            body = {"items": []}
        return type("P", (), {"returncode": 0, "stdout": json.dumps(body), "stderr": ""})()

    with mock.patch.object(wh, "read_meminfo", lambda *a, **k: (MEMINFO_FULL_HOST, None)):
        assert wh.main([], runner=runner, now=NOW) == 1
    out = capsys.readouterr().out
    assert "CANNOT ATTRIBUTE MEMORY" in out
    assert "MEMORY WENT" not in out


def test_pods_above_the_anonymous_total_never_print_a_negative():
    """The two numbers come from different instruments and can cross."""
    lines = wh.attribution(MEMINFO_FULL_HOST, (9000.0, 42))
    joined = "\n".join(lines)
    assert "-" not in joined.split("account for")[1]
    assert "no measurable memory outside its Pod cgroups" in joined


def test_read_pods_counts_every_container_not_only_the_limited_ones():
    """`limits` records only the containers that set one, so the count of
    containers is the other half of saying a limit is missing."""
    body = {"items": [{
        "metadata": {"namespace": "agents", "name": "two-sided"},
        "spec": {"containers": [
            {"name": "app", "resources": {"limits": {"memory": "512Mi"}}},
            {"name": "sidecar", "resources": {}},
        ]},
        "status": {"phase": "Running"},
    }]}

    def runner(args, **kwargs):
        return mock.Mock(returncode=0, stdout=json.dumps(body), stderr="")

    pods, why = wh.read_pods(runner=runner)
    assert why is None
    assert pods[0]["limits"] == {"app": "512Mi"}
    assert pods[0]["container_count"] == 2
    assert wh.declared_ceiling(pods) == (512, 1, 1)


# --- Cycle 713, issue #131: naming the memory outside every Pod cgroup. ---
#
# `attribution` above could say ~1,900Mi sat outside every Pod and could not
# say whose it was; `nodes/proxy` was granted on 2026-08-31 and cAdvisor
# carries the name. Every fixture below is the real endpoint's shape, trimmed:
# one series per cgroup, Pod lines carrying a non-empty `pod` label.

CADVISOR = "\n".join([
    "# HELP container_memory_working_set_bytes Current working set in bytes.",
    "# TYPE container_memory_working_set_bytes gauge",
    'container_memory_working_set_bytes{container="",id="/",image="",name="",namespace="",pod=""} 5.50383616e+09 1788176238682',
    'container_memory_working_set_bytes{container="",id="/kubepods.slice",image="",name="",namespace="",pod=""} 2.937020416e+09 1788176238732',
    'container_memory_working_set_bytes{container="",id="/kubepods.slice/kubepods-besteffort.slice",image="",name="",namespace="",pod=""} 6.97311232e+08 1788176224254',
    'container_memory_working_set_bytes{container="",id="/kubepods.slice/kubepods-burstable.slice",image="",name="",namespace="",pod=""} 2.243534848e+09 1788176240729',
    'container_memory_working_set_bytes{container="",id="/system.slice/k3s.service",image="",name="",namespace="",pod=""} 2.437492736e+09 1788176240125',
    'container_memory_working_set_bytes{container="agora",id="/kubepods.slice/pod-abc/xyz",image="i",name="n",namespace="agents",pod="agora-1"} 1.048576e+08 1788176240125',
    "container_memory_rss{container=\"\",id=\"/\",image=\"\",name=\"\",namespace=\"\",pod=\"\"} 4.0e+09 1788176238682",
    # Cycle 716, issue #131: the swap and rss series ride the same fetch.
    'container_memory_rss{container="",id="/system.slice/k3s.service",image="",name="",namespace="",pod=""} 2.202009600e+09 1788176240125',
    # Two of the dozen other container_memory_* series the real endpoint
    # carries, so the reader is asked to ignore something rather than only
    # being handed what it wants.
    'container_memory_cache{container="",id="/",image="",name="",namespace="",pod=""} 1.979711488e+09 1788176238682',
    'container_memory_usage_bytes{container="",id="/",image="",name="",namespace="",pod=""} 6.81836544e+09 1788176238682',
    'container_memory_swap{container="",id="/",image="",name="",namespace="",pod=""} 1.7433624576e+09 1788176238682',
    'container_memory_swap{container="",id="/kubepods.slice",image="",name="",namespace="",pod=""} 1.048576e+06 1788176238732',
    'container_memory_swap{container="",id="/system.slice/k3s.service",image="",name="",namespace="",pod=""} 1.77209344e+08 1788176240125',
    'container_memory_swap{container="agora",id="/kubepods.slice/pod-abc/xyz",image="i",name="n",namespace="agents",pod="agora-1"} 9.0e+08 1788176240125',
])


def _cadvisor(body=CADVISOR, returncode=0, stderr="", seen=None):
    def runner(args, **kwargs):
        assert args[:3] == ["kubectl", "get", "--raw"]
        if seen is not None:
            seen.append(args[3])
        return type("P", (), {"returncode": returncode,
                              "stdout": body, "stderr": stderr})()
    return runner


def test_read_node_cgroups_keeps_the_machine_slices_and_drops_the_pod_lines():
    cgroups, why = wh.read_node_cgroups("server1", runner=_cadvisor())
    assert why is None
    # The Pod line is 100Mi and would land under an id of its own if kept;
    # `read_pod_working_set` already counts it, so counting it here doubles it.
    assert set(cgroups) == {"/", "/kubepods.slice",
                            "/kubepods.slice/kubepods-besteffort.slice",
                            "/kubepods.slice/kubepods-burstable.slice",
                            "/system.slice/k3s.service"}
    assert cgroups["/system.slice/k3s.service"] == pytest.approx(2324.5, abs=0.5)
    assert cgroups["/"] == pytest.approx(5248.7, abs=0.5)


def test_read_node_cgroups_asks_the_named_host_for_the_cadvisor_endpoint():
    seen = []
    wh.read_node_cgroups("server9", runner=_cadvisor(seen=seen))
    assert seen == ["/api/v1/nodes/server9/proxy/metrics/cadvisor"]


def test_read_node_cgroups_reports_a_forbidden_read_rather_than_an_empty_one():
    cgroups, why = wh.read_node_cgroups(
        "server1", runner=_cadvisor(returncode=1, stderr='nodes "server1" is forbidden'))
    assert cgroups is None
    assert "forbidden" in why


def test_read_node_cgroups_treats_a_200_with_no_series_as_no_instrument():
    cgroups, why = wh.read_node_cgroups("server1", runner=_cadvisor(body="# nothing\n"))
    assert cgroups is None
    assert "no container_memory_working_set_bytes series" in why


def test_host_cgroup_shares_counts_kubepods_once_not_once_per_child():
    cgroups, _ = wh.read_node_cgroups("server1", runner=_cadvisor())
    root, kubepods, tops, unnamed = wh.host_cgroup_shares(cgroups)
    # Parent 2801Mi; besteffort + burstable are another 2804Mi of the same Pods.
    assert kubepods == pytest.approx(2801.0, abs=1.0)
    assert tops == [("/system.slice/k3s.service", pytest.approx(2324.5, abs=0.5))]
    assert root == pytest.approx(5248.7, abs=0.5)
    assert unnamed == pytest.approx(root - kubepods - 2324.5, abs=1.0)


def test_host_cgroup_shares_drops_a_slice_nested_inside_another_it_names():
    cgroups = {"/": 5000.0, "/kubepods.slice": 2000.0,
               "/system.slice": 2400.0, "/system.slice/k3s.service": 2300.0}
    _, _, tops, unnamed = wh.host_cgroup_shares(cgroups)
    # Naming both would charge k3s twice and leave a negative remainder.
    assert tops == [("/system.slice", 2400.0)]
    assert unnamed == pytest.approx(600.0)


def test_attribution_names_the_cgroup_holding_the_memory_outside_every_pod():
    cgroups, _ = wh.read_node_cgroups("server1", runner=_cadvisor())
    joined = "\n".join(wh.attribution(MEMINFO_FULL_HOST, (2086.0, 42), cgroups, None))
    assert "~4756Mi of anonymous memory outside every Pod cgroup" in joined
    assert "/system.slice/k3s.service 2325Mi" in joined
    assert "leaving 123Mi in no cgroup it breaks out" in joined
    # The Pod line must not appear as a cgroup of its own.
    assert "/kubepods.slice/pod-abc" not in joined


def test_attribution_says_the_owner_is_unread_rather_than_dropping_the_line():
    joined = "\n".join(wh.attribution(
        MEMINFO_FULL_HOST, (2086.0, 42), None, "nodes/proxy is forbidden"))
    assert "~4756Mi of anonymous memory outside every Pod cgroup" in joined
    assert "CANNOT NAME THAT SHARE" in joined
    assert "nodes/proxy is forbidden" in joined


def test_attribution_still_never_raises_when_it_names_a_cgroup():
    cgroups, _ = wh.read_node_cgroups("server1", runner=_cadvisor())
    lines = wh.attribution(MEMINFO_FULL_HOST, (2086.0, 42), cgroups, None)
    assert not any(line.lstrip().startswith("NODE OUT OF") for line in lines)


def test_matching_host_is_the_node_whose_capacity_equals_this_meminfo():
    assert wh.matching_host(MEMINFO_FULL_HOST,
                            {"server1": 7746.0, "other": 16000.0}) == "server1"
    assert wh.matching_host(MEMINFO_FULL_HOST, {"other": 16000.0}) is None


def test_main_asks_cadvisor_about_the_node_that_matched_this_meminfo():
    """The URL carries a node name, so a wrong one reads another box's cgroups."""
    meminfo = dict(MEMINFO_FULL_HOST, MemAvailable=6000000.0, AnonPages=900000.0,
                   Cached=800000.0, SwapFree=2000000.0)
    raw = []

    def runner(args, **kwargs):
        if args[:3] == ["kubectl", "top", "pods"]:
            return type("P", (), {"returncode": 0, "stdout": "a b 1m 100Mi", "stderr": ""})()
        if args[:3] == ["kubectl", "get", "--raw"]:
            raw.append(args[3])
            if args[3].endswith("/stats/summary"):
                summary = {"node": {"memory": {"availableBytes": 1 << 30},
                                    "swap": {"swapAvailableBytes": 0,
                                             "swapUsageBytes": 0}}}
                return type("P", (), {"returncode": 0,
                                      "stdout": json.dumps(summary), "stderr": ""})()
            return type("P", (), {"returncode": 0, "stdout": CADVISOR, "stderr": ""})()
        if "pods" in args:
            body = {"items": [{"metadata": {"namespace": "agents", "name": "p"},
                               "spec": {"containers": []}, "status": {"phase": "Running"}}]}
        elif "nodes" in args:
            body = {"items": [
                {"metadata": {"name": "elsewhere"},
                 "status": {"capacity": {"memory": "16000000Ki"}}},
                {"metadata": {"name": "server1"},
                 "status": {"capacity": {"memory": "7931600Ki"}}}]}
        else:
            body = {"items": []}
        return type("P", (), {"returncode": 0, "stdout": json.dumps(body), "stderr": ""})()

    with mock.patch.object(wh, "read_meminfo", lambda *a, **k: (meminfo, None)):
        assert wh.main([], runner=runner, now=NOW) == 0
    cadvisor = [r for r in raw if r.endswith("/metrics/cadvisor")]
    assert cadvisor == ["/api/v1/nodes/server1/proxy/metrics/cadvisor"]
    # The other node is asked for its own headroom, and only the other
    # node -- this host's comes from the /proc/meminfo above it.
    assert [r for r in raw if r.endswith("/stats/summary")] == [
        "/api/v1/nodes/elsewhere/proxy/stats/summary"]


def test_a_negative_remainder_is_named_as_skew_not_printed_as_a_quantity():
    """cAdvisor samples each series at its own instant, so the parts can cross."""
    cgroups = {"/": 3000.0, "/kubepods.slice": 2000.0, "/system.slice": 1500.0}
    joined = "\n".join(wh.name_the_host_share(cgroups, None))
    assert "-500Mi in no cgroup" in joined
    assert "do not add up" in joined


# --- Cycle 716, issue #131: whose swap is it, and is the working set real. ---


def test_one_fetch_carries_the_swap_and_rss_series_beside_the_working_set():
    series, why = wh.read_node_cgroup_series("server1", runner=_cadvisor())
    assert why is None
    # Three series off one read: a second fetch would be a second sample of a
    # moving machine, and the parts would disagree for no real reason.
    assert set(series) == {wh.CADVISOR_SERIES, wh.CADVISOR_RSS_SERIES,
                           wh.CADVISOR_SWAP_SERIES}
    swap = series[wh.CADVISOR_SWAP_SERIES]
    # The Pod line is 900Mi of swap and must not land here: `/kubepods.slice`
    # already charges it, and counting it twice inflates the workloads' share.
    assert "/kubepods.slice/pod-abc/xyz" not in swap
    assert round(swap["/kubepods.slice"]) == 1


def test_a_series_the_endpoint_does_not_carry_is_absent_not_empty():
    body = "\n".join(l for l in CADVISOR.splitlines()
                     if not l.startswith("container_memory_swap"))
    series, why = wh.read_node_cgroup_series("server1", runner=_cadvisor(body=body))
    assert why is None
    # A kernel without swap accounting publishes no such series. "0Mi swapped"
    # would be a claim; a missing key is the truth, and `name_the_swap` below
    # is what turns that into silence rather than into a number.
    assert wh.CADVISOR_SWAP_SERIES not in series
    assert wh.name_the_swap(series.get(wh.CADVISOR_SWAP_SERIES)) == []


def test_the_swap_line_names_the_share_that_is_neither_k3s_nor_the_pods():
    series, _ = wh.read_node_cgroup_series("server1", runner=_cadvisor())
    line = "\n".join(wh.name_the_swap(series[wh.CADVISOR_SWAP_SERIES]))
    # 1663Mi at the root, 1Mi in every Pod together, 169Mi in k3s. The whole
    # point of issue #131's swap half is that the remaining ~1.5GB is neither.
    assert "1663Mi swapped out" in line
    assert "/kubepods.slice holds 1Mi" in line
    assert "/system.slice/k3s.service 169Mi" in line
    assert "1493Mi swapped by processes in no cgroup" in line


def test_a_node_with_nothing_swapped_says_so_rather_than_naming_shares():
    line = "\n".join(wh.name_the_swap({"/": 0.0}))
    assert "Nothing on this node is swapped out" in line
    assert "holds" not in line


def test_the_named_share_says_how_much_of_it_is_resident_not_cache():
    series, _ = wh.read_node_cgroup_series("server1", runner=_cadvisor())
    line = "\n".join(wh.name_the_host_share(series[wh.CADVISOR_SERIES], None,
                                            series[wh.CADVISOR_RSS_SERIES]))
    # Working set counts reclaimable page cache; 2100Mi of k3s's 2325Mi is
    # anonymous, which is what makes it a real claim on the box.
    assert "/system.slice/k3s.service 2325Mi (2100Mi of it resident anonymous)" in line


def test_the_named_share_stays_silent_about_residency_when_rss_is_unread():
    series, _ = wh.read_node_cgroup_series("server1", runner=_cadvisor())
    line = "\n".join(wh.name_the_host_share(series[wh.CADVISOR_SERIES], None, None))
    assert "/system.slice/k3s.service 2325Mi." in line
    assert "resident" not in line


TWO_NODES = {"server1": 7931600 / 1024, "server2": 7931600 / 1024}


def test_a_pods_node_is_read_off_the_spec():
    """The field the whole per-node split rests on, straight from kubectl."""
    body = {"items": [
        {"metadata": {"namespace": "agents", "name": "here"},
         "spec": {"nodeName": "server2", "containers": [{"name": "c"}]},
         "status": {"phase": "Running"}},
        {"metadata": {"namespace": "agents", "name": "unplaced"},
         "spec": {"containers": [{"name": "c"}]},
         "status": {"phase": "Pending"}},
    ]}
    def runner(args, **kwargs):
        return mock.Mock(returncode=0, stdout=json.dumps(body), stderr="")

    pods, why = wh.read_pods(runner=runner)
    assert why is None
    assert [p["node"] for p in pods] == ["server2", ""]


def test_another_nodes_pods_do_not_count_against_this_ones_budget():
    """The defect server2 created on 2026-09-03, in one assertion.

    Four 1Gi containers on server1 and four on server2. Server1's own
    budget is 4096Mi of 7746Mi and fits; the cluster-wide sum is 8192Mi and
    does not. Before the split this raised MEMORY OVERCOMMITTED on server1
    for memory promised on the other box.
    """
    pods = ([_budget_pod(f"a{i}", {"c": "1Gi"}) for i in range(4)]
            + [_budget_pod(f"b{i}", {"c": "1Gi"}, node="server2") for i in range(4)])
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), TWO_NODES, pods, stats=_stats())
    assert judged is True
    assert actionable is False
    text = " ".join(lines)
    assert "MEMORY OVERCOMMITTED" not in text
    assert "BUDGET  server1: declared limits sum to 4096Mi" in text


def test_the_largest_container_to_fit_is_the_largest_on_this_node():
    """2048Mi on the other box cannot be what this box has to be able to start."""
    pods = [_pod_with_limit(64, name="small"),
            _pod_with_limit(2048, name="elsewhere")]
    pods[1]["node"] = "server2"
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=451752), TWO_NODES, pods, stats=_stats())
    assert actionable is False, "441Mi available still fits the 64Mi container here"
    assert "NODE OUT OF MEMORY" not in " ".join(lines)


def test_every_other_node_gets_its_own_budget_line():
    pods = [_budget_pod("a", {"c": "1Gi"}),
            _budget_pod("b", {"c": "512Mi"}, node="server2")]
    lines, _, _ = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), TWO_NODES, pods, stats=_stats())
    text = " ".join(lines)
    assert "BUDGET  server2: declared limits sum to 512Mi of 7746Mi" in text
    assert "MEMORY  server2: 4096Mi of 7746Mi available" in text


def test_an_overcommitted_other_node_raises_too():
    """A node I am not standing on is still a node that can be oversubscribed."""
    pods = [_budget_pod("a", {"c": "1Gi"})] + [
        _budget_pod(f"b{i}", {"c": "1Gi"}, node="server2") for i in range(8)]
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), TWO_NODES, pods, stats=_stats())
    assert actionable is True
    assert "MEMORY OVERCOMMITTED — server2 is 7746Mi" in " ".join(lines)


def test_an_unscheduled_pod_counts_against_nobody_and_is_named():
    pods = [_budget_pod("a", {"c": "1Gi"}),
            _budget_pod("waiting", {"c": "4Gi"}, node="")]
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), TWO_NODES, pods, stats=_stats())
    text = " ".join(lines)
    assert actionable is False, "4Gi on no node is on nobody's budget"
    assert "BUDGET  server1: declared limits sum to 1024Mi" in text
    assert "1 Pod(s) are on no node yet (waiting)" in text


def test_a_single_node_cluster_prints_no_other_node_section():
    """The shape before 2026-09-03, which must not grow a caveat it does not need."""
    lines, _, _ = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), NODES, [_budget_pod("a", {"c": "1Gi"})])
    text = " ".join(lines)
    assert "MEMORY  server2" not in text
    assert "on no node yet" not in text


def test_another_nodes_headroom_is_read_from_its_own_kubelet():
    """The half Cycle 860 left as "not judged", answered.

    /proc/meminfo in a pod is one host's, so server2's real headroom could
    only ever be a caveat here. The kubelet publishes it per node.
    """
    pods = [_budget_pod("a", {"c": "1Gi"}, requests={"c": "1Gi"}),
            _budget_pod("b", {"c": "512Mi"}, node="server2",
                        requests={"c": "512Mi"})]
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), TWO_NODES, pods,
        stats=_stats(available_mib=4096))
    text = " ".join(lines)
    assert judged is True
    assert actionable is False
    assert "MEMORY  server2: 4096Mi of 7746Mi available (52.9%), above the largest" in text
    assert "not judged for this node" not in text



def test_another_node_below_its_own_largest_request_raises():
    """server1 having room says nothing about whether server2 can start its own."""
    pods = [_budget_pod("a", {"c": "1Gi"}, requests={"c": "1Gi"}),
            _budget_pod("big", {"c": "2Gi"}, node="server2",
                        requests={"c": "2Gi"})]
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), TWO_NODES, pods,
        stats=_stats(available_mib=512))
    assert judged is True
    assert actionable is True
    assert "NODE OUT OF MEMORY — server2 has 512Mi available" in " ".join(lines)


def test_another_node_with_no_swap_says_what_that_costs_without_raising():
    """server2 has no swap at all. That is Hetzner's default, not an incident."""
    pods = [_budget_pod("a", {"c": "1Gi"}),
            _budget_pod("b", {"c": "512Mi"}, node="server2")]
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), TWO_NODES, pods,
        stats=_stats(swap_total_mib=0, swap_free_mib=0))
    assert actionable is False
    assert "SWAP    server2: none configured" in " ".join(lines)
    assert "a memory spike here is a kill rather than a slowdown" in " ".join(lines)


def test_another_nodes_swap_nearly_gone_raises_too():
    pods = [_budget_pod("a", {"c": "1Gi"}),
            _budget_pod("b", {"c": "512Mi"}, node="server2")]
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), TWO_NODES, pods,
        stats=_stats(swap_total_mib=2048, swap_free_mib=100))
    assert actionable is True
    assert "SWAP EXHAUSTED — server2" in " ".join(lines)


def test_an_unreadable_kubelet_is_not_a_clean_node():
    """A partial sweep must never read as a whole one -- exit 1, not exit 0."""
    pods = [_budget_pod("a", {"c": "1Gi"}),
            _budget_pod("b", {"c": "512Mi"}, node="server2")]
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), TWO_NODES, pods,
        stats=_stats(why="403 Forbidden"))
    text = " ".join(lines)
    assert judged is False, "unreadable is not clean"
    assert actionable is False, "unreadable is not an incident either"
    assert "CANNOT JUDGE server2's headroom — 403 Forbidden" in text
    assert "BUDGET  server2: declared limits sum to 512Mi" in text, (
        "the half that needs only kubectl still stands")


def test_a_kubelet_that_publishes_no_swap_block_is_not_a_node_without_swap():
    pods = [_budget_pod("a", {"c": "1Gi"}),
            _budget_pod("b", {"c": "512Mi"}, node="server2")]
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=1934336), TWO_NODES, pods,
        stats=_stats(swap_total_mib=None, swap_free_mib=None))
    text = " ".join(lines)
    assert actionable is False
    assert "SWAP    server2: not judged — the kubelet publishes no swap" in text
    assert "SWAP    server2: none configured" not in text


def test_read_node_memory_stats_sums_the_swap_total():
    """The kubelet publishes free and used; the total is their sum, not a field."""
    body = {"node": {"nodeName": "server1",
                     "memory": {"availableBytes": 3214393344},
                     "swap": {"swapAvailableBytes": 1616375808,
                              "swapUsageBytes": 531103744}}}

    def runner(args, **kwargs):
        assert args == ["kubectl", "get", "--raw",
                        "/api/v1/nodes/server1/proxy/stats/summary"]
        return mock.Mock(returncode=0, stdout=json.dumps(body), stderr="")

    figures, why = wh.read_node_memory_stats("server1", runner=runner)
    assert why is None
    assert round(figures["available_mib"]) == 3065
    assert round(figures["swap_total_mib"]) == 2048
    assert round(figures["swap_free_mib"]) == 1541


def test_read_node_memory_stats_without_availablebytes_is_unreadable():
    body = {"node": {"nodeName": "server2", "memory": {"workingSetBytes": 1}}}

    def runner(args, **kwargs):
        return mock.Mock(returncode=0, stdout=json.dumps(body), stderr="")

    figures, why = wh.read_node_memory_stats("server2", runner=runner)
    assert figures is None
    assert "availableBytes" in why


def test_read_node_memory_stats_reports_a_refusal_rather_than_zero():
    def runner(args, **kwargs):
        return mock.Mock(returncode=1, stdout="", stderr="Error: forbidden")

    figures, why = wh.read_node_memory_stats("server2", runner=runner)
    assert figures is None
    assert "forbidden" in why


def test_a_kubelet_with_no_swap_block_at_all_is_unread_not_zero():
    """No swap accounting and no swap are opposite facts behind one absence."""
    body = {"node": {"nodeName": "server2",
                     "memory": {"availableBytes": 1 << 30}}}

    def runner(args, **kwargs):
        return mock.Mock(returncode=0, stdout=json.dumps(body), stderr="")

    figures, why = wh.read_node_memory_stats("server2", runner=runner)
    assert why is None, "the node's headroom is still readable"
    assert figures["swap_total_mib"] is None
    assert figures["swap_free_mib"] is None
    line, actionable = wh.swap_line("server2", figures["swap_total_mib"],
                                    figures["swap_free_mib"])
    assert actionable is False
    assert "not judged" in line and "none configured, so" not in line


# --- Terminating: Sokrates reported a runner Pod "stuck in Terminating for
# --- ~20min" on 2026-09-03. It had 28 minutes of its own grace left.

def test_a_pod_draining_inside_its_own_deadline_does_not_raise():
    """The report that started this: 20 minutes into a 48-minute drain."""
    pods = [_pod(name="agora-persona-runner-x",
                 deletion="2026-08-29T08:28:00Z",
                 containers=[_container()])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 0
    body = "\n".join(lines)
    assert "DRAINING" in body
    assert "TERMINATING PAST ITS OWN DEADLINE" not in body
    assert "28m left" in body


def test_a_pod_past_its_own_deadline_raises():
    pods = [_pod(name="wedged", deletion="2026-08-29T07:30:00Z",
                 containers=[_container()])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 2
    body = "\n".join(lines)
    assert "TERMINATING PAST ITS OWN DEADLINE" in body
    assert "agents/wedged on server1" in body


def test_the_kill_margin_is_real_and_covers_the_status_write():
    """One minute past the deadline is the status write, not a wedged Pod."""
    pods = [_pod(name="justover", deletion="2026-08-29T07:59:00Z",
                 containers=[_container()])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 0
    assert "DRAINING" in "\n".join(lines)


def test_a_terminating_pod_with_no_readable_deadline_is_the_loud_case():
    pods = [_pod(name="nodate", deletion="not a timestamp",
                 containers=[_container()])]
    lines, status = wh.report(pods, [], NOW)
    assert status == 2
    assert "no readable deadline" in "\n".join(lines)


def test_a_pod_that_is_not_being_deleted_is_in_neither_list():
    past, draining = wh.terminating([_pod(containers=[_container()])], NOW)
    assert past == []
    assert draining == []


def test_read_pods_carries_the_deletion_deadline_off_the_object():
    body = {"items": [{
        "metadata": {"namespace": "agents", "name": "p",
                     "deletionTimestamp": "2026-08-29T08:28:00Z",
                     "deletionGracePeriodSeconds": 2880},
        "spec": {"containers": [{"name": "c"}], "nodeName": "server1"},
        "status": {"phase": "Running", "containerStatuses": []},
    }]}
    runner = mock.Mock(return_value=mock.Mock(
        returncode=0, stdout=json.dumps(body), stderr=""))
    pods, why = wh.read_pods(runner)
    assert why is None
    assert pods[0]["deletion"] == "2026-08-29T08:28:00Z"


# --- A finished Pod is not a live outage -------------------------------
#
# Cycle 882. `marcus-backup` ran at 13:20 on 2026-09-03, failed, and three
# later runs of the same CronJob succeeded. Fifteen hours on, the report
# still carried that Pod under CONTAINER DIED as "still down" and under
# NOT READY as "not serving right now". Three call sites read
# `container.ready` as "is this serving", which on a Pod that has already
# finished is false forever and means nothing.


def _failed_job_pod(name="marcus-backup-1-abc", at="2026-09-03T11:20:00Z"):
    """A CronJob Pod whose single run exited 1. Phase Failed, never ready."""
    return _pod(name=name, phase="Failed", containers=[
        _container(ready=False, state={"terminated": {
            "reason": "Error", "exitCode": 1, "finishedAt": at}})])


def test_finished_pods_death_ages_out_like_any_other():
    # 15h old, well past RECENT_DEATH, on a Pod that is over.
    fresh, old = wh.deaths([_failed_job_pod()], NOW.replace(
        year=2026, month=9, day=4, hour=2, minute=20))
    assert fresh == []
    assert [d["pod"] for d in old] == ["marcus-backup-1-abc"]


def test_a_running_pods_death_still_stays_loud_past_the_window():
    # The complement, and the reason the guard is on the phase rather than
    # on the age: a container that died and is still not ready is a live
    # outage no matter how long ago it happened.
    pod = _pod(name="agora", phase="Running", containers=[
        _container(ready=False, state={"terminated": {
            "reason": "OOMKilled", "exitCode": 137,
            "finishedAt": "2026-09-03T11:20:00Z"}})])
    fresh, old = wh.deaths([pod], NOW.replace(
        year=2026, month=9, day=4, hour=2, minute=20))
    assert [d["pod"] for d in fresh] == ["agora"]
    assert old == []


def test_a_fresh_failure_on_a_finished_pod_is_still_reported():
    # A CronJob that failed minutes ago is a real finding; only the ageing
    # changed, not the raising.
    fresh, old = wh.deaths([_failed_job_pod(at="2026-09-04T02:10:00Z")],
                           NOW.replace(year=2026, month=9, day=4,
                                       hour=2, minute=20))
    assert [d["pod"] for d in fresh] == ["marcus-backup-1-abc"]
    assert old == []


def test_finished_pod_is_not_listed_as_not_serving():
    assert wh.not_ready([_failed_job_pod()]) == []


def test_a_pending_pod_is_still_listed_as_not_serving():
    # The complement: not_ready must not have been emptied out.
    stuck = wh.not_ready([_pod(phase="Pending", containers=[
        _container(ready=False, state={"waiting": {
            "reason": "ImagePullBackOff", "message": "no such image"}})])])
    assert [s["reason"] for s in stuck] == ["ImagePullBackOff"]


def test_finished_pod_is_not_described_as_still_down():
    pods = [_failed_job_pod(at="2026-09-04T02:10:00Z")]
    lines, actionable = wh.report(pods, [], NOW.replace(
        year=2026, month=9, day=4, hour=2, minute=20))
    body = "\n".join(lines)
    assert "still down" not in body
    assert "Pod finished" in body
    assert "NOT READY" not in body


def test_a_running_pod_that_died_is_still_described_as_still_down():
    pod = _pod(name="agora", phase="Running", containers=[
        _container(ready=False, state={"terminated": {
            "reason": "OOMKilled", "exitCode": 137,
            "finishedAt": "2026-09-04T02:10:00Z"}})])
    lines, actionable = wh.report([pod], [], NOW.replace(
        year=2026, month=9, day=4, hour=2, minute=20))
    body = "\n".join(lines)
    assert "still down" in body
    assert actionable
