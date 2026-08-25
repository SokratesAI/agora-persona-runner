"""What a security sweep must never do is print silence when it is blind.

Every payload here is written out in the test itself rather than built
from the module's own constants, so a fixture cannot agree with the code
by construction. The cases that matter are the two where "nothing found"
and "nothing checked" would look identical on the page.
"""

import json

from tools import security_alerts
from tools.security_alerts import (
    DISABLED,
    ERROR,
    OK,
    alerts_for,
    format_report,
)


def _alert(severity, package, patched="9.9.9"):
    return {
        "html_url": f"https://github.com/o/r/security/dependabot/{package}",
        "security_advisory": {"severity": severity, "summary": f"{package} is bad"},
        "security_vulnerability": {
            "package": {"ecosystem": "npm", "name": package},
            "first_patched_version": {"identifier": patched},
        },
        "dependency": {
            "package": {"ecosystem": "npm", "name": package},
            "manifest_path": "package-lock.json",
        },
    }


def _runner(code, stdout="", stderr=""):
    def run(args):
        return code, stdout, stderr

    return run


def test_a_clean_repo_reports_ok_and_no_alerts():
    state, payload = alerts_for("o/r", run=_runner(0, "[]"))
    assert state == OK
    assert payload == []


def test_alerts_are_parsed_down_to_the_fields_worth_printing():
    body = json.dumps([_alert("high", "brace-expansion", patched="2.1.4")])
    state, payload = alerts_for("o/r", run=_runner(0, body))
    assert state == OK
    assert payload[0]["severity"] == "high"
    assert payload[0]["package"] == "brace-expansion"
    assert payload[0]["patched"] == "2.1.4"
    assert payload[0]["manifest"] == "package-lock.json"


def test_a_withdrawn_advisory_with_no_patched_version_still_parses():
    alert = _alert("low", "leftpad")
    alert["security_vulnerability"]["first_patched_version"] = None
    state, payload = alerts_for("o/r", run=_runner(0, json.dumps([alert])))
    assert state == OK
    assert payload[0]["patched"] == "none published"


def test_alerts_disabled_is_its_own_state_not_an_empty_list():
    # The one that put this module here: a repo with the feature switched
    # off must never be reported the same way as a repo with no alerts.
    state, payload = alerts_for(
        "o/r",
        run=_runner(1, "", "gh: Dependabot alerts are disabled for this repository."),
    )
    assert state == DISABLED
    assert payload != []


def test_a_403_that_is_not_about_the_feature_is_an_error_not_disabled():
    state, payload = alerts_for(
        "o/r", run=_runner(1, "", "gh: Resource not accessible by integration")
    )
    assert state == ERROR
    assert "not accessible" in payload


def test_non_json_output_is_an_error_rather_than_a_crash():
    state, _ = alerts_for("o/r", run=_runner(0, "<html>maintenance</html>"))
    assert state == ERROR


def test_report_ranks_critical_above_high_above_moderate():
    results = {
        "o/a": (OK, [{"severity": "moderate", "package": "m", "ecosystem": "npm",
                      "summary": "s", "manifest": "p", "patched": "1", "url": ""}]),
        "o/b": (OK, [{"severity": "critical", "package": "c", "ecosystem": "npm",
                      "summary": "s", "manifest": "p", "patched": "1", "url": ""},
                     {"severity": "high", "package": "h", "ecosystem": "npm",
                      "summary": "s", "manifest": "p", "patched": "1", "url": ""}]),
    }
    lines, code = format_report(results)
    order = [ln.split()[0] for ln in lines if ln.startswith("  ") and not ln.startswith("   ")]
    assert order == ["CRITICAL", "HIGH", "MODERATE"]
    assert code == 2


def test_an_unknown_severity_sorts_last_instead_of_raising():
    results = {
        "o/a": (OK, [{"severity": "spicy", "package": "z", "ecosystem": "npm",
                      "summary": "s", "manifest": "p", "patched": "1", "url": ""},
                     {"severity": "low", "package": "a", "ecosystem": "npm",
                      "summary": "s", "manifest": "p", "patched": "1", "url": ""}]),
    }
    lines, code = format_report(results)
    order = [ln.split()[0] for ln in lines if ln.startswith("  ") and not ln.startswith("   ")]
    assert order == ["LOW", "SPICY"]
    assert code == 2


def test_a_clean_sweep_and_a_blind_one_do_not_print_the_same_thing():
    clean, clean_code = format_report({"o/a": (OK, []), "o/b": (OK, [])})
    blind, blind_code = format_report(
        {"o/a": (OK, []), "o/b": (DISABLED, "Dependabot alerts are disabled")}
    )
    assert clean != blind
    assert clean_code == 0
    assert blind_code == 1
    assert any("DISABLED" in ln for ln in blind)
    assert not any("DISABLED" in ln for ln in clean)


def test_an_unreadable_repo_is_named_in_the_report():
    lines, code = format_report({"o/a": (ERROR, "gh exited 1")})
    assert code == 1
    assert any("COULD NOT READ" in ln and "o/a" in ln for ln in lines)


def test_no_repos_at_all_is_a_finding_not_a_pass():
    lines, code = format_report({})
    assert code == 1
    assert any("nothing was measured" in ln for ln in lines)


def test_main_prints_the_report_and_returns_its_code(monkeypatch, capsys):
    body = json.dumps([_alert("high", "brace-expansion", patched="2.1.4")])
    monkeypatch.setattr(security_alerts, "_gh", lambda args: (0, body, ""))
    code = security_alerts.main(["--repo", "o/r"])
    out = capsys.readouterr().out
    assert code == 2
    assert "brace-expansion" in out
    assert "HIGH" in out
