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
            patch.object(nova_site, "audit"), \
            patch.object(nova_site, "_record_stop_timing"):
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


# ---------------------------------------------------------------------------
# the stop timing (issue #240, nova-kr-control-stop-seconds)
# ---------------------------------------------------------------------------

def _stop_with(answer, delay=0.0):
    import threading
    import time
    from agora_runner import nova_site
    handler = nova_site.NovaSiteHandler.__new__(nova_site.NovaSiteHandler)
    handler.headers = {}
    handler._send_json = MagicMock()
    recorded = threading.Event()
    seen = []

    def fake_record(seconds):
        seen.append(seconds)
        recorded.set()

    def slow_cancel(_conversation_id):
        time.sleep(delay)
        return answer

    with patch.object(nova_site, "conversation_cancel", side_effect=slow_cancel), \
            patch.object(nova_site, "audit"), \
            patch.object(nova_site, "_record_stop_timing", side_effect=fake_record):
        handler._post_conversation_cancel({"conversationId": "conv-1"})
        recorded.wait(2)
    return seen


def test_a_stop_that_stopped_a_turn_records_how_long_the_bridge_took():
    seen = _stop_with((True, "stopped"), delay=0.2)
    assert len(seen) == 1
    assert 0.2 <= seen[0] < 2


def test_a_stop_that_found_nothing_running_is_not_an_attempt():
    assert _stop_with((True, "nothing was running")) == []


def test_a_failed_stop_is_not_recorded_as_a_time():
    assert _stop_with((False, "could not reach the bridge")) == []


def test_a_ledger_write_failure_is_logged_and_never_raises():
    from agora_runner import nova_site
    with patch.object(nova_site, "record_stop_timing",
                      side_effect=RuntimeError("couch down")), \
            patch.object(nova_site, "log") as log:
        nova_site._record_stop_timing(1.5)
    assert "1.50s" in log.call_args[0][0]


# ---------------------------------------------------------------------------
# stopping Marcus (issue #239, nova-kr-control-stop-coverage)
# ---------------------------------------------------------------------------

def test_stop_marcus_cancels_the_coach_conversation_on_the_bridge():
    opener = MagicMock(return_value=_Response({"cancelled": 1}))
    with patch.object(nova_conversations, "CLAUDE_BRIDGE_URL", "http://bridge:8090"), \
            patch.object(nova_conversations, "CLAUDE_BRIDGE_TOKEN", "tok"), \
            patch.object(nova_conversations, "MARCUS_COACH_CONVERSATION_ID", "coach-1"), \
            patch.object(nova_conversations.urllib.request, "urlopen", opener):
        result = nova_conversations.stop_marcus()
    assert result == (True, "stopped")
    request = opener.call_args[0][0]
    assert request.full_url == "http://bridge:8090/cancel"
    assert json.loads(request.data) == {"conversation_id": "coach-1"}


def test_the_default_coach_conversation_is_the_one_marcus_runs_in():
    """The id the marcus Deployment carries in MARCUS_COACH_CONVERSATION_ID.
    A different default here would be a Stop button that always answers
    "nothing was running"."""
    assert nova_conversations.MARCUS_COACH_CONVERSATION_ID == \
        "cc484b5a-ad53-420e-93f1-5efacdfb4760"


def _marcus_stop_with(answer):
    from agora_runner import nova_site
    handler = nova_site.NovaSiteHandler.__new__(nova_site.NovaSiteHandler)
    handler.headers = {}
    handler._send_json = MagicMock()
    with patch.object(nova_site, "stop_marcus", return_value=answer) as stop, \
            patch.object(nova_site, "audit"), \
            patch.object(nova_site.threading, "Thread") as thread:
        handler._post_marcus_stop()
    stop.assert_called_once_with()
    return handler._send_json.call_args[0], thread


def test_the_marcus_stop_route_answers_what_the_bridge_stopped():
    (status, body), thread = _marcus_stop_with((True, "stopped"))
    assert status == 200 and body == {"ok": True, "message": "stopped"}
    assert thread.call_args.kwargs["target"].__name__ == "_record_stop_timing"


def test_marcus_with_nothing_running_is_a_success_and_not_a_timed_attempt():
    (status, body), thread = _marcus_stop_with((True, "nothing was running"))
    assert status == 200 and body["ok"] is True
    thread.assert_not_called()


def test_a_marcus_stop_that_could_not_reach_the_bridge_is_a_502():
    (status, body), thread = _marcus_stop_with((False, "could not reach the bridge"))
    assert status == 502 and body["ok"] is False
    thread.assert_not_called()


def test_stop_coverage_reads_all_three_agent_kinds_off_the_real_app():
    from tools import goal_measures
    value, detail = goal_measures.measure_nova_control_stop_coverage(None, None)
    assert value == 100.0, detail
