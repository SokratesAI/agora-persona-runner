"""The runner's HTTP server reads `Content-Length` bytes before it has
decided anything about the caller, and until now it read however many were
claimed.

Found auditing this platform's MCP server against the NSA AI Security
Center's May 2026 Cybersecurity Information Sheet on MCP (idea #175), which
names content-length checks and denial of service among the things an MCP
deployment owes. `nova_site.py` already held this exact line -- its
`_read_json_body` says "The length is checked *before* the read, not after"
-- and `invoke_server.py`, the sibling server in the same repo running in
the pod with the *tighter* 256Mi limit, did not. Two of its three routes
(/tool-activity, /mcp) authenticate from the body and the header
respectively, which is after the read, and `allow-intra-namespace-ingress`
admits every pod in `agents`.

The assertion that matters in each test below is that `read` was never
called at all, not that a 413 came back: a test that only checks the status
code passes just as well against a server that allocates the gigabyte first
and complains afterwards, which is the whole failure.
"""

import json
from unittest.mock import patch

import pytest

from agora_runner import invoke_server


class _RecordingReader:
    """An rfile that refuses to be read and says how much was asked for."""

    def __init__(self):
        self.asked = []

    def read(self, n):
        self.asked.append(n)
        raise AssertionError(f"the body was read: {n} bytes")


def _handler(path, length):
    handler = invoke_server.InvokeHandler.__new__(invoke_server.InvokeHandler)
    handler.path = path
    handler.rfile = _RecordingReader()
    handler.headers = {"Content-Length": str(length)}
    sent = {}

    def fake_send(status, payload):
        sent["status"] = status
        sent["payload"] = payload

    handler._send = fake_send
    return handler, sent


@pytest.mark.parametrize("path", ["/invoke", "/mcp", "/tool-activity"])
def test_an_oversized_body_is_refused_without_being_read(path):
    """A Content-Length past the cap gets a 413 and no allocation."""
    handler, sent = _handler(path, invoke_server.MAX_REQUEST_BYTES + 1)
    with patch.object(invoke_server, "AGORA_TOKEN", ""):
        handler.do_POST()
    assert handler.rfile.asked == []
    assert sent["status"] == 413


@pytest.mark.parametrize("path", ["/invoke", "/mcp", "/tool-activity"])
def test_a_missing_content_length_is_refused_without_being_read(path):
    """No length is not a zero-length body -- `rfile.read` on a socket with
    no length would block on the peer rather than return."""
    handler, sent = _handler(path, 0)
    with patch.object(invoke_server, "AGORA_TOKEN", ""):
        handler.do_POST()
    assert handler.rfile.asked == []
    assert sent["status"] == 411


def test_the_cap_is_between_the_largest_real_body_and_the_pod_limit():
    """A limit needs a danger and a number I can defend, from both ends.

    Floor: the largest document in this vault is ~534KB
    (`projects/sokrates/projects/nova/ideas.md`, measured 2026-08-31) and
    /mcp carries `vault_write` content, so a cap anywhere near nova_site's
    64KiB would refuse legitimate work.

    Ceiling: this is a `ThreadingHTTPServer`, so the allocation is per
    concurrent request, and the pod's limit is 256Mi. A cap that only had a
    floor would be satisfied by a number that bounds nothing -- my own
    mutation check raised it to 8GiB and every other test here stayed
    green, which is the "a test that agrees with the author either way"
    failure in the review checklist.
    """
    assert 8 * 1024 * 1024 <= invoke_server.MAX_REQUEST_BYTES <= 16 * 1024 * 1024


def test_a_normal_body_still_reaches_the_route():
    """The cap must not be the thing that answers an ordinary request."""
    import io

    handler = invoke_server.InvokeHandler.__new__(invoke_server.InvokeHandler)
    handler.path = "/tool-activity"
    raw = json.dumps({"token": "t", "capability": "vault_read",
                      "detail": "x"}).encode()
    handler.rfile = io.BytesIO(raw)
    handler.headers = {"Content-Length": str(len(raw))}
    sent = {}
    handler._send = lambda status, payload: sent.update(
        status=status, payload=payload)
    with patch.object(invoke_server, "report_tool_activity",
                      lambda *a, **k: True):
        handler.do_POST()
    assert sent["status"] == 202
