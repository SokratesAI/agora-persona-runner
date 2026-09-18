"""`tools.ask_watch` -- does a cycle find out that he answered?"""

import io
import json
from datetime import datetime, timedelta, timezone

from agora_runner import http_util
from agora_runner import nudge_ask
from tools import ask_watch


NOW = datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc)


def _row(cid, name="Nova needs you — Yes or no, is this a thing?", tags=None, archived=False):
    return {"id": cid, "name": name, "tags": tags if tags is not None else ["nova:needs-input"],
            "archived": archived}


def _msg(sender, text="hi", ts="2026-09-15T05:00:00.000Z"):
    return {"sender": sender, "text": text, "ts": ts}


def _fake_get(listing, threads, calls=None):
    def get(path):
        if calls is not None:
            calls.append(path)
        if path.startswith("/conversations?"):
            return 200, {"conversations": listing}
        cid = path.split("/conversations/")[1].split("/")[0]
        status, body = threads[cid]
        return status, body
    return get


def _run(monkeypatch, listing, threads, calls=None):
    monkeypatch.setattr(ask_watch, "agora_get", _fake_get(listing, threads, calls))
    out = io.StringIO()
    code = ask_watch.report(*ask_watch.check(now=NOW), out=out, now=NOW)
    return code, out.getvalue()


def test_his_reply_raises_and_is_quoted(monkeypatch):
    code, text = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova"), _msg("Edvard", "strike 4 and 7")]})})
    assert code == 2
    assert "ANSWERED" in text
    assert "strike 4 and 7" in text
    assert "last word from Edvard" in text


def test_my_own_last_word_after_his_reply_is_not_a_finding(monkeypatch):
    """The self-clearing half: a cycle answering him closes the thread out.

    This is the whole reason the predicate is "the newest message is not mine"
    rather than "he has written in it" -- the latter can never go back to green.
    """
    code, text = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova"), _msg("Edvard"), _msg("Nova", "done")]})})
    assert code == 0
    assert "0 open ask(s) still waiting on him and 1 answered and closed out" in text
    assert "he has not written in it" not in text


def test_a_thread_he_never_wrote_in_is_waiting_not_settled(monkeypatch):
    code, text = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova")]})})
    assert code == 0
    assert "1 open ask(s) still waiting on him and 0 answered" in text
    assert "he has not written in it" in text


def test_a_thread_with_no_sender_is_unreadable_not_waiting(monkeypatch):
    """A message this cannot attribute must not be counted as mine, because
    that is the one mistake that silently swallows his answer."""
    code, text = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("")]})})
    assert code == 1
    assert "names no sender" in text


def test_an_unreadable_thread_does_not_read_as_clean(monkeypatch):
    code, text = _run(monkeypatch, [_row("c1")], {"c1": (503, {})})
    assert code == 1
    assert "HTTP 503" in text
    assert "no instrument, not no answer" in text


def test_an_unreachable_listing_does_not_read_as_no_open_asks(monkeypatch):
    monkeypatch.setattr(ask_watch, "agora_get", lambda path: (502, {}))
    out = io.StringIO()
    code = ask_watch.report(*ask_watch.check(now=NOW), out=out, now=NOW)
    assert code == 1
    assert "no instrument, not no answer" in out.getvalue()


def test_an_untagged_thread_is_still_found_by_its_name(monkeypatch):
    """`needs_input` logs rather than raises when the tag PATCH fails, so a
    real ask can carry no tag at all."""
    calls = []
    code, text = _run(monkeypatch, [_row("c1", tags=[])], {
        "c1": (200, {"messages": [_msg("Edvard", "yes")]})}, calls)
    assert code == 2
    assert any("/conversations/c1/messages" in c for c in calls)


def test_a_tagged_thread_he_renamed_is_still_found(monkeypatch):
    code, _ = _run(monkeypatch, [_row("c1", name="objectives")], {
        "c1": (200, {"messages": [_msg("Edvard", "yes")]})})
    assert code == 2


def test_an_ordinary_conversation_is_not_read_at_all(monkeypatch):
    calls = []
    code, text = _run(monkeypatch, [_row("c1", name="Nova — Cycle 1617", tags=[])], {}, calls)
    assert code == 0
    assert not any("/messages" in c for c in calls)
    assert "0 open ask(s)" in text


def test_an_archived_ask_is_left_alone(monkeypatch):
    """Archiving is his 'I am done with this'; arguing with it is not this
    check's job."""
    calls = []
    code, _ = _run(monkeypatch, [_row("c1", archived=True)], {}, calls)
    assert code == 0
    assert not any("/messages" in c for c in calls)


def test_age_is_measured_from_the_newest_message(monkeypatch):
    code, text = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova", ts="2026-09-13T19:00:00.000Z")]})})
    assert code == 0
    assert "36.0h ago" in text


def test_an_unparseable_timestamp_says_so_rather_than_inventing_zero(monkeypatch):
    code, text = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova", ts="last tuesday")]})})
    assert code == 0
    assert "age unknown" in text


def test_the_window_is_asked_for(monkeypatch):
    calls = []
    _run(monkeypatch, [_row("c1")], {"c1": (200, {"messages": [_msg("Nova")]})}, calls)
    assert f"limit={ask_watch.WINDOW}" in calls[-1]
    assert ask_watch.WINDOW > 1, "one message cannot tell waiting from settled"


def test_sender_comes_from_needs_input(monkeypatch):
    """One constant, shared with the module that writes it -- a second copy
    would make every thread read as answered the day one of them changed."""
    from agora_runner import needs_input
    assert ask_watch.SENDER is needs_input.SENDER
    code, _ = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg(needs_input.SENDER)]})})
    assert code == 0


def test_registered_in_preflight():
    from tools import preflight
    assert "ask_watch" in preflight.CHECKS
    assert "ask_watch" in preflight.SUBJECT
    assert not preflight.unlabelled_checks(["ask_watch"])
    assert not preflight.uncadenced_checks(["ask_watch"])


def test_main_returns_the_report_code(monkeypatch):
    # The goals read is stubbed so this test says the same thing on the
    # bridge pod, which has a vault client, and on the runner pod, which
    # does not.
    monkeypatch.setattr(ask_watch, "read_goals", lambda path=None: (None, None, True))
    monkeypatch.setattr(ask_watch, "agora_get", _fake_get(
        [_row("c1")], {"c1": (200, {"messages": [_msg("Edvard", "no")]})}))
    assert ask_watch.main([]) == 2


# --- an ask whose push was withheld -------------------------------------
# His capture, 2026-09-15: "Never got a notification for the ask thread from
# cycle 1617 ... my silence is a symptom of them not reaching me, not me
# ignoring them." 02:22 Oslo is 00:22 UTC, inside Agora's 22:00-07:00 window.

QUIET_TS = "2026-09-15T00:22:00.000Z"   # 02:22 Oslo -- withheld
AUDIBLE_TS = "2026-09-15T05:30:00.000Z"  # 07:30 Oslo -- sent


def test_an_ask_posted_in_quiet_hours_is_not_reported_as_merely_waiting(monkeypatch):
    code, text = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova", ts=QUIET_TS)]})})
    assert code == 2
    assert "NEVER REACHED HIS PHONE" in text
    # It must not also be counted as an ordinary waiting thread, or the
    # summary line double-counts one ask.
    assert "0 never reached his phone" not in text
    assert "1 never reached his phone; 0 still waiting on him" in text


def test_an_ask_posted_while_audible_stays_an_ordinary_wait(monkeypatch):
    """The separating input: same thread, same shape, one timestamp apart."""
    code, text = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova", ts=AUDIBLE_TS)]})})
    assert code == 0
    assert "NEVER REACHED HIS PHONE" not in text
    assert "1 open ask(s) still waiting on him" in text


def test_an_answered_thread_is_never_reported_as_silenced(monkeypatch):
    """His reply is proof he saw it, whatever hour my message landed at."""
    code, text = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova", ts=QUIET_TS),
                                  _msg("Edvard", "no", ts=QUIET_TS)]})})
    assert code == 2
    assert "NEVER REACHED HIS PHONE" not in text
    assert "ANSWERED" in text


def test_quiet_hours_wraps_midnight_and_is_half_open():
    def utc(h, m):
        # Oslo is UTC+2 in September, so each of these reads two hours later
        # on his phone -- which is the clock Agora's window is written in.
        return datetime(2026, 9, 15, h, m, tzinfo=timezone.utc)
    assert ask_watch.in_quiet_hours(utc(0, 22)) is True    # 02:22 Oslo
    assert ask_watch.in_quiet_hours(utc(20, 0)) is True     # 22:00 Oslo, quiet
    assert ask_watch.in_quiet_hours(utc(19, 59)) is False   # 21:59 Oslo, audible
    assert ask_watch.in_quiet_hours(utc(5, 0)) is False     # 07:00 Oslo, audible
    assert ask_watch.in_quiet_hours(utc(4, 59)) is True     # 06:59 Oslo, quiet
    assert ask_watch.in_quiet_hours(None) is False


def test_nudge_is_offered_but_not_sent_without_the_flag(monkeypatch):
    sent = []
    monkeypatch.setattr(nudge_ask, "agora_internal",
                        lambda *a, **k: sent.append(a) or (200, {"status": "sent"}))
    code, text = _run(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova", ts=QUIET_TS)]})})
    assert sent == []
    assert "--nudge" in text
    assert code == 2


def _run_nudging(monkeypatch, listing, threads, now):
    monkeypatch.setattr(ask_watch, "agora_get", _fake_get(listing, threads))
    monkeypatch.setattr(nudge_ask, "agora_get", _fake_get(listing, threads))
    out = io.StringIO()
    code = ask_watch.report(*ask_watch.check(now=now), out=out,
                            do_nudge=True, now=now)
    return code, out.getvalue()


def test_nudge_posts_the_re_announcement_when_it_is_audible(monkeypatch):
    calls = []

    def fake(method, path, payload=None):
        calls.append((method, path, payload))
        return 200, {"status": "sent", "message": {"id": "m1"}}

    monkeypatch.setattr(nudge_ask, "agora_internal", fake)
    code, text = _run_nudging(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova", ts=QUIET_TS)]})}, NOW)
    assert calls == [("POST", "/conversations/c1/notify",
                      {"text": ask_watch.NUDGE_TEXT, "sender": "Nova",
                       "system": False})]
    assert "re-announced" in text
    assert "his phone buzzed" in text
    assert code == 2


def test_nudge_does_not_post_during_quiet_hours(monkeypatch):
    """A nudge withheld for the same reason is not a nudge, and posting it
    would also move the newest message forward -- clearing the predicate
    without ever telling him."""
    calls = []
    monkeypatch.setattr(nudge_ask, "agora_internal",
                        lambda *a, **k: calls.append(a) or (200, {"status": "sent"}))
    quiet_now = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)  # 03:00 Oslo
    code, text = _run_nudging(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova", ts=QUIET_TS)]})}, quiet_now)
    assert calls == []
    assert "it is quiet hours right now" in text
    assert code == 2


def test_a_nudge_agora_withheld_again_is_not_reported_as_delivered(monkeypatch):
    monkeypatch.setattr(nudge_ask, "agora_internal",
                        lambda *a, **k: (200, {"status": "recorded", "muted": True}))
    code, text = _run_nudging(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova", ts=QUIET_TS)]})}, NOW)
    assert "COULD NOT re-announce" in text
    assert "nova:mute" in text


def test_a_failed_nudge_call_says_so(monkeypatch):
    monkeypatch.setattr(nudge_ask, "agora_internal", lambda *a, **k: (502, {}))
    code, text = _run_nudging(monkeypatch, [_row("c1")], {
        "c1": (200, {"messages": [_msg("Nova", ts=QUIET_TS)]})}, NOW)
    assert "COULD NOT re-announce" in text
    assert "HTTP 502" in text


def _chat(cid, name="Manual feedback & improvements", last="2026-09-15T06:00:00.000Z"):
    """An ordinary conversation row — not an ask, so it is only ever opened
    when something is waiting."""
    return {"id": cid, "name": name, "tags": [], "archived": False,
            "lastMessageAt": last}


def test_his_answer_in_another_thread_is_named_rather_than_read_as_silence(monkeypatch):
    calls = []
    code, text = _run(monkeypatch, [_row("c1"), _chat("c2")], {
        "c1": (200, {"messages": [_msg("Nova")]}),
        "c2": (200, {"messages": [
            _msg("Nova", "what about the objectives?", ts="2026-09-15T05:50:00.000Z"),
            _msg("Edvard", "Option 1. Own project, Vault store.",
                 ts="2026-09-15T06:00:00.000Z"),
        ]}),
    }, calls)
    assert code == 2, text
    assert "HE HAS BEEN TALKING ELSEWHERE — Manual feedback & improvements" in text
    assert "Option 1. Own project, Vault store." in text
    # and it did have to open the ordinary thread to know that
    assert any("/conversations/c2/messages" in c for c in calls), calls


def test_another_persona_writing_elsewhere_is_not_him(monkeypatch):
    code, text = _run(monkeypatch, [_row("c1"), _chat("c2", name="K3s Sentinel")], {
        "c1": (200, {"messages": [_msg("Nova")]}),
        "c2": (200, {"messages": [
            _msg("K3s Sentinel", "kubectl_read: get nodes",
                 ts="2026-09-15T06:00:00.000Z")]}),
    })
    assert code == 0, text
    assert "TALKING ELSEWHERE" not in text


def test_he_spoke_elsewhere_before_the_ask_was_posted_is_not_a_finding(monkeypatch):
    code, text = _run(monkeypatch, [_row("c1"), _chat("c2")], {
        "c1": (200, {"messages": [_msg("Nova")]}),
        "c2": (200, {"messages": [
            _msg("Edvard", "older", ts="2026-09-15T04:00:00.000Z"),
            _msg("Nova", "still thinking", ts="2026-09-15T06:00:00.000Z"),
        ]}),
    })
    assert code == 0, text
    assert "TALKING ELSEWHERE" not in text


def test_a_thread_that_has_not_moved_since_the_ask_is_never_opened(monkeypatch):
    calls = []
    _run(monkeypatch, [_row("c1"), _chat("c2", last="2026-09-15T04:00:00.000Z")], {
        "c1": (200, {"messages": [_msg("Nova")]}),
        "c2": (200, {"messages": [_msg("Edvard", "older", ts="2026-09-15T04:00:00.000Z")]}),
    }, calls)
    assert not any("/conversations/c2/messages" in c for c in calls), calls


def test_nothing_waiting_means_no_other_thread_is_opened(monkeypatch):
    calls = []
    code, _ = _run(monkeypatch, [_row("c1"), _chat("c2")], {
        "c1": (200, {"messages": [
            _msg("Edvard", "yes", ts="2026-09-15T02:00:00.000Z"),
            _msg("Nova", "thanks", ts="2026-09-15T03:00:00.000Z"),
        ]}),
    }, calls)
    assert code == 0
    assert not any("/conversations/c2/messages" in c for c in calls), calls


def test_an_unreadable_other_thread_is_not_read_as_him_being_silent(monkeypatch):
    code, text = _run(monkeypatch, [_row("c1"), _chat("c2")], {
        "c1": (200, {"messages": [_msg("Nova")]}),
        "c2": (503, {}),
    })
    assert code == 1, text
    assert "COULD NOT READ — Manual feedback & improvements" in text


def test_the_age_reported_is_his_newest_word_not_his_oldest(monkeypatch):
    code, text = _run(monkeypatch, [_row("c1"), _chat("c2")], {
        "c1": (200, {"messages": [_msg("Nova")]}),
        "c2": (200, {"messages": [
            _msg("Edvard", "first thought", ts="2026-09-15T05:30:00.000Z"),
            _msg("Edvard", "Perfect. Cycles.", ts="2026-09-15T06:30:00.000Z"),
        ]}),
    })
    assert code == 2, text
    assert "wrote there 2 time(s)" in text
    assert "last 30 min ago" in text, text
    # newest first, so the freshest thing he said is the first one I read
    assert text.index("Perfect. Cycles.") < text.index("first thought"), text


# --- `--resolve`: an ask he answered in some other thread ---------------------


def _fake_internal(log, statuses):
    def internal(method, path, payload=None):
        log.append((method, path, payload))
        return statuses.pop(0), {}
    return internal


def test_resolve_posts_the_reason_then_archives(monkeypatch):
    log = []
    monkeypatch.setattr(ask_watch, "agora_internal", _fake_internal(log, [200, 200]))
    ok, detail = ask_watch.resolve(
        "c1", "You settled it in Manual feedback at 06:57 and I re-homed 58 rows.",
        [_msg("Nova")])
    assert ok, detail
    (m1, p1, b1), (m2, p2, b2) = log
    assert (m1, p1) == ("POST", "/conversations/c1/notify")
    assert "Manual feedback" in b1["text"]
    assert b1["sender"] == ask_watch.SENDER
    assert (m2, p2, b2) == ("PATCH", "/conversations/c1", {"archived": True})


def test_resolve_refuses_without_a_reason(monkeypatch):
    log = []
    monkeypatch.setattr(ask_watch, "agora_internal", _fake_internal(log, [200, 200]))
    ok, detail = ask_watch.resolve("c1", "   ", [_msg("Nova")])
    assert not ok
    assert "--because is empty" in detail
    assert log == []


def test_resolve_refuses_a_thread_that_is_not_mine(monkeypatch):
    log = []
    monkeypatch.setattr(ask_watch, "agora_internal", _fake_internal(log, [200, 200]))
    ok, detail = ask_watch.resolve("c1", "he answered elsewhere", [_msg("Edvard")])
    assert not ok
    assert "not an ask of mine" in detail
    assert log == []


def test_a_failed_archive_does_not_read_as_resolved(monkeypatch):
    log = []
    monkeypatch.setattr(ask_watch, "agora_internal", _fake_internal(log, [200, 500]))
    ok, detail = ask_watch.resolve("c1", "he answered elsewhere", [_msg("Nova")])
    assert not ok
    assert "still reads as waiting" in detail
    assert len(log) == 2


def test_nothing_is_archived_when_the_message_never_posted(monkeypatch):
    log = []
    monkeypatch.setattr(ask_watch, "agora_internal", _fake_internal(log, [502, 200]))
    ok, detail = ask_watch.resolve("c1", "he answered elsewhere", [_msg("Nova")])
    assert not ok
    assert "nothing posted and nothing archived" in detail
    assert len(log) == 1


def test_resolve_from_main_exits_nonzero_when_it_refuses(monkeypatch):
    monkeypatch.setattr(ask_watch, "messages", lambda cid: ([_msg("Nova")], None))
    monkeypatch.setattr(ask_watch, "agora_internal",
                        _fake_internal([], [200, 200]))
    assert ask_watch.main(["--resolve", "c1"]) == 1
    assert ask_watch.main(["--resolve", "c1", "--because", "he said yes in chat"]) == 0


def test_resolve_does_not_run_the_sweep(monkeypatch):
    """The sweep costs a listing plus one read per ask; --resolve needs neither."""
    monkeypatch.setattr(ask_watch, "check", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(ask_watch, "messages", lambda cid: ([_msg("Nova")], None))
    monkeypatch.setattr(ask_watch, "agora_internal", _fake_internal([], [200, 200]))
    assert ask_watch.main(["--resolve", "c1", "--because", "answered in chat"]) == 0


# --- a goal discussion is a question waiting on him ----------------------
# 2026-09-16: this printed `0 open ask(s) still waiting on him` while
# `project_goals_check` printed eleven of eleven projects still discussing.
# A goal thread is not tagged `nova:needs-input`, so `_is_ask` was false for
# every one of them.

GOALS = """# Project goals

## Cycles

```objective
statement: A cycle lands a real change.
status: discussing
conversation: 0af15d7d
```

```key-result
id: c-kr-one
name: one
measure: a thing
now: 1
target: 2
status: discussing
```
"""


def _goal_run(monkeypatch, listing, threads, markdown=GOALS, **kw):
    monkeypatch.setattr(ask_watch, "agora_get", _fake_get(listing, threads))
    monkeypatch.setattr(nudge_ask, "agora_get", _fake_get(listing, threads))
    out = io.StringIO()
    code = ask_watch.report(
        *ask_watch.check(now=NOW, goals_markdown=markdown, **kw),
        out=out, now=NOW)
    return code, out.getvalue()


def _plain(cid, name="Manual feedback & improvements"):
    return {"id": cid, "name": name, "tags": [], "archived": False}


def test_an_untagged_goal_thread_is_counted_as_waiting(monkeypatch):
    code, text = _goal_run(
        monkeypatch, [_plain("0af15d7d-0000-0000-0000-000000000000")],
        {"0af15d7d-0000-0000-0000-000000000000": (
            200, {"messages": [_msg("Nova", "here are the key results",
                                    "2026-09-16T08:00:00.000Z")]})})
    assert code == 0
    assert "waiting on him — Manual feedback & improvements — goals: Cycles" in text
    assert "Of 1 project(s) still discussing their goals, 1 thread(s) were judged" in text


def test_the_count_is_on_the_clean_line_too(monkeypatch):
    """A count printed only when it is interesting is one nobody can trust."""
    code, text = _goal_run(monkeypatch, [], {}, markdown=None)
    assert code == 0
    assert "still discussing their goals" not in text


def test_a_goal_thread_he_answered_raises(monkeypatch):
    code, text = _goal_run(
        monkeypatch, [_plain("0af15d7d-0000-0000-0000-000000000000")],
        {"0af15d7d-0000-0000-0000-000000000000": (
            200, {"messages": [_msg("Nova"),
                               _msg("Edvard", "drop the second one",
                                    "2026-09-16T08:00:00.000Z")]})})
    assert code == 2
    assert "ANSWERED — Manual feedback & improvements — goals: Cycles" in text
    assert "drop the second one" in text


def test_a_goal_thread_i_replied_in_is_still_waiting_not_settled(monkeypatch):
    """The difference from an ask: only the document closes a goal.

    An ask is finished when he answers and a cycle replies. A goal is
    finished when its status reads `agreed` or `struck`, so a thread where
    both of us have spoken still has an undecided objective hanging off it.
    Reading it as settled is what produced the 0.
    """
    code, text = _goal_run(
        monkeypatch, [_plain("0af15d7d-0000-0000-0000-000000000000")],
        {"0af15d7d-0000-0000-0000-000000000000": (
            200, {"messages": [_msg("Nova"), _msg("Edvard", "ok"),
                               _msg("Nova", "noted",
                                    "2026-09-16T08:00:00.000Z")]})})
    assert code == 0
    assert "waiting on him — Manual feedback & improvements — goals: Cycles" in text
    assert "answered and closed out" in text
    assert "1 thread(s) were judged" in text


def test_a_goal_thread_posted_in_quiet_hours_never_reached_his_phone(monkeypatch):
    code, text = _goal_run(
        monkeypatch, [_plain("0af15d7d-0000-0000-0000-000000000000")],
        {"0af15d7d-0000-0000-0000-000000000000": (
            200, {"messages": [_msg("Nova", "the batch",
                                    "2026-09-16T03:28:00.000Z")]})})
    assert code == 2
    assert "NEVER REACHED HIS PHONE — Manual feedback & improvements — goals: Cycles" in text


def test_a_thread_the_listing_does_not_carry_is_no_instrument(monkeypatch):
    """Archived, deleted or renamed away — the one thing that must not read 0."""
    code, text = _goal_run(monkeypatch, [_plain("ffffffff-0000-0000-0000-000000000000")],
                           {"ffffffff-0000-0000-0000-000000000000": (
                               200, {"messages": [_msg("Nova")]})})
    assert code == 1
    assert "CANNOT SEE THE THREAD — Cycles" in text
    assert "0af15d7d" in text


def test_a_project_arguing_nowhere_is_named_but_does_not_raise(monkeypatch):
    markdown = GOALS.replace("conversation: 0af15d7d", "")
    code, text = _goal_run(monkeypatch, [], {}, markdown=markdown)
    assert code == 0
    assert "ARGUED NOWHERE — Cycles" in text
    assert "1 are argued nowhere" in text


def test_a_tagged_ask_that_is_also_a_goal_thread_is_judged_once(monkeypatch):
    """Two of the three live goal threads ARE tagged asks.

    Judged in both branches, the same thread reads as `settled` under the ask
    predicate (he wrote, I wrote back) and as waiting under the goal one, so
    the summary would count it twice and one of the two counts would be the
    wrong one. The goal predicate is the stricter of the two and wins.
    """
    cid = "0af15d7d-0000-0000-0000-000000000000"
    code, text = _goal_run(
        monkeypatch,
        [_row(cid, name="Nova needs you — the twelve objectives")],
        {cid: (200, {"messages": [_msg("Nova"), _msg("Edvard", "ok"),
                                  _msg("Nova", "noted",
                                       "2026-09-16T08:00:00.000Z")]})})
    assert code == 0
    assert text.count(cid) == 1
    assert "0 answered and closed out" in text
    assert "1 open ask(s) still waiting on him" in text


def test_no_vault_client_says_so_and_does_not_raise(monkeypatch):
    """`nas_health`'s call: no pull request fixes running on the wrong pod."""
    code, text = _goal_run(monkeypatch, [], {}, markdown=None,
                           goals_problem="no vault client on this pod")
    assert code == 0
    assert "CANNOT SEE THE GOALS — no vault client on this pod" in text


def test_an_unreadable_goals_document_is_no_instrument(monkeypatch):
    code, text = _goal_run(monkeypatch, [], {}, markdown=None,
                           goals_unreadable="vault_tool.py exited 1")
    assert code == 1
    assert "COULD NOT READ THE GOALS — vault_tool.py exited 1" in text


def test_read_goals_from_a_local_path(tmp_path):
    path = tmp_path / "goals.md"
    path.write_text(GOALS)
    markdown, problem, from_this_pod = ask_watch.read_goals(str(path))
    assert problem is None and from_this_pod
    assert ask_watch.goal_discussions(markdown) == [("Cycles", "0af15d7d", 1)]


def test_read_goals_from_a_missing_local_path(tmp_path):
    markdown, problem, from_this_pod = ask_watch.read_goals(str(tmp_path / "nope.md"))
    assert markdown is None and from_this_pod and "nope.md" in problem


def test_a_401_with_no_token_names_the_pod_rather_than_blaming_agora(monkeypatch):
    """Cycle 1677 ran the nudge this tool prints as its own remedy from the
    bridge pod and got a bare `notify returned HTTP 401`, which reads as Agora
    refusing the message. `agora_internal` had sent no token, because the
    bridge pod holds none -- the same two calls returned 200 from the runner
    pod minutes later. The status alone cannot tell those apart, so the
    message has to."""
    monkeypatch.setattr(nudge_ask, "agora_internal", lambda *a, **k: (401, {}))
    monkeypatch.setattr(http_util, "AGORA_TOKEN", "")
    ok, detail = ask_watch.nudge("c1", newest=_msg("Nova", ts=QUIET_TS), now=NOW)
    assert ok is False
    assert "HTTP 401" in detail
    assert "no AGORA_TOKEN" in detail
    assert "runner pod" in detail


def test_a_401_with_a_token_present_is_a_real_refusal(monkeypatch):
    """The mirror of the test above, and the reason the hint reads the token
    rather than the status alone: a credential that was sent and rejected is
    Agora's answer, and explaining it away as a missing token would point the
    next cycle at the wrong pod."""
    monkeypatch.setattr(nudge_ask, "agora_internal", lambda *a, **k: (401, {}))
    monkeypatch.setattr(http_util, "AGORA_TOKEN", "a-real-token")
    ok, detail = ask_watch.nudge("c1", newest=_msg("Nova", ts=QUIET_TS), now=NOW)
    assert ok is False
    assert detail == "notify returned HTTP 401"


# --- paging him on Telegram when an ask has waited ---------------------------
# His words on the cycle 1811 card, 2026-09-18: "if the whole system stands
# still and just waiting for me, that is critical and worth a telegram message!"

from zoneinfo import ZoneInfo

AFTERNOON = datetime(2026, 9, 18, 14, 0, tzinfo=ZoneInfo("Europe/Oslo"))


def _sender(sent):
    def send(text, url):
        sent.append(text)
        return 0, "sent"
    return send


def test_an_ask_waiting_past_the_threshold_pages_him_once_ever(tmp_path):
    sent, state = [], str(tmp_path / "state.json")
    waiting = [("Nova needs you — Yes or no, keep the goals?", "0256140f-aaaa", 20.3)]
    first = ask_watch.page(waiting, [], out=io.StringIO(), send=_sender(sent),
                           now=AFTERNOON, state_path=state)
    again = ask_watch.page(waiting, [], out=io.StringIO(), send=_sender(sent),
                           now=AFTERNOON, state_path=state)
    # A week later it is still waiting, and still not sent again: he said
    # hard questions can take weeks, and a repeat is what makes him mute it.
    week = ask_watch.page(waiting, [], out=io.StringIO(), send=_sender(sent),
                          now=AFTERNOON + timedelta(days=7), state_path=state)
    assert (first, again, week) == (0, 3, 3)
    assert len(sent) == 1
    assert "keep the goals?" in sent[0] and "20.3h ago" in sent[0]


def test_a_fresh_ask_does_not_page(tmp_path):
    sent = []
    code = ask_watch.page([("n", "c1", ask_watch.PAGE_AFTER_HOURS - 0.1)], [],
                          out=io.StringIO(), send=_sender(sent), now=AFTERNOON,
                          state_path=str(tmp_path / "s.json"))
    assert code is None and sent == []


def test_a_new_ask_joining_the_set_pages_again(tmp_path):
    sent, state = [], str(tmp_path / "state.json")
    old = ("old", "aaaaaaaa-1", 30.0)
    ask_watch.page([old], [], out=io.StringIO(), send=_sender(sent),
                   now=AFTERNOON, state_path=state)
    ask_watch.page([old], [("new", "bbbbbbbb-2", 5.0)], out=io.StringIO(),
                   send=_sender(sent), now=AFTERNOON, state_path=state)
    assert len(sent) == 2 and "new" in sent[1]
    # Only the new ask: the old one was already sent once.
    assert "- old" not in sent[1]


def test_an_ask_paged_under_the_old_set_key_is_not_paged_again(tmp_path):
    # The live state on 2026-09-18 held set keys like this one; those asks
    # were already sent and must not go out again when the set changes.
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"asks-waiting:0af15d7d,3f42afbc": {
        "last_sent": "2026-09-18T13:59:06+02:00", "urgent": False}}))
    sent = []
    code = ask_watch.page([("goals", "0af15d7d-x", 30.0), ("kpi", "3f42afbc-y", 30.0)],
                          [], out=io.StringIO(), send=_sender(sent),
                          now=AFTERNOON + timedelta(days=2), state_path=str(state))
    assert code == 3 and sent == []


def test_main_pages_only_with_the_flag(monkeypatch):
    monkeypatch.setattr(ask_watch, "read_goals", lambda path=None: (None, None, True))
    monkeypatch.setattr(ask_watch, "agora_get", _fake_get(
        [_row("c1")], {"c1": (200, {"messages": [_msg(ask_watch.SENDER, "q?")]})}))
    calls = []
    monkeypatch.setattr(ask_watch, "page", lambda w, s: calls.append((w, s)))
    ask_watch.main([])
    assert calls == []
    ask_watch.main(["--notify"])
    assert len(calls) == 1 and calls[0][0][0][1] == "c1"


def test_preflight_runs_it_with_notify():
    from tools import preflight
    assert preflight.CHECK_ARGS["ask_watch"] == ["--notify"]
