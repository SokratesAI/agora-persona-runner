"""Cycle 615. The runner was OOMKilled and left nobody serving, and none of
the eighteen step-1a checks read the three fields that say so."""
import datetime
import json

from tools import workload_health as wh

NOW = datetime.datetime(2026, 8, 29, 8, 0, tzinfo=datetime.timezone.utc)


def _pod(namespace="agents", name="p", phase="Running", containers=(), limits=None):
    statuses = []
    for c in containers:
        statuses.append(c)
    return {
        "namespace": namespace, "name": name, "phase": phase,
        "containers": list(statuses), "limits": dict(limits or {}),
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
    assert "CONTAINER DIED" in body
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
    deps = [_deployment(name="whatsapp-bridge", replicas=0,
                        since="2026-08-01T00:00:00Z", grace=30)]
    lines, status = wh.report([_pod()], deps, NOW)
    assert status == 0
    assert "whatsapp-bridge" not in "\n".join(lines)


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
