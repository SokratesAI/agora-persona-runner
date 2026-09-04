"""The preflight check that watches the size of Marcus's state document.

The exit code is the whole product of this module, so every test asserts on
it. The thresholds are never re-spelled as literals here -- `WARN_BYTES` and
`BLOCK_BYTES` are imported, so a test cannot keep passing against a number
the module has moved away from, which is the drift the check exists to be
free of.

The failure this file is really built around is the one `prompt.md` names: a
positive result guaranteed in advance. A check whose subject is "how many
bytes did that URL return" will happily measure a proxy's error page, a
static file server, or an empty body, and report a small, healthy-looking
number for an app that is not running. So the unreachable, non-200, empty and
not-JSON cases each get their own test asserting exit 1, and none of them is
allowed to collapse into the clean path.
"""

import json
import urllib.error

import pytest

from tools.marcus_capacity import (
    BACKUPS_PER_DAY,
    BLOCK_BYTES,
    WARN_BYTES,
    human,
    main,
    read_state,
    report,
)


class _Response:
    """The slice of an http.client.HTTPResponse this module actually uses."""

    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(body, status=200):
    def open_it(url, timeout=None):
        return _Response(body, status)
    return open_it


def _raising(exc):
    def open_it(url, timeout=None):
        raise exc
    return open_it


def _lines(size):
    out = []
    code = report(size, out=out.append)
    return code, "\n".join(out)


# --- read_state: every failure is "I could not measure", never "it is small"

def test_reads_a_json_body_whole():
    body = json.dumps({"sessions": [1, 2, 3]}).encode()
    assert read_state("http://x", opener=_opener(body)) == body


def test_unreachable_is_none_not_zero():
    assert read_state("http://x", opener=_raising(urllib.error.URLError("no route"))) is None


def test_connection_error_is_none():
    assert read_state("http://x", opener=_raising(OSError("refused"))) is None


def test_non_200_is_none():
    body = json.dumps({"sessions": []}).encode()
    assert read_state("http://x", opener=_opener(body, status=503)) is None


def test_empty_body_is_none():
    assert read_state("http://x", opener=_opener(b"")) is None


def test_html_error_page_is_none_even_though_it_has_a_length():
    """The whole point: a static responder's page is bytes with a size.

    404 pages and proxy errors are the two things most likely to be sitting on
    that address, and both would report as a comfortably small state document
    if length alone were the measurement.
    """
    page = b"<!doctype html><title>404 Not Found</title>" * 4
    assert read_state("http://x", opener=_opener(page)) is None


# --- report: the exit contract

def test_unmeasured_exits_one_and_says_so():
    code, text = _lines(None)
    assert code == 1
    assert "CANNOT SEE" in text
    # It must not be possible to read this as a clean, small document.
    assert "OK" not in text.split("\n")[0]


def test_small_document_is_clean():
    code, text = _lines(11_256)
    assert code == 0
    assert "OK" in text


def test_just_under_the_warning_threshold_is_still_clean():
    code, _ = _lines(WARN_BYTES - 1)
    assert code == 0


def test_at_the_warning_threshold_raises():
    code, text = _lines(WARN_BYTES)
    assert code == 2
    assert "TOO BIG" in text


def test_at_the_block_threshold_raises_and_says_the_push_is_rejected():
    code, text = _lines(BLOCK_BYTES)
    assert code == 2
    assert "rejected" in text


def test_the_two_raising_bands_say_different_things():
    """A push that warns and a push that is refused are different problems.

    Both exit 2, so the exit code cannot separate them; the prose has to.
    """
    _, warn = _lines(WARN_BYTES + 1)
    _, block = _lines(BLOCK_BYTES + 1)
    assert warn != block


def test_clean_line_carries_the_daily_git_cost():
    """The number that makes the threshold make sense, not decoration.

    A reader who does not already know the backup commits the whole document
    every hour cannot tell why a 50 MiB JSON file is a problem at all.
    """
    size = 2 * 1024 * 1024
    _, text = _lines(size)
    assert human(size * BACKUPS_PER_DAY) in text


def test_clean_line_refuses_to_judge_the_pvc_request():
    """local-path enforces no quota, so this check must not imply it does."""
    _, text = _lines(11_256)
    assert "1Gi" in text and "disk_health" in text


# --- human()

@pytest.mark.parametrize("count,expected", [
    (0, "0 B"),
    (1023, "1023 B"),
    (1024, "1.0 KiB"),
    (11_256, "11.0 KiB"),
    (50 * 1024 * 1024, "50.0 MiB"),
    (3 * 1024 ** 3, "3.0 GiB"),
])
def test_human_units(count, expected):
    assert human(count) == expected


# --- main(): one read, and the size reported is the size measured

def test_main_reads_the_endpoint_exactly_once(monkeypatch):
    """A second HTTP call can answer differently from the first.

    The first draft of this module called `read_state` twice -- once to decide
    whether it was None and once for the length -- so the number printed was
    from a different request than the one that passed the check.
    """
    calls = []

    def fake(url, opener=None):
        calls.append(url)
        return json.dumps({"sessions": []}).encode()

    monkeypatch.setattr("tools.marcus_capacity.read_state", fake)
    assert main(["--url", "http://somewhere/api/state"]) == 0
    assert calls == ["http://somewhere/api/state"]


def test_main_exits_one_when_marcus_is_down(monkeypatch):
    monkeypatch.setattr("tools.marcus_capacity.read_state", lambda url, opener=None: None)
    assert main([]) == 1


def test_failure_line_names_the_address_actually_tried():
    """Printing the default on a failure sends the reader to the wrong door.

    Caught by the negative control on the live run: `--url` pointed at a dead
    port and the refusal quoted the default endpoint, which was up.
    """
    out = []
    assert report(None, out=out.append, url="http://elsewhere:9/api/state") == 1
    assert "http://elsewhere:9/api/state" in "\n".join(out)
