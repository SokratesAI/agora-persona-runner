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

#: Real rows off the NAS, captured 2026-08-30 12:07 Oslo, byte for byte. They
#: are here rather than hand-written because the byte order of the address
#: field is the one thing in this module that fails silently: a wrong reversal
#: yields a plausible address rather than an error.
PROC_TCP = """\
  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:B55C 00000000:0000 0A 00000000:00000000 00:00000000 00000000 297536        0 48391736 1 ffff880137968000 100 0 0 10 0
   1: 7744A8C0:545C 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 917681096 1 ffff880137968700 100 0 0 10 0
   2: 00000000:231D 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345678 1 ffff880137968800 100 0 0 10 0
   3: 00000000:1A6F 00000000:0000 0A 00000000:00000000 00:00000000 00000000 244383        0 12345679 1 ffff880137968900 100 0 0 10 0
   4: 0100007F:1538 0100007F:2710 01 00000000:00000000 00:00000000 00000000     0        0 12345680 1 ffff880137968a00 100 0 0 10 0
  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000000000000000000000000000:7E90 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000 297536        0 48391737 1 ffff880137968b00 100 0 0 10 0
"""

#: The ports `PROC_TCP` holds, in the shape `parse_listeners` returns.
PROC_PORTS = {46428: ["127.0.0.1"], 21596: ["192.168.68.119"], 8989: ["0.0.0.0"],
              6767: ["0.0.0.0"], 32400: ["::"]}


def _runner(answers=None, returncode=7, stderr="", ports=None, table=PROC_TCP,
            table_returncode=0):
    """A fake `subprocess.run` standing in for both remote commands.

    It dispatches on the command argv ends with, because this module now makes
    two different SSH calls and a fake that answers curl's shape to both would
    make the listener read look permanently broken.
    """
    answers = LIVE if answers is None else answers

    def run(argv, input=None, capture_output=None, text=None, timeout=None):
        if argv[-1] == nas_ports.LISTENER_COMMAND:
            return subprocess.CompletedProcess(argv, table_returncode, table, stderr)
        swept = ports
        if swept is None:
            swept = sorted(set(nas_ports.CANDIDATES) | set(nas_ports.parse_listeners(table)))
        lines = []
        for port in swept:
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
    assert "read from the box's own TCP listener table" in text


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


def test_the_address_field_is_read_in_the_right_byte_order():
    """The one silent failure in this module: a wrong reversal yields a
    plausible address, not an error. Pinned against real captured rows."""
    assert nas_ports.parse_listeners(PROC_TCP) == PROC_PORTS


def test_a_socket_that_is_not_listening_is_not_a_listener():
    """Row 4 of the fixture is an ESTABLISHED connection (`st` 01)."""
    assert 5432 not in nas_ports.parse_listeners(PROC_TCP)


def test_a_listening_port_outside_the_candidate_list_is_swept():
    """The whole point: a port nothing hand-wrote is still probed and still
    raises. The example used to be 6767, a real listener on the box -- Bazarr
    was uninstalled Cycle 676 and 6767 is now in neither list, so this uses a
    port that has never been in either and would break the same way if one of
    them were ever added."""
    assert 9999 not in nas_ports.CANDIDATES and 9999 not in nas_ports.BASELINE
    # One synthetic row, on 0.0.0.0:270F. The byte-order pin lives in its own
    # test against the captured `PROC_TCP`; all this one needs is a listener
    # the record has never heard of.
    unknown = PROC_TCP.replace(
        "   3: 00000000:1A6F",
        "   5: 00000000:270F 00000000:0000 0A 00000000:00000000 00:00000000 "
        "00000000     0        0 12345681 1 ffff880137968c00 100 0 0 10 0\n"
        "   3: 00000000:1A6F")
    status, text = _report(answers={**LIVE, 9999: (200, 0)}, table=unknown)
    assert status == 2
    assert "NEW LISTENER ON THE HOME LAN" in text
    assert "9999/tcp  HTTP 200  (not a candidate port, listening on 0.0.0.0)" in text


def test_a_connection_that_is_not_http_is_a_listener_not_an_unswept_port():
    """curl 52 and 56 are `synobtrfsreplica` and `tailscaled` on the real box.
    Both connected; neither spoke HTTP. Read as unswept -- which is what
    happened until Cycle 670 -- a measured listener reports as unmeasured, and
    `tools.nas_ports` exits 1 forever on two ports that answered every time."""
    for code in (52, 56):
        status, text = _report(answers={**LIVE, 5566: (0, code)})
        assert "NOT SWEPT" not in text, code
        assert "5566/tcp  listening, not HTTP" in text, code
        # 5566 is on the record, so a listener there is printed, not raised.
        assert status == 0, code


def test_the_services_i_installed_myself_are_on_the_record():
    """Heimdall, Prowlarr and Tautulli are containers I started on that box in
    cycles 666, 667 and 669. Each one raised as an unrecorded listener on the
    home LAN until this record caught up with my own journal."""
    for port in (8085, 8181, 9696):
        assert port in nas_ports.BASELINE, port


def test_an_unreadable_listener_table_says_so_and_does_not_read_as_clean():
    """An empty table and an unread one look identical and mean opposite
    things, so the fallback to the curated list has to raise."""
    status, text = _report(table_returncode=1, stderr="cat: /proc/net/tcp: no")
    assert status == 1
    assert "the listener table did not read" in text
    assert "curated candidate list" in text


def test_the_listener_command_carries_nothing_variable():
    """It is a constant, and that is what makes a second remote command a
    smaller surface than the curl one rather than a larger one."""
    seen = []

    def run(argv, input=None, capture_output=None, text=None, timeout=None):
        seen.append((argv[-1], input))
        return _runner()(argv, input=input, capture_output=capture_output,
                         text=text, timeout=timeout)

    nas_ports.report(env={}, out=io.StringIO(), ssh=HOP, run=run)
    table_calls = [c for c in seen if c[0] == nas_ports.LISTENER_COMMAND]
    assert table_calls == [(nas_ports.LISTENER_COMMAND, None)]
    assert nas_ports.LISTENER_COMMAND == "cat /proc/net/tcp /proc/net/tcp6"
