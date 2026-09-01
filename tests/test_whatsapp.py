"""Tests for tools.whatsapp.

The ones that carry weight are the three failures a hand-rolled `curl`
makes invisible: a 503 that reads as sent, an unreachable bridge that
reads the same as a refused one, and a message the shell ate before the
tool ever saw it.
"""

import io
import json
import urllib.error

import pytest

from tools import whatsapp


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
    code, line = whatsapp.send("hello", opener=opener_returning(200, {"status": "sent"}))
    assert code == 0
    assert "sent" in line


def test_unpaired_bridge_does_not_read_as_sent():
    """The bug this tool exists to close: `curl` exits 0 on this 503."""
    code, line = whatsapp.send("hello", opener=opener_returning(503, {"error": "whatsapp connection not ready"}))
    assert code != 0
    assert code == 2
    assert "not connected" in line
    assert "nothing was sent" in line


def test_send_failure_and_unreachable_are_different_exit_codes():
    refused, _ = whatsapp.send("hello", opener=opener_returning(502, {"error": "send failed"}))
    unreachable, line = whatsapp.send("hello", opener=opener_that_cannot_connect())
    assert refused == 2
    assert unreachable == 1
    assert refused != unreachable
    assert "could not reach" in line


def test_an_unknown_status_still_reports_the_bridges_own_error():
    code, line = whatsapp.send("hello", opener=opener_returning(400, {"error": "text is required"}))
    assert code == 2
    assert "400" in line and "text is required" in line


def test_an_empty_message_is_refused_without_a_request():
    opener = opener_returning(200, {"status": "sent"})
    code, line = whatsapp.send("   \n  ", opener=opener)
    assert code == 2
    assert opener.seen == [], "a message the bridge would reject was still put on the wire"
    assert "empty" in line


def test_an_oversize_message_is_refused_with_its_own_length():
    opener = opener_returning(200, {"status": "sent"})
    body = "x" * (whatsapp.MAX_TEXT_BYTES + 1)
    code, line = whatsapp.send(body, opener=opener)
    assert code == 2
    assert opener.seen == []
    assert str(len(body)) in line


def test_a_message_just_under_the_limit_is_sent():
    """The complement of the test above: the cap refuses only what is over it."""
    opener = opener_returning(200, {"status": "sent"})
    code, _ = whatsapp.send("x" * whatsapp.MAX_TEXT_BYTES, opener=opener)
    assert code == 0
    assert len(opener.seen) == 1


def test_the_payload_is_the_bridges_own_shape_and_carries_no_prefix():
    opener = opener_returning(200, {"status": "sent"})
    whatsapp.send("the newspaper job is dead", opener=opener)
    request = opener.seen[0]
    assert request.full_url.endswith("/send")
    assert request.get_method() == "POST"
    payload = json.loads(request.data.decode())
    assert payload == {"text": "the newspaper job is dead"}, "the server adds the robot prefix itself"


def test_status_reads_health_not_healthz():
    """/healthz answers 200 while unpaired, so it cannot answer this question."""
    opener = opener_returning(200, {"status": "ok"})
    code, _ = whatsapp.status_of(opener=opener)
    assert code == 0
    assert opener.seen[0].full_url.endswith("/health")


def test_status_separates_unpaired_from_unreachable():
    unpaired, line = whatsapp.status_of(opener=opener_returning(503, {"status": "not_ready"}))
    unreachable, _ = whatsapp.status_of(opener=opener_that_cannot_connect())
    assert unpaired == 2
    assert unreachable == 1
    assert "not connected" in line


def test_text_from_a_file_keeps_backticks_and_newlines(tmp_path):
    """A shell would have run the backticks and dropped the text."""
    raw = "line one\n`whoami`\n\"quoted\" and 'quoted'\n"
    path = tmp_path / "msg.txt"
    path.write_text(raw, encoding="utf-8")
    args = whatsapp.build_parser().parse_args(["send", "--file", str(path)])
    assert whatsapp.read_text(args) == raw


def test_text_from_stdin():
    args = whatsapp.build_parser().parse_args(["send", "-"])
    assert whatsapp.read_text(args, stdin=io.StringIO("from a pipe")) == "from a pipe"


def test_a_message_given_twice_or_not_at_all_is_refused(tmp_path):
    path = tmp_path / "msg.txt"
    path.write_text("hi", encoding="utf-8")
    both = whatsapp.build_parser().parse_args(["send", "hi", "--file", str(path)])
    neither = whatsapp.build_parser().parse_args(["send"])
    for args in (both, neither):
        with pytest.raises(ValueError):
            whatsapp.read_text(args)


def test_dry_run_prints_the_message_and_sends_nothing(capsys, monkeypatch):
    called = []
    monkeypatch.setattr(whatsapp, "send", lambda *a, **k: called.append(a) or (0, "sent"))
    assert whatsapp.main(["send", "hello there", "--dry-run"]) == 0
    assert called == []
    out = capsys.readouterr().out
    assert "hello there" in out
    assert "🤖" in out


def test_dry_run_still_refuses_a_message_that_cannot_be_sent(capsys):
    assert whatsapp.main(["send", "   ", "--dry-run"]) == 2
    assert "empty" in capsys.readouterr().out
