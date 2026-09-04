"""Tests for tools.telegram.

The ones that carry weight are the three failures a hand-rolled `curl`
makes invisible: a 503 that reads as sent, an unreachable bridge that
reads the same as a refused one, and a message the shell ate before the
tool ever saw it.
"""

import io
import json
import urllib.error

import pytest

from tools import telegram


class FakeResponse(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(json.dumps(body).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(status, body=None):
    """A urlopen stand-in that records every request it is handed."""
    seen = []

    def opener(request, timeout=None):
        seen.append(request)
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "", {}, io.BytesIO(json.dumps(body or {}).encode())
            )
        return FakeResponse(status, body or {})

    opener.seen = seen
    return opener


def opener_that_cannot_connect():
    def opener(request, timeout=None):
        raise urllib.error.URLError("Connection refused")

    return opener


def test_a_sent_message_exits_zero():
    code, line = telegram.send("hello", opener=opener_returning(200, {"status": "sent"}))
    assert code == 0
    assert "sent" in line


def test_unconfigured_bridge_does_not_read_as_sent():
    """The bug this tool exists to close: `curl` exits 0 on this 503."""
    code, line = telegram.send("hello", opener=opener_returning(503, {"error": "no owner chat id yet"}))
    assert code != 0
    assert code == 2
    assert "cannot send yet" in line
    assert "nothing was sent" in line


def test_send_failure_and_unreachable_are_different_exit_codes():
    refused, _ = telegram.send("hello", opener=opener_returning(502, {"error": "send failed"}))
    unreachable, line = telegram.send("hello", opener=opener_that_cannot_connect())
    assert refused == 2
    assert unreachable == 1
    assert refused != unreachable
    assert "could not reach" in line


def test_an_unknown_status_still_reports_the_bridges_own_error():
    code, line = telegram.send("hello", opener=opener_returning(400, {"error": "text is required"}))
    assert code == 2
    assert "400" in line and "text is required" in line


def test_an_empty_message_is_refused_without_a_request():
    opener = opener_returning(200, {"status": "sent"})
    code, line = telegram.send("   \n  ", opener=opener)
    assert code == 2
    assert opener.seen == [], "a message the bridge would reject was still put on the wire"
    assert "empty" in line


def test_an_oversize_message_is_refused_with_its_own_length():
    opener = opener_returning(200, {"status": "sent"})
    body = "x" * (telegram.MAX_TEXT_BYTES + 1)
    code, line = telegram.send(body, opener=opener)
    assert code == 2
    assert opener.seen == []
    assert str(len(body)) in line


def test_a_message_just_under_the_guideline_is_sent_with_nothing_said():
    opener = opener_returning(200, {"status": "sent"})
    code, line = telegram.send("x" * telegram.GUIDELINE_CHARS, opener=opener)
    assert code == 0
    assert len(opener.seen) == 1
    assert "guideline" not in line


# --- "not a hard cap at 280 please, more like a guideline" (him, 2026-09-04) ---

def test_a_message_over_the_guideline_still_reaches_the_wire():
    """The correction. Cycle 915 refused this and he said not to.

    The thing being protected is not brevity, it is him hearing something
    that mattered: a length rule that can stop a message is a rule that can
    make a cycle stay quiet.
    """
    opener = opener_returning(200, {"status": "sent"})
    code, line = telegram.send("x" * (telegram.GUIDELINE_CHARS + 1), opener=opener)
    assert code == 0
    assert len(opener.seen) == 1
    assert "guideline" in line


def test_the_sender_is_told_by_how_much_it_went_over():
    # The guideline has to still guide, and the only pressure left is that
    # the author is handed the measurement on the way past.
    opener = opener_returning(200, {"status": "sent"})
    _, line = telegram.send("x" * (telegram.GUIDELINE_CHARS + 40), opener=opener)
    assert str(telegram.GUIDELINE_CHARS + 40) in line
    assert "40 over" in line


def test_the_bare_send_cli_says_it_too(capsys):
    """This CLI is what the module docstring tells a cycle to type."""
    code = telegram.main(["send", "x" * (telegram.GUIDELINE_CHARS + 1), "--dry-run"])
    assert code == 0
    assert "guideline" in capsys.readouterr().out


def test_the_byte_ceiling_still_refuses():
    # The two numbers are different kinds of thing: his taste advises, the
    # bridge's own ceiling still refuses, and softening one must not soften
    # the other.
    opener = opener_returning(200, {"status": "sent"})
    code, _ = telegram.send("x" * (telegram.MAX_TEXT_BYTES + 1), opener=opener)
    assert code == 2
    assert opener.seen == []


def test_his_own_example_of_enough_is_short_enough():
    example = (
        "Yes - done, and it was the nameapace cap that was holding it, "
        "not an oversight"
    )
    assert telegram.over_guideline(example) is None


def test_the_payload_is_the_bridges_own_shape_and_carries_no_prefix():
    opener = opener_returning(200, {"status": "sent"})
    telegram.send("the newspaper job is dead", opener=opener)
    request = opener.seen[0]
    assert request.full_url.endswith("/send")
    assert request.get_method() == "POST"
    payload = json.loads(request.data.decode())
    assert payload == {"text": "the newspaper job is dead"}, "the server adds the robot prefix itself"


def test_status_reads_health_not_healthz():
    """/healthz answers 200 while unconfigured, so it cannot answer this question."""
    opener = opener_returning(200, {"status": "ok"})
    code, _ = telegram.status_of(opener=opener)
    assert code == 0
    assert opener.seen[0].full_url.endswith("/health")


def test_status_separates_unconfigured_from_unreachable():
    unconfigured, line = telegram.status_of(
        opener=opener_returning(503, {"hint": "set the telegram-bridge-owner secret"})
    )
    unreachable, _ = telegram.status_of(opener=opener_that_cannot_connect())
    assert unconfigured == 2
    assert unreachable == 1
    # The bridge names which half is missing -- a missing token is the owner's
    # problem and a missing owner chat id is his phone's. Swallowing the hint
    # would collapse two different jobs into one "not ready".
    assert "set the telegram-bridge-owner secret" in line


def test_status_says_something_when_the_bridge_sends_no_hint():
    """An older bridge, or a truncated body: still exit 2, still readable."""
    code, line = telegram.status_of(opener=opener_returning(503, {}))
    assert code == 2
    assert "not ready" in line


def test_text_from_a_file_keeps_backticks_and_newlines(tmp_path):
    """A shell would have run the backticks and dropped the text."""
    raw = "line one\n`whoami`\n\"quoted\" and 'quoted'\n"
    path = tmp_path / "msg.txt"
    path.write_text(raw, encoding="utf-8")
    args = telegram.build_parser().parse_args(["send", "--file", str(path)])
    assert telegram.read_text(args) == raw


def test_text_from_stdin():
    args = telegram.build_parser().parse_args(["send", "-"])
    assert telegram.read_text(args, stdin=io.StringIO("from a pipe")) == "from a pipe"


def test_a_message_given_twice_or_not_at_all_is_refused(tmp_path):
    path = tmp_path / "msg.txt"
    path.write_text("hi", encoding="utf-8")
    both = telegram.build_parser().parse_args(["send", "hi", "--file", str(path)])
    neither = telegram.build_parser().parse_args(["send"])
    for args in (both, neither):
        with pytest.raises(ValueError):
            telegram.read_text(args)


def test_dry_run_prints_the_message_and_sends_nothing(capsys, monkeypatch):
    called = []
    monkeypatch.setattr(telegram, "send", lambda *a, **k: called.append(a) or (0, "sent"))
    assert telegram.main(["send", "hello there", "--dry-run"]) == 0
    assert called == []
    out = capsys.readouterr().out
    assert "hello there" in out
    assert "🤖" in out


def test_dry_run_still_refuses_a_message_that_cannot_be_sent(capsys):
    assert telegram.main(["send", "   ", "--dry-run"]) == 2
    assert "empty" in capsys.readouterr().out
