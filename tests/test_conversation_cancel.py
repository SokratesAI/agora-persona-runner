"""The stop button, runner half: /api/conversations/cancel and the bridge
client behind it.

The bridge half (agora-claude-bridge#108) holds the CLI subprocess and does
the killing; everything here is about calling it correctly and about telling
him the truth when it could not be called.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from agora_runner import nova_conversations


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _cancel(payload=None, url="http://bridge:8090", token="tok", error=None):
    opener = MagicMock()
    if error is not None:
        opener.side_effect = error
    else:
        opener.return_value = _Response(payload if payload is not None else {"cancelled": 1})
    with patch.object(nova_conversations, "CLAUDE_BRIDGE_URL", url), \
            patch.object(nova_conversations, "CLAUDE_BRIDGE_TOKEN", token), \
            patch.object(nova_conversations.urllib.request, "urlopen", opener):
        result = nova_conversations.cancel("conv-1")
    return result, opener


def test_cancel_posts_the_conversation_to_the_bridge():
    (ok, message), opener = _cancel({"cancelled": 1})
    assert (ok, message) == (True, "stopped")
    request = opener.call_args[0][0]
    assert request.full_url == "http://bridge:8090/cancel"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"conversation_id": "conv-1"}


def test_cancel_sends_the_bridge_token():
    _, opener = _cancel()
    request = opener.call_args[0][0]
    assert request.headers.get("X-bridge-token") == "tok"


def test_cancel_sends_no_token_header_when_none_is_configured():
    """A literal empty token would be rejected by the bridge's own guard,
    which compares the header against BRIDGE_TOKEN rather than checking for
    its presence."""
    _, opener = _cancel(token="")
    request = opener.call_args[0][0]
    assert "X-bridge-token" not in request.headers


def test_nothing_running_is_a_success_not_a_failure():
    """The turn may have finished between him pressing stop and this call
    landing. Telling him the stop failed would be wrong about the only thing
    he cares about: nothing is running now."""
    (ok, message), _ = _cancel({"cancelled": 0})
    assert ok is True
    assert message == "nothing was running"


def test_an_unreachable_bridge_is_a_failure():
    (ok, message), _ = _cancel(error=OSError("connection refused"))
    assert ok is False
    assert "bridge" in message


def test_a_bridge_answer_with_no_count_is_a_failure():
    """A 200 with an unexpected body is not proof anything was stopped, and
    painting the composer back to Send over a turn that is still burning is
    the one outcome worse than the button doing nothing."""
    (ok, _), _ = _cancel({"unexpected": True})
    assert ok is False


def test_no_bridge_configured_is_a_failure_and_never_calls_out():
    opener = MagicMock()
    with patch.object(nova_conversations, "CLAUDE_BRIDGE_URL", ""), \
            patch.object(nova_conversations.urllib.request, "urlopen", opener):
        ok, message = nova_conversations.cancel("conv-1")
    assert ok is False
    opener.assert_not_called()


def test_cancel_without_a_conversation_is_refused_before_the_network():
    opener = MagicMock()
    with patch.object(nova_conversations, "CLAUDE_BRIDGE_URL", "http://bridge:8090"), \
            patch.object(nova_conversations.urllib.request, "urlopen", opener):
        ok, message = nova_conversations.cancel("")
    assert ok is False
    assert message == "which conversation?"
    opener.assert_not_called()


def test_the_timeout_outlasts_the_bridges_own_kill_grace():
    """The bridge SIGTERMs and waits 5s before SIGKILL. A shorter timeout
    here would report a stop that is working as a failure while the kill is
    still landing."""
    assert nova_conversations.CANCEL_TIMEOUT_SECONDS > 5
    _, opener = _cancel()
    assert opener.call_args[1]["timeout"] == nova_conversations.CANCEL_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# the route
# ---------------------------------------------------------------------------

def test_the_route_dispatches_to_the_cancel_handler():
    """That the route is also on `do_POST`'s allowlist is
    tests/test_post_allowlist.py's job -- it derives both lists from the
    source and compares them, so it fails for every route that misses one.
    This only pins that the dispatch arm exists and reaches this handler."""
    from agora_runner import nova_site
    handler = nova_site.NovaSiteHandler.__new__(nova_site.NovaSiteHandler)
    handler.headers = {}
    handler._send_json = MagicMock()
    with patch.object(nova_site, "conversation_cancel",
                      return_value=(True, "stopped")) as stop, \
            patch.object(nova_site, "audit"):
        handler._post_conversation_cancel({"conversationId": "conv-1"})
    stop.assert_called_once_with("conv-1")
    assert handler._send_json.call_args[0][0] == 200
    assert handler._send_json.call_args[0][1] == {"ok": True, "message": "stopped"}


def test_an_unreachable_bridge_is_a_502_to_his_screen():
    from agora_runner import nova_site
    handler = nova_site.NovaSiteHandler.__new__(nova_site.NovaSiteHandler)
    handler.headers = {}
    handler._send_json = MagicMock()
    with patch.object(nova_site, "conversation_cancel",
                      return_value=(False, "could not reach the bridge")), \
            patch.object(nova_site, "audit"):
        handler._post_conversation_cancel({"conversationId": "conv-1"})
    assert handler._send_json.call_args[0][0] == 502


def test_a_missing_conversation_id_is_a_400_and_never_calls_the_bridge():
    from agora_runner import nova_site
    handler = nova_site.NovaSiteHandler.__new__(nova_site.NovaSiteHandler)
    handler.headers = {}
    handler._send_json = MagicMock()
    with patch.object(nova_site, "conversation_cancel") as stop:
        handler._post_conversation_cancel({})
    stop.assert_not_called()
    assert handler._send_json.call_args[0][0] == 400
