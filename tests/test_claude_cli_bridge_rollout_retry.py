"""A delivery the bridge never took is offered again, not lost.

The owner lost a chat message on 2026-09-08 at ~14:52: no reply, the chat spun
forever, and the pod restarted. The bridge Deployment uses strategy
Recreate, so the old pod is scaled to 0 before the replacement is created,
and there was a ~7 minute window with no bridge at all. The bridge already
answers a draining request with 503 `shutting_down` explicitly so the caller
can retry against the replacement pod; nothing on this side did.

The line these tests defend is *which* failures are safe to offer again: only
the ones where the request provably never reached a running turn.
"""
import socket
import urllib.error

import pytest

from agora_runner.providers import claude_cli


@pytest.fixture(autouse=True)
def _no_real_waiting(monkeypatch):
    # Every wait in the retry loop happens while no turn is running
    # anywhere, so the durations are not what these tests are about.
    monkeypatch.setattr(claude_cli, "BRIDGE_RETRY_BACKOFF", (0,))


def _generate(monkeypatch, responses):
    """Run one full turn with `responses` as the bridge, returning the calls.

    `responses` items are either an exception to raise or a (status, body)
    to return, consumed in order.
    """
    calls = []

    def fake_http_json(method, url, payload=None, headers=None, **kw):
        calls.append(url)
        nxt = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    monkeypatch.setattr(claude_cli, "http_json", fake_http_json)
    monkeypatch.setattr(claude_cli, "grant_tool_activity", lambda *a, **k: "")
    monkeypatch.setattr(claude_cli, "grant_mcp", lambda *a, **k: "")
    text = claude_cli.claude_cli_generate(
        "claude-cli:claude-opus-5", None, "sys",
        [{"role": "user", "content": "hi"}], {}, {"name": "Nova"}, "conv-1")
    return text, calls


def test_a_draining_pod_is_offered_the_turn_again(monkeypatch):
    text, calls = _generate(monkeypatch, [
        (503, {"error": "shutting_down"}),
        (503, {"error": "shutting_down"}),
        (200, {"text": "answered by the replacement pod"}),
    ])
    assert text == "answered by the replacement pod"
    assert len(calls) == 3


def test_a_refused_connection_is_offered_the_turn_again(monkeypatch):
    text, calls = _generate(monkeypatch, [
        urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
        (200, {"text": "answered by the replacement pod"}),
    ])
    assert text == "answered by the replacement pod"
    assert len(calls) == 2


def test_a_name_that_does_not_resolve_is_offered_the_turn_again(monkeypatch):
    text, calls = _generate(monkeypatch, [
        urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
        (200, {"text": "answered by the replacement pod"}),
    ])
    assert text == "answered by the replacement pod"
    assert len(calls) == 2


def test_a_reset_after_the_request_was_accepted_is_not_retried(monkeypatch):
    """The failure this loop must NOT paper over. A reset can mean the pod
    took the turn and is running it, so offering it again would ask a live
    bridge to answer the same message twice. Without this the retry loop is
    a duplicate-turn generator rather than a fix."""
    with pytest.raises(urllib.error.URLError):
        _generate(monkeypatch, [
            urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer")),
            (200, {"text": "should never be reached"}),
        ])


def test_a_503_that_is_not_shutting_down_is_not_retried(monkeypatch):
    """A 503 the bridge raised out of a turn is a real failure, and the
    caller has always seen it as one. Only the drain answer is transient."""
    with pytest.raises(RuntimeError, match="claude_cli 503"):
        _generate(monkeypatch, [
            (503, {"error": "boom"}),
            (200, {"text": "should never be reached"}),
        ])


def test_a_429_usage_limit_still_raises_immediately(monkeypatch):
    """`ClaudeBridgeUsageLimited` is the one status callers branch on, and
    it must not be swallowed or delayed by the retry loop."""
    with pytest.raises(claude_cli.ClaudeBridgeUsageLimited):
        _generate(monkeypatch, [(429, {"detail": "usage limit"})])


def test_the_budget_ends_the_wait_rather_than_hanging(monkeypatch):
    """A bridge that never comes back has to end as a stated failure. With
    no budget the turn would block forever and the chat would spin, which
    is the bug, one layer down."""
    monkeypatch.setattr(claude_cli, "BRIDGE_RETRY_SECONDS", 0)
    # A budget that never binds is an infinite loop, and an infinite loop is
    # a hung test rather than a failing one -- so the fake bridge counts the
    # offers itself and refuses a second one. Without this the assertion
    # below would be defended by nothing but CI's own timeout.
    offers = []

    def one_offer_only(method, url, payload=None, headers=None, **kw):
        offers.append(url)
        assert len(offers) == 1, "the spent budget did not end the wait"
        return 503, {"error": "shutting_down"}

    monkeypatch.setattr(claude_cli, "http_json", one_offer_only)
    monkeypatch.setattr(claude_cli, "grant_tool_activity", lambda *a, **k: "")
    monkeypatch.setattr(claude_cli, "grant_mcp", lambda *a, **k: "")
    with pytest.raises(RuntimeError, match="claude_cli 503"):
        claude_cli.claude_cli_generate(
            "claude-cli:claude-opus-5", None, "sys",
            [{"role": "user", "content": "hi"}], {}, {"name": "Nova"}, "conv-1")
    assert offers == [f"{claude_cli.CLAUDE_BRIDGE_URL}/generate"]


def test_the_grants_are_revoked_once_and_only_after_the_last_attempt(monkeypatch):
    """The per-turn activity and MCP tokens live in this process's memory
    and are revoked in the `finally` around the call. Retrying inside that
    block is deliberate: revoking between attempts would hand the
    replacement pod a turn with no Agora tools at all."""
    revoked = []
    monkeypatch.setattr(claude_cli, "grant_tool_activity", lambda *a, **k: "activity-token")
    monkeypatch.setattr(claude_cli, "grant_mcp", lambda *a, **k: "mcp-token")
    monkeypatch.setattr(claude_cli, "revoke_tool_activity", lambda t: revoked.append(t))
    monkeypatch.setattr(claude_cli, "revoke_mcp", lambda t: revoked.append(t))

    calls = []

    def fake_http_json(method, url, payload=None, headers=None, **kw):
        calls.append(payload)
        if len(calls) == 1:
            return 503, {"error": "shutting_down"}
        # The retry still carries the grants -- an untooled retry is the
        # silent-failure version of this fix.
        assert payload["activity"]["token"] == "activity-token"
        assert payload["mcp"]["token"] == "mcp-token"
        return 200, {"text": "ok"}

    monkeypatch.setattr(claude_cli, "http_json", fake_http_json)
    claude_cli.claude_cli_generate(
        "claude-cli:claude-opus-5", None, "sys",
        [{"role": "user", "content": "hi"}], {}, {"name": "Nova"}, "conv-1")

    assert len(calls) == 2
    assert revoked == ["activity-token", "mcp-token"]
