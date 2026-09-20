"""Tests for the real-subprocess guard in `tests/conftest.py`.

The guard is the only thing standing between a missing stub and a real ssh
at the NAS during a unit run, so it needs tests of its own -- a guard that
silently stopped matching would look exactly like a suite with no leaks.
"""

import subprocess

import pytest

from conftest import (
    BLOCKED_BINARIES,
    NetworkBlockedInTests,
    _blocked_binary_in,
    _pass_through_unless_default_runner,
)


@pytest.mark.parametrize("argv, expected", [
    (["kubectl", "get", "pods"], "kubectl"),
    (["/usr/local/bin/kubectl", "get", "pods"], "kubectl"),
    (["ssh", "-i", "/etc/nas-ssh/id_ed25519", "nova@nas"], "ssh"),
    (["rsync", "-a", "src", "dst"], "rsync"),
    (["git", "log", "-1"], None),
    (["python3", "-m", "tools.preflight"], None),
    ([], None),
    # A shell is where the name moves out of argv[0].
    (["bash", "-lc", "kubectl get pods -n agents"], "kubectl"),
    (["sh", "-c", "ssh nas true"], "ssh"),
    # ...but only when it is actually running a command string, and not on a
    # word that merely contains one.
    (["bash", "script.sh"], None),
    (["bash", "-lc", "echo kubectlish"], None),
])
def test_it_names_the_binary_an_argv_would_actually_run(argv, expected):
    assert _blocked_binary_in(argv) == expected


def test_git_is_deliberately_not_blocked():
    # 2,641 of the 2,912 real processes a full run spawns are git against
    # temporary local repositories, which is the thing under test.
    assert "git" not in BLOCKED_BINARIES


def test_a_real_kubectl_is_refused_rather_than_run():
    with pytest.raises(NetworkBlockedInTests) as raised:
        subprocess.run(["kubectl", "get", "pods"], capture_output=True)
    assert "kubectl" in str(raised.value)


def test_a_shell_string_hiding_ssh_is_refused_too():
    with pytest.raises(NetworkBlockedInTests):
        subprocess.run("ssh nas true", shell=True, capture_output=True)


def test_a_local_process_still_runs():
    proc = subprocess.run(["echo", "hello"], capture_output=True, text=True)
    assert proc.stdout.strip() == "hello"


def _default_runner(*args, **kwargs):
    raise AssertionError("the default runner must never be called")


def test_the_stand_in_is_hermetic_when_the_default_runner_arrives():
    stand_in = _pass_through_unless_default_runner(
        _default_runner, _default_runner, ("hermetic", None), "run")
    assert stand_in("anything") == ("hermetic", None)
    assert stand_in("anything", run=_default_runner) == ("hermetic", None)


def test_the_stand_in_hands_a_supplied_runner_through_to_the_real_function():
    def real(*args, **kwargs):
        return "real", kwargs.get("run")

    mine = lambda *a, **k: None
    stand_in = _pass_through_unless_default_runner(
        real, _default_runner, ("hermetic", None), "run")
    assert stand_in("x", run=mine) == ("real", mine)
    # Positional, which is how `tautulli_key(ssh, env, run)` is called.
    assert stand_in("x", mine)[0] == "real"
