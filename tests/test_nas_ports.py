"""Tests for `tools.nas_ports`.

Nothing here touches the real NAS: a fake `run` stands in for the SSH hop and
returns the `PORT <n> <code> <exit>` lines curl writes.

The exit contract is what every test protects, in both directions. A port
reachable on the LAN that is not on the record must exit 2 even though the
sweep itself worked; a port that neither answered nor refused must exit 1
rather than being cleared, because a silent listener and a dropped packet look
identical; a port curl said nothing about must exit 1 rather than reading as
closed; and the expected state -- his DSM UI and his media apps answering --
must exit 0 and still print, because a check that goes red on the normal state
is one that stops being read.
"""

import io
import subprocess

import pytest

from tools import nas, nas_ports


HOP = {"host": "nas.example", "user": "nova", "key": "/etc/nas-ssh/id_ed25519"}

#: What the real box answered when this check was written (Cycle 649). Ports
#: not named here come back refused.
LIVE = {22: (0, 1), 139: (0, 28), 445: (0, 28), 5000: (200, 0), 5001: (400, 0),
        6789: (401, 0), 7878: (200, 0), 8989: (200, 0), 32400: (401, 0)}


def _runner(answers=None, returncode=7, stderr="", ports=None):
    """A fake `subprocess.run` that replies for every candidate port."""
    answers = LIVE if answers is None else answers

    def run(argv, input=None, capture_output=None, text=None, timeout=None):
        lines = []
        for port in (ports if ports is not None else sorted(nas_ports.CANDIDATES)):
            code, exit_code = answers.get(port, (0, 7))
            lines.append(f"PORT {port} {code:03d} {exit_code}")
        return subprocess.CompletedProcess(argv, returncode, "\n".join(lines) + "\n", stderr)

    return run


def _report(run=None, env=None, **kwargs):
    out = io.StringIO()
    status = nas_ports.report(env={} if env is None else env, out=out, ssh=HOP,
                              run=_runner(**kwargs) if run is None else run)
    return status, out.getvalue()


def test_the_live_baseline_is_clean_and_still_printed():
    status, text = _report()
    assert status == 0
    assert "NOTHING NEW IS REACHABLE" in text
    # The expected state is printed, not swallowed: he asked for everything
    # kept and nothing hidden.
    assert "8989/tcp  HTTP 200" in text
    assert "22/tcp  listening, not HTTP" in text
    assert "Judged 28 of 28 candidate port(s)" in text


def test_a_new_http_listener_raises_two():
    status, text = _report(answers={**LIVE, 8096: (200, 0)})
    assert status == 2
    assert "NEW LISTENER ON THE HOME LAN" in text
    assert "8096/tcp  HTTP 200  (jellyfin)" in text


def test_a_new_non_http_listener_raises_two():
    """ssh answers exit 1, not a status code -- a listener that does not speak
    HTTP is still a listener and must not be cleared for it."""
    status, text = _report(answers={**LIVE, 3306: (0, 1)})
    assert status == 2
    assert "3306/tcp  listening, not HTTP" in text


def test_an_unknown_port_that_times_out_cannot_be_judged():
    status, text = _report(answers={**LIVE, 9091: (0, 28)})
    assert status == 1
    assert "CANNOT JUDGE" in text
    assert "9091/tcp  no answer and no refusal" in text
    assert "NEW LISTENER" not in text


def test_a_baseline_port_that_times_out_is_on_the_record():
    """139 and 445 answer this way on the real box; that is the baseline, not
    a finding."""
    status, text = _report()
    assert status == 0
    assert "445/tcp  no answer and no refusal" in text
    assert "CANNOT JUDGE" not in text


def test_a_port_curl_said_nothing_about_is_not_read_as_closed():
    status, text = _report(ports=[p for p in nas_ports.CANDIDATES if p != 8989])
    assert status == 1
    assert "NOT SWEPT" in text
    assert "8989/tcp  (sonarr)" in text
    assert "Judged 27 of 28" in text


def test_a_baseline_port_gone_quiet_prints_without_raising():
    """Whether a service is down is tools.nas_health's question. Two checks
    answering it is two places to fix one answer."""
    status, text = _report(answers={k: v for k, v in LIVE.items() if k != 7878})
    assert status == 0
    assert "GONE QUIET" in text
    assert "7878/tcp  closed" in text


def test_an_ssh_failure_is_not_a_clean_sweep():
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 255, "", "Permission denied")

    status, text = _report(run=run)
    assert status == 1
    assert "CANNOT SEE" in text
    assert "An unswept port is not a closed one." in text


def test_a_pod_with_no_hop_judges_nothing_and_does_not_raise():
    out = io.StringIO()
    assert nas_ports.report(env={}, out=out, ssh=None) == 0
    assert "CANNOT SEE FROM THIS POD" in out.getvalue()
    assert "Judged 0 port(s)" in out.getvalue()


def test_a_public_lan_address_is_refused():
    """The address is env-overridable, so the one thing this must never do is
    sweep two dozen ports on a host that is not his."""
    with pytest.raises(ValueError):
        nas_ports.lan_address({"NAS_LAN_ADDR": "8.8.8.8"})
    status, text = _report(env={"NAS_LAN_ADDR": "8.8.8.8"})
    assert status == 1
    assert "refusing to sweep" in text


def test_the_tailnet_address_is_accepted_and_a_name_is_not():
    """100.64.0.0/10 is neither private nor global on this Python, and it is
    the address the SSH hop itself uses -- so an `is_private` guard would
    refuse the one host this tool exists to ask about."""
    assert nas_ports.lan_address({"NAS_LAN_ADDR": "100.89.37.25"}) == "100.89.37.25"
    with pytest.raises(ValueError):
        nas_ports.lan_address({"NAS_LAN_ADDR": "nas.local"})


def test_write_out_is_per_transfer_or_every_line_names_the_last_port():
    """`next` is load-bearing: without it curl's options are global, the last
    `write-out` wins, and all 28 result lines come back carrying port 32469.
    That is what the first draft of this did against the real box."""
    config = nas_ports.config_for("192.168.0.119", [22, 5000, 8989])
    assert config.count("\nnext\n") == 2
    assert not config.rstrip().endswith("next")
    assert 'write-out = "PORT 22 %{http_code} %{exitcode}\\n"' in config
    assert 'write-out = "PORT 8989 %{http_code} %{exitcode}\\n"' in config


def test_the_probe_is_a_plain_get_and_carries_no_body():
    """The remote command stays the constant `curl --config -`; nothing here
    may turn it into a write."""
    seen = {}

    def run(argv, input=None, **kwargs):
        seen["argv"], seen["input"] = argv, input
        return subprocess.CompletedProcess(argv, 7, "", "")

    nas_ports.sweep(HOP, "192.168.0.119", [8989], run=run)
    assert seen["argv"][-1] == "curl --config -"
    for forbidden in ("request =", "data =", "upload-file", "POST"):
        assert forbidden not in seen["input"]


def test_every_baseline_port_is_a_candidate():
    """A baseline entry for a port nothing asks about would silently never be
    checked."""
    assert set(nas_ports.BASELINE) <= set(nas_ports.CANDIDATES)
