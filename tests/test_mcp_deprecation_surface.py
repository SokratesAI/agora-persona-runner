"""None of the MCP features on a deprecation clock are in our server.

Idea #238. The 2026-07-28 MCP specification deprecated Roots, Sampling and
Logging with a floor of twelve months, deprecated the legacy HTTP+SSE
transport on the same clock, and deprecated Dynamic Client Registration in
favour of Client ID Metadata Documents. The row existed because nobody could
say whether any of that touched us, and an answer written into a journal
entry decays the moment someone adds a capability. This file is the answer
kept measurable.

What I read, 2026-09-20, before writing it down:

  * `handle()` dispatches exactly three methods -- `initialize`,
    `tools/list`, `tools/call` -- and answers -32601 to everything else.
    `roots/list`, `sampling/createMessage` and `logging/setLevel` are in
    that everything else.
  * `initialize` answers `capabilities: {"tools": {"listChanged": False}}`.
    A client only sends a deprecated request after the server advertises
    the matching capability, so the empty set is the real guarantee and the
    -32601 is the backstop.
  * `invoke_server` defines `do_POST` and no `do_GET`, so the legacy
    HTTP+SSE transport -- a long-lived `GET /sse` stream plus a separate
    `POST /messages` -- cannot exist here. `handle_http_streaming` is the
    current Streamable HTTP shape: one POST that may answer as an event
    stream. Deprecating HTTP+SSE does not touch it.
  * The bridge writes `{"type": "http", "url", "headers": {"Authorization":
    "Bearer ..."}}` into `--mcp-config` (agora-claude-bridge/bridge/cli.py
    `write_mcp_config`). The token is minted by `tools_mcp.grant` inside
    this process. There is no OAuth flow, no `/register` endpoint and no
    `.well-known` metadata, so Dynamic Client Registration is not a thing
    we do and CIMD is not a migration we owe.

So: we use none of them, and the row closes. What these tests protect is
the future version of that sentence -- adding `sampling` to the capability
dict, or a `do_GET` that serves an SSE stream, would put this loop on a
clock that expires around 2027-07-28, and would otherwise do it silently.
"""
import json

import pytest

from agora_runner import invoke_server, nova_replies, tools_mcp

# Deprecated 2026-07-28, functional for at least twelve months after that.
DEPRECATED_CAPABILITIES = ("roots", "sampling", "logging")
DEPRECATED_METHODS = (
    "roots/list",
    "sampling/createMessage",
    "logging/setLevel",
)


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


def _call(method, token, request_id=1):
    body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method}).encode()
    return tools_mcp.handle_http(f"Bearer {token}", body)


def test_initialize_advertises_no_deprecated_capability(token):
    """The capability dict is what puts a server on the clock. A client does
    not send `sampling/createMessage` at a server that never offered it."""
    status, payload = _call("initialize", token)
    assert status == 200 and "error" not in payload, payload
    caps = payload["result"]["capabilities"]
    assert caps, "an empty capabilities dict would make this test vacuous"
    offered = set(caps)
    assert offered & {"tools"}, f"tools is the one we do advertise: {offered}"
    on_the_clock = offered & set(DEPRECATED_CAPABILITIES)
    assert not on_the_clock, (
        f"advertising {sorted(on_the_clock)} puts this server on the "
        "2026-07-28 deprecation clock; see the module docstring"
    )


@pytest.mark.parametrize("method", DEPRECATED_METHODS)
def test_deprecated_methods_are_not_dispatched(method, token):
    """The backstop behind the capability dict: even a client that asks
    anyway gets `method not found` rather than an implementation."""
    status, payload = _call(method, token)
    assert status == 200, status
    assert payload["error"]["code"] == -32601, payload


def test_there_is_no_legacy_http_sse_endpoint():
    """The deprecated transport is a `GET /sse` stream with a separate
    `POST /messages`. Our handler answers POST only, so it is structurally
    the current Streamable HTTP transport and not the retiring one."""
    handler = invoke_server.InvokeHandler
    assert hasattr(handler, "do_POST"), "the handler must still serve POST"
    assert not hasattr(handler, "do_GET"), (
        "a do_GET on this handler is how the legacy HTTP+SSE transport "
        "would arrive; the deprecated transport retires around 2027-07-28"
    )


def test_the_grant_is_a_local_token_not_dynamic_client_registration():
    """DCR is deprecated in favour of CIMD. We owe that migration only if we
    run an OAuth registration endpoint -- `grant` mints a token in process
    memory and the bridge writes it into the client config as a static
    bearer, which is neither."""
    tok = tools_mcp.grant(
        nova_replies.REPLY_PERSONA, nova_replies.REPLY_CAPS, nova_replies.CONVERSATION_ID
    )
    try:
        assert isinstance(tok, str) and tok
        status, payload = _call("tools/list", tok)
        assert status == 200 and "error" not in payload, payload
        # An unregistered token is simply unknown: there is no flow for a
        # client to register itself, which is the whole reason DCR does not
        # apply to us.
        status, payload = _call("tools/list", tok + "x")
        assert status == 401, (status, payload)
    finally:
        tools_mcp.revoke(tok)
