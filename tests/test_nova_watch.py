"""The off-box watcher's decisions, driven with no socket and no phone.

Every test here is written so that it fails if the behaviour it names is
removed -- checked by breaking each one deliberately before committing, which
is the step Cycle 505 and Cycle 503 both found a vacuous test by skipping.
"""

import contextlib
import json
import sys
import types

import pytest

from offbox import nova_watch


class Phone:
    """Records what would have been pushed."""

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def __call__(self, verdict, text):
        if self.fail:
            raise RuntimeError("push service refused")
        self.sent.append((verdict, text))


def answering(**status):
    """A `fetch` that answers with the given status object."""
    return lambda: status


def dead(detail="ConnectionRefusedError: nope"):
    def fetch():
        raise nova_watch.Unreachable(detail)
    return fetch


def test_one_failed_poll_is_not_an_alarm():
    """The owner's broadband blipping must not ring the phone. That is the grace."""
    phone = Phone()
    watch = nova_watch.Watch(fetch=dead(), send=phone, grace=2)
    assert watch.poll(now=100) is None
    assert phone.sent == []


def test_two_failed_polls_in_a_row_report_unreachable():
    phone = Phone()
    watch = nova_watch.Watch(fetch=dead(), send=phone, grace=2)
    watch.poll(now=100)
    assert watch.poll(now=400) == "UNREACHABLE"
    assert len(phone.sent) == 1
    verdict, text = phone.sent[0]
    assert verdict == "UNREACHABLE"
    assert "UNREACHABLE, not a stalled loop" in text


def test_a_running_outage_sends_once_not_once_per_poll():
    """Keyed on the first failure of the outage, so the key stops moving."""
    phone = Phone()
    watch = nova_watch.Watch(fetch=dead(), send=phone, grace=2)
    for tick in range(100, 100 + 300 * 10, 300):
        watch.poll(now=tick)
    assert len(phone.sent) == 1


def test_recovery_re_arms_the_unreachable_alarm():
    """A second outage is a second message. Otherwise the first one silences
    the watchdog for the life of the process."""
    phone = Phone()
    state = {"up": False}
    def fetch():
        if state["up"]:
            return {"stalled": False}
        raise nova_watch.Unreachable("down")
    watch = nova_watch.Watch(fetch=fetch, send=phone, grace=2)
    watch.poll(now=100)
    assert watch.poll(now=400) == "UNREACHABLE"
    state["up"] = True
    assert watch.poll(now=700) is None
    state["up"] = False
    watch.poll(now=1000)
    assert watch.poll(now=1300) == "UNREACHABLE"
    assert len(phone.sent) == 2


def test_a_send_that_failed_is_not_recorded_as_sent():
    """The one failure mode a watchdog may not have: believing it spoke."""
    phone = Phone(fail=True)
    watch = nova_watch.Watch(fetch=dead(), send=phone, grace=2)
    watch.poll(now=100)
    assert watch.poll(now=400) is None
    phone.fail = False
    assert watch.poll(now=700) == "UNREACHABLE"


def test_a_box_that_answers_and_is_not_stalled_says_nothing():
    phone = Phone()
    watch = nova_watch.Watch(
        fetch=answering(stalled=False, lastWrittenAt="2026-08-27T01:19:00+02:00"),
        send=phone)
    assert watch.poll(now=100) is None
    assert phone.sent == []


def test_a_stalled_box_reports_silent_not_unreachable():
    phone = Phone()
    watch = nova_watch.Watch(fetch=answering(
        stalled=True, silentIntervals=4, cycle=508,
        lastWokeDate="2026-08-27", lastWokeTime="01:00",
        lastWrittenAt="2026-08-27T01:19:00+02:00"), send=phone)
    assert watch.poll(now=100) == "SILENT"
    verdict, text = phone.sent[0]
    assert verdict == "SILENT"
    assert "SILENT, not UNREACHABLE" in text
    assert "Cycle 508" in text
    assert "4 heartbeat intervals" in text


def test_silent_dedupes_on_the_write_stamp_and_re_arms_when_a_cycle_writes():
    """`stall_notice`'s rule, copied: the stamp does not move while the loop
    is down, and moving it is the same event that ends the stall."""
    phone = Phone()
    status = {"stalled": True, "silentIntervals": 3, "cycle": 508,
              "lastWrittenAt": "2026-08-27T01:19:00+02:00"}
    watch = nova_watch.Watch(fetch=lambda: status, send=phone)
    assert watch.poll(now=100) == "SILENT"
    assert watch.poll(now=400) is None
    assert watch.poll(now=700) is None
    status["lastWrittenAt"] = "2026-08-27T03:00:00+02:00"
    assert watch.poll(now=1000) == "SILENT"
    assert len(phone.sent) == 2


def test_a_stall_with_no_write_stamp_is_refused():
    """An alarm with nothing to dedupe on would fire every poll, which is the
    one shape that gets it muted."""
    phone = Phone()
    watch = nova_watch.Watch(
        fetch=answering(stalled=True, silentIntervals=3, lastWrittenAt=""),
        send=phone)
    assert watch.poll(now=100) is None
    assert phone.sent == []


def test_an_unexpected_exception_in_fetch_counts_as_unreachable():
    """The loop body may never raise: it has to still be running when the
    thing it watches is not."""
    phone = Phone()
    def fetch():
        raise ValueError("something nobody predicted")
    watch = nova_watch.Watch(fetch=fetch, send=phone, grace=1)
    assert watch.poll(now=100) == "UNREACHABLE"
    assert "ValueError" in phone.sent[0][1]


def test_the_two_verdicts_are_never_the_same_message():
    """Merging the causes is the failure this design exists to avoid."""
    stalled = nova_watch.silent_text(
        {"stalled": True, "silentIntervals": 2, "cycle": 1})
    down = nova_watch.unreachable_text(2, "2026-08-27 01:00", "refused")
    # `SILENT` may name UNREACHABLE exactly once, and only to say it is not
    # that. Counting is the assertion; `split("not ")[0]` was the first version
    # and it could only ever look at the four characters before that phrase.
    assert stalled.count("UNREACHABLE") == 1
    assert "SILENT, not UNREACHABLE" in stalled
    assert "SILENT" not in down
    assert "agora-persona-runner pod" in stalled
    assert "agora-persona-runner pod" not in down


def test_fetch_status_turns_every_transport_failure_into_unreachable():
    def opener(url, timeout=None):
        raise OSError("dns went away")
    with pytest.raises(nova_watch.Unreachable) as caught:
        nova_watch.fetch_status(opener=opener)
    assert "dns went away" in str(caught.value)


def test_fetch_status_refuses_a_body_with_no_status_object():
    """A 200 from something that is not our site must not read as healthy —
    the same shape as Cycle 196's static server answering the front page."""
    class Response:
        def read(self):
            return b'{"entries": []}'
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
    with pytest.raises(nova_watch.Unreachable):
        nova_watch.fetch_status(opener=lambda url, timeout=None: Response())


def test_fetch_status_reads_the_status_object():
    class Response:
        def read(self):
            return json.dumps({"status": {"stalled": True}}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
    assert nova_watch.fetch_status(
        opener=lambda url, timeout=None: Response()) == {"stalled": True}


def test_it_refuses_to_start_without_the_values_it_needs_to_speak():
    """A watchdog that cannot reach him looks exactly like coverage."""
    send, problem = nova_watch.sender_from_env({})
    assert send is None
    assert "NOVA_WATCH_SUBSCRIPTION" in problem and "VAPID_PRIVATE_KEY" in problem
    assert nova_watch.main(env={}) == 2


def test_it_refuses_a_subscription_with_no_endpoint():
    send, problem = nova_watch.sender_from_env(
        {"NOVA_WATCH_SUBSCRIPTION": '{"keys": {}}', "VAPID_PRIVATE_KEY": "k"})
    assert send is None
    assert "endpoint" in problem


def test_run_polls_the_number_of_times_it_was_asked_to():
    phone = Phone()
    calls = []
    watch = nova_watch.Watch(fetch=answering(stalled=False), send=phone,
                             interval=7)
    watch.run(sleep=calls.append, polls=3)
    assert calls == [7, 7]


def full_env(**over):
    env = {"NOVA_WATCH_SUBSCRIPTION":
           '{"endpoint": "https://fcm.example/x", "keys": {"p256dh": "a", "auth": "b"}}',
           "VAPID_PRIVATE_KEY": "k"}
    env.update(over)
    return env


def test_a_subscription_with_no_encryption_keys_is_refused():
    """`pywebpush` cannot encrypt without them, so this would start, print that
    it is polling, and never ring the phone for any verdict."""
    for broken in ('{"endpoint": "https://fcm.example/x"}',
                   '{"endpoint": "https://fcm.example/x", "keys": {"p256dh": "a"}}',
                   '{"endpoint": "https://fcm.example/x", "keys": {"auth": "b"}}'):
        send, problem = nova_watch.sender_from_env(full_env(NOVA_WATCH_SUBSCRIPTION=broken))
        assert send is None, broken
        assert "keys." in problem


def test_a_complete_subscription_is_accepted():
    with fake_pywebpush() as calls:
        send, problem = nova_watch.sender_from_env(full_env())
        assert problem is None and callable(send)
        send("SILENT", "text")
    assert calls["data"]


def test_the_default_url_names_the_site_that_serves_the_journal():
    """`agora` and `nova` are different services on the same tailnet and only
    one of them has `/api/journal`. The wrong one 404s on every poll, which
    reads as a permanent outage and silences the watcher after one message."""
    assert "//nova." in nova_watch.DEFAULT_URL
    assert "/api/journal" in nova_watch.DEFAULT_URL


def test_a_status_field_of_the_wrong_type_does_not_kill_the_loop():
    """`poll` promises it never raises, and building the message used to happen
    outside the try that keeps that promise."""
    phone = Phone()
    watch = nova_watch.Watch(fetch=answering(
        stalled=True, silentIntervals=2, cycle=5,
        lastWokeDate=20260827, lastWokeTime="01:00",
        lastWrittenAt="2026-08-27T01:19:00+02:00"), send=phone)
    assert watch.poll(now=100) is None
    assert phone.sent == []
    watch.run(sleep=lambda s: None, polls=2)


@contextlib.contextmanager
def fake_pywebpush():
    """`pywebpush` is not installed here — it only has to exist on the NAS —
    so the send path is exercised against a stand-in that records its call."""
    calls = {}
    module = types.ModuleType("pywebpush")
    module.webpush = lambda **kwargs: calls.update(kwargs)
    sys.modules["pywebpush"] = module
    try:
        yield calls
    finally:
        del sys.modules["pywebpush"]


def test_the_push_send_carries_a_timeout():
    """A hung push endpoint is not an exception, so `_deliver`'s catch does not
    cover it and only a timeout does."""
    with fake_pywebpush() as calls:
        send = nova_watch.web_push_sender(
            {"endpoint": "https://fcm.example/x"}, "key", "https://example")
        send("SILENT", "text")
    assert calls["timeout"] == nova_watch.SEND_TIMEOUT
    assert calls["vapid_private_key"] == "key"
    assert calls["vapid_claims"] == {"sub": "https://example"}
