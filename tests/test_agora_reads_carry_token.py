"""Every runner call to Agora's public app carries the agent token (issue #287).

The guard that refuses an untokened read on :8080 can only land after the
runner sends the token there, or it silences this loop's own polling.
"""

import pytest

from agora_runner import http_util


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_http_json(method, url, payload=None, headers=None, timeout=30):
        calls.append(headers or {})
        return 200, {}

    def fake_http_bytes(url, timeout=30, headers=None):
        calls.append(headers or {})
        return 200, b"x"

    monkeypatch.setattr(http_util, "http_json", fake_http_json)
    monkeypatch.setattr(http_util, "http_bytes", fake_http_bytes)
    return calls


@pytest.mark.parametrize("call", [
    lambda: http_util.agora_get("/conversations/c/messages"),
    lambda: http_util.agora_public("DELETE", "/conversations/c"),
    lambda: http_util.agora_internal("POST", "/notify", {}),
    lambda: http_util.fetch_attachment_bytes("a"),
])
def test_token_sent_when_configured(monkeypatch, sent, call):
    monkeypatch.setattr(http_util, "AGORA_TOKEN", "tok")
    call()
    assert sent == [{"x-agora-token": "tok"}]


@pytest.mark.parametrize("call", [
    lambda: http_util.agora_get("/conversations"),
    lambda: http_util.fetch_attachment_bytes("a"),
])
def test_no_header_without_a_token(monkeypatch, sent, call):
    """The bridge pod holds no token; an empty header value would be a
    credential that is wrong rather than one that is absent."""
    monkeypatch.setattr(http_util, "AGORA_TOKEN", "")
    call()
    assert sent == [{}]
