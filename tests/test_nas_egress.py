"""Tests for `tools.nas_egress`.

Nothing here touches the real NAS: `ssh` stands in for the hop and `get` for
the two apps' download-client lists.

The exit contract is what every test protects, in both directions, because a
wrong answer here is worse than no check at all. A row pointing at a public
address must exit 2 even though everything was readable; an app that could not
be read must exit 1 rather than reading as an empty list, which is the failure
that would make an injected row invisible; a name this pod cannot resolve must
exit 1 rather than being cleared; and a second download client on his own LAN
must exit 0 and still be printed, because a check that goes red on a
legitimate action stops being read.
"""

import io

import pytest

from tools import nas, nas_egress


HOP = {"host": "nas.example", "user": "nova", "key": "/etc/nas-ssh/id_ed25519"}


def _conf(*services):
    return {name: {"url": f"http://127.0.0.1:{8989 + i}", "key": "k", "ssh": HOP}
            for i, name in enumerate(services)}


def _client(name="nzbget", host="192.168.0.119", port=6789, enable=True,
            implementation="Nzbget"):
    return {
        "name": name,
        "implementation": implementation,
        "enable": enable,
        "fields": [
            {"name": "host", "value": host},
            {"name": "port", "value": port},
            {"name": "username", "value": "nzbget"},
            {"name": "password", "value": "hunter2"},  # gitleaks:allow
        ],
    }


def _get_returning(by_service):
    """A fake `nas._get` that serves a canned download-client list per service."""
    asked = []

    def get(service, conf, path, **kwargs):
        asked.append((service, path))
        answer = by_service[service]
        if isinstance(answer, Exception):
            raise answer
        return answer

    get.asked = asked
    return get


def _report(by_service, ssh=HOP, get=None):
    """Run the report against canned lists, with `nas.config` stubbed out."""
    get = _get_returning(by_service) if get is None else get
    out = io.StringIO()
    conf = _conf(*sorted(by_service))
    original = nas.config
    nas.config = lambda env=None, ssh=None, run=None: conf
    try:
        status = nas_egress.report(env={}, out=out, get=get, ssh=ssh)
    finally:
        nas.config = original
    return status, out.getvalue()


@pytest.mark.parametrize("host", [
    "192.168.0.119", "10.0.0.5", "172.16.4.4", "127.0.0.1", "::1",
    "169.254.1.1", "100.89.37.25", "localhost", "nzbget", "nas.local",
    "203.0.113.7",
])
def test_local_hosts_are_local(host):
    assert nas_egress.classify(host) == nas_egress.LOCAL


@pytest.mark.parametrize("host", [
    "8.8.8.8", "93.184.216.34", "2001:4860:4860::8888",
    # A trailing dot is legal FQDN notation and `ip_address` refuses it, so
    # these used to fall through to the name branch and come out unjudged.
    "8.8.8.8.", "93.184.216.34.", " 8.8.8.8 ",
])
def test_public_addresses_are_off_lan(host):
    assert nas_egress.classify(host) == nas_egress.OFF_LAN


@pytest.mark.parametrize("host", ["evil.example.com", "", None, "   "])
def test_unresolvable_names_are_not_cleared(host):
    assert nas_egress.classify(host) == nas_egress.UNJUDGED


def test_a_non_string_host_classifies_rather_than_raising():
    """This parses a home box's JSON; a numeric host must not be a traceback."""
    assert nas_egress.classify(6789) == nas_egress.UNJUDGED
    status, text = _report({"sonarr": [_client(host=6789)]})
    assert status == 1
    assert "CANNOT JUDGE" in text


def test_lan_download_clients_exit_zero_and_are_printed():
    status, text = _report({"sonarr": [_client()], "radarr": [_client()]})
    assert status == 0
    assert "EVERY DOWNLOAD CLIENT IS ON HIS OWN NETWORK on 2 service(s)" in text
    assert "sonarr: nzbget | Nzbget -> 192.168.0.119:6789 (enabled)" in text
    assert "Judged the download clients of 2 service(s) of 2" in text


def test_a_public_destination_raises():
    status, text = _report({
        "sonarr": [_client(host="93.184.216.34", name="not-mine")],
        "radarr": [_client()],
    })
    assert status == 2
    assert "DOWNLOAD CLIENT OFF THE LAN" in text
    assert "sonarr: not-mine | Nzbget -> 93.184.216.34:6789 (enabled)" in text
    # The LAN row is still printed; a finding must not hide the rest of the list.
    assert "radarr: nzbget | Nzbget -> 192.168.0.119:6789 (enabled)" in text


def test_a_disabled_public_destination_still_raises():
    """The anomaly is the row existing; enabling it is one more open POST."""
    status, text = _report({"sonarr": [_client(host="8.8.8.8", enable=False)]})
    assert status == 2
    assert "(disabled)" in text


def test_a_second_lan_client_does_not_raise():
    """He may add another download client himself. That is not a finding."""
    status, text = _report({
        "sonarr": [_client(), _client(name="sab", host="192.168.0.50", port=8080,
                                      implementation="Sabnzbd")],
        "radarr": [_client()],
    })
    assert status == 0
    assert "sab | Sabnzbd -> 192.168.0.50:8080" in text


def test_an_unresolvable_name_exits_one_and_is_not_cleared():
    status, text = _report({"sonarr": [_client(host="downloads.example.com")]})
    assert status == 1
    assert "CANNOT JUDGE" in text
    assert "EVERY DOWNLOAD CLIENT IS ON HIS OWN NETWORK" not in text


def test_an_unreadable_service_is_not_an_empty_list():
    status, text = _report({
        "sonarr": nas.Unreachable("refused the key"),
        "radarr": [_client()],
    })
    assert status == 1
    assert "CANNOT JUDGE" in text
    assert "sonarr: refused the key" in text
    assert "Judged the download clients of 1 service(s) of 2" in text


def test_json_that_is_not_a_list_is_unreadable():
    status, text = _report({"sonarr": {"error": "unauthorized"}})
    assert status == 1
    assert "answered dict, not a list" in text


def test_a_finding_outranks_an_unreadable_service():
    status, _ = _report({
        "sonarr": nas.Unreachable("down"),
        "radarr": [_client(host="93.184.216.34")],
    })
    assert status == 2


def test_no_hop_says_so_and_does_not_raise():
    status, text = _report({}, ssh=None)
    assert status == 0
    assert "CANNOT SEE FROM THIS POD" in text
    assert "Judged 0 service(s)" in text


def test_no_configurable_service_is_not_a_clean_sweep():
    get = _get_returning({})
    out = io.StringIO()
    original = nas.config
    nas.config = lambda env=None, ssh=None, run=None: {}
    try:
        status = nas_egress.report(env={}, out=out, get=get, ssh=HOP)
    finally:
        nas.config = original
    assert status == 1
    assert "SERVICES UNREADABLE" in out.getvalue()


def test_it_only_ever_asks_for_the_download_client_endpoint():
    """The path actually requested, not just the constant's presence.

    The first version of this asserted only that the constant was in
    `nas.READ_ONLY`, which the `tools/nas.py` diff guarantees unconditionally.
    My reviewer mutated `report()` to request the notification endpoint
    instead and all 30 tests still passed, so it protected nothing.
    """
    get = _get_returning({"sonarr": [_client()], "radarr": [_client()]})
    _report({"sonarr": [_client()], "radarr": [_client()]}, get=get)
    assert get.asked == [("radarr", nas_egress.DOWNLOAD_CLIENT_PATH),
                         ("sonarr", nas_egress.DOWNLOAD_CLIENT_PATH)]
    assert nas_egress.DOWNLOAD_CLIENT_PATH in nas.READ_ONLY


def test_a_service_whose_key_discovery_failed_is_not_a_clean_sweep():
    """The bug this file's denominator used to hide, and the reason it matters.

    `nas.config` drops a service whose API-key discovery failed, so one
    transient fetch of sonarr's `/initialize.js` removed sonarr from the sweep
    entirely -- and with a denominator of `len(conf_all)` the report printed
    "1 of 1", said every download client was on his own network, and exited 0
    over an app it had never asked. Radarr answering cleanly must not clear
    sonarr.
    """
    status, text = _report({"radarr": [_client()]})
    assert status == 1
    assert "CANNOT JUDGE" in text
    assert "sonarr" in text
    assert "EVERY DOWNLOAD CLIENT IS ON HIS OWN NETWORK" not in text
    assert "Judged the download clients of 1 service(s) of 2" in text


def test_an_off_lan_row_still_outranks_a_service_that_was_never_asked():
    """A real finding must not be demoted to 1 by an unasked service beside it."""
    status, text = _report({"radarr": [_client(host="8.8.8.8")]})
    assert status == 2
    assert "DOWNLOAD CLIENT OFF THE LAN" in text
    assert "sonarr" in text
