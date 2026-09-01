"""The 18-minute-cadence switch: one env var, off by default.

`allow_concurrent` is what decides whether a second heartbeat overlaps the
first or queues behind the bridge's process-wide lock. It has to be off
until the bridge deployment that provisions a git worktree per turn is
live, and it has to be flippable without a code change once it is.
"""
import importlib

import pytest


def _reload(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CLAUDE_CLI_CONCURRENT", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CLI_CONCURRENT", value)
    from agora_runner import config
    return importlib.reload(config)


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off"])
def test_concurrency_is_off_unless_explicitly_switched_on(monkeypatch, value):
    assert _reload(monkeypatch, value).CLAUDE_CLI_CONCURRENT is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_concurrency_switches_on_from_the_env_alone(monkeypatch, value):
    assert _reload(monkeypatch, value).CLAUDE_CLI_CONCURRENT is True


def test_the_flag_reaches_the_bridge_request_body(monkeypatch):
    """The config value is only useful if it is actually sent. It travels
    on the same body as `model` and `stateless`, and the bridge reads it
    off the payload (server.py) -- a value that never leaves the runner
    would look identical from here."""
    sent = {}

    def fake_http_json(method, url, payload=None, headers=None, **kw):
        sent.update(payload or {})
        return 200, {"text": "ok", "session_id": "s"}

    monkeypatch.setenv("CLAUDE_CLI_CONCURRENT", "1")
    from agora_runner import config
    importlib.reload(config)
    from agora_runner.providers import claude_cli
    importlib.reload(claude_cli)
    monkeypatch.setattr(claude_cli, "http_json", fake_http_json)
    monkeypatch.setattr(claude_cli, "grant_tool_activity", lambda *a, **k: "")
    monkeypatch.setattr(claude_cli, "grant_mcp", lambda *a, **k: "")

    claude_cli.claude_cli_generate(
        "claude-cli:claude-opus-5", None, "sys",
        [{"role": "user", "content": "hi"}], {}, {"name": "Nova"}, "conv-1")

    assert sent["allow_concurrent"] is True

    # And back off again, from the env alone.
    monkeypatch.delenv("CLAUDE_CLI_CONCURRENT")
    importlib.reload(config)
    importlib.reload(claude_cli)
    monkeypatch.setattr(claude_cli, "http_json", fake_http_json)
    monkeypatch.setattr(claude_cli, "grant_tool_activity", lambda *a, **k: "")
    monkeypatch.setattr(claude_cli, "grant_mcp", lambda *a, **k: "")
    claude_cli.claude_cli_generate(
        "claude-cli:claude-opus-5", None, "sys",
        [{"role": "user", "content": "hi"}], {}, {"name": "Nova"}, "conv-1")
    assert sent["allow_concurrent"] is False
