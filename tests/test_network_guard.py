"""The guard in conftest.py is only worth having if it actually holds.

These assert the three escapes that matter, in the order they were
found: a name lookup, a connect to a literal IP that never needs a name
lookup at all, and -- the one this is really for -- a call to the real
`agora_internal` helper against its real production default URL, which
is precisely what escaped its mocks and PATCHed live Agora in
agora-persona-runner#29.
"""
import socket
from urllib.parse import urlsplit

import pytest

from agora_runner import config
from agora_runner.http_util import agora_internal, http_json

GUARD_MARKER = "network access is blocked in tests"


def test_name_lookup_is_blocked():
    with pytest.raises(RuntimeError) as excinfo:
        socket.getaddrinfo("agora.agents.svc.cluster.local", 8081)
    assert GUARD_MARKER in str(excinfo.value)


def test_connect_to_a_literal_ip_is_blocked():
    """Blocking DNS alone would let this through -- an IP needs no lookup."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            sock.connect(("10.43.68.216", 8081))
        assert GUARD_MARKER in str(excinfo.value)
    finally:
        sock.close()


def test_http_json_cannot_reach_a_real_host():
    with pytest.raises(RuntimeError) as excinfo:
        http_json("GET", "http://192.0.2.1:8080/conversations")
    assert GUARD_MARKER in str(excinfo.value)
    assert "192.0.2.1" in str(excinfo.value)


def test_agora_internal_cannot_reach_production():
    """The #29 regression itself: an unmocked agora_internal must not be
    able to write to the live cluster just because the suite happens to
    be running inside it.

    Asserting on the target as well as the marker is deliberate. This is
    the one test in this file that must never be run with the guard
    removed to check it fails -- doing so would send a real PATCH to a
    real heartbeat row, which is the exact accident being prevented. So
    it proves the aim instead: the call really was pointed at the
    production internal API, and it really was stopped before any I/O.
    """
    host = urlsplit(config.AGORA_INTERNAL_URL).hostname
    with pytest.raises(RuntimeError) as excinfo:
        agora_internal("PATCH", "/heartbeats/some-id", {"forceRun": False})
    assert GUARD_MARKER in str(excinfo.value)
    assert host in str(excinfo.value)
