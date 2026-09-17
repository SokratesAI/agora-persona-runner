"""A message the owner sends in the chat dock stays on screen until the server has it.

Issue #233's spike (agora-persona-runner#1200) showed the vanishing message is
a data bug, not a rendering one: a poll that lands before the server has stored
his send repaints the thread without it, under any framework. These tests cut
`mergePendingSends` out of the shipped `app.js` by brace matching and run it
under `node`, so they exercise the real function rather than a copy of it.
"""

import json
import shutil
import subprocess
import textwrap

import pytest

from tests.test_heartbeats_page_polls import APP_JS, extract_function, extract_var

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

SENT_AT = 1_790_000_000_000  # ms


def merge(messages, pending, now):
    source = APP_JS.read_text(encoding="utf-8")
    script = "\n".join([
        extract_var(source, "OWNER_RECORD"),
        extract_var(source, "PENDING_SEND_SKEW_MS"),
        extract_var(source, "PENDING_SEND_EXPIRES_MS"),
        extract_function(source, "mergePendingSends"),
        "var r = mergePendingSends(%s, %s, %d);" % (json.dumps(messages), json.dumps(pending), now),
        "console.log(JSON.stringify(r));",
    ])
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def iso(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


NOVA = {"sender": "Nova", "text": "Earlier answer", "createdAt": iso(SENT_AT - 60_000)}


def test_a_poll_without_his_send_still_draws_it():
    r = merge([NOVA], [{"text": "hello", "sentAt": SENT_AT}], SENT_AT + 4_000)
    assert [m["text"] for m in r["messages"]] == ["Earlier answer", "hello"]
    assert r["messages"][-1]["sender"] == "Edvard"
    assert r["unconfirmed"] is True
    assert len(r["pending"]) == 1


def test_the_server_echo_settles_it_without_a_duplicate():
    echoed = {"sender": "Edvard", "text": "hello\n", "createdAt": iso(SENT_AT + 500)}
    r = merge([NOVA, echoed], [{"text": "hello", "sentAt": SENT_AT}], SENT_AT + 8_000)
    assert [m["text"] for m in r["messages"]] == ["Earlier answer", "hello\n"]
    assert r["unconfirmed"] is False
    assert r["pending"] == []


def test_an_older_identical_message_does_not_settle_a_new_send():
    old = {"sender": "Edvard", "text": "ok", "createdAt": iso(SENT_AT - 3_600_000)}
    r = merge([old, NOVA], [{"text": "ok", "sentAt": SENT_AT}], SENT_AT + 4_000)
    assert r["unconfirmed"] is True
    assert [m["text"] for m in r["messages"]] == ["ok", "Earlier answer", "ok"]


def test_one_echo_settles_only_one_of_two_identical_sends():
    echoed = {"sender": "Edvard", "text": "ok", "createdAt": iso(SENT_AT + 500)}
    pending = [{"text": "ok", "sentAt": SENT_AT}, {"text": "ok", "sentAt": SENT_AT + 2_000}]
    r = merge([echoed], pending, SENT_AT + 4_000)
    assert len(r["pending"]) == 1
    assert [m["text"] for m in r["messages"]] == ["ok", "ok"]


def test_novas_message_with_the_same_text_does_not_settle_his_send():
    nova_ok = {"sender": "Nova", "text": "ok", "createdAt": iso(SENT_AT + 500)}
    r = merge([nova_ok], [{"text": "ok", "sentAt": SENT_AT}], SENT_AT + 4_000)
    assert r["unconfirmed"] is True


def test_a_send_the_server_never_shows_expires_after_ten_minutes():
    r = merge([NOVA], [{"text": "hello", "sentAt": SENT_AT}], SENT_AT + 600_001)
    assert r["unconfirmed"] is False
    assert r["pending"] == []
    assert [m["text"] for m in r["messages"]] == ["Earlier answer"]


def test_the_dock_wires_the_merge_into_send_paint_and_poll():
    source = APP_JS.read_text(encoding="utf-8")
    paint = source[source.index("    function paint(payload) {"):]
    paint = paint[: paint.index("renderAskThread(thread, payload")]
    assert "mergePendingSends(messages, pendingSends[key], Date.now())" in paint
    assert "if (merged.unconfirmed) {\n        messages = merged.messages;" in paint
    poll = extract_function(source[source.index("    function pollChat("):], "pollChat")
    assert "(pendingSends[sourceKey()] || []).length" in poll
    # One clock reading for the pending send and the bubble, so they share a key.
    assert "var sentAt = Date.now();" in source
    assert "{ text: body, sentAt: sentAt }" in source
    assert "askPaintSent(thread, body, sentAt);" in source
