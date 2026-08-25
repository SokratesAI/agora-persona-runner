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


# --- an alert whose fix has already merged -------------------------------
#
# The window these cover is real and was paid for three times: GitHub
# closes an alert on its own re-scan schedule, so between the fixing merge
# and that re-scan the API reports an open high-severity alert over a
# patched default branch. The direction that matters is asymmetric --
# marking a vulnerable branch as fixed is far worse than one more false
# alarm -- so most of these assert that an *uncertain* answer stays
# actionable.


def _lockfile(**versions):
    packages = {"": {"name": "root"}}
    for name, version in versions.items():
        packages[f"node_modules/{name}"] = {"version": version}
    return json.dumps({"lockfileVersion": 3, "packages": packages})


def test_a_patched_default_branch_is_not_something_to_act_on():
    alert = _alert("high", "brace-expansion", patched="2.1.4")
    results = {"o/r": (OK, [security_alerts._summarise(alert)])}
    security_alerts.verify_landed(
        results, run=_runner(0, _lockfile(**{"brace-expansion": "2.1.4"}))
    )
    lines, code = format_report(results)
    assert code == 0
    assert any("ALREADY FIXED ON THE DEFAULT BRANCH" in line for line in lines)
    assert not any(line.startswith("OPEN SECURITY ALERTS") for line in lines)


def test_a_version_above_the_patch_counts_as_patched():
    alert = _alert("high", "brace-expansion", patched="2.1.4")
    results = {"o/r": (OK, [security_alerts._summarise(alert)])}
    security_alerts.verify_landed(
        results, run=_runner(0, _lockfile(**{"brace-expansion": "2.2.0"}))
    )
    assert results["o/r"][1][0]["landed"] is True


def test_a_still_vulnerable_default_branch_stays_actionable():
    alert = _alert("high", "brace-expansion", patched="2.1.4")
    results = {"o/r": (OK, [security_alerts._summarise(alert)])}
    security_alerts.verify_landed(
        results, run=_runner(0, _lockfile(**{"brace-expansion": "2.1.3"}))
    )
    lines, code = format_report(results)
    assert code == 2
    assert any(line.startswith("OPEN SECURITY ALERTS") for line in lines)


def test_one_nested_copy_left_behind_keeps_the_whole_alert_actionable():
    # npm records a package once per resolution path. A top-level bump
    # that leaves an older nested copy is exactly the half-fix that must
    # not read as clean.
    alert = _alert("high", "brace-expansion", patched="2.1.4")
    body = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/brace-expansion": {"version": "2.1.4"},
                "node_modules/glob/node_modules/brace-expansion": {"version": "2.0.1"},
            },
        }
    )
    results = {"o/r": (OK, [security_alerts._summarise(alert)])}
    security_alerts.verify_landed(results, run=_runner(0, body))
    assert results["o/r"][1][0]["landed"] is False
    assert format_report(results)[1] == 2


def test_a_failed_manifest_read_leaves_the_alert_at_full_weight():
    alert = _alert("high", "brace-expansion", patched="2.1.4")
    results = {"o/r": (OK, [security_alerts._summarise(alert)])}
    security_alerts.verify_landed(results, run=_runner(1, "", "not found"))
    assert results["o/r"][1][0]["landed"] is False
    assert format_report(results)[1] == 2


def test_an_ecosystem_with_no_reader_stays_actionable_and_says_so():
    alert = _alert("critical", "django", patched="4.2.1")
    alert["dependency"]["manifest_path"] = "requirements.txt"
    results = {"o/r": (OK, [security_alerts._summarise(alert)])}
    security_alerts.verify_landed(results, run=_runner(0, "django==4.2.1"))
    lines, code = format_report(results)
    assert code == 2
    assert any("not verified as fixed: no reader for" in line for line in lines)


def test_a_prerelease_is_never_compared_by_guesswork():
    # `1.2.3-rc1` sorts below `1.2.3` under semver and above it under a
    # string compare. Refusing is the only safe answer.
    assert security_alerts._version_tuple("2.1.4-rc1") is None
    assert security_alerts._version_tuple("2.1.4") == (2, 1, 4)
    assert security_alerts._version_tuple("v2.1.4") == (2, 1, 4)
    assert security_alerts._version_tuple("none published") is None


def test_a_v1_lockfile_is_refused_rather_than_read_wrongly():
    # v1 has no `packages` map; reading its `dependencies` tree would be a
    # second parser, and a wrong answer here is a suppressed vulnerability.
    body = json.dumps(
        {"lockfileVersion": 1, "dependencies": {"brace-expansion": {"version": "2.1.4"}}}
    )
    assert security_alerts.lockfile_versions(body, "brace-expansion") is None


def test_a_package_missing_from_the_manifest_is_not_read_as_fixed():
    body = _lockfile(**{"something-else": "1.0.0"})
    assert security_alerts.lockfile_versions(body, "brace-expansion") == []
    alert = _alert("high", "brace-expansion", patched="2.1.4")
    results = {"o/r": (OK, [security_alerts._summarise(alert)])}
    security_alerts.verify_landed(results, run=_runner(0, body))
    assert results["o/r"][1][0]["landed"] is False


def test_a_landed_alert_does_not_hide_a_real_one_beside_it():
    fixed = security_alerts._summarise(_alert("high", "brace-expansion", patched="2.1.4"))
    fixed["landed"], fixed["landed_note"] = True, "default branch resolves 2.1.4"
    real = security_alerts._summarise(_alert("critical", "left-pad", patched="1.0.0"))
    real["landed"], real["landed_note"] = False, "still vulnerable"
    lines, code = format_report({"o/r": (OK, [fixed, real])})
    assert code == 2
    assert any("left-pad" in line for line in lines)
    assert any("brace-expansion" in line for line in lines)
