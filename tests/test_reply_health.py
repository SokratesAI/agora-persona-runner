"""`tools.reply_health` — a cycle that never answered the owner."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tools import reply_health

NOW = datetime(2026, 8, 31, 16, 30, tzinfo=timezone.utc)


def _stamp(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z")


def _listing(*conversations):
    return {"conversations": list(conversations)}


def _conversation(name, minutes_ago, cycle=True, ident=None):
    return {"id": ident or name.lower().replace(" ", "-"), "name": name,
            "updatedAt": _stamp(minutes_ago), "cycleThread": cycle}


def _thread(*messages):
    return {"messages": list(messages)}


def _narration(text):
    return {"text": text, "partial": True}


def _reply(text):
    return {"text": text, "partial": False}


def _getter(listing, threads):
    """A stand-in for `urlopen`, keyed on the two routes the tool calls."""
    def get(url, timeout=30):
        if url.endswith("/api/conversations"):
            return listing
        ident = url.split("id=")[1].split("&")[0]
        if ident not in threads:
            raise OSError(f"no such thread {ident}")
        return threads[ident]
    return get


def _sweep(listing, threads, **kwargs):
    return reply_health.sweep(site="http://site", now=NOW,
                              get=_getter(listing, threads), **kwargs)


def test_a_finished_cycle_that_never_replied_raises():
    listing = _listing(_conversation("Nova — Cycle 721", 90, ident="c721"))
    threads = {"c721": _thread(_narration("Writing my reply now."))}
    status, lines = _sweep(listing, threads)
    assert status == 2
    assert any("NO REPLY" in line for line in lines)
    assert any("Cycle 721" in line and "Writing my reply now." in line
               for line in lines)


def test_a_finished_cycle_that_replied_is_clean():
    listing = _listing(_conversation("Nova — Cycle 720", 90, ident="c720"))
    threads = {"c720": _thread(_narration("Now the journal."),
                               _reply("Done. Here is what I did."))}
    status, lines = _sweep(listing, threads)
    assert status == 0
    assert not any("NO REPLY" in line for line in lines)


def test_a_cycle_still_inside_the_grace_is_not_judged():
    """The cycle running this check has no reply yet by construction."""
    listing = _listing(_conversation("Nova — Cycle 722", 10))
    status, lines = _sweep(listing, {})
    assert status == 0
    assert "1 still inside the 60m grace" in lines[-1]


def test_a_cycle_older_than_the_window_is_not_judged():
    """A missed reply is permanent, so the alarm has to expire."""
    listing = _listing(_conversation("Nova — Cycle 600", 60 * 30))
    status, lines = _sweep(listing, {})
    assert status == 0
    assert "1 older than 24h" in lines[-1]


def test_a_thread_edvard_started_is_not_owed_a_reply():
    listing = _listing(_conversation("Improvements", 90, cycle=False))
    status, lines = _sweep(listing, {})
    assert status == 1
    assert any("listed no cycle threads" in line for line in lines)


def test_an_unreadable_thread_never_reads_as_clean():
    listing = _listing(_conversation("Nova — Cycle 719", 90, ident="gone"))
    status, lines = _sweep(listing, {})
    assert status == 1
    assert any("COULD NOT READ" in line for line in lines)


def test_an_unreadable_thread_outranks_a_silent_one():
    """Exit 1 must win: a sweep that could not finish is not a verdict."""
    listing = _listing(_conversation("Nova — Cycle 719", 90, ident="gone"),
                       _conversation("Nova — Cycle 721", 90, ident="silent"))
    threads = {"silent": _thread(_narration("Writing my reply now."))}
    status, lines = _sweep(listing, threads)
    assert status == 1
    assert any("NO REPLY" in line for line in lines)


def test_an_unreadable_listing_is_status_one():
    def get(url, timeout=30):
        raise OSError("connection refused")
    status, lines = reply_health.sweep(site="http://site", now=NOW, get=get)
    assert status == 1
    assert any("COULD NOT READ" in line for line in lines)


def test_a_conversation_with_no_timestamp_is_unreadable_not_clean():
    listing = _listing({"id": "x", "name": "Nova — Cycle 1", "cycleThread": True})
    status, lines = _sweep(listing, {})
    assert status == 1
    assert any("no timestamp" in line for line in lines)


def test_replied_reads_the_partial_flag_not_the_position():
    """The reply is not always last: a cycle can narrate after it."""
    assert reply_health.replied(_thread(_reply("done"), _narration("bye")))
    assert not reply_health.replied(_thread(_narration("a"), _narration("b")))


@pytest.mark.parametrize("minutes_ago,expected", [
    (10, "live"), (59, "live"), (61, "judge"),
    (60 * 23, "judge"), (60 * 25, "old")])
def test_the_two_gates_are_on_the_boundaries_they_claim(minutes_ago, expected):
    assert reply_health.judge(_conversation("c", minutes_ago), NOW, 60,
                              24) == expected


def test_the_relay_finds_the_words_in_a_thread_the_real_builder_produced():
    """The fixtures above are hand-written and were a shape simpler than the
    server's, which is how a real regression stayed green: Cycle 780 moved a
    silent cycle's prose out of the row's `text` and into its `steps`, and
    `last_narration` -- reading `text` -- answered "" for every silent cycle.
    The push to his phone dropped its "the last thing it said was" line and
    nothing said so.

    So this one builds the thread with `nova_conversations.visible_rows`, the
    function that actually feeds `/api/conversations/thread`. A hand-made
    fixture cannot catch that class at all; only the real builder can."""
    from agora_runner import nova_conversations, reply_check
    rows = nova_conversations.visible_rows([
        {"id": "a", "sender": "Edvard", "text": "how many pods?"},
        {"id": "b", "sender": "Nova", "text": "Bash: kubectl get pods",
         "activity": {"capability": "Bash", "detail": "kubectl get pods",
                      "toolUseId": "t1"}},
        {"id": "c", "sender": "Nova", "text": "assistant_text: Checking the cluster now.",
         "activity": {"capability": "assistant_text",
                      "detail": "Checking the cluster now."}},
    ])
    thread = {"messages": rows}
    # It really is a silent cycle: nothing here is a reply.
    assert reply_check.replied(thread) is True  # his own question is settled
    assert rows[-1]["stepsOnly"] is True
    assert reply_check.last_narration(thread) == "Checking the cluster now."


def test_the_relay_does_not_quote_a_tool_call_as_something_the_cycle_said():
    """"It ran kubectl" is not the sentence he is owed. A turn whose last
    step is a tool call has said nothing since its previous passage, and
    that passage is the honest answer."""
    from agora_runner import nova_conversations, reply_check
    rows = nova_conversations.visible_rows([
        {"id": "a", "sender": "Nova", "text": "assistant_text: Looking now.",
         "activity": {"capability": "assistant_text", "detail": "Looking now."}},
        {"id": "b", "sender": "Nova", "text": "Bash: kubectl get pods",
         "activity": {"capability": "Bash", "detail": "kubectl get pods",
                      "toolUseId": "t1"}},
    ])
    assert reply_check.last_narration({"messages": rows}) == "Looking now."
