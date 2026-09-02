"""kubectl_test -- the namespace pin, and the four ways round it.

Idea #230 item 2 on the owner's board. Every test here names the escape it closes, so a
regression says which door reopened rather than only that something broke.
"""

from unittest.mock import patch

import pytest

from agora_runner import tools_kubectl_test as kt
from agora_runner.tools_kubectl_test import KUBECTL_TEST_NAMESPACE, kubectl_test


@pytest.fixture
def ran():
    """Capture the argv and stdin instead of running kubectl, and assert the
    precondition that a call actually reached subprocess.run -- a refusal
    returns before this and leaves the list empty."""
    calls = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input")))
        return Result()

    with patch.object(kt.subprocess, "run", side_effect=fake_run):
        yield calls


def test_pins_the_namespace_when_none_is_given(ran):
    kubectl_test({"verb": "get", "resource": "pods"})
    assert len(ran) == 1, "the call should have reached kubectl"
    cmd, _ = ran[0]
    assert cmd == ["kubectl", "get", "pods", "-n", KUBECTL_TEST_NAMESPACE]
    assert "--all-namespaces" not in cmd


def test_refuses_another_namespace_rather_than_rewriting_it(ran):
    result = kubectl_test({"verb": "delete", "resource": "pods", "namespace": "agents"})
    assert "refused" in result and "'agents'" in result
    assert ran == [], "a refused namespace must not reach kubectl at all"


def test_accepts_the_test_namespace_spelled_out(ran):
    kubectl_test({"verb": "get", "resource": "pods", "namespace": KUBECTL_TEST_NAMESPACE})
    assert len(ran) == 1
    assert ran[0][0][-2:] == ["-n", KUBECTL_TEST_NAMESPACE]


@pytest.mark.parametrize("flag", ["-n", "--namespace=agents", "-A", "--all-namespaces"])
def test_refuses_a_namespace_flag_smuggled_through_args(ran, flag):
    result = kubectl_test({"verb": "get", "resource": "pods", "args": [flag, "agents"]})
    assert "not allowed" in result
    assert ran == []


@pytest.mark.parametrize("flag", ["-f", "--filename=/tmp/x.yaml", "--raw", "--kubeconfig=/tmp/k"])
def test_refuses_flags_that_reach_a_file_or_the_raw_api(ran, flag):
    result = kubectl_test({"verb": "get", "resource": "pods", "args": [flag]})
    assert "not allowed" in result
    assert ran == []


@pytest.mark.parametrize("resource", ["nodes", "node/server1", "clusterrolebinding",
                                      "namespaces", "persistentvolumes", "crd"])
def test_refuses_cluster_scoped_resources(ran, resource):
    """`-n` is ignored on a cluster-scoped resource rather than rejected, so a
    namespace check alone would have let `delete node server1` through."""
    result = kubectl_test({"verb": "delete", "resource": resource})
    assert "not namespaced or is not allowed" in result
    assert ran == []


@pytest.mark.parametrize("resource", ["secrets", "secret/foo", "secretstore"])
def test_refuses_secrets_the_way_kubectl_read_does(ran, resource):
    result = kubectl_test({"verb": "get", "resource": resource})
    assert "Secrets" in result
    assert ran == []


def test_refuses_a_verb_outside_the_allowlist(ran):
    result = kubectl_test({"verb": "port-forward", "resource": "pods"})
    assert "not allowed" in result
    assert ran == []


def test_apply_sends_the_manifest_on_stdin_not_as_a_path(ran):
    manifest = "kind: ConfigMap\nmetadata:\n  name: scratch\n"
    kubectl_test({"verb": "apply", "manifest": manifest})
    assert len(ran) == 1
    cmd, stdin = ran[0]
    assert cmd == ["kubectl", "apply", "-f", "-", "-n", KUBECTL_TEST_NAMESPACE]
    assert stdin == manifest


def test_apply_accepts_a_manifest_that_names_the_test_namespace(ran):
    manifest = f"kind: Deployment\nmetadata:\n  name: d\n  namespace: {KUBECTL_TEST_NAMESPACE}\n"
    kubectl_test({"verb": "apply", "manifest": manifest})
    assert len(ran) == 1


def test_apply_refuses_a_manifest_declaring_another_namespace(ran):
    manifest = "kind: Deployment\nmetadata:\n  name: d\n  namespace: agents\n"
    result = kubectl_test({"verb": "apply", "manifest": manifest})
    assert "'agents'" in result
    assert ran == []


def test_apply_refuses_a_second_document_that_escapes(ran):
    """A multi-document manifest is only as safe as its worst document."""
    manifest = (f"kind: ConfigMap\nmetadata:\n  name: a\n  namespace: {KUBECTL_TEST_NAMESPACE}\n"
                "---\n"
                "kind: ConfigMap\nmetadata:\n  name: b\n  namespace: infra\n")
    result = kubectl_test({"verb": "apply", "manifest": manifest})
    assert "'infra'" in result
    assert ran == []


def test_apply_refuses_a_cluster_scoped_kind(ran):
    manifest = "kind: ClusterRoleBinding\nmetadata:\n  name: give-me-everything\n"
    result = kubectl_test({"verb": "apply", "manifest": manifest})
    assert "may not write" in result or "may write" in result
    assert ran == []


def test_apply_refuses_a_secret_kind(ran):
    manifest = "kind: Secret\nmetadata:\n  name: s\n"
    result = kubectl_test({"verb": "create", "manifest": manifest})
    assert "Secrets" in result
    assert ran == []


def test_apply_refuses_a_path_where_yaml_was_expected(ran):
    result = kubectl_test({"verb": "apply", "manifest": "/tmp/manifest.yaml"})
    assert "must be a mapping" in result
    assert ran == []


def test_apply_refuses_broken_yaml(ran):
    result = kubectl_test({"verb": "apply", "manifest": "kind: [unclosed\n"})
    assert "not valid YAML" in result
    assert ran == []


def test_apply_without_a_manifest_is_refused(ran):
    result = kubectl_test({"verb": "apply", "resource": "deployment/d"})
    assert "manifest" in result
    assert ran == []


def test_exec_appends_the_command_after_a_separator(ran):
    kubectl_test({"verb": "exec", "resource": "pod/scratch",
                  "command": ["sh", "-c", "ls /app"]})
    assert len(ran) == 1
    cmd, _ = ran[0]
    assert cmd == ["kubectl", "exec", "pod/scratch", "-n", KUBECTL_TEST_NAMESPACE,
                   "--", "sh", "-c", "ls /app"]


def test_exec_without_a_command_is_refused(ran):
    result = kubectl_test({"verb": "exec", "resource": "pod/scratch"})
    assert "command" in result
    assert ran == []


def test_missing_binary_degrades_gracefully():
    with patch.object(kt.subprocess, "run", side_effect=FileNotFoundError()):
        result = kubectl_test({"verb": "get", "resource": "pods"})
    assert "not installed" in result


def test_nonzero_exit_is_logged_outside_debug():
    logged = []

    class Result:
        returncode = 1
        stdout = ""
        stderr = "Error from server (Forbidden)"

    with patch.object(kt.subprocess, "run", return_value=Result()), \
         patch.object(kt, "log", side_effect=lambda msg: logged.append(msg)):
        result = kubectl_test({"verb": "get", "resource": "pods"})
    assert "Forbidden" in result
    assert any("exited 1" in m for m in logged)


def test_the_tool_is_gated_behind_its_own_capability():
    from agora_runner.tools_schemas import TOOL_TO_CAPABILITY, client_tool_schemas
    assert TOOL_TO_CAPABILITY["kubectl_test"] == "kubectlTest"
    names = [t["name"] for t in client_tool_schemas({"kubectlRead": True})]
    assert "kubectl_read" in names, "precondition: kubectlRead alone gives the read tool"
    assert "kubectl_test" not in names, "kubectlRead must not imply kubectlTest"
    names = [t["name"] for t in client_tool_schemas({"kubectlTest": True})]
    assert "kubectl_test" in names


def test_the_capability_is_off_by_default():
    from agora_runner.config import DEFAULT_CAPS, NO_CAPS
    assert DEFAULT_CAPS["kubectlTest"] is False
    assert NO_CAPS["kubectlTest"] is False
