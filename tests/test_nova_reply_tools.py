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
import base64
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
def test_the_reply_turn_is_not_offered_anything_that_changes_the_world(forbidden):
    """Advertisement only -- *offered*, not *reachable*, and the name has to
    say which.

    It used to be called `..._cannot_reach_...`, and that name was false at
    the commit that introduced it: `tools/call` dispatched on the tool name
    with no capability check, so the reply grant could call `terminal_exec`
    despite this test passing. Reachability is pinned by
    `test_a_tool_that_was_not_granted_cannot_be_called_by_name` below, which
    is a different assertion against a different layer.

    Named one by one rather than as a count: a count passes the moment
    someone adds a tool and removes another, and the whole point of this
    list is that each entry is a specific thing a comment must not do.
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
        # `vault_read` reads through `vault_read_path_rev` since the tool
        # belt started remembering what revision it read at, so that is the
        # reference the module actually uses.
        with patch("agora_runner.tools_dispatch.vault_read_path_rev",
                   return_value=("We are Nova.", "3-abc")), \
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


def test_a_capture_that_did_not_write_is_reported_as_an_error():
    """A failed capture must not read as a filed one.

    `execute_tool` returns one string and has no channel for "this did not
    work", so a 409 or a missing target file arrived at the model looking
    exactly like a success -- while the system prompt tells it to file the
    thing and then tell Edvard it filed it. The reviewer subagent found
    this; it is the one finding of four that was not already fixed.
    """
    token = tools_mcp.grant(
        nova_replies.REPLY_PERSONA, nova_replies.REPLY_CAPS, nova_replies.CONVERSATION_ID
    )
    try:
        with patch("agora_runner.tools_dispatch.capture_to_backlog",
                   return_value=(False, "could not write to issues: 409")), \
                patch("agora_runner.tools_dispatch.audit"):
            _, payload = tools_mcp.handle_http(
                f"Bearer {token}",
                json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "nova_capture",
                               "arguments": {"target": "issues", "text": "x"}},
                }).encode(),
            )
    finally:
        tools_mcp.revoke(token)
    result = payload["result"]
    assert result["isError"] is True
    assert "409" in result["content"][0]["text"]


def test_a_capture_that_did_write_is_not_reported_as_an_error():
    """The other direction: marking everything an error would pass the test
    above and make the flag useless."""
    token = tools_mcp.grant(
        nova_replies.REPLY_PERSONA, nova_replies.REPLY_CAPS, nova_replies.CONVERSATION_ID
    )
    try:
        with patch("agora_runner.tools_dispatch.capture_to_backlog",
                   return_value=(True, "captured to issues")), \
                patch("agora_runner.tools_dispatch.audit"):
            _, payload = tools_mcp.handle_http(
                f"Bearer {token}",
                json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "nova_capture",
                               "arguments": {"target": "issues", "text": "x"}},
                }).encode(),
            )
    finally:
        tools_mcp.revoke(token)
    assert payload["result"]["isError"] is False
    assert payload["result"]["content"][0]["text"] == "captured to issues"


def test_the_capture_tool_offers_every_target_the_backend_accepts():
    """The second capture entry point, and the one that went stale.

    `nova_capture` is the tool a journal-comment reply uses to file a line
    for Edvard, and its schema carried a literal `["issues", "ideas"]`.
    Adding `notes` to CAPTURE_TARGETS shipped a working button on the site
    and a reply turn that could still only offer his two old files -- the
    exact two-way choice he asked three times to be rid of, with nothing
    red anywhere. Found by a reviewer, not by a test; this is the test.
    """
    from agora_runner.nova_capture import CAPTURE_TARGETS
    from agora_runner.tools_schemas import client_tool_schemas

    tools = client_tool_schemas({"novaCapture": True})
    schema = next(t for t in tools if t["name"] == "nova_capture")
    assert schema["input_schema"]["properties"]["target"]["enum"] == sorted(CAPTURE_TARGETS)


def test_an_attached_image_reaches_the_reply_turn_as_a_picture():
    """The whole point: bytes in the vault must arrive as image content.

    Edvard attached a screenshot to a comment on 2026-08-21 and the instant
    reply under it said "I can't see images in this chat" while the bytes
    were already stored and being served at HTTP 200. The hourly cycle got
    a fix that same day (`tools.fetch_attachments`, PR #278); this lane did
    not, because it is `restricted` and has no file access at all. MCP is
    the one channel it does have that can carry a picture, so the assertion
    that matters is the shape of the content array -- a text block the
    model can always read, and a real `image` block beside it.
    """
    png = b"\x89PNG\r\n\x1a\n" + b"not really a png, but real bytes"
    token = tools_mcp.grant(
        nova_replies.REPLY_PERSONA, nova_replies.REPLY_CAPS, nova_replies.CONVERSATION_ID
    )
    try:
        with patch("agora_runner.tools_dispatch.read_upload",
                   return_value=("image/png", png)) as reader, \
                patch("agora_runner.tools_dispatch.audit"):
            status, payload = tools_mcp.handle_http(
                f"Bearer {token}",
                json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "nova_read_image",
                               "arguments": {"name": "/api/upload/abc.png"}},
                }).encode(),
            )
    finally:
        tools_mcp.revoke(token)

    assert status == 200
    result = payload["result"]
    assert result["isError"] is False
    kinds = [block["type"] for block in result["content"]]
    assert kinds == ["text", "image"], f"reply turn got {kinds}, not a picture"
    image = result["content"][1]
    assert image["mimeType"] == "image/png"
    assert base64.b64decode(image["data"]) == png
    # The model copies the name out of markdown it was shown, so the tool
    # strips `/api/upload/` rather than making it round-trip to be told to.
    # Asserting only on the returned image left that stripping unpinned --
    # the reviewer disabled it and this test still passed, because a mocked
    # `read_upload` answers the same whatever it is handed.
    reader.assert_called_once_with("abc.png")


def test_the_upload_name_is_extracted_from_whatever_shape_he_pasted():
    """A bare name, a URL, and a whole markdown line must all resolve.

    `read_upload` refuses anything that is not `<32 hex>.<ext>`, so a name
    that arrives still wrapped in markdown is not a cosmetic problem -- it
    is a failed read reported to Edvard as "no image stored under that".
    """
    from agora_runner.tools_dispatch import execute_tool
    name = "89f92e607e3e8a3e85a40b40f4a07609.jpg"
    for pasted in (name, f"/api/upload/{name}", f"![1000031053.jpg](/api/upload/{name})"):
        with patch("agora_runner.tools_dispatch.read_upload",
                   return_value=("image/jpeg", b"\xff\xd8\xff")) as reader, \
                patch("agora_runner.tools_dispatch.audit"):
            execute_tool("nova_read_image", {"name": pasted},
                         nova_replies.REPLY_PERSONA, nova_replies.CONVERSATION_ID)
        reader.assert_called_once_with(name), f"{pasted!r} did not resolve"


def test_an_upload_the_vault_cannot_fully_read_is_not_reported_as_seen():
    """A half-stored upload must not arrive looking like a picture.

    An upload is ~450KB of base64 spread over many chunk documents, so a
    partly-missing one is the realistic failure. `read_upload` raises, and
    without an explicit catch that lands in `execute_tool`'s outer handler
    as `[tool error: ...]` -- which carries no `FAILED` prefix, so
    `tools_mcp` reports `isError: false` and the model is told a read
    worked when it did not. Reviewer finding on #280.
    """
    from agora_runner.tools_dispatch import execute_tool
    from agora_runner.vault import VaultIncompleteDocument
    with patch("agora_runner.tools_dispatch.read_upload",
               side_effect=VaultIncompleteDocument("3 of 9 chunks missing")), \
            patch("agora_runner.tools_dispatch.audit"):
        out = execute_tool("nova_read_image", {"name": "abc.png"},
                           nova_replies.REPLY_PERSONA, nova_replies.CONVERSATION_ID)
    assert out.startswith("FAILED"), f"reported as success: {out!r}"
    assert "chunks missing" in out


def test_a_missing_image_is_an_error_not_a_confident_blank():
    """`read_upload` returns None for a name that is not ours or not there.

    Reported as a success it would reach the model as an empty-ish text
    block, and the model would then tell Edvard something about a picture
    it never saw. `FAILED` is the convention `tools_mcp` already maps onto
    `isError`, so this rides the existing channel rather than adding one.
    """
    token = tools_mcp.grant(
        nova_replies.REPLY_PERSONA, nova_replies.REPLY_CAPS, nova_replies.CONVERSATION_ID
    )
    try:
        with patch("agora_runner.tools_dispatch.read_upload", return_value=None), \
                patch("agora_runner.tools_dispatch.audit"):
            status, payload = tools_mcp.handle_http(
                f"Bearer {token}",
                json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "nova_read_image", "arguments": {"name": "nope.png"}},
                }).encode(),
            )
    finally:
        tools_mcp.revoke(token)
    assert payload["result"]["isError"] is True
    assert "nope.png" in payload["result"]["content"][0]["text"]


def test_the_prompt_names_the_attachment_so_the_model_cannot_miss_it():
    """A capability in the system prompt is not the same as noticing.

    The failure was never that the tool was absent -- it is that the model
    reads `![x.jpg](/api/upload/89f9….jpg)` as a link it cannot open and
    apologises. So the user message names the file and says to call the
    tool, and this pins that the argument it names is the upload name
    rather than the markdown alt text.
    """
    entry = {"cycle": 303, "body": "Did a thing.", "pr": "none", "outcome": "shipped"}
    thread = [{"stamp": "2026-08-21 16:06",
               "text": "![1000031053.jpg](/api/upload/89f92e607e3e8a3e85a40b40f4a07609.jpg)"}]
    prompt = nova_replies.build_prompt(entry, thread, "2026-08-21 16:06")
    assert "nova_read_image" in prompt
    assert "89f92e607e3e8a3e85a40b40f4a07609.jpg" in prompt


def test_a_comment_with_no_attachment_says_nothing_about_images():
    """The nudge above must not fire on every comment.

    Without this, the fix for "he never looks at the image" becomes "he
    hunts for an image that is not there", which costs a tool call and a
    round trip on every ordinary comment Edvard writes.
    """
    entry = {"cycle": 303, "body": "Did a thing.", "pr": "none", "outcome": "shipped"}
    thread = [{"stamp": "2026-08-21 16:06", "text": "Looks good, thanks."}]
    prompt = nova_replies.build_prompt(entry, thread, "2026-08-21 16:06")
    assert "nova_read_image" not in prompt


def test_nova_read_image_on_a_text_file_returns_its_text_not_a_picture():
    """He can attach a `.log` now, and the vision API cannot take one.

    Reviewer finding on #283: the branch existed and nothing exercised it.
    Handing a non-image to `ToolImage` is a rejected request rather than a
    picture, so a text file comes back as text and anything else says what
    it is.
    """
    from agora_runner.tools_dispatch import execute_tool
    name = "0123456789abcdef0123456789abcdef.log"
    with patch("agora_runner.tools_dispatch.read_upload",
               return_value=("text/plain", b"traceback: boom")), \
            patch("agora_runner.tools_dispatch.audit"):
        result = execute_tool("nova_read_image", {"name": name},
                              nova_replies.REPLY_PERSONA, nova_replies.CONVERSATION_ID)
    assert isinstance(result, str)
    assert "traceback: boom" in result

    with patch("agora_runner.tools_dispatch.read_upload",
               return_value=("application/pdf", b"%PDF-1.4")), \
            patch("agora_runner.tools_dispatch.audit"):
        result = execute_tool("nova_read_image", {"name": name},
                              nova_replies.REPLY_PERSONA, nova_replies.CONVERSATION_ID)
    assert isinstance(result, str)
    assert result.startswith("FAILED")
    assert "application/pdf" in result
