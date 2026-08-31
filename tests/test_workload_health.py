"""Cycle 615. The runner was OOMKilled and left nobody serving, and none of
the eighteen step-1a checks read the three fields that say so."""
import datetime
import json
import pytest
from unittest import mock

from tools import workload_health as wh

NOW = datetime.datetime(2026, 8, 29, 8, 0, tzinfo=datetime.timezone.utc)


def _pod(namespace="agents", name="p", phase="Running", containers=(), limits=None,
         conditions=()):
    return {
        "namespace": namespace, "name": name, "phase": phase,
        "containers": list(containers), "conditions": list(conditions),
        "limits": dict(limits or {}),
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


def _meminfo(**overrides):
    fields = {}
    for line in MEMINFO.splitlines():
        name, _, rest = line.partition(":")
        fields[name.strip()] = float(rest.split()[0])
    fields.update(overrides)
    return fields


def _pod_with_limit(mib, name="big"):
    return _pod(name=name, limits={"c": f"{mib}Mi"})


def _budget_pod(name, limits=None, containers=1, phase="Running"):
    pod = _pod(name=name, phase=phase, limits=limits or {})
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


def test_available_below_the_largest_configured_limit_raises():
    lines, actionable, judged = wh.memory_headroom(
        _meminfo(MemAvailable=451752), NODES, [_pod_with_limit(2048)])
    assert judged is True
    assert actionable is True
    assert any("NODE OUT OF MEMORY" in line for line in lines)


def test_available_above_the_largest_configured_limit_is_quiet():
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=451752), NODES, [_pod_with_limit(64)])
    assert actionable is False
    assert any(line.startswith("MEMORY ") for line in lines)


def test_the_threshold_is_the_biggest_limit_not_the_first_one():
    lines, actionable, _ = wh.memory_headroom(
        _meminfo(MemAvailable=451752), NODES,
        [_pod_with_limit(64, name="small"), _pod_with_limit(2048, name="big")])
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
    assert raw == ["/api/v1/nodes/server1/proxy/metrics/cadvisor"]
