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


def _locked(_hop):
    """nzbget answering that its control interface is locked. `False` is the
    healthy answer there and this check reads neither branch -- it reads that
    the call returned rather than raised."""
    return False


def _plex(_hop):
    return "1.41.6.9685-d301f511a"


def _bazarr(_hop):
    """Bazarr's key read off its front page. Like `_plex` above, this check
    reads that the call returned rather than what it returned."""
    return "deadbeefcafe0123deadbeefcafe0123"  # gitleaks:allow -- fabricated, not his key


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
    code = nas_health.main([], env=env, out=out, connect=_answers(), ssh=HOP, get=get,
                           nzbget=_locked, plex=_plex, bazarr=_bazarr)
    assert code == 2
    body = out.getvalue()
    assert "SERVICE DOWN" in body
    assert "REACHABLE" in body  # the box was up; only the app was not
    assert "5 service(s) of 5" in body  # all five were judged; one of them is down


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
    code = nas_health.main([], env=env, out=out, connect=_answers(), ssh=HOP, get=get,
                           nzbget=_locked, plex=_plex, bazarr=_bazarr)
    assert code == 0
    body = out.getvalue()
    assert "SERVICES OK" in body
    assert "4.3.2.6857" in body
    assert "5 service(s) of 5" in body


def test_a_hop_that_configures_nothing_is_unreadable_not_clean():
    # The hop exists, so this is not the bridge pod's expected gap -- something
    # that should have been readable was not, and 1 is the only honest answer.
    # `run` is faked so this never shells out to a real ssh: on a machine that
    # has one, discovery would spend the connect timeout dialling a fake host.
    def run(argv, **kw):
        raise FileNotFoundError("no ssh here")

    out = io.StringIO()
    code = nas_health.main([], env={}, out=out, connect=_answers(), ssh=HOP, run=run,
                           nzbget=_locked, plex=_plex, bazarr=_bazarr)
    assert code == 1
    body = out.getvalue()
    assert "SERVICES UNREADABLE" in body
    # Neither of these needs an API key, so a failed discovery must not turn a
    # live nzbget into an unjudged one.
    assert "nzbget answered over the hop" in body
    assert "3 service(s) of 5" in body


def test_the_host_is_named_even_with_no_hop_and_an_override_wins():
    out = io.StringIO()
    nas_health.main([], env={"NAS_SSH_HOST": "10.9.9.9"}, out=out, connect=_answers(), ssh=None)
    assert "10.9.9.9" in out.getvalue()


# --- the two services that answer with no credential -------------------------


def test_a_dead_nzbget_exits_2_even_though_the_arr_apps_are_fine():
    # This is the whole point of Cycle 648's change: before it, nzbget could be
    # down and this check said "2 service(s)" and exited 0.
    def get(name, conf, path, **kw):
        return {"version": "4.3.2.6857"}

    def dead(_hop):
        raise nas.Unreachable("curl on the NAS exited 7: connection refused")

    env = {
        "SONARR_URL": "http://127.0.0.1:8989",
        "SONARR_API_KEY": "a",
        "RADARR_URL": "http://127.0.0.1:7878",
        "RADARR_API_KEY": "b",
    }
    out = io.StringIO()
    code = nas_health.main([], env=env, out=out, connect=_answers(), ssh=HOP, get=get,
                           nzbget=dead, plex=_plex, bazarr=_bazarr)
    assert code == 2
    body = out.getvalue()
    assert "nzbget did not answer over the hop" in body
    assert "5 service(s) of 5" in body


def test_a_dead_plex_exits_2():
    def get(name, conf, path, **kw):
        return {"version": "4.3.2.6857"}

    def dead(_hop):
        raise nas.Unreachable("plex answered 000 on /identity")

    env = {
        "SONARR_URL": "http://127.0.0.1:8989",
        "SONARR_API_KEY": "a",
        "RADARR_URL": "http://127.0.0.1:7878",
        "RADARR_API_KEY": "b",
    }
    out = io.StringIO()
    code = nas_health.main([], env=env, out=out, connect=_answers(), ssh=HOP, get=get,
                           nzbget=_locked, plex=dead, bazarr=_bazarr)
    assert code == 2
    assert "plex did not answer over the hop" in out.getvalue()


def test_an_unlocked_nzbget_is_still_alive_here_and_does_not_raise():
    # `nas_watch` owns that verdict. If this check raised on it too, the same
    # finding would have to be cleared in two places.
    def get(name, conf, path, **kw):
        return {"version": "4.3.2.6857"}

    env = {
        "SONARR_URL": "http://127.0.0.1:8989",
        "SONARR_API_KEY": "a",
        "RADARR_URL": "http://127.0.0.1:7878",
        "RADARR_API_KEY": "b",
    }
    out = io.StringIO()
    code = nas_health.main([], env=env, out=out, connect=_answers(), ssh=HOP, get=get,
                           nzbget=lambda _hop: True, plex=_plex, bazarr=_bazarr)
    assert code == 0
    assert "5 service(s) of 5" in out.getvalue()


def test_no_hop_says_all_five_went_unjudged():
    out = io.StringIO()
    code = nas_health.main([], env={}, out=out, connect=_answers(), ssh=None)
    assert code == 0
    body = out.getvalue()
    assert "none of the 5 service(s)" in body
    assert "0 service(s) of 5" in body
