"""`agora_runner.reply_notice` — the push that tells him a cycle went silent.

His issue #105. The tests drive `ReplyWatch` end to end with the clock, the
conversation listing, the heartbeat lookup and the post all injected, so the
thing that actually rings his phone is exercised rather than its parts.
"""
from datetime import datetime, timedelta, timezone

import pytest

from agora_runner import reply_notice
from agora_runner.config import NOVA_PERSONA_ID
from agora_runner.reply_check import Silence, find_silences

NOW = datetime(2026, 8, 31, 16, 30, tzinfo=timezone.utc)


def _stamp(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z")


def _conversation(name, minutes_ago, ident=None, cycle=True):
    return {"id": ident or name.lower().replace(" ", "-"), "name": name,
            "updatedAt": _stamp(minutes_ago), "cycleThread": cycle}


def _narration(text):
    return {"text": text, "partial": True}


def _reply(text):
    return {"text": text, "partial": False}


def _heartbeats(conversation_id="thread-1", push=True):
    """Nova's own cycle heartbeat, as `nova_cycle_heartbeats` matches it."""
    return lambda: [{"id": "hb-nova", "enabled": True, "workflowId": None,
                     "personaId": NOVA_PERSONA_ID,
                     "conversationId": conversation_id,
                     "pushNotifications": push}]


class Recorder:
    """A stand-in for the post, remembering what was sent and answering 200."""

    def __init__(self, status=200):
        self.status = status
        self.sent = []

    def __call__(self, conversation_id, text, push):
        self.sent.append((conversation_id, text, push))
        return self.status


def _watch(conversations, threads, post, **kwargs):
    return reply_notice.ReplyWatch(
        listing=lambda: {"conversations": conversations},
        fetch_thread=lambda cid: threads[cid],
        heartbeats=_heartbeats(),
        post=post,
        clock=lambda: NOW,
        **kwargs)


def _run(watch, interval=None):
    """First tick arms, second tick checks. Returns the second's result."""
    interval = interval or reply_notice.REPLY_CHECK_SECONDS
    watch.tick(now=0.0)
    return watch.tick(now=interval + 1)


# --- the decision, without a clock or a socket -------------------------


def test_due_offers_a_message_per_unannounced_silent_cycle():
    silences = [Silence(_conversation("Cycle 696", 300), "half a sentence"),
                Silence(_conversation("Cycle 721", 120), "Writing my reply")]
    pending = reply_notice.due(silences, announced=set())
    assert [ident for ident, _text in pending] == ["cycle-696", "cycle-721"]


def test_due_is_oldest_thread_first():
    silences = [Silence(_conversation("Cycle 721", 120), ""),
                Silence(_conversation("Cycle 696", 300), "")]
    pending = reply_notice.due(silences, announced=set())
    assert [ident for ident, _text in pending] == ["cycle-696", "cycle-721"]


def test_due_skips_a_cycle_already_announced():
    silences = [Silence(_conversation("Cycle 696", 300), ""),
                Silence(_conversation("Cycle 721", 120), "")]
    pending = reply_notice.due(silences, announced={"cycle-696"})
    assert [ident for ident, _text in pending] == ["cycle-721"]


def test_due_skips_a_conversation_with_no_id():
    silences = [Silence({"name": "Cycle 700", "updatedAt": _stamp(120)}, "")]
    assert reply_notice.due(silences, announced=set()) == []


def test_the_notice_names_the_cycle_and_quotes_what_it_was_doing():
    text = reply_notice.notice_text(
        Silence(_conversation("Cycle 721", 120), "Writing my reply now."))
    assert "Cycle 721" in text
    assert "Writing my reply now." in text
    assert "without ever replying" in text


def test_the_notice_holds_no_quote_when_the_cycle_said_nothing_at_all():
    text = reply_notice.notice_text(Silence(_conversation("Cycle 700", 120), ""))
    assert "The last thing it said" not in text
    assert "Cycle 700" in text


# --- the whole path, through ReplyWatch --------------------------------


def test_a_silent_cycle_is_pushed_to_him():
    post = Recorder()
    watch = _watch([_conversation("Cycle 721", 120, ident="c721")],
                   {"c721": {"messages": [_narration("Writing my reply now.")]}},
                   post)
    assert _run(watch) == 1
    conversation_id, text, push = post.sent[0]
    assert conversation_id == "thread-1"
    assert "Cycle 721" in text
    assert push is True


def test_a_cycle_that_replied_is_never_pushed():
    post = Recorder()
    watch = _watch([_conversation("Cycle 723", 120, ident="c723")],
                   {"c723": {"messages": [_narration("working"),
                                          _reply("Merged runner#605.")]}},
                   post)
    assert _run(watch) == 0
    assert post.sent == []


def test_the_same_silent_cycle_is_only_ever_pushed_once():
    post = Recorder()
    watch = _watch([_conversation("Cycle 721", 120, ident="c721")],
                   {"c721": {"messages": [_narration("still going")]}},
                   post)
    watch.tick(now=0.0)
    assert watch.tick(now=reply_notice.REPLY_CHECK_SECONDS + 1) == 1
    assert watch.tick(now=2 * reply_notice.REPLY_CHECK_SECONDS + 2) == 0
    assert len(post.sent) == 1


def test_a_failed_post_is_retried_on_the_next_check():
    post = Recorder(status=500)
    watch = _watch([_conversation("Cycle 721", 120, ident="c721")],
                   {"c721": {"messages": [_narration("still going")]}},
                   post)
    watch.tick(now=0.0)
    assert watch.tick(now=reply_notice.REPLY_CHECK_SECONDS + 1) == 0
    post.status = 200
    assert watch.tick(now=2 * reply_notice.REPLY_CHECK_SECONDS + 2) == 1
    assert len(post.sent) == 2


def test_the_first_tick_checks_nothing():
    post = Recorder()
    calls = []

    def listing():
        calls.append(1)
        return {"conversations": []}

    watch = reply_notice.ReplyWatch(
        listing=listing, fetch_thread=lambda cid: {},
        heartbeats=_heartbeats(), post=post)
    assert watch.tick(now=0.0) == 0
    assert calls == []


def test_a_check_inside_the_interval_does_not_run():
    post = Recorder()
    watch = _watch([_conversation("Cycle 721", 120, ident="c721")],
                   {"c721": {"messages": [_narration("still going")]}},
                   post)
    watch.tick(now=0.0)
    assert watch.tick(now=reply_notice.REPLY_CHECK_SECONDS - 1) == 0
    assert post.sent == []


def test_a_muted_heartbeat_posts_without_buzzing():
    post = Recorder()
    watch = reply_notice.ReplyWatch(
        listing=lambda: {"conversations": [
            _conversation("Cycle 721", 120, ident="c721")]},
        fetch_thread=lambda cid: {"messages": [_narration("still going")]},
        heartbeats=_heartbeats(push=False), post=post, clock=lambda: NOW)
    assert _run(watch) == 1
    assert post.sent[0][2] is False


def test_nothing_is_posted_when_no_conversation_is_bound():
    post = Recorder()
    watch = reply_notice.ReplyWatch(
        listing=lambda: {"conversations": [
            _conversation("Cycle 721", 120, ident="c721")]},
        fetch_thread=lambda cid: {"messages": [_narration("still going")]},
        heartbeats=lambda: [], post=post, clock=lambda: NOW)
    assert _run(watch) == 0
    assert post.sent == []


def test_a_listing_that_raises_costs_a_check_not_the_process():
    post = Recorder()

    def listing():
        raise RuntimeError("agora is down")

    watch = reply_notice.ReplyWatch(
        listing=listing, fetch_thread=lambda cid: {},
        heartbeats=_heartbeats(), post=post)
    assert _run(watch) == 0
    assert post.sent == []


def test_an_unreadable_thread_does_not_silence_the_rest():
    post = Recorder()

    def fetch(cid):
        if cid == "broken":
            raise RuntimeError("thread would not load")
        return {"messages": [_narration("still going")]}

    watch = reply_notice.ReplyWatch(
        listing=lambda: {"conversations": [
            _conversation("Cycle 700", 300, ident="broken"),
            _conversation("Cycle 721", 120, ident="c721")]},
        fetch_thread=fetch, heartbeats=_heartbeats(), post=post,
        clock=lambda: NOW)
    assert _run(watch) == 1
    assert "Cycle 721" in post.sent[0][1]


# --- the gates this shares with tools.reply_health ----------------------


@pytest.mark.parametrize("minutes_ago", [30, 59])
def test_a_cycle_still_inside_the_grace_is_never_pushed(minutes_ago):
    post = Recorder()
    watch = _watch([_conversation("Cycle 724", minutes_ago, ident="c724")],
                   {"c724": {"messages": [_narration("mid-flight")]}},
                   post)
    assert _run(watch) == 0


def test_a_cycle_older_than_the_window_is_never_pushed():
    post = Recorder()
    watch = _watch([_conversation("Cycle 500", 60 * 25, ident="c500")],
                   {"c500": {"messages": [_narration("ancient")]}},
                   post)
    assert _run(watch) == 0


def test_a_thread_he_started_is_not_owed_a_reply():
    post = Recorder()
    watch = _watch([_conversation("Ask", 120, ident="ask", cycle=False)],
                   {"ask": {"messages": [_narration("hello")]}},
                   post)
    assert _run(watch) == 0


# --- the shared rule, read from the module both callers use -------------


def test_find_silences_is_what_both_callers_agree_on():
    """The notifier and `tools.reply_health` must judge the same threads."""
    from tools import reply_health

    assert reply_health.replied.__module__ == "agora_runner.reply_check"
    assert reply_health.judge.__module__ == "agora_runner.reply_check"
    found = find_silences(
        {"conversations": [_conversation("Cycle 721", 120, ident="c721")]},
        lambda cid: {"messages": [_narration("still going")]},
        now=NOW)
    assert [s.name for s in found.silent] == ["Cycle 721"]
    assert found.judged == 1
