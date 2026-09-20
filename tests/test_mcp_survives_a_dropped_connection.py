"""A dropped connection must cost the turn one tool call, not every tool call.

Idea #161. Claude Code 2.1.228 fixed remote MCP servers in headless sessions
never recovering from a dropped connection, and we now run 2.1.272, so the
client half is covered. This file pins the half that is ours: /mcp keeps no
per-connection state, so a client that lost its socket can open a new one and
go straight back to calling tools.

Measured live, 2026-09-20, before writing it down. A throwaway MCP server that
answers `initialize` and `tools/list`, then kills the TCP connection with an RST
on the first `tools/call` and answers every later one, was handed to a headless
`claude -p` session on 2.1.272: it reported two attempts, "the first attempt
failed with a socket connection error", and the second returned the real result.
The tools came back.

The same probe measured our own end: `POST /mcp` answers HTTP/1.0 with no
`Mcp-Session-Id` header, so there is no session for a drop to lose. That is a
property of `invoke_server`'s `BaseHTTPRequestHandler` rather than a decision
anyone wrote down, which is exactly why it is worth a test -- the grant already
lives in this process's memory, and quietly moving the handshake in there too
would turn a recoverable drop into a dead hour.

What this deliberately does NOT claim to cover: a runner pod REPLACED mid-turn.
The bridge writes the runner's pod IP into --mcp-config, and the grant only
exists in that process, so a new pod is a different server with no knowledge of
the token. No amount of client reconnection recovers that, and a Service would
not either.
"""
import json

import pytest

from agora_runner import nova_replies, tools_mcp


def _call(method, token, request_id=1):
    body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method}).encode()
    return tools_mcp.handle_http(f"Bearer {token}", body)


@pytest.fixture
def token():
    tok = tools_mcp.grant(
        nova_replies.REPLY_PERSONA, nova_replies.REPLY_CAPS, nova_replies.CONVERSATION_ID
    )
    assert tok, "the fixture caps must grant something or the test is vacuous"
    try:
        yield tok
    finally:
        tools_mcp.revoke(tok)


def test_tools_list_needs_no_handshake_before_it(token):
    """A reconnecting client sends tools/list on a socket that never saw
    initialize. If that starts failing, a dropped connection costs the rest
    of the turn rather than one call."""
    status, payload = _call("tools/list", token)
    assert status == 200
    assert "error" not in payload, payload
    assert payload["result"]["tools"], "an empty tool list is not a working reconnect"


def test_the_handshake_can_arrive_after_the_first_call(token):
    """Order-independence is the actual property. A client that reconnects
    mid-turn may re-handshake at any point, or not at all."""
    first, _ = _call("tools/list", token, request_id=1)
    status, payload = _call("initialize", token, request_id=2)
    assert first == 200
    assert status == 200 and "error" not in payload, payload
    assert payload["result"]["serverInfo"]["name"]


def test_two_calls_on_two_connections_see_the_same_tools(token):
    """`handle_http` is handed one request's bytes and nothing about the
    socket it arrived on, so two calls cannot diverge -- unless somebody
    gives it connection state to remember."""
    _, first = _call("tools/list", token, request_id=1)
    _, second = _call("tools/list", token, request_id=2)
    names = lambda p: sorted(t["name"] for t in p["result"]["tools"])
    assert names(first) == names(second)
    assert names(first)
