"""Tests for `tools.nas_watch`.

Nothing here touches the real NAS: `ssh` stands in for the hop and `get` for
the two apps' notification lists.

The exit contract is what every test protects, in both directions, because
this check exists to answer one question and a wrong answer is worse than no
check. A `CustomScript` row must exit 2 even though everything was readable;
an app that could not be read must exit 1 rather than reading as an empty
list, which is the failure that would make an injected row invisible; and an
ordinary Discord notification must exit 0 and still be printed, because a
check that goes red on a legitimate action stops being read.
"""

import io

import pytest

from tools import nas, nas_watch


HOP = {"host": "nas.example", "user": "nova", "key": "/etc/nas-ssh/id_ed25519"}


def _conf(*services):
    return {name: {"url": f"http://127.0.0.1:{8989 + i}", "key": "k", "ssh": HOP}
            for i, name in enumerate(services)}


def _get_returning(by_service):
    """A fake `nas._get` that serves a canned notification list per service."""
    asked = []

    def get(service, conf, path, **kwargs):
        asked.append((service, path))
        answer = by_service[service]
        if isinstance(answer, Exception):
            raise answer
        return answer

    get.asked = asked
    return get


def _run(get, conf=None, ssh=HOP, env=None):
    out = io.StringIO()
    conf = _conf("sonarr", "radarr") if conf is None else conf
    status = nas_watch.report(
        env=env or {},
        out=out,
        get=get,
        ssh=ssh,
        run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess in tests")),
    )
    return status, out.getvalue()


@pytest.fixture(autouse=True)
def _no_real_config(monkeypatch):
    """`report` builds its own config from the hop; give it a canned one."""
    monkeypatch.setattr(nas, "config", lambda env=None, ssh=None, run=None: _conf("sonarr", "radarr"))


def test_empty_lists_are_clean_and_name_what_was_swept():
    status, text = _run(_get_returning({"sonarr": [], "radarr": []}))
    assert status == 0
    assert "NO CODE EXECUTION CONFIGURED on 2 service(s)" in text
    assert "sonarr" in text and "radarr" in text


def test_custom_script_raises_and_prints_the_path_it_runs():
    rows = [{"name": "post-import", "implementation": "CustomScript",
             "fields": [{"name": "path", "value": "/volume1/x.sh"}]}]
    status, text = _run(_get_returning({"sonarr": rows, "radarr": []}))
    assert status == 2
    assert "CODE EXECUTION CONFIGURED" in text
    assert "sonarr: post-import | CustomScript" in text
    assert "runs: /volume1/x.sh" in text


def test_an_ordinary_notification_is_printed_and_does_not_raise():
    rows = [{"name": "phone", "implementation": "Discord"}]
    status, text = _run(_get_returning({"sonarr": rows, "radarr": []}))
    assert status == 0
    assert "OTHER NOTIFICATIONS" in text
    assert "sonarr: phone | Discord" in text
    # The clean line must still be printed: nothing that executes was found.
    assert "NO CODE EXECUTION CONFIGURED" in text


def test_a_webhook_is_printed_but_does_not_raise():
    rows = [{"name": "out", "implementation": "Webhook"}]
    status, text = _run(_get_returning({"sonarr": [], "radarr": rows}))
    assert status == 0
    assert "radarr: out | Webhook" in text


def test_an_unreadable_service_is_1_and_never_reads_as_empty():
    get = _get_returning({"sonarr": nas.Unreachable("sonarr refused the API key (401)"),
                          "radarr": []})
    status, text = _run(get)
    assert status == 1
    assert "UNREADABLE  sonarr" in text
    # It judged one of two, and says so, so a partial sweep cannot be read as
    # a clean one.
    assert "1 service(s) of 2" in text


def test_a_finding_outranks_an_unreadable_service():
    rows = [{"name": "x", "implementation": "CustomScript", "fields": []}]
    get = _get_returning({"sonarr": rows, "radarr": nas.Unreachable("down")})
    status, _ = _run(get)
    assert status == 2


def test_json_that_is_not_a_list_is_unreadable_rather_than_empty():
    status, text = _run(_get_returning({"sonarr": {"error": "nope"}, "radarr": []}))
    assert status == 1
    assert "answered dict, not a list" in text


def test_no_hop_cannot_see_and_does_not_raise():
    status, text = _run(_get_returning({}), ssh=None)
    assert status == 0
    assert "CANNOT SEE FROM THIS POD" in text
    assert "Judged 0 service(s)" in text


def test_no_configurable_service_is_1(monkeypatch):
    monkeypatch.setattr(nas, "config", lambda env=None, ssh=None, run=None: {})
    status, text = _run(_get_returning({}))
    assert status == 1
    assert "SERVICES UNREADABLE" in text


def test_a_thin_row_still_reports_rather_than_crashing():
    status, text = _run(_get_returning({"sonarr": [{}], "radarr": []}))
    assert status == 0
    assert "(unnamed) | (no implementation)" in text


def test_the_endpoint_is_on_the_read_only_allowlist():
    # `nas._get` refuses any path outside that set, so the check cannot work
    # unless the path is listed -- and listing it is what keeps this module
    # read-only.
    assert nas_watch.NOTIFICATION_PATH in nas.READ_ONLY
