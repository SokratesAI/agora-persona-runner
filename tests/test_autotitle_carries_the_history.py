"""The autotitle route carries his earlier messages, and validates them.

`issues.md` #139, part three: a thread should keep adjusting as the topic
shifts. `nova_conversations.autotitle` decides that from `recent` -- his own
earlier messages in the thread -- and `recent` is a list, where every other
field on this route is a string.

`_conversation_write` validates strings and nothing else, so a list handed
to it would pass the type check by not being a string at all and land in the
store function unchecked. That is why the route checks it itself, and why
that check is pinned here rather than left to the shared body: a wrong type
reaching `topic_words` raises, and the handler turns a raise into a 502,
which tells him the store failed when the page sent nonsense.
"""
import json

import agora_runner.nova_site as nova_site
from tests.test_nova_site import _post

SEEN = []


def _stub(monkeypatch):
    SEEN.clear()

    def fake(conversation_id, current_name, text, recent=None):
        SEEN.append((conversation_id, current_name, text, recent))
        return True, "Some title"

    monkeypatch.setattr(nova_site, "conversation_autotitle", fake)


def test_the_history_reaches_the_store_function(monkeypatch):
    _stub(monkeypatch)
    status, _head, body = _post("/api/conversations/autotitle", {
        "id": "c1", "name": "The NAS backup", "text": "Pick a palette",
        "recent": ["Can you look at the NAS backup?", "Which job is it"],
    })
    assert status == 200 and json.loads(body)["ok"] is True
    assert SEEN == [("c1", "The NAS backup", "Pick a palette",
                     ["Can you look at the NAS backup?", "Which job is it"])]


def test_a_page_that_sends_no_history_still_works(monkeypatch):
    """The older page, and the opening message on this one. `recent` is
    absent, not empty, and the route must not refuse it."""
    _stub(monkeypatch)
    status, _head, body = _post("/api/conversations/autotitle", {
        "id": "c1", "name": "New chat", "text": "Can you look at the backup?",
    })
    assert status == 200 and json.loads(body)["ok"] is True
    assert SEEN == [("c1", "New chat", "Can you look at the backup?", [])]


def test_a_history_that_is_not_a_list_of_strings_is_his_fault_not_the_stores(monkeypatch):
    _stub(monkeypatch)
    for bad in ("one message", [1, 2], [None], {"a": "b"}):
        status, _head, body = _post("/api/conversations/autotitle", {
            "id": "c1", "name": "New chat", "text": "Hello there",
            "recent": bad,
        })
        assert status == 400, f"{bad!r} answered {status}"
        assert "recent" in json.loads(body)["error"]
    assert SEEN == []
