"""Scrolling back through a chat thread.

His capture, `issues.md` 2026-08-31, with a screenshot of the chat dock:
*"I can only see the latest messages in the chat. I can't scroll upwards
and see the earlier messages."*

Both thread endpoints answered with the newest `MAX_THREAD` messages and
took no parameter, so a thread longer than one page had a hard floor. 79 of
the 720 conversations in the store are longer than that (measured against
the live store the same morning).

What is worth pinning is what would silently break while the dock still
renders a thread:

- `hasMore` is counted on the **raw** rows Agora returned, not on the
  visible ones, because `_visible` drops narration and a page whose older
  half is all narration would report "nothing older" while older messages
  exist;
- the upstream fetch asks for `limit + 1`, because that extra row *is*
  `hasMore` -- ask for exactly `limit` and a full page always reads as the
  end of the thread;
- the clamp has a floor as well as a ceiling: `?limit=1` would otherwise
  make the dock open on one message and page forever.
"""
from unittest.mock import patch

import agora_runner.nova_ask as ask
import agora_runner.nova_conversations as convs


def _messages(count):
    return [
        {"id": f"m-{i}", "sender": "Edvard" if i % 2 else "Nova Answers",
         "text": f"message {i}", "ts": "2026-08-31T05:00:00.000Z"}
        for i in range(count)
    ]


def _thread(stored, limit, conversation_id="c-1"):
    """Run `convs.thread`, returning (payload, the upstream path asked for)."""
    seen = []

    def fake_get(path):
        seen.append(path)
        # Agora answers with the newest N.
        wanted = int(path.split("limit=")[1])
        return 200, {"messages": stored[-wanted:]}

    with patch.object(convs, "agora_get", side_effect=fake_get):
        return convs.thread(conversation_id, limit), seen[-1]


def test_a_thread_longer_than_the_page_says_there_is_more():
    payload, _ = _thread(_messages(120), convs.MAX_THREAD)
    assert len(payload["messages"]) == convs.MAX_THREAD
    assert payload["hasMore"] is True
    assert payload["messages"][-1]["text"] == "message 119"


def test_a_thread_that_fits_the_page_says_there_is_not():
    payload, _ = _thread(_messages(12), convs.MAX_THREAD)
    assert len(payload["messages"]) == 12
    assert payload["hasMore"] is False


def test_a_thread_exactly_one_page_long_is_not_reported_as_having_more():
    # The boundary the `limit + 1` fetch exists for: 40 stored, 40 asked for.
    payload, _ = _thread(_messages(convs.MAX_THREAD), convs.MAX_THREAD)
    assert len(payload["messages"]) == convs.MAX_THREAD
    assert payload["hasMore"] is False


def test_the_upstream_fetch_asks_for_one_more_than_the_page():
    _, path = _thread(_messages(120), convs.MAX_THREAD)
    assert path.endswith(f"limit={convs.MAX_THREAD + 1}")


def test_a_bigger_page_returns_more_and_still_reports_the_rest():
    payload, path = _thread(_messages(120), 80)
    assert len(payload["messages"]) == 80
    assert payload["hasMore"] is True
    assert path.endswith("limit=81")


def test_the_last_page_of_a_long_thread_reports_no_more():
    payload, _ = _thread(_messages(120), 120)
    assert len(payload["messages"]) == 120
    assert payload["hasMore"] is False


def test_has_more_counts_raw_rows_not_visible_ones():
    """The narration has to be *inside* the page, or this proves nothing.

    Agora answers with the newest `limit + 1` rows, so narration older than
    that window is never fetched and never counted either way. The rows that
    decide the question are the ones in the window: 41 raw rows of which 10
    are narration leave 31 visible, so counting the visible rows says "that
    is not a full page, the thread is finished" while Agora is holding 100
    more messages behind it.
    """
    window = []
    for i in range(41):
        window.append({"id": f"m-{i}", "sender": "Edvard", "text": f"message {i}",
                       "ts": "2026-08-31T05:00:00.000Z"})
        if i % 4 == 0:
            window.append({"id": f"n-{i}", "sender": "Nova Answers", "text": "step",
                           "system": True, "ts": "2026-08-31T05:00:00.000Z"})
    stored = _messages(100) + window
    payload, _ = _thread(stored, convs.MAX_THREAD)
    assert len(payload["messages"]) < convs.MAX_THREAD, (
        "the filter dropped nothing, so counting either list would pass")
    assert payload["hasMore"] is True


def test_a_limit_below_the_page_size_is_raised_to_it():
    assert convs.clamp_thread_limit(1) == convs.MAX_THREAD
    assert convs.clamp_thread_limit(0) == convs.MAX_THREAD
    assert convs.clamp_thread_limit(-5) == convs.MAX_THREAD


def test_a_limit_above_the_ceiling_is_capped():
    assert convs.clamp_thread_limit(10 ** 9) == convs.MAX_THREAD_CEILING
    assert convs.clamp_thread_limit(convs.MAX_THREAD_CEILING) == convs.MAX_THREAD_CEILING


def test_an_unreadable_limit_falls_back_to_the_page_size():
    for junk in ("", "forty", None, "12x"):
        assert convs.clamp_thread_limit(junk) == convs.MAX_THREAD


def test_the_ceiling_admits_the_longest_thread_in_the_store():
    # Measured 2026-08-31 from the runner pod: the longest conversation held
    # 500 messages. A ceiling below that would make some thread unreachable
    # in full however far he scrolled.
    assert convs.MAX_THREAD_CEILING >= 500


def test_an_empty_conversation_id_still_answers_the_question():
    payload = convs.thread("")
    assert payload["hasMore"] is False


def test_the_ask_thread_pages_the_same_way():
    stored = _messages(120)
    seen = []

    def fake_get(path):
        seen.append(path)
        if path == "/conversations":
            return 200, {"conversations": []}
        wanted = int(path.split("limit=")[1])
        return 200, {"messages": stored[-wanted:]}

    with patch.object(ask, "agora_get", side_effect=fake_get), \
         patch.object(ask, "conversation_id", return_value="c-ask"):
        payload = ask.thread(80)
    assert len(payload["messages"]) == 80
    assert payload["hasMore"] is True
    assert seen[-1].endswith("limit=81")


def test_the_ask_thread_clamps_its_limit_too():
    stored = _messages(10)

    def fake_get(path):
        wanted = int(path.split("limit=")[1])
        return 200, {"messages": stored[-wanted:]}

    with patch.object(ask, "agora_get", side_effect=fake_get), \
         patch.object(ask, "conversation_id", return_value="c-ask"):
        payload = ask.thread("junk")
    assert payload["hasMore"] is False
    assert len(payload["messages"]) == 10


def test_no_ask_conversation_still_answers_the_question():
    with patch.object(ask, "conversation_id", return_value=""):
        assert ask.thread()["hasMore"] is False


# The two routes, driven through the real handler. `nova_site` reads
# `limit` off the query string and hands it to the two `thread` functions
# above; without these, dropping the parameter from either route leaves
# every test on this page green while the dock can no longer page.

def _route(path, thread_fn_name, module):
    import agora_runner.nova_site as site
    from tests.test_nova_site import _get
    seen = {}

    def fake(*args):
        seen["args"] = args
        return {"conversationId": "c-1", "messages": [], "waiting": False,
                "hasMore": False}

    with patch.object(site, thread_fn_name, side_effect=fake):
        _get(path)
    return seen.get("args")


def test_the_conversation_route_passes_his_page_size_through():
    assert _route("/api/conversations/thread?id=c-1&limit=160",
                  "conversation_thread", convs) == ("c-1", "160")


def test_the_ask_route_passes_his_page_size_through():
    assert _route("/api/ask?limit=160", "ask_thread", ask) == ("160",)


# The drawer's second route. `/api/conversations/step` is separate from the
# thread for one measured reason (`nova_conversations._steps`): a tool output
# is capped at 20,000 characters and a window holds forty calls, so folding
# outputs into the thread would put up to 800KB on his phone for a drawer he
# may never open.

def _step_route(path, answer=None):
    import json
    import agora_runner.nova_site as site
    from tests.test_nova_site import _get
    seen = {}

    def fake(*args):
        seen["args"] = args
        return answer

    with patch.object(site, "conversation_step_output", side_effect=fake):
        status, _head, body = _get(path)
    return seen.get("args"), status, json.loads(body or b"{}")


def test_the_step_route_hands_back_what_the_call_returned():
    found = {"capability": "Bash", "input": "ls", "output": "a\nb",
             "status": "done"}
    _args, status, body = _step_route(
        "/api/conversations/step?id=c-1&tool=toolu_a", answer=found)
    assert status == 200
    assert body == found


def test_the_step_route_passes_the_thread_the_call_and_the_page_size():
    args, _status, _body = _step_route(
        "/api/conversations/step?id=c-1&tool=toolu_a&limit=160")
    assert args == ("c-1", "toolu_a", "160")


def test_a_call_this_thread_does_not_hold_is_a_404_not_an_empty_output():
    """"It returned nothing" and "I could not find it" are different answers
    and the drawer says which. A 200 with an empty body would draw an empty
    Output block over a call that may have printed a page."""
    _args, status, body = _step_route("/api/conversations/step?id=c-1&tool=gone")
    assert status == 404
    assert "error" in body
