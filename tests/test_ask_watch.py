"""`tools.ask_watch` -- does a cycle find out that he answered?"""

import io
from datetime import datetime, timezone

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
    code = ask_watch.report(*ask_watch.check(now=NOW), out=out)
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
    code = ask_watch.report(*ask_watch.check(now=NOW), out=out)
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
    monkeypatch.setattr(ask_watch, "agora_get", _fake_get(
        [_row("c1")], {"c1": (200, {"messages": [_msg("Edvard", "no")]})}))
    assert ask_watch.main([]) == 2
