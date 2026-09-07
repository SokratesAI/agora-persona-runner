"""A per-turn grant lives in one process, so its callback URL must name that process.

Measured 2026-09-07, Cycle 1168, mid-rollout. The runner Deployment is
`RollingUpdate` with `maxSurge: 1` and a 2880s termination grace, so for up
to 48 minutes after a roll two runner pods are alive and the old one is
still running cycles. The MCP token minted for the live cycle answered
`HTTP 200` at the old pod's IP (10.42.0.114) and
`401 unknown or expired mcp token` at the new one's (10.42.0.122). The CLI
reaches the Service, so it got the new pod, and the whole `agora` MCP server
-- terminal_exec, create_pr, merge_pr, every vault tool -- was dead for the
turn. Two cycles before this one wrote no journal entry at all.

`config.py` had the assumption written down beside the constant: "this
points at the Service, so it only comes back to the replica that issued the
grant because this deployment runs exactly one (strategy Recreate, one
replica)". The manifest stopped being Recreate and nothing re-read the
comment.
"""
import importlib

import pytest


def _config(monkeypatch, **env):
    for key in ("RUNNER_CALLBACK_URL", "POD_IP", "RUNNER_PORT", "RUNNER_SELF_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import agora_runner.config as config
    return importlib.reload(config)


def test_the_pod_ip_becomes_the_callback_host(monkeypatch):
    config = _config(monkeypatch, POD_IP="10.42.0.114", RUNNER_PORT="8082")
    assert config.RUNNER_CALLBACK_URL == "http://10.42.0.114:8082"


def test_the_callback_is_not_the_service(monkeypatch):
    """The precondition the test above cannot state on its own.

    A URL equal to the Service is exactly the bug, and `assert == pod ip`
    would still pass if some later change made both the same string.
    """
    config = _config(monkeypatch, POD_IP="10.42.0.114")
    assert config.RUNNER_SELF_URL.startswith("http://agora-persona-runner.")
    assert config.RUNNER_CALLBACK_URL != config.RUNNER_SELF_URL


def test_it_follows_runner_port(monkeypatch):
    config = _config(monkeypatch, POD_IP="10.42.0.114", RUNNER_PORT="9999")
    assert config.RUNNER_CALLBACK_URL == "http://10.42.0.114:9999"


def test_an_explicit_override_wins_over_the_pod_ip(monkeypatch):
    config = _config(
        monkeypatch, POD_IP="10.42.0.114",
        RUNNER_CALLBACK_URL="http://somewhere-else:1234")
    assert config.RUNNER_CALLBACK_URL == "http://somewhere-else:1234"


def test_the_hostname_answers_when_there_is_no_pod_ip_env(monkeypatch):
    """The kubelet writes `<pod ip> <pod name>` into /etc/hosts, and the pod
    name is the hostname, so resolving our own hostname returns the pod IP.
    That is the path in production today -- the runner Deployment sets no
    POD_IP, checked against the live object on 2026-09-07."""
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "agora-persona-runner-8bfd4c79f-kmqd6")
    monkeypatch.setattr(socket, "gethostbyname", lambda h: "10.42.0.122")
    config = _config(monkeypatch, RUNNER_PORT="8082")
    assert config.RUNNER_CALLBACK_URL == "http://10.42.0.122:8082"


def test_loopback_is_not_a_self_address(monkeypatch):
    """Off a cluster the hostname resolves to 127.0.0.1, which the bridge
    could never reach. Falling back to the Service is the old behaviour and
    is the right failure -- a callback URL of `http://127.0.0.1:8082` would
    point the bridge pod at itself."""
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "laptop")
    monkeypatch.setattr(socket, "gethostbyname", lambda h: "127.0.1.1")
    config = _config(monkeypatch)
    assert config.RUNNER_CALLBACK_URL == config.RUNNER_SELF_URL


def test_a_resolver_failure_falls_back_to_the_service(monkeypatch):
    import socket

    def boom(_):
        raise socket.gaierror("no")

    monkeypatch.setattr(socket, "gethostname", lambda: "unresolvable")
    monkeypatch.setattr(socket, "gethostbyname", boom)
    config = _config(monkeypatch)
    assert config.RUNNER_CALLBACK_URL == config.RUNNER_SELF_URL


def _sent_body(monkeypatch, callback_url):
    monkeypatch.setenv("RUNNER_CALLBACK_URL", callback_url)
    import agora_runner.config as config
    importlib.reload(config)
    from agora_runner.providers import claude_cli
    importlib.reload(claude_cli)

    sent = {}

    def fake_http_json(method, url, payload=None, headers=None, **kw):
        sent.update(payload or {})
        return 200, {"text": "ok", "session_id": "s"}

    claude_cli.http_json = fake_http_json
    claude_cli.grant_tool_activity = lambda *a, **k: "activity-token"
    claude_cli.grant_mcp = lambda *a, **k: "mcp-token"
    claude_cli.revoke_tool_activity = lambda *a, **k: None
    claude_cli.revoke_mcp = lambda *a, **k: None
    claude_cli.claude_cli_generate(
        "claude-cli:claude-opus-5", None, "sys",
        [{"role": "user", "content": "hi"}], {"terminal_exec": True},
        {"name": "Nova", "id": "08ffac94-7c4a-4506-897f-968c592358cb"}, "conv-1")
    return sent


def test_the_mcp_url_the_bridge_receives_is_this_pod(monkeypatch):
    sent = _sent_body(monkeypatch, "http://10.42.0.114:8082")
    assert sent["mcp"]["url"] == "http://10.42.0.114:8082/mcp"
    assert sent["mcp"]["token"] == "mcp-token"


def test_the_activity_url_the_bridge_receives_is_this_pod(monkeypatch):
    sent = _sent_body(monkeypatch, "http://10.42.0.114:8082")
    assert sent["activity"]["url"] == "http://10.42.0.114:8082/tool-activity"


def test_neither_callback_still_names_the_service(monkeypatch):
    """The one assertion that would have caught the live failure.

    Both URLs came from RUNNER_SELF_URL before this change, so a test that
    only checked the shape `.../mcp` passed on the broken code.
    """
    sent = _sent_body(monkeypatch, "http://10.42.0.114:8082")
    for url in (sent["mcp"]["url"], sent["activity"]["url"]):
        assert "svc.cluster.local" not in url
