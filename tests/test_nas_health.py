"""Tests for `tools.nas_health`.

Nothing here touches the real NAS. The two halves of the check are driven
separately: `connect` is a fake socket factory for the reachability half, and
`ssh` plus `get` stand in for the SSH hop and the app responses.

The one thing every test here is written to protect is the exit contract, in
both directions. A check that comes back 0 because it could not look is the
failure `tools.preflight` exists to prevent, and a check that comes back 2 on
a gap no pull request can close is the noise that makes a cycle stop reading
it. So there is a test for each: `CANNOT SEE FROM THIS POD` must exit 0 and
must say so on the page, and a service that is down must exit 2 even though
the box itself answered.
"""

import io
import socket

import pytest

from tools import nas, nas_health


class _FakeSock:
    def __init__(self, greeting):
        self.greeting = greeting
        self.closed = False

    def recv(self, _n):
        return self.greeting

    def close(self):
        self.closed = True


def _answers(greeting=b"SSH-2.0-OpenSSH_8.2\r\n"):
    seen = []

    def connect(address, timeout):
        seen.append((address, timeout))
        return _FakeSock(greeting)

    connect.seen = seen
    return connect


def _refuses(exc=None, delay=0.0):
    def connect(address, timeout):
        if delay:
            import time

            time.sleep(delay)
        raise exc or ConnectionRefusedError(111, "Connection refused")

    return connect


HOP = {"host": "10.0.0.9", "user": "nova", "key": "/etc/nas-ssh/id_ed25519"}


# --- the reachability half --------------------------------------------------


def test_probe_reads_the_banner_and_reports_the_time():
    ok, detail, seconds = nas_health.probe("nas", connect=_answers())
    assert ok
    assert "SSH-2.0-OpenSSH_8.2" in detail
    assert "s" in detail and seconds >= 0


def test_probe_rejects_a_port_that_answers_with_something_else():
    # A listener that is not sshd is not the NAS being up. Without this, any
    # process bound to 22 -- or a proxy that accepts and says nothing -- would
    # be read as a healthy box.
    ok, detail, _ = nas_health.probe("nas", connect=_answers(b"HTTP/1.1 400 Bad Request\r\n"))
    assert not ok
    assert "did not greet as SSH" in detail


def test_probe_names_a_local_denial_differently_from_a_dead_box():
    # These two raise the identical exception and mean opposite things: one is
    # our own NetworkPolicy, the other is the NAS being down.
    _, local, _ = nas_health.probe("nas", connect=_refuses())
    assert "this pod's own kernel" in local

    _, remote, _ = nas_health.probe(
        "nas", connect=_refuses(delay=nas_health.LOCAL_DENY_SECONDS * 3)
    )
    assert "this pod's own kernel" not in remote
    assert "no answer after" in remote


def test_unreachable_nas_exits_2():
    out = io.StringIO()
    code = nas_health.main([], env={}, out=out, connect=_refuses(), ssh=None)
    assert code == 2
    assert "NAS UNREACHABLE" in out.getvalue()


# --- the applications half --------------------------------------------------


def test_no_hop_on_this_pod_does_not_raise_the_status():
    out = io.StringIO()
    code = nas_health.main([], env={}, out=out, connect=_answers(), ssh=None)
    assert code == 0
    body = out.getvalue()
    assert "CANNOT SEE FROM THIS POD" in body
    # and it must not read as a clean sweep of the services
    assert "0 service(s)" in body


def test_a_service_that_is_down_exits_2_even_though_the_box_answered():
    def get(name, conf, path, **kw):
        if name == "radarr":
            raise nas.Unreachable("connection refused")
        return {"version": "3.0.9.1549"}

    env = {
        "SONARR_URL": "http://127.0.0.1:8989",
        "SONARR_API_KEY": "a",
        "RADARR_URL": "http://127.0.0.1:7878",
        "RADARR_API_KEY": "b",
    }
    out = io.StringIO()
    code = nas_health.main([], env=env, out=out, connect=_answers(), ssh=HOP, get=get)
    assert code == 2
    body = out.getvalue()
    assert "SERVICE DOWN" in body
    assert "REACHABLE" in body  # the box was up; only the app was not
    assert "2 service(s)" in body


def test_both_services_answering_is_a_clean_sweep():
    def get(name, conf, path, **kw):
        return {"version": "4.3.2.6857"}

    env = {
        "SONARR_URL": "http://127.0.0.1:8989",
        "SONARR_API_KEY": "a",
        "RADARR_URL": "http://127.0.0.1:7878",
        "RADARR_API_KEY": "b",
    }
    out = io.StringIO()
    code = nas_health.main([], env=env, out=out, connect=_answers(), ssh=HOP, get=get)
    assert code == 0
    body = out.getvalue()
    assert "SERVICES OK" in body
    assert "4.3.2.6857" in body
    assert "2 service(s)" in body


def test_a_hop_that_configures_nothing_is_unreadable_not_clean():
    # The hop exists, so this is not the bridge pod's expected gap -- something
    # that should have been readable was not, and 1 is the only honest answer.
    # `run` is faked so this never shells out to a real ssh: on a machine that
    # has one, discovery would spend the connect timeout dialling a fake host.
    def run(argv, **kw):
        raise FileNotFoundError("no ssh here")

    out = io.StringIO()
    code = nas_health.main([], env={}, out=out, connect=_answers(), ssh=HOP, run=run)
    assert code == 1
    assert "SERVICES UNREADABLE" in out.getvalue()


def test_the_host_is_named_even_with_no_hop_and_an_override_wins():
    out = io.StringIO()
    nas_health.main([], env={"NAS_SSH_HOST": "10.9.9.9"}, out=out, connect=_answers(), ssh=None)
    assert "10.9.9.9" in out.getvalue()
