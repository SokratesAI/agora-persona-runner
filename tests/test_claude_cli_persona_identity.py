"""Idea #165: the bridge never learned which persona it was running.

The measurement behind the row is that all three of Agora's memory stores
are empty -- persona `sharedMemory` 0 bytes on all 18 personas, conversation
notes 0 of 594, and the CLI's own file-based memory pinned only when the
opening message is a Nova cycle. The last of those three is the one this
file is about, and it was not a missing feature: the bridge pinned a memory
directory for a cycle and `None` for everything else, because the request
carried no identity to key one on. Sending it is the whole change here.
"""
import importlib

import pytest


def _send(persona):
    sent = {}

    def fake_http_json(method, url, payload=None, headers=None, **kw):
        sent.update(payload or {})
        return 200, {"text": "ok", "session_id": "s"}

    from agora_runner.providers import claude_cli
    importlib.reload(claude_cli)
    claude_cli.http_json = fake_http_json
    claude_cli.grant_tool_activity = lambda *a, **k: ""
    claude_cli.grant_mcp = lambda *a, **k: ""
    claude_cli.claude_cli_generate(
        "claude-cli:claude-opus-5", None, "sys",
        [{"role": "user", "content": "hi"}], {}, persona, "conv-1")
    return sent


def test_the_persona_id_reaches_the_bridge_request_body():
    sent = _send({"name": "Vergil", "id": "7c4a4506-897f-4a50-9682-968c59235800"})
    assert sent["persona_id"] == "7c4a4506-897f-4a50-9682-968c59235800"


def test_two_personas_send_two_different_ids():
    """The directory the bridge derives is per persona, so an id that were
    constant -- a name, a conversation, a default -- would hand two personas
    one memory and make each one's notes a standing instruction to the
    other. That failure is silent and only visible weeks later."""
    a = _send({"name": "A", "id": "aaaaaaaa-0000-0000-0000-000000000000"})
    b = _send({"name": "B", "id": "bbbbbbbb-0000-0000-0000-000000000000"})
    assert a["persona_id"] != b["persona_id"]


@pytest.mark.parametrize("persona", [{"name": "No id"}, {"name": "Null", "id": None}])
def test_a_persona_with_no_id_sends_an_empty_string_not_null(persona):
    """The bridge turns this into a directory name. `None` would arrive as
    JSON null and go through `str()` on the far side as the literal "None",
    which is a perfectly valid directory -- shared by every persona that
    has no id."""
    sent = _send(persona)
    assert sent["persona_id"] == ""


def test_nova_sends_no_persona_id_so_its_memory_does_not_split():
    """Nova is an ordinary persona with an ordinary id, and the bridge picks
    its memory directory off the heartbeat text rather than off an id. A
    non-heartbeat turn addressed to Nova -- the owner replying in a live Nova
    conversation -- would therefore be pinned to `persona-memory/<nova id>`,
    a second working memory that no cycle reads and that never sees a
    cycle's notes. Sending nothing leaves that turn as inert as it was
    before this field existed."""
    from agora_runner.config import NOVA_PERSONA_ID
    sent = _send({"name": "Nova", "id": NOVA_PERSONA_ID})
    assert sent["persona_id"] == ""
    other = _send({"name": "Vergil", "id": "cccccccc-0000-0000-0000-000000000000"})
    assert other["persona_id"] == "cccccccc-0000-0000-0000-000000000000"
