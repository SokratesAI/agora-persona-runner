"""The chat dock reads one key off every write; every write must answer under it.

`app.js` has exactly one writer for the chat dock -- `chatWrite` -- and it
returns a single key off the JSON body for every route it posts to. Five
routes go through it. Four of them are answered by the shared
`_conversation_write`, which sends `{"ok", "result", "message"}`; the fifth,
`/api/conversations/new`, was written on its own and sent the id under
`conversationId` instead.

So a conversation he started really was created, the route really answered
200, and the page still got `undefined` back. `switchTo` then opened
`/api/conversations/thread?id=undefined`, which is a 404, and the composer
under it refused his first message with "conversationId and text must be
strings". That is what the owner photographed on 2026-08-27 08:47 and filed
'Not able to start new converssations from the Nova app' at 🔴 Immediately.

This is `test_post_allowlist.py`'s failure one layer over: two halves of one
contract written apart, each correct on its own, and no test between them
because the Python tests call handlers and the browser tests mock `fetch`.
The fix for the *shape* is to derive both halves from the source -- the key
from `app.js`, the routes from the `chatWrite(` calls in it -- and drive a
real POST through `do_POST` for each one. A sixth route added to the dock
fails here rather than on his phone.
"""
import json
import pathlib
import re

import pytest

import agora_runner.nova_site as nova_site
from tests.test_nova_site import _post

APP_JS = pathlib.Path(nova_site.__file__).parent / "nova_public" / "app.js"


def _chat_write_source():
    text = APP_JS.read_text()
    start = text.index("function chatWrite(")
    # The body ends at the first line that closes the function at its own
    # indentation -- `chatWrite` is nested, so a bare "}" would match early.
    end = text.index("\n    }\n", start)
    return text[start:end]


def chat_write_key():
    """The one JSON key `chatWrite` hands its callers."""
    match = re.search(r"return\s+result\.(\w+);", _chat_write_source())
    assert match, "chatWrite no longer returns a key off the result body"
    return match.group(1)


def chat_write_paths():
    """Every route the dock posts through `chatWrite`."""
    return sorted(set(re.findall(r'chatWrite\("([^"]+)"', APP_JS.read_text())))


# A minimally valid body per route, and the argument-taking store function
# each one calls. The store is stubbed: this test is about the envelope the
# handler puts around a success, not about what the store does with it.
ROUTE_FIXTURES = {
    "/api/conversations/new": ({"name": "STUFF", "personaId": "p1"}, "conversation_create"),
    "/api/conversations/rename": ({"id": "c1", "name": "STUFF"}, "conversation_rename"),
    "/api/conversations/move": ({"id": "c1", "folderId": "f1"}, "conversation_move"),
    "/api/conversations/model": ({"id": "c1", "model": "claude-cli:claude-sonnet-5"},
                                 "conversation_set_model"),
    "/api/conversations/folder": ({"name": "Nova"}, "conversation_folder_create"),
    "/api/conversations/delete": ({"id": "c1"}, "conversation_remove"),
}

MADE = "conv-9f2b"


@pytest.fixture
def stubbed_store(monkeypatch):
    for _, store in ROUTE_FIXTURES.values():
        monkeypatch.setattr(nova_site, store, lambda *a, **k: (True, MADE))
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: None)


def test_every_chat_write_route_has_a_fixture():
    """A new route in the dock has to be described here before it can be tested."""
    assert set(chat_write_paths()) == set(ROUTE_FIXTURES)


@pytest.mark.parametrize("path", sorted(ROUTE_FIXTURES))
def test_chat_write_route_answers_under_the_key_the_page_reads(path, stubbed_store):
    payload, _ = ROUTE_FIXTURES[path]
    status, _, body = _post(path, payload)
    assert status == 200
    answered = json.loads(body)
    key = chat_write_key()
    assert answered.get("ok") is True
    assert key in answered, (
        f"{path} answered with {sorted(answered)}; the page reads result.{key} "
        f"and would hand its caller undefined"
    )
    assert answered[key] == MADE


def test_new_conversation_hands_the_page_the_id_it_navigates_to(stubbed_store):
    """The regression itself, named rather than only covered by the sweep above.

    `switchTo({kind: "conv", id: <this>})` is the next thing the page does,
    so an id that is not a usable string is a 404 he cannot get out of
    except by reloading.
    """
    status, _, body = _post("/api/conversations/new", {"name": "STUFF", "personaId": "p1"})
    assert status == 200
    handed = json.loads(body)[chat_write_key()]
    assert isinstance(handed, str) and handed == MADE


def test_a_refused_write_hands_the_page_nothing_to_navigate_to(monkeypatch):
    """`ok: false` must not carry a truthy id -- the page throws on the message."""
    monkeypatch.setattr(nova_site, "conversation_create", lambda *a, **k: (False, "pick who to talk to"))
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: None)
    status, _, body = _post("/api/conversations/new", {"name": "STUFF", "personaId": "p1"})
    answered = json.loads(body)
    assert status == 400
    assert answered["ok"] is False
    assert not answered[chat_write_key()]
