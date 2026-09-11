"""Conversation titles: Haiku names a thread once, and on demand from Settings.

His ask, 2026-09-11: *"have my first message analysed by a cheap model who
sets the topic as title"* -- set once -- plus a "generate title" button in the
composer's Settings drawer for a thread that drifts. Until then a derived title
was re-derived whenever the topic "moved", which renamed threads to a clipped
copy of his latest line.
"""
import json
from unittest.mock import patch

import agora_runner.nova_conversations as convs


def _bridge(text="Marcus test deployment", status=200, seen=None):
    def fake(method, url, body=None, headers=None, timeout=30):
        if seen is not None:
            seen.append({"url": url, "body": body, "timeout": timeout})
        return status, {"text": text}
    return fake


def test_the_title_call_spends_the_subscription_and_never_waits_behind_a_cycle():
    """Rule 9 of identity.md: production never spends the metered API. Haiku
    goes through the bridge -- the claude-cli model, stateless, no tools, on
    the lane that skips the cycle lock -- and under an id of its own, so his
    Stop button cannot cancel a title and a title cannot cancel his turn."""
    seen = []
    with patch.object(convs, "http_json", side_effect=_bridge(seen=seen)):
        assert convs.model_title(["Spin up a test deployment"], "c-1") == "Marcus test deployment"
    body = seen[0]["body"]
    assert seen[0]["url"].endswith("/generate")
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["stateless"] is True and body["restricted"] is True
    assert body["allow_concurrent"] is True
    assert body["conversation_id"] == "nova-title:c-1"
    assert seen[0]["timeout"] <= 60


def test_model_output_is_cleaned_into_a_title():
    for raw, want in [
        ('"Marcus test deployment."', "Marcus test deployment"),
        ("Title: Board records\nbecause the store...", "Board records"),
        ("   ", ""),
        ("...", ""),
    ]:
        with patch.object(convs, "http_json", side_effect=_bridge(raw)):
            assert convs.model_title(["x"], "c-1") == want, raw


def test_a_reply_shaped_answer_is_not_a_title():
    """Measured through the real bridge on 2026-09-11, before the prompt was
    fenced: Haiku answered the message instead of labelling it. Those exact
    answers must fall back rather than become his thread's name."""
    for raw in ["I'll help you spin up a test deployment for the Marcus app.",
                "The chat title should be based on the conversation's topic, not",
                "Sure! Marcus deployment",
                "Here's a title for this",
                "One two three four five six seven eight nine"]:
        with patch.object(convs, "http_json", side_effect=_bridge(raw)):
            assert convs.model_title(["x"], "c-1") == "", raw


def test_the_instruction_rides_in_the_user_turn_with_his_text_fenced():
    """`system` alone lost to the CLI's own helpful-agent prompt."""
    seen = []
    with patch.object(convs, "http_json", side_effect=_bridge(seen=seen)):
        convs.model_title(["Spin up a test deployment"], "c-1")
    prompt = seen[0]["body"]["prompt"]
    assert "Do NOT reply" in prompt
    assert "<excerpt>" in prompt and "Spin up a test deployment" in prompt
    assert prompt.rstrip().endswith("Title:")


def test_an_unreachable_bridge_is_no_title_not_an_error():
    def boom(*a, **k):
        raise OSError("connection refused")
    with patch.object(convs, "http_json", side_effect=boom):
        assert convs.model_title(["x"], "c-1") == ""


def test_the_first_message_is_titled_by_haiku():
    with patch.object(convs, "model_title", return_value="Marcus test deployment"), \
            patch.object(convs, "rename", side_effect=lambda cid, name: (True, name)):
        ok, name = convs.autotitle("c-1", "New chat", "Spin up a test deployments for the Marcus app. I want")
    assert (ok, name) == (True, "Marcus test deployment")


def test_the_first_message_falls_back_to_his_opening_line_when_haiku_cannot():
    """A busy bridge must not leave a thread called "New chat"."""
    with patch.object(convs, "model_title", return_value=""), \
            patch.object(convs, "rename", side_effect=lambda cid, name: (True, name)):
        ok, name = convs.autotitle("c-1", "New chat", "Why is the NAS backup failing? It ran fine")
    assert ok and name == "Why is the NAS backup failing?"


def test_a_named_thread_is_never_retitled_by_what_he_says_next():
    """Set once. The drift path that renamed threads to his latest line is gone."""
    called = []
    with patch.object(convs, "model_title", side_effect=lambda *a: called.append(a) or "X"), \
            patch.object(convs, "rename", side_effect=lambda cid, name: (True, name)):
        ok, message = convs.autotitle("c-1", "Marcus test deployment",
                                      "Compare that to whats actually been built",
                                      ["Spin up a test deployment", "and the tests?"])
    assert not ok and message == "that conversation already has a name"
    assert called == [], "Haiku was asked to title a thread that already had one"


def _messages(*texts):
    rows = [{"id": f"m{i}", "sender": "Edvard", "text": t} for i, t in enumerate(texts)]
    rows.insert(1, {"id": "n", "sender": "Nova", "text": "a reply"})
    rows.insert(2, {"id": "a", "sender": "Nova", "text": "", "activity": {"capability": "Bash"}})
    return rows


def test_retitle_reads_his_first_and_most_recent_messages():
    texts = [f"message {i}" for i in range(12)]
    seen = []
    with patch.object(convs, "agora_get", return_value=(200, {"messages": _messages(*texts)})), \
            patch.object(convs, "model_title", side_effect=lambda t, cid: seen.append(t) or "New topic"), \
            patch.object(convs, "rename", side_effect=lambda cid, name: (True, name)):
        assert convs.retitle("c-1") == (True, "New topic")
    sent = seen[0]
    assert sent[0] == "message 0", "the opening message was not sent"
    assert sent[-1] == "message 11", "the latest message was not sent"
    assert "a reply" not in sent, "his title was built from Nova's words"
    assert len(sent) <= 9


def test_retitle_does_not_fall_back_to_a_clipped_latest_line():
    """A mechanical title from his latest line is the exact thing he asked to
    be rid of -- so when Haiku fails, the button says so."""
    with patch.object(convs, "agora_get", return_value=(200, {"messages": _messages("a", "b")})), \
            patch.object(convs, "model_title", return_value=""), \
            patch.object(convs, "rename") as rename:
        ok, message = convs.retitle("c-1")
    assert not ok and "could not write a title" in message
    rename.assert_not_called()


# The route's `result` key is pinned with every other chat write in
# tests/test_chat_write_result_key.py, which is where a new route belongs.
