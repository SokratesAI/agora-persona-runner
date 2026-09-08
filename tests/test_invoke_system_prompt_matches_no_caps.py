"""Ask's system prompt must be built from the capabilities Ask actually grants.

`/invoke` is tool-less by design (Decisions/0005): it hands `generate_reply`
a `dict(NO_CAPS)`, so no tool of any kind is offered to the model. The
system prompt was built from the *persona* instead, and a persona that
curates a conversation elsewhere carries real capabilities. Marcus's coach
-- Ask's only production caller -- has `vaultRead: true`, so every Ask turn
opened with a "## Vault access" section naming `save_memory` and eight
vault query tools that the same turn withheld.

Measured live on 2026-09-08 against the running coach, twice: asked "kan du
huske dette?" at the end of a long self-introduction, the whole reply was
the string `save_memory`; asked to consult its notes, the whole reply was
`**1 tool use**`. That is the CLI's placeholder for a tool call standing in
for the answer, and it is exactly what Edvard reported -- a reply that says
it will check its notes "but never did anything more".

The assertion is on the produced prompt rather than on the argument
`build_system` was handed, because the second is the fix re-spelled and
would pass against any implementation that merely renamed the variable.
"""

import json
from unittest.mock import patch

from agora_runner import invoke_server


COACH = {
    "id": "bb2567cd-c523-4456-8303-7d58d5189cbd",
    "name": "Marcus — coach",
    "personality": "You are Marcus, Edvard's strength coach.",
    "model": "claude-cli:claude-sonnet-5",
    "thinking": False,
    "sharedMemory": "",
    # The live values, read off the running persona on 2026-09-08.
    "capabilities": {"webSearch": True, "vaultRead": True, "vaultWrite": False},
}


def _ask(persona):
    """Drive /invoke to the point where the system prompt is built, and
    return the prompt and the capabilities the turn was given."""
    handler = invoke_server.InvokeHandler.__new__(invoke_server.InvokeHandler)
    handler.path = "/invoke"
    handler.headers = {}
    handler._send = lambda status, payload: None
    body = json.dumps({
        "personaId": persona["id"],
        "conversationId": "cc484b5a-ad53-420e-93f1-5efacdfb4760",
        "messages": [{"role": "user", "content": "kan du huske dette?"}],
    }).encode()
    seen = {}

    def fake_generate_reply(_persona, caps, system, *args, **kwargs):
        seen["caps"] = caps
        seen["system"] = system
        return "ok"

    with patch.object(invoke_server, "AGORA_TOKEN", ""), \
            patch.object(invoke_server, "_read_body_unused", None, create=True), \
            patch.object(invoke_server, "fetch_persona", lambda _id: persona), \
            patch.object(invoke_server, "generate_reply", fake_generate_reply), \
            patch.object(invoke_server.InvokeHandler, "_read_body", lambda _self: body):
        handler._handle_post()
    return seen


def test_ask_does_not_offer_the_persona_s_vault_tools():
    """The turn grants nothing, so the prompt may promise nothing."""
    seen = _ask(dict(COACH))
    assert seen["caps"] == dict(invoke_server.NO_CAPS)
    assert "save_memory" not in seen["system"]
    assert "Vault access" not in seen["system"]


def test_the_persona_s_own_prose_still_reaches_the_model():
    """Stripping the capabilities must not strip the personality with them
    -- that would make Ask answer as a generic assistant, which is a worse
    failure than the one being fixed and would not be caught by the test
    above."""
    seen = _ask(dict(COACH))
    assert "You are Marcus, Edvard's strength coach." in seen["system"]


def test_the_persona_object_is_not_mutated():
    """`fetch_persona` caches and shares the persona (see the model-override
    note beside this call), so the strip has to be a copy."""
    persona = dict(COACH)
    _ask(persona)
    assert persona["capabilities"]["vaultRead"] is True
