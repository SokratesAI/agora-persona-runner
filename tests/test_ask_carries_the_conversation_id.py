"""Ask has never worked on a `claude-cli:` persona, and the reason was one
hardcoded `None`.

`POST /conversations/:id/ask` reaches this repo as `POST /invoke`, which
called `generate_reply(..., None, ...)` for the `conversation_id` argument.
The `claude_cli` provider sends that on to the bridge, and the bridge refuses
a request without one -- 400 "conversation_id and prompt (or attachments) are
required" -- so the runner returned 500 and Agora returned 502. Measured live
twice against the deployed Marcus coach on 2026-09-06. Ask therefore only
ever worked on the `anthropic:` models, which need no conversation and which
identity.md rule 9 forbids production from using, so nothing subscription-
backed had ever exercised it.

Handing the real id over is necessary and, on its own, would have been worse
than the 502: the bridge resumes the conversation's stored CLI session by
that id and writes the new one back, and `grant_tool_activity` mints a chip
token for any truthy id. A side question would then land inside the
conversation Decisions/0005 says it never touches. So the tests below pin
both halves -- the id arrives, and the turn stays outside the conversation --
and the last one pins that a *heartbeat* turn is unaffected, because a change
that made every turn stateless would satisfy the others.
"""

import json

import pytest

from agora_runner import invoke_server
from agora_runner.providers import claude_cli


class _Reader:
    def __init__(self, body):
        self._body = body

    def read(self, n):
        return self._body[:n]


def _post(payload):
    """Drive /invoke with a body, returning what generate_reply was handed."""
    body = json.dumps(payload).encode()
    handler = invoke_server.InvokeHandler.__new__(invoke_server.InvokeHandler)
    handler.path = "/invoke"
    handler.rfile = _Reader(body)
    handler.headers = {"Content-Length": str(len(body))}
    sent = {}
    handler._send = lambda status, p: sent.update(status=status, payload=p)
    seen = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, **kw):
        seen["conversation_id"] = conversation_id
        seen["kwargs"] = kw
        return "answer"

    original_token = invoke_server.AGORA_TOKEN
    original_reply = invoke_server.generate_reply
    original_system = invoke_server.build_system
    invoke_server.AGORA_TOKEN = ""
    invoke_server.generate_reply = fake_generate_reply
    invoke_server.build_system = lambda persona: "sys"
    try:
        handler._handle_post()
    finally:
        invoke_server.AGORA_TOKEN = original_token
        invoke_server.generate_reply = original_reply
        invoke_server.build_system = original_system
    return sent, seen


_PREVIEW = {"personality": "p", "model": "claude-cli:claude-sonnet-5", "thinking": False}


def test_ask_forwards_the_conversation_id_agora_sent():
    sent, seen = _post({
        "persona": _PREVIEW,
        "conversationId": "conv-42",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert sent["status"] == 200
    assert seen["conversation_id"] == "conv-42"


def test_preview_has_no_conversation_and_still_sends_none():
    """Preview is the other caller of this route and genuinely has no
    conversation, so the fix must not invent one for it."""
    sent, seen = _post({
        "persona": _PREVIEW,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert sent["status"] == 200
    assert seen["conversation_id"] is None


def test_invoke_marks_the_turn_ephemeral():
    _, seen = _post({
        "persona": _PREVIEW,
        "conversationId": "conv-42",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert seen["kwargs"]["ephemeral"] is True


def _send_turn(monkeypatch, persona, ephemeral):
    """Run one claude_cli turn, returning the body the bridge would receive."""
    sent = {}

    def fake_http_json(method, url, payload=None, headers=None, **kw):
        sent.update(payload or {})
        return 200, {"text": "ok", "session_id": "s"}

    monkeypatch.setattr(claude_cli, "http_json", fake_http_json)
    monkeypatch.setattr(claude_cli, "grant_tool_activity",
                        lambda *a, **k: "activity-token")
    monkeypatch.setattr(claude_cli, "grant_mcp", lambda *a, **k: "")
    claude_cli.claude_cli_generate(
        "claude-sonnet-5", None, "sys", [{"role": "user", "content": "hi"}],
        {}, persona, "conv-42", ephemeral=ephemeral)
    return sent


_OPEN_PERSONA = {"name": "Marcus", "claudeCliRestricted": False,
                 "claudeCliStateless": False}


def test_an_ephemeral_turn_sends_the_id_but_stays_out_of_the_conversation(monkeypatch):
    """The persona's own flags are both off, so every assertion here is the
    ephemeral flag and not the persona."""
    sent = _send_turn(monkeypatch, dict(_OPEN_PERSONA), ephemeral=True)
    assert sent["conversation_id"] == "conv-42"
    assert sent["stateless"] is True
    assert sent["restricted"] is True
    assert "activity" not in sent


def test_a_heartbeat_turn_still_resumes_the_session_and_reports_its_tools(monkeypatch):
    """The same persona on the normal path. Without this, forcing every turn
    stateless, restricted and chip-less would pass the test above."""
    sent = _send_turn(monkeypatch, dict(_OPEN_PERSONA), ephemeral=False)
    assert sent["conversation_id"] == "conv-42"
    assert sent["stateless"] is False
    assert sent["restricted"] is False
    assert sent["activity"]["token"] == "activity-token"


@pytest.mark.parametrize("flag", ["claudeCliRestricted", "claudeCliStateless"])
def test_a_persona_that_opted_in_keeps_its_own_flag_off_the_ask_path(monkeypatch, flag):
    """`or ephemeral` must not become `= ephemeral`."""
    persona = dict(_OPEN_PERSONA)
    persona[flag] = True
    sent = _send_turn(monkeypatch, persona, ephemeral=False)
    key = {"claudeCliRestricted": "restricted",
           "claudeCliStateless": "stateless"}[flag]
    assert sent[key] is True
