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


def _raises(exc):
    """A seam that raises whatever it was given, for the unreadable-app cases."""
    def call(*a, **k):
        raise exc
    return call


def _run(get, conf=None, ssh=HOP, env=None, unlocked=False, config=None, credential=None,
         key=None, notifiers=None):
    """Run the check. `unlocked`/`config` stand in for nzbget, `key`/`notifiers` for Tautulli.

    nzbget defaults to "locked, no credential in the environment", which is
    the live state of the pods today and contributes nothing to the status --
    so every *arr test below reads as if nzbget were not there, which is what
    those tests are about.

    Tautulli defaults to "readable, no agents configured", which is what the
    live box answered on 2026-08-30. It contributes nothing to the status
    either, and it does move the sweep line -- deliberately: a judged surface
    counts in both halves of that fraction.
    """
    out = io.StringIO()
    conf = _conf("sonarr", "radarr") if conf is None else conf

    def _unlocked(hop, **kwargs):
        if isinstance(unlocked, Exception):
            raise unlocked
        return unlocked

    def _config(hop, credential, **kwargs):
        if isinstance(config, Exception):
            raise config
        return config or {}

    status = nas_watch.report(
        env=env or {},
        out=out,
        get=get,
        ssh=ssh,
        run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess in tests")),
        unlocked=_unlocked,
        config=_config,
        # Environment only. The real `nzbget_credential` falls back to reading
        # the compose file off the NAS, and a unit test must not go there --
        # `test_the_hop_is_offered_to_the_credential_lookup` below covers that
        # it is offered the hop at all.
        credential=credential or (lambda env, ssh=None, **k: nas.nzbget_credential(env)),
        key=key or (lambda hop, env=None, **k: "tautulli-key"),
        notifiers=notifiers or (lambda hop, k_, **k: []),
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
    assert "NOT ASKED" in text
    assert "sonarr" in text
    # It judged one of three, and says so, so a partial sweep cannot be read as
    # a clean one. Three, not two: nzbget's extension list is the third
    # code-execution surface on that box.
    assert "2 service(s) of 4" in text


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


# --- nzbget -----------------------------------------------------------------
#
# The two questions are separate on purpose and the tests keep them separate:
# whether the control interface is locked needs no credential and runs every
# cycle, and whether an extension is configured is behind that lock. The
# failure this guards against is the second one quietly reading as clean when
# it never ran at all.

CLEAN = {"sonarr": [], "radarr": []}


def test_nzbget_locked_and_no_credential_is_clean_and_says_what_it_skipped():
    status, text = _run(_get_returning(CLEAN), unlocked=False)
    assert status == 0
    assert "NZBGET CONTROL IS LOCKED" in text
    assert "NOT JUDGED  nzbget's extension list" in text
    assert "NZBGET_USER" in text and "NZBGET_PASS" in text


def test_nzbget_answering_without_a_credential_raises():
    status, text = _run(_get_returning(CLEAN), unlocked=True)
    assert status == 2
    assert "NZBGET CONTROL IS OPEN" in text
    assert "saveconfig" in text
    # It must not then claim anything about the extension list it never read.
    assert "No extension or script task is configured" not in text


def test_an_extension_raises_and_names_the_setting_and_the_script_dir():
    status, text = _run(
        _get_returning(CLEAN),
        unlocked=False,
        env={"NZBGET_USER": "admin", "NZBGET_PASS": "x"},
        config={"category2.extensions": "Evil.py", "scriptdir": "/downloads/scripts", "controlip": "0.0.0.0"},
    )
    assert status == 2
    assert "CODE EXECUTION CONFIGURED" in text
    assert "category2.extensions = Evil.py" in text
    assert "/downloads/scripts" in text


def test_a_scheduler_task_that_runs_a_script_raises():
    status, text = _run(
        _get_returning(CLEAN),
        unlocked=False,
        env={"NZBGET_USER": "admin", "NZBGET_PASS": "x"},
        config={"task1.command": "Script", "task1.param": "wipe.sh"},
    )
    assert status == 2
    assert "task1.command = Script" in text


def test_an_empty_extension_list_is_a_real_negative_not_a_finding():
    # This is the live shape measured on the NAS on 2026-08-30: every
    # Extensions key present and empty. It must read as clean, or the check
    # is red forever and stops being read.
    status, text = _run(
        _get_returning(CLEAN),
        unlocked=False,
        env={"NZBGET_USER": "admin", "NZBGET_PASS": "x"},
        config={"extensions": "", "category1.extensions": "", "category2.extensions": "",
                "scriptdir": "/downloads/scripts", "scriptorder": "", "scriptpausequeue": "no"},
    )
    assert status == 0
    assert "No extension or script task is configured; 6 setting(s) read." in text


def test_scriptdir_alone_is_not_a_finding():
    # ScriptDir says where scripts are found, not that one runs. If this
    # raised, the check would be red on the box's default configuration.
    status, _ = _run(
        _get_returning(CLEAN),
        unlocked=False,
        env={"NZBGET_USER": "admin", "NZBGET_PASS": "x"},
        config={"scriptdir": "/downloads/scripts"},
    )
    assert status == 0


def test_an_unreachable_nzbget_is_1_and_never_reads_as_locked():
    status, text = _run(_get_returning(CLEAN), unlocked=nas.Unreachable("no answer"))
    assert status == 1
    assert "UNREADABLE  nzbget" in text
    assert "NZBGET CONTROL IS LOCKED" not in text


def test_a_refused_credential_is_1_rather_than_an_empty_config():
    status, text = _run(
        _get_returning(CLEAN),
        unlocked=False,
        env={"NZBGET_USER": "admin", "NZBGET_PASS": "wrong"},
        config=nas.Unreachable("nzbget refused the control credential (401)"),
    )
    assert status == 1
    assert "refused the control credential" in text
    assert "No extension or script task is configured" not in text


def test_an_arr_finding_still_raises_when_nzbget_is_clean():
    status, text = _run(
        _get_returning({"sonarr": [{"name": "n", "implementation": "CustomScript"}], "radarr": []}),
        unlocked=False,
    )
    assert status == 2
    assert "CODE EXECUTION CONFIGURED" in text
    assert "NZBGET CONTROL IS LOCKED" in text


def test_no_hop_judges_nothing_at_all_including_nzbget():
    out = io.StringIO()
    status = nas_watch.report(
        env={}, out=out, get=_get_returning(CLEAN), ssh=None,
        unlocked=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not probe with no hop")),
    )
    assert status == 0
    assert "CANNOT SEE FROM THIS POD" in out.getvalue()
    assert "NZBGET" not in out.getvalue()


def test_nzbget_is_still_judged_when_no_arr_can_be_configured(monkeypatch):
    # The *arrs need a discovered API key and nzbget does not, so whatever
    # broke them does not reach it -- and an open control interface must not
    # be lost behind an unrelated failure on the line above.
    monkeypatch.setattr(nas, "config", lambda env=None, ssh=None, run=None: {})
    out = io.StringIO()
    status = nas_watch.report(
        env={}, out=out, get=_get_returning({}), ssh=HOP,
        unlocked=lambda *a, **k: True,
        config=lambda *a, **k: {},
    )
    assert status == 2
    assert "SERVICES UNREADABLE" in out.getvalue()
    assert "NZBGET CONTROL IS OPEN" in out.getvalue()


def test_a_service_whose_key_discovery_failed_is_not_a_clean_sweep(monkeypatch):
    """One unasked app must not read as "no code execution configured".

    `nas.config` drops a service whose API-key discovery failed. With a
    denominator of `len(conf_all)` this check printed "NO CODE EXECUTION
    CONFIGURED on 1 service(s)" and "1 of 1" and exited 0 while sonarr -- the
    app that would carry a Custom Script -- was never asked at all.
    """
    monkeypatch.setattr(nas, "config", lambda env=None, ssh=None, run=None: _conf("radarr"))
    status, text = _run(_get_returning({"radarr": []}))
    assert status == 1
    assert "NOT ASKED" in text
    assert "sonarr" in text
    assert "NO CODE EXECUTION CONFIGURED" not in text
    assert "Judged the code-execution surface of 2 service(s) of 4" in text


def _sweep_line(text):
    """The line `tools.preflight` collapses this check to: the last one with a digit."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in reversed(lines):
        if any(ch.isdigit() for ch in line):
            return line
    return ""


def test_an_unjudged_nzbget_is_short_of_the_denominator():
    """The whole point of the sweep line: nzbget is a surface, not a footnote.

    Locked with no credential is the live state of both pods, and it is exit 0
    -- correctly, an unprovisioned credential is a fact about this pod. What
    was wrong until Cycle 646 is that the sweep line then read "2 service(s) of
    2", a complete-sweep sentence about a box whose third code-execution
    surface nobody had asked.
    """
    status, text = _run(_get_returning({"sonarr": [], "radarr": []}))
    assert status == 0
    assert "NOT JUDGED" in text
    assert "3 service(s) of 4" in _sweep_line(text)


def test_the_line_preflight_reads_carries_the_unjudged_third():
    """`preflight` shows one line per clean check and picks the last with a digit.

    The three lines `_nzbget` prints when it has no credential carry no digit,
    so they never reached that table -- the `NOT JUDGED` was in the full output
    and invisible in the only place a cycle reads it every morning.
    """
    _, text = _run(_get_returning({"sonarr": [], "radarr": []}))
    line = _sweep_line(text)
    assert line.startswith("Judged the code-execution surface of")
    assert "of 4" in line


def test_a_judged_nzbget_completes_the_denominator():
    """With the credential in hand all three surfaces are judged, and it says 3 of 3."""
    status, text = _run(_get_returning({"sonarr": [], "radarr": []}),
                        env={"NZBGET_USER": "u", "NZBGET_PASS": "p"},
                        config={"scriptdir": "/scripts", "extensions": ""})
    assert status == 0
    assert "4 service(s) of 4" in text
    assert "not a clean sweep of the box" not in text


def test_an_open_nzbget_counts_as_judged_while_raising():
    """An open control interface is a finding about that surface, so it was judged.

    A raise beside a short denominator would say "I found this on a service I
    did not look at", which is two opposite claims in one report.
    """
    status, text = _run(_get_returning({"sonarr": [], "radarr": []}), unlocked=True)
    assert status == 2
    assert "NZBGET CONTROL IS OPEN" in text
    assert "4 service(s) of 4" in text


def test_an_unreadable_nzbget_is_not_counted_as_judged():
    status, text = _run(_get_returning({"sonarr": [], "radarr": []}),
                        unlocked=nas.Unreachable("nzbget did not answer"))
    assert status == 1
    assert "3 service(s) of 4" in text
    assert "not a clean sweep of the box" in text


def test_the_hop_is_offered_to_the_credential_lookup():
    # The seam above lets every other test stay environment-only, so this is
    # the one that holds the real behaviour: `_nzbget` must hand the SSH hop
    # down, or nzbget goes back to being the third service nothing judges.
    seen = {}

    def credential(env, ssh=None, **kwargs):
        seen["ssh"] = ssh
        return ("admin", "potatopass")

    status, text = _run(
        _get_returning({"sonarr": [], "radarr": []}),
        unlocked=False,
        config={"scriptdir": "/downloads/scripts"},
        credential=credential,
    )
    assert seen["ssh"] == HOP
    assert status == 0
    assert "4 service(s) of 4" in text


# --- Tautulli ---------------------------------------------------------------
#
# Tautulli is the fourth code-execution surface on that box and the check did
# not ask it anything until Cycle 671. I installed it myself in Cycle 669; its
# Script notification agent runs an executable on the NAS when an event fires,
# which is the same shape as the *arr `CustomScript` type this check was built
# for. The tests below hold the same contract in both directions the *arr ones
# do: a script agent raises, an ordinary agent does not, and nothing unreadable
# is ever allowed to read as an empty list.


def test_a_tautulli_script_agent_raises_and_names_it():
    rows = [{"id": 3, "agent_name": "scripts", "agent_label": "Script",
             "friendly_name": "on play"}]
    status, text = _run(_get_returning({"sonarr": [], "radarr": []}),
                        notifiers=lambda hop, key, **k: rows)
    assert status == 2
    assert "CODE EXECUTION CONFIGURED" in text
    assert "on play | Script" in text


def test_a_tautulli_discord_agent_is_printed_and_does_not_raise():
    """Same judgement `EXECUTES` makes for the *arr apps: posting somewhere is not running something.

    A check that goes red the day he adds a Discord notification is one that
    stops being read, so the row is printed in full and the status stays 0.
    """
    rows = [{"id": 1, "agent_name": "discord", "agent_label": "Discord",
             "friendly_name": "family"}]
    status, text = _run(_get_returning({"sonarr": [], "radarr": []}),
                        notifiers=lambda hop, key, **k: rows)
    assert status == 0
    assert "family | Discord" in text
    assert "Tautulli is set to run a script" not in text


def test_an_unreadable_tautulli_is_not_counted_as_judged():
    """The failure this check cannot afford: an app that refused, reported as clean.

    A 401 from Tautulli reads as zero notifiers unless the reader raises, and
    "no script agent is configured" produced by never having been let in is
    exactly the confident wrong answer this whole module exists to avoid.
    """
    status, text = _run(_get_returning({"sonarr": [], "radarr": []}),
                        notifiers=_raises(nas.Unreachable("tautulli answered 401 on get_notifiers")))
    assert status == 1
    assert "UNREADABLE  tautulli" in text
    assert "2 service(s) of 4" in text
    assert "not a clean sweep of the box" in text


def test_an_unreadable_tautulli_key_does_not_raise_but_shortens_the_sweep():
    """An unreadable key is a fact about this pod, not a finding about the NAS.

    Same contract `_nzbget` holds for a missing credential: status 0, and the
    surface is explicitly not counted as judged, so the sweep line cannot say
    it swept a box it did not.
    """
    status, text = _run(_get_returning({"sonarr": [], "radarr": []}),
                        key=_raises(nas.Unreachable("config.ini carried no api_key")))
    assert status == 0
    assert "NOT JUDGED" in text
    assert "Tautulli's notification agents" in text
    assert "2 service(s) of 4" in text


def test_a_judged_tautulli_and_nzbget_together_complete_the_denominator():
    status, text = _run(_get_returning({"sonarr": [], "radarr": []}),
                        env={"NZBGET_USER": "u", "NZBGET_PASS": "p"},
                        config={"scriptdir": "/scripts", "extensions": ""})
    assert status == 0
    assert "4 service(s) of 4" in text
    assert "not a clean sweep of the box" not in text
