"""ask_edvard (idea #164, slice 3): the options a persona offers during a turn
ride on that turn's reply -- the last message it posts, and the only one the
Nova app draws live buttons on."""
from unittest.mock import patch

from agora_runner import conversations, heartbeats, pending_options
from agora_runner.config import NO_CAPS
from agora_runner.tools_dispatch import execute_tool
from agora_runner.tools_schemas import client_tool_schemas

PERSONA = {"id": "p1", "name": "Test", "model": "claude-cli:claude-sonnet-5",
           "capabilities": dict(NO_CAPS, novaCapture=True)}
DETAIL = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}], "name": "Test"}


def _posts():
    """A stand-in for Agora's notify route that records every body the real
    `conversations.notify` sends it."""
    bodies = []

    def fake(method, path, payload=None):
        bodies.append(payload)
        return 200, {"message": {"id": f"m{len(bodies)}"}}
    return bodies, fake


def _turn(tool_calls, chunks):
    """A generate_reply that makes `tool_calls` through the real execute_tool,
    the way an MCP call reaches back into this process mid-turn, then streams
    `chunks`, the last one final."""
    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                            sticky=False, on_text=None, on_thinking=None, unattended=True):
        for args in tool_calls:
            execute_tool("ask_edvard", args, persona, conversation_id)
        for i, chunk in enumerate(chunks):
            on_text(chunk, i == len(chunks) - 1)
        return chunks[-1]
    return fake_generate_reply


def _speak(tool_calls, chunks, conversation_id="conv-1"):
    bodies, fake = _posts()
    with patch.object(conversations, "fetch_persona", return_value=PERSONA), \
         patch.object(conversations, "generate_reply", side_effect=_turn(tool_calls, chunks)), \
         patch.object(conversations, "agora_internal", side_effect=fake), \
         patch("agora_runner.tools_dispatch.audit"):
        conversations.speak({"id": conversation_id}, DETAIL, [], "Test")
    return bodies


def test_options_land_on_the_final_chunk_only():
    bodies = _speak([{"options": ["Yes", "No"]}], ["Looking.", "Keep the symbols?"])
    assert "options" not in bodies[0]
    assert bodies[1]["options"] == ["Yes", "No"]
    assert bodies[1]["text"] == "Keep the symbols?"


def test_a_reply_without_the_tool_carries_no_options():
    bodies = _speak([], ["Just an answer."])
    assert "options" not in bodies[0]


def test_options_are_spent_by_the_reply_they_ride_on():
    _speak([{"options": ["Yes", "No"]}], ["Keep them?"])
    assert "options" not in _speak([], ["Done."])[0]


def test_options_left_by_a_failed_turn_do_not_reach_the_next_reply():
    pending_options.offer("conv-1", ["A", "B"])  # a turn that raised before posting
    assert "options" not in _speak([], ["Unrelated."])[0]


def test_a_second_call_replaces_the_first():
    bodies = _speak([{"options": ["A", "B"]}, {"options": ["C", "D", "E"]}], ["Which?"])
    assert bodies[0]["options"] == ["C", "D", "E"]


def test_a_malformed_list_fails_the_tool_call_and_holds_nothing():
    for bad in (["only one"], "Yes", ["a", "a"], ["a", ""], ["a", "two\nlines"], ["a", "x" * 81],
                [1, 2], ["a"] * 7):
        with patch("agora_runner.tools_dispatch.audit"):
            result = execute_tool("ask_edvard", {"options": bad}, PERSONA, "conv-bad")
        assert result.startswith("FAILED:"), bad
        assert pending_options.take("conv-bad") is None, bad


def test_a_heartbeat_reply_carries_the_options_too():
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-hb",
                 "schedule": "every@1h", "name": "HB"}
    sent = []

    def fake_generate_reply(persona, caps, system, history, conversation_id, **kwargs):
        execute_tool("ask_edvard", {"options": ["Merge", "Wait"]}, persona, conversation_id)
        return "Merge it now?"

    with patch.object(heartbeats, "fetch_persona", return_value=PERSONA), \
         patch.object(heartbeats, "agora_get", return_value=(200, {"personas": [], "messages": []})), \
         patch.object(heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(heartbeats, "notify", side_effect=lambda *a, **k: sent.append(k) or 200), \
         patch.object(heartbeats, "audit"), \
         patch("agora_runner.tools_dispatch.audit"), \
         patch.object(heartbeats, "agora_internal", return_value=(200, {})):
        heartbeats.run_heartbeat(heartbeat)
    assert [k.get("options") for k in sent if "options" in k] == [["Merge", "Wait"]]


def test_the_tool_is_offered_with_nova_capture_and_not_without():
    names = lambda caps: {t["name"] for t in client_tool_schemas(caps)}
    assert "ask_edvard" in names(dict(NO_CAPS, novaCapture=True))
    assert "ask_edvard" not in names(dict(NO_CAPS))
