"""Tests for `tools.nas_privilege`.

Nothing here touches the real NAS: `run` stands in for the ssh hop.

The whole value of this check is that an *unmeasured* privilege never reads as
a measured one, so that is what most of these protect, and in both directions.
A hop that fails must exit 1 and must not print either "I have root" or "I lost
root". A probe that answers a short report -- one key missing -- must exit 1
rather than defaulting the absent key, because a default is exactly the
inherited assumption this file exists to delete. And losing root must exit 0
and say so plainly: the owner narrowing his own box is an ordinary act, and a
check that goes red on it is one I would learn to ignore.
"""

import io
import subprocess

import pytest

from tools import nas, nas_privilege


HOP = {"host": "nas.example", "user": "nova", "key": "/etc/nas-ssh/id_ed25519"}

FULL = ("user=nova\nroot=yes\ndockerbin=/usr/local/bin/docker\n"
        "docker=yes\nstanding=yes\n")


def _run(stdout="", returncode=0, stderr="", record=None):
    def run(argv, **kwargs):
        if record is not None:
            record.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
    return run


def _report(stdout="", returncode=0, stderr="", ssh=HOP, record=None):
    out = io.StringIO()
    status = nas_privilege.report(
        out=out, ssh=ssh, run=_run(stdout, returncode, stderr, record))
    return status, out.getvalue()


def test_standing_root_is_reported_and_does_not_raise():
    status, text = _report(FULL)
    assert status == 0
    assert "I can become root without a password" in text
    assert "nova-full-access: present" in text
    assert "docker daemon: reachable through sudo" in text


def test_losing_root_is_reported_plainly_and_still_exits_zero():
    status, text = _report("user=nova\nroot=no\ndockerbin=none\n"
                           "docker=nobinary\nstanding=no\n")
    assert status == 0
    assert "I cannot become root" in text
    # The sudoers line is only meaningful when root was granted; printing
    # "absent" beside "I cannot become root" is two ways of saying one thing.
    assert "nova-full-access" not in text
    assert "docker daemon: not reachable" in text


def test_root_without_the_grant_file_says_so_rather_than_assuming_it():
    status, text = _report("user=nova\nroot=yes\ndockerbin=/usr/local/bin/docker\n"
                           "docker=yes\nstanding=no\n")
    assert status == 0
    assert "root comes from somewhere else" in text


def test_ssh_failure_is_unmeasured_not_a_lost_privilege():
    status, text = _report(returncode=255, stderr="Connection refused")
    assert status == 1
    assert "CANNOT SEE" in text
    assert "Connection refused" in text
    assert "I can become root" not in text
    assert "I cannot become root" not in text
    assert "Judged 0 host(s) of 1" in text


def test_a_missing_key_is_unreadable_rather_than_defaulted():
    # `standing` absent: the box answered, but not the whole promise. A check
    # that filled this in would be inheriting an assumption, which is the exact
    # failure this module was built to remove.
    status, text = _report("user=nova\nroot=yes\ndockerbin=/x\ndocker=yes\n")
    assert status == 1
    assert "did not answer: standing" in text


def test_no_hop_at_all_is_cannot_see():
    status, text = _report(ssh=None)
    assert status == 1
    assert "no ssh binary or no readable key" in text
    assert "Judged 0 host(s)." in text


def test_a_timeout_is_unmeasured():
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 30)
    out = io.StringIO()
    assert nas_privilege.report(out=out, ssh=HOP, run=run) == 1
    assert "CANNOT SEE" in out.getvalue()


def test_the_remote_command_is_the_constant_with_no_variable_part():
    # Two different hops have to produce a byte-identical remote string. That
    # is the assertion that actually holds the contract: not "the user's name
    # does not appear" -- the grant file is literally called nova-full-access
    # and that is a constant, not an interpolation -- but that nothing the
    # caller controls can reach the far side. Same contract as
    # nas_ports.LISTENER_COMMAND.
    other = {"host": "other.example", "user": "someone", "key": "/tmp/other-key"}
    first, second = [], []
    _report(FULL, record=first)
    _report(FULL, ssh=other, record=second)
    assert first[0][-1] == second[0][-1] == nas_privilege.PRIVILEGE_COMMAND
    assert f"{HOP['user']}@{HOP['host']}" in first[0]
    assert f"{other['user']}@{other['host']}" in second[0]
    assert other["host"] not in nas_privilege.PRIVILEGE_COMMAND
    assert other["key"] not in nas_privilege.PRIVILEGE_COMMAND


def test_parse_ignores_noise_lines_but_not_a_missing_promise():
    # A DSM login banner ahead of the output must not break the parse.
    found = nas_privilege.parse("Welcome to nas\n" + FULL)
    assert found["root"] == "yes"
    with pytest.raises(nas_privilege.Unreadable):
        nas_privilege.parse("Welcome to nas\n")


def test_it_is_registered_in_preflight():
    from tools import preflight
    assert "nas_privilege" in preflight.CHECKS
