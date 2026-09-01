"""The loop telling the owner it has stopped -- issue #70's open half.

The thing under test is a message that rings a phone, so the tests are
written against the two failures that matter in opposite directions: it
never fires while the loop is alive, and it fires exactly once while the
loop is dead no matter how often it is asked.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from agora_runner.config import NOVA_PERSONA_ID
from agora_runner.nova_site import OSLO
from agora_runner.stall_notice import (
    StallWatch, due, notice_text, nova_conversation,
)


def _status(**over):
    base = {
        "cycle": 196,
        "stalled": True,
        "silentIntervals": 3,
        "lastWrittenAt": "2026-08-14T18:07:00+02:00",
        "lastWokeDate": "2026-08-14",
        "lastWokeTime": "18:07",
    }
    base.update(over)
    return base


def _heartbeat(**over):
    base = {
        "id": "hb-nova", "enabled": True, "workflowId": None,
        "personaId": NOVA_PERSONA_ID, "conversationId": "conv-1",
    }
    base.update(over)
    return base


# -- due(): the whole decision ------------------------------------------

def test_healthy_loop_says_nothing():
    assert due(_status(stalled=False, silentIntervals=1), None) is None


def test_stalled_loop_with_no_prior_notice_speaks():
    verdict = due(_status(), None)
    assert verdict is not None
    key, text = verdict
    assert key == "2026-08-14T18:07:00+02:00"
    assert "Cycle 196" in text


def test_the_same_stall_is_announced_once_and_never_again():
    key, _text = due(_status(), None)
    # The stall does not end and the page is asked again and again; the
    # write time cannot move until a cycle writes, so nothing more is sent.
    assert due(_status(silentIntervals=4), key) is None
    assert due(_status(silentIntervals=99), key) is None


def test_a_new_entry_re_arms_the_alarm():
    _key, _text = due(_status(), None)
    later = _status(cycle=197, lastWrittenAt="2026-08-14T19:07:00+02:00")
    assert due(later, "2026-08-14T18:07:00+02:00") is not None


def test_a_stall_with_no_write_time_is_refused():
    # Nothing to dedupe on means every check would send another message.
    assert due(_status(lastWrittenAt=""), None) is None
    assert due(_status(lastWrittenAt=None), None) is None


def test_the_message_says_when_and_how_long():
    text = notice_text(_status())
    assert "18:07" in text
    assert "3 heartbeat intervals" in text


def test_one_interval_is_not_pluralised():
    # Unreachable while STALL_GRACE_INTERVALS is 2, and one ternary cheaper
    # than the day somebody lowers it and this reads "1 heartbeat intervals".
    assert "1 heartbeat interval " in notice_text(_status(silentIntervals=1))


def test_a_missing_cycle_number_does_not_print_none():
    text = notice_text(_status(cycle=None))
    assert "None" not in text
    assert "the last cycle" in text


# -- nova_conversation_id() ---------------------------------------------

def test_picks_the_conversation_nova_is_bound_to_right_now():
    assert nova_conversation([
        _heartbeat(personaId="someone-else", conversationId="conv-other"),
        _heartbeat(conversationId="conv-nova"),
    ]) == ("conv-nova", True)


def test_ignores_a_disabled_or_workflow_bound_heartbeat():
    # A workflow heartbeat writes no journal entry, so it has nothing to do
    # with the silence being measured -- and a disabled one is not running.
    assert nova_conversation([_heartbeat(enabled=False)]) == (None, True)
    assert nova_conversation([_heartbeat(workflowId="wf-1")]) == (None, True)


def test_no_heartbeats_at_all_is_not_a_crash():
    assert nova_conversation([]) == (None, True)
    assert nova_conversation(None) == (None, True)


def test_a_muted_heartbeat_still_gets_the_message_without_the_buzz():
    # The owner's #79, shipped the same day: pushNotifications:false mutes the
    # phone, it does not withhold the message. A stall notice that ignored
    # it would defeat the mute for the one case a mute cannot tell apart
    # from nothing happening.
    assert nova_conversation([_heartbeat(pushNotifications=False)]) == ("conv-1", False)


def test_a_heartbeat_without_the_field_still_notifies():
    # Every heartbeat created before #79 has no such field, and absent must
    # read as notify -- only a literal false mutes.
    assert nova_conversation([_heartbeat()])[1] is True
    assert nova_conversation([_heartbeat(pushNotifications=True)])[1] is True


# -- StallWatch: the rate limiter and the failure paths ------------------

class _Recorder:
    def __init__(self, status, result=200):
        self.status = status
        self.result = result
        self.posts = []
        self.checks = 0

    def check(self):
        self.checks += 1
        return self.status

    def heartbeats(self):
        return [_heartbeat()]

    def post(self, conversation_id, text, push):
        self.posts.append((conversation_id, text, push))
        return self.result


def _watch(rec, interval=300):
    return StallWatch(check=rec.check, heartbeats=rec.heartbeats,
                      post=rec.post, interval=interval)


def test_posts_once_then_stays_quiet_however_often_it_is_ticked():
    rec = _Recorder(_status())
    watch = _watch(rec)
    assert watch.tick(now=0.0) is False, "the first tick must not build a cold cache"
    assert watch.tick(now=300.0) is True
    # A whole day of checks at the real interval, same stall throughout.
    for step in range(2, 290):
        assert watch.tick(now=step * 300.0) is False
    assert len(rec.posts) == 1
    assert rec.posts[0][0] == "conv-1"


def test_a_tick_inside_the_interval_does_not_even_check():
    rec = _Recorder(_status(stalled=False))
    watch = _watch(rec)
    for step in range(0, 300):
        watch.tick(now=float(step))
    assert rec.checks == 0, "the site's 1s shutdown loop must not cost 300 checks"
    watch.tick(now=300.0)
    assert rec.checks == 1


def test_the_first_tick_never_touches_the_journal_cache():
    # It lands ~1s after start_nova_site kicked off the warming thread; a
    # cold cached_payload build there blocks the SIGTERM poll for seconds.
    rec = _Recorder(_status())
    _watch(rec).tick(now=123.4)
    assert rec.checks == 0


def test_a_failed_post_is_retried_rather_than_counted_as_announced():
    rec = _Recorder(_status(), result=503)
    watch = _watch(rec)
    assert watch.tick(now=0.0) is False
    assert watch.tick(now=300.0) is False
    rec.result = 200
    assert watch.tick(now=600.0) is True
    assert len(rec.posts) == 2


def test_no_bound_conversation_posts_nothing_and_stays_armed():
    rec = _Recorder(_status())
    rec.heartbeats = lambda: []
    watch = _watch(rec)
    watch.tick(now=0.0)
    assert watch.tick(now=300.0) is False
    assert rec.posts == []


def test_a_broken_check_costs_a_check_not_the_site():
    # This runs inside the loop that keeps the owner's app served. A vault or
    # Agora hiccup must not take the process down with it.
    rec = _Recorder(_status())

    def boom():
        raise RuntimeError("vault said no")

    watch = StallWatch(check=boom, heartbeats=rec.heartbeats, post=rec.post)
    watch.tick(now=0.0)
    assert watch.tick(now=300.0) is False
    assert rec.posts == []


def test_the_live_wiring_is_the_default():
    # Nothing in production passes these in, so a rename that unhooks the
    # notifier would otherwise be invisible: every test above injects.
    from agora_runner import stall_notice

    watch = StallWatch()
    assert watch._check is stall_notice._live_status
    assert watch._heartbeats is stall_notice._live_heartbeats
    assert watch._post is stall_notice._live_post


def test_the_site_process_actually_ticks_it():
    # The feature is a background check wired into a loop; a test on the
    # class alone would pass with the wiring deleted.
    import inspect

    from agora_runner import nova_site_main

    # Split on comment markers first: `# watch.tick()` would satisfy a bare
    # substring check while the notifier did nothing at all.
    source = inspect.getsource(nova_site_main.main)
    live = "\n".join(line.split("#")[0] for line in source.splitlines())
    assert "watch.tick()" in live
    assert "StallWatch()" in live


@pytest.mark.parametrize("code", [200, 201])
def test_both_success_codes_count_as_posted(code):
    rec = _Recorder(_status(), result=code)
    watch = _watch(rec)
    watch.tick(now=0.0)
    assert watch.tick(now=300.0) is True


def test_the_live_post_speaks_as_agora_and_honours_the_mute(monkeypatch):
    # The one place the push flag actually reaches the wire, and the only
    # test that would notice `push=True` being hardcoded back in.
    from agora_runner import conversations, stall_notice

    seen = {}

    def fake_notify(conversation_id, text, sender, system=False, push=True,
                    thinking=False):
        seen.update(conversation=conversation_id, sender=sender, system=system,
                    push=push)
        return 200, "m1"

    monkeypatch.setattr(conversations, "notify", fake_notify)
    assert stall_notice._live_post("conv-1", "text", False) == 200
    assert seen == {"conversation": "conv-1", "sender": "Agora",
                    "system": True, "push": False}


def test_a_record_the_site_cannot_refresh_does_not_push_a_stall_to_his_phone():
    """The badge's bug, in the consumer where it costs the most.

    `_with_silence` measures a live `now` against a cached
    `lastWrittenAt`, so a rebuild that keeps failing reads as a loop that
    stopped writing. Here that does not colour a badge -- it posts "Nova
    has stopped writing" to the owner's phone, on evidence only that this
    process lost its own connection to the vault.

    Measured 2026-08-15: `nova-site-preview` served
    `stalled: true, silentIntervals: 9` for hours off a payload built the
    previous evening, while the loop wrote an entry every hour.

    Same payload, same clock; only the age of the served copy differs.
    """
    from agora_runner import nova_site
    from agora_runner.stall_notice import _live_status

    written = (datetime.now(OSLO) - timedelta(hours=9)).isoformat()
    built = {"entries": [], "status": {"cycle": 206, "lastWrittenAt": written}}

    nova_site.reset_cache()
    try:
        with patch.object(nova_site, "journal_payload", lambda: built):
            fresh = _live_status()
            assert fresh["stalled"] is True, "a genuinely quiet loop must still be reported"

            # Same reason as the two tests above: no rebuild thread.
            with nova_site._cache_lock:
                nova_site._refreshing.add("journal")
                payload, body, etag, at = nova_site._cache["journal"]
                nova_site._cache["journal"] = (
                    payload, body, etag, at - nova_site.RECORD_TRUST_SECONDS - 1,
                )
            frozen = _live_status()
    finally:
        nova_site.reset_cache()

    assert frozen["recordStale"] is True
    assert frozen["stalled"] is False
