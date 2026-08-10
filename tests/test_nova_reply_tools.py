"""The journal-card reply turn holds tools, and holds only these tools.

Edvard, on the cycle 86 card: *"I wished you had more read capabilities to
answer questions. And maybe some tools to add issues or report bugs we
find."* Before this the turn had none, and answered three of his questions
with "I don't know" about facts that were sitting in the vault.

What these pin is the boundary rather than the feature. The turn is
triggered by an HTTP POST carrying text he typed, so the set of things a
comment can provoke is a security property, not a convenience -- and it is
an allowlist (`REPLY_CAPS`) rather than a subtraction, which is the part a
future edit could quietly widen without anything failing.
"""
import json

from unittest.mock import patch

import pytest

from agora_runner import nova_replies, nova_site, tools_mcp
from agora_runner.tools_schemas import TOOL_TO_CAPABILITY, client_tool_schemas


def _tool_names(caps):
    return {tool["name"] for tool in client_tool_schemas(caps)}


def test_the_reply_turn_can_read_the_vault_and_file_a_capture():
    """The two halves of what he asked for, and nothing arrives by accident."""
    names = _tool_names(nova_replies.REPLY_CAPS)
    assert "vault_read" in names
    assert "nova_capture" in names


@pytest.mark.parametrize("forbidden", [
    "terminal_exec", "create_pr", "merge_pr", "github_comment",
    "vault_write", "vault_append", "kubectl_read", "github_read",
])
def test_the_reply_turn_cannot_reach_anything_that_changes_the_world(forbidden):
    """Named one by one rather than as a count.

    A count passes the moment someone adds a tool and removes another, and
    the whole point of this list is that each entry is a specific thing a
    comment must not be able to do.
    """
    assert forbidden not in _tool_names(nova_replies.REPLY_CAPS)


def test_no_capability_in_the_reply_grant_is_unknown_to_the_tool_map():
    """A typo'd cap key is silently False everywhere and grants nothing --
    the failure would be a turn that quietly lost its tools, with a reply
    that still reads fine."""
    known = set(TOOL_TO_CAPABILITY.values())
    assert set(nova_replies.REPLY_CAPS) <= known


def test_nova_capture_is_off_unless_it_is_asked_for():
    """It is not part of vaultWrite and not part of any existing persona."""
    assert "nova_capture" not in _tool_names({"vaultWrite": True, "vaultRead": True})


def test_the_bridge_call_carries_an_mcp_grant_that_serves_those_tools():
    """The grant is real: the token handed to the bridge lists these tools.

    Asserting through `tools_mcp.handle` rather than on the request body is
    deliberate -- a token pointing at a grant that does not exist would
    look identical in the body and fail on the first tool call.
    """
    seen = {}

    def fake_post(method, url, body, headers, timeout=None):
        seen.update(body)
        auth = f"Bearer {body['mcp']['token']}"
        status, payload = tools_mcp.handle_http(auth, b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
        seen["listed"] = {t["name"] for t in payload["result"]["tools"]}
        seen["list_status"] = status
        return 200, {"text": "answered"}

    with patch.object(nova_replies, "http_json", fake_post):
        assert nova_replies._generate("sys", "prompt") == "answered"

    assert seen["list_status"] == 200
    assert "vault_read" in seen["listed"]
    assert "nova_capture" in seen["listed"]
    assert "terminal_exec" not in seen["listed"]
    # Back to the site, not to the runner: the grant lives in this
    # process's memory and a callback to the runner would 401 on every call.
    assert seen["mcp"]["url"].endswith("/mcp")
    assert "nova-site" in seen["mcp"]["url"]
    # The CLI's own built-in roster stays blocked -- the tools above are an
    # allowlist on top of that, not a replacement for it.
    assert seen["restricted"] is True


def test_the_grant_is_revoked_even_when_the_bridge_fails():
    """A live token outliving its turn is a standing capability nobody
    asked for. The `finally` is what stops that, and a test that only
    exercises the happy path would not notice it going missing."""
    captured = {}

    def failing_post(method, url, body, headers, timeout=None):
        captured["token"] = body["mcp"]["token"]
        raise RuntimeError("bridge unreachable")

    with patch.object(nova_replies, "http_json", failing_post):
        with pytest.raises(RuntimeError):
            nova_replies._generate("sys", "prompt")

    status, payload = tools_mcp.handle_http(
        f"Bearer {captured['token']}", b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    )
    assert status == 401


def test_an_unknown_mcp_token_reaches_no_tool():
    status, payload = tools_mcp.handle_http(
        "Bearer not-a-real-token", b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}'
    )
    assert status == 401


def test_a_capture_from_a_reply_goes_through_the_same_writer_as_the_box():
    """`nova_capture` the tool is a thin front on `nova_capture.capture` --
    the function that already knows the two target paths, the bullet
    contract and the 409 retry. A second implementation here is the drift
    this repo keeps writing down."""
    with patch("agora_runner.tools_dispatch.capture_to_backlog", return_value=(True, "captured to issues")) as writer, \
            patch("agora_runner.tools_dispatch.audit"):
        from agora_runner.tools_dispatch import execute_tool
        out = execute_tool(
            "nova_capture", {"target": "issues", "text": "the spinner never clears"},
            nova_replies.REPLY_PERSONA, nova_replies.CONVERSATION_ID,
        )
    writer.assert_called_once_with("issues", "the spinner never clears")
    assert out == "captured to issues"


def _post_mcp(payload, auth):
    """One real POST /mcp through the site's own handler.

    Through the socket rather than by calling `_handle_mcp`, because the
    thing that has to hold is the *route*: removing `if path == "/mcp"`
    from `do_POST` left all 1279 other tests green, which is a route pinned
    by nothing. In the pod that mutation is not cosmetic -- every tool call
    from a reply would 404 and the turn would quietly answer without them.
    """
    from tests.test_nova_site import _FakeServer, _FakeSocket

    body = json.dumps(payload).encode()
    request = (
        f"POST /mcp HTTP/1.1\r\nHost: nova\r\nAuthorization: {auth}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    sock = _FakeSocket(request)
    nova_site.NovaSiteHandler(sock, ("127.0.0.1", 50000), _FakeServer())
    head, _, response_body = sock.sent.getvalue().partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), response_body


def test_the_site_serves_mcp_so_a_reply_can_reach_its_tools():
    token = tools_mcp.grant(
        nova_replies.REPLY_PERSONA, nova_replies.REPLY_CAPS, nova_replies.CONVERSATION_ID
    )
    try:
        status, body = _post_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, f"Bearer {token}"
        )
    finally:
        tools_mcp.revoke(token)
    assert status == 200
    names = {t["name"] for t in json.loads(body)["result"]["tools"]}
    assert "vault_read" in names and "nova_capture" in names


def test_the_site_refuses_an_mcp_call_with_no_live_grant():
    status, _ = _post_mcp(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "Bearer nope"
    )
    assert status == 401


def test_a_tool_that_was_not_granted_cannot_be_called_by_name():
    """The grant has to be a gate, not a menu.

    `execute_tool` dispatches on the tool name alone and checks no
    capability, so for as long as `tools/call` trusted the name it was
    handed, filtering `tools/list` was decoration. Measured against a real
    server on this handler before the fix: a {vaultRead, novaCapture} grant
    ran `terminal_exec` and returned `uid=10001(bridge)`.

    Parametrised over the two that matter most differently -- one runs a
    shell, one changes a repo -- because a single case would let a fix that
    special-cased `terminal_exec` pass.
    """
    token = tools_mcp.grant(
        nova_replies.REPLY_PERSONA, nova_replies.REPLY_CAPS, nova_replies.CONVERSATION_ID
    )
    try:
        for forbidden, args in (
            ("terminal_exec", {"command": "id"}),
            ("merge_pr", {"repo": "SokratesAI/agora-persona-runner", "pr_number": 1}),
        ):
            status, payload = tools_mcp.handle_http(
                f"Bearer {token}",
                json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": forbidden, "arguments": args},
                }).encode(),
            )
            assert status == 200
            result = payload["result"]
            assert result["isError"] is True, f"{forbidden} was executed"
            assert "not available" in result["content"][0]["text"]
    finally:
        tools_mcp.revoke(token)


def test_a_granted_tool_still_runs():
    """The gate above must not be a wall. Without this, refusing
    everything would pass the test that matters most."""
    token = tools_mcp.grant(
        nova_replies.REPLY_PERSONA, nova_replies.REPLY_CAPS, nova_replies.CONVERSATION_ID
    )
    try:
        with patch("agora_runner.tools_dispatch.vault_read_path", return_value="We are Nova."), \
                patch("agora_runner.tools_dispatch.audit"):
            status, payload = tools_mcp.handle_http(
                f"Bearer {token}",
                json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "vault_read", "arguments": {"path": "a.md"}},
                }).encode(),
            )
    finally:
        tools_mcp.revoke(token)
    assert payload["result"]["isError"] is False
    assert payload["result"]["content"][0]["text"] == "We are Nova."
