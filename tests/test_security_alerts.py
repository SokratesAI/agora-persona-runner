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


def _section_of(lines, package):
    """Which heading `package` is printed under, or None if it is absent.

    Asserting only that both packages appear somewhere is what the first
    version of this test did, and the reviewer proved it passed against
    the pre-diff code -- which prints every alert under one heading and
    never reads the `landed` flag. Membership of the right *section* is
    the property this test is named for, so it has to be what it checks.
    """
    heading = None
    for line in lines:
        if line.startswith(("OPEN SECURITY ALERTS", "ALREADY FIXED")):
            heading = line.split(" — ")[0]
        elif package in line and heading is not None and line.startswith("  "):
            return heading
    return None


def test_a_landed_alert_does_not_hide_a_real_one_beside_it():
    fixed = security_alerts._summarise(_alert("high", "brace-expansion", patched="2.1.4"))
    fixed["landed"], fixed["landed_note"] = True, "default branch resolves 2.1.4"
    real = security_alerts._summarise(_alert("critical", "left-pad", patched="1.0.0"))
    real["landed"], real["landed_note"] = False, "still vulnerable"
    lines, code = format_report({"o/r": (OK, [fixed, real])})
    assert code == 2
    assert _section_of(lines, "left-pad") == "OPEN SECURITY ALERTS"
    assert _section_of(lines, "brace-expansion") == "ALREADY FIXED ON THE DEFAULT BRANCH"
    # The count in the actionable heading must be the actionable count, not
    # the total -- "2 across 1 repo(s)" over one real alert is the same
    # overstatement this whole change exists to stop.
    assert "OPEN SECURITY ALERTS — 1 across 1 repo(s):" in lines


# --- the sweep's own repo list (Cycle 432) -------------------------------
#
# The failure these guard is one level above every test above it: the
# alerts machinery worked perfectly and answered about the wrong set of
# repos. A sweep of three repos out of twenty-three prints exactly what a
# sweep of twenty-three clean ones prints, which is the one equation this
# module keeps refusing to write.


def _org_listing(*entries):
    return json.dumps(
        [{"nameWithOwner": name, "isArchived": archived} for name, archived in entries]
    )


def test_the_org_listing_drops_archived_repos_and_reports_them():
    body = _org_listing(("o/live", False), ("o/old", True))
    live, error, archived = security_alerts.repos_in_org("o", run=_runner(0, body))
    assert live == ["o/live"]
    assert archived == ["o/old"]
    assert error is None


def test_a_failed_org_listing_returns_an_error_and_no_repos():
    live, error, archived = security_alerts.repos_in_org(
        "o", run=_runner(1, "", "HTTP 404: Not Found")
    )
    assert live == [] and archived == []
    assert "404" in error


def test_the_sweep_reaches_repos_with_no_checkout_here(monkeypatch):
    monkeypatch.setattr(
        security_alerts, "_repos_from_workspace", lambda: (["o/cloned"], [])
    )
    body = _org_listing(("o/cloned", False), ("o/never-cloned", False))
    repos, unplaceable, notes, incomplete = security_alerts._repos_to_sweep(
        run=_runner(0, body)
    )
    assert repos == ["o/cloned", "o/never-cloned"]
    assert incomplete is False
    assert unplaceable == []
    assert any("2 repo(s) in the org" in note for note in notes)


def test_a_checkout_outside_the_org_is_still_swept(monkeypatch):
    # Unioning the checkouts back in is what makes this change incapable of
    # shrinking the sweep, whatever the org listing says.
    monkeypatch.setattr(
        security_alerts,
        "_repos_from_workspace",
        lambda: (["o/cloned", "elsewhere/thing"], []),
    )

    def run(args):
        org = args[2]
        if org == "o":
            return 0, _org_listing(("o/cloned", False)), ""
        return 0, _org_listing(("elsewhere/thing", False)), ""

    repos, _, _, incomplete = security_alerts._repos_to_sweep(run=run)
    assert repos == ["elsewhere/thing", "o/cloned"]
    assert incomplete is False


def test_an_unlistable_org_is_flagged_incomplete_rather_than_swept_quietly(monkeypatch):
    monkeypatch.setattr(
        security_alerts, "_repos_from_workspace", lambda: (["o/cloned"], [])
    )
    repos, _, notes, incomplete = security_alerts._repos_to_sweep(
        run=_runner(1, "", "HTTP 403: Forbidden")
    )
    assert repos == ["o/cloned"]
    assert incomplete is True
    assert any("COULD NOT LIST THE ORG" in note for note in notes)


def test_no_org_at_all_is_incomplete_rather_than_clean(monkeypatch):
    monkeypatch.setattr(security_alerts, "_repos_from_workspace", lambda: ([], []))
    repos, _, notes, incomplete = security_alerts._repos_to_sweep(run=_runner(0, "[]"))
    assert repos == []
    assert incomplete is True
    assert any("No org to enumerate" in note for note in notes)


def test_main_never_exits_clean_on_an_incomplete_sweep(monkeypatch, capsys):
    # The whole point of the exit code: a sweep that could not build its own
    # repo list must not print the same status as one that checked them all.
    monkeypatch.setattr(
        security_alerts,
        "_repos_to_sweep",
        lambda: (["o/clean"], [], ["⚠ o: COULD NOT LIST THE ORG — HTTP 403"], True),
    )
    monkeypatch.setattr(security_alerts, "alerts_for", lambda repo: (OK, []))
    monkeypatch.setattr(security_alerts, "verify_landed", lambda results: None)
    monkeypatch.setattr(security_alerts, "_orgs_from_workspace", lambda: [])
    assert security_alerts.main([]) == 1
    assert "COULD NOT LIST THE ORG" in capsys.readouterr().out


def test_every_disabled_repo_is_named_on_the_one_collapsed_line():
    # The layout collapses; the names must not. A count without the names
    # would tell a cycle that something is blind and not which thing.
    results = {
        f"o/r{n}": (DISABLED, "Dependabot alerts are disabled") for n in range(16)
    }
    lines, code = format_report(results)
    disabled_lines = [ln for ln in lines if "DISABLED" in ln]
    assert len(disabled_lines) == 1
    assert "16 repo(s)" in disabled_lines[0]
    for repo in results:
        assert repo in disabled_lines[0]
    assert code == 1


def test_a_repo_that_errored_still_gets_its_own_line():
    lines, code = format_report(
        {"o/off": (DISABLED, "disabled"), "o/broken": (ERROR, "HTTP 500")}
    )
    assert any(ln.startswith("⚠ o/broken: COULD NOT READ") for ln in lines)
    assert not any("o/broken" in ln and "DISABLED" in ln for ln in lines)
    assert code == 1


def test_an_org_listing_at_the_limit_is_an_error_rather_than_a_short_sweep():
    body = _org_listing(
        *((f"o/r{n}", False) for n in range(security_alerts._ORG_PAGE_LIMIT))
    )
    live, error, archived = security_alerts.repos_in_org("o", run=_runner(0, body))
    assert live == [] and archived == []
    assert "truncated" in error


# --- a dismissal that a published patch overturns (Cycle 457) ---
#
# Two high-severity `image-size` advisories on `sokrates-docs` had no
# patched version to apply, so the sweep exited 2 every cycle with nothing
# any cycle could do. Dismissing them is honest. Dismissing them forever is
# not, because GitHub never reopens a dismissed alert on its own, so the
# day a patch is published nothing anywhere would say so.


def _dismissed(severity, package, reason, patched="9.9.9"):
    alert = _alert(severity, package, patched=patched)
    alert["state"] = "dismissed"
    alert["dismissed_reason"] = reason
    return alert


def test_an_open_alert_is_still_reported_when_dismissed_ones_are_read_too():
    body = json.dumps([_alert("high", "brace-expansion", patched="2.1.4")])
    state, payload = alerts_for("o/r", run=_runner(0, body))
    assert state == OK
    assert [a["package"] for a in payload] == ["brace-expansion"]


def test_the_query_asks_for_dismissed_alerts_as_well_as_open_ones():
    seen = []

    def run(args):
        seen.append(args)
        return 0, "[]", ""

    alerts_for("o/r", run=run)
    assert "state=open,dismissed" in seen[0][-1]


def test_a_dismissal_with_no_patch_published_stays_out_of_the_report():
    alert = _dismissed("high", "image-size", "tolerable_risk", patched=None)
    alert["security_vulnerability"]["first_patched_version"] = None
    state, payload = alerts_for("o/r", run=_runner(0, json.dumps([alert])))
    assert state == OK
    assert payload == []


def test_a_dismissal_comes_back_once_a_patch_is_published():
    alert = _dismissed("high", "image-size", "tolerable_risk", patched="2.0.3")
    state, payload = alerts_for("o/r", run=_runner(0, json.dumps([alert])))
    assert state == OK
    assert [a["package"] for a in payload] == ["image-size"]
    assert payload[0]["patched"] == "2.0.3"


def test_a_revived_alert_raises_the_exit_status_and_says_why_it_is_back():
    alert = _dismissed("high", "image-size", "tolerable_risk", patched="2.0.3")
    _, payload = alerts_for("o/r", run=_runner(0, json.dumps([alert])))
    lines, code = format_report({"o/r": (OK, payload)})
    assert code == 2
    blob = "\n".join(lines)
    assert "previously dismissed as tolerable_risk" in blob
    assert "2.0.3 is now published" in blob


def test_not_used_is_a_judgement_a_patch_does_not_overturn():
    alert = _dismissed("high", "image-size", "not_used", patched="2.0.3")
    _, payload = alerts_for("o/r", run=_runner(0, json.dumps([alert])))
    assert payload == []


def test_inaccurate_is_a_judgement_a_patch_does_not_overturn():
    alert = _dismissed("high", "image-size", "inaccurate", patched="2.0.3")
    _, payload = alerts_for("o/r", run=_runner(0, json.dumps([alert])))
    assert payload == []


def test_no_bandwidth_and_fix_started_both_come_back_when_a_patch_lands():
    body = json.dumps(
        [
            _dismissed("high", "a-pkg", "no_bandwidth", patched="2.0.3"),
            _dismissed("high", "b-pkg", "fix_started", patched="2.0.3"),
        ]
    )
    _, payload = alerts_for("o/r", run=_runner(0, body))
    assert sorted(a["package"] for a in payload) == ["a-pkg", "b-pkg"]


def test_a_reason_this_loop_has_not_reasoned_about_stays_dismissed():
    alert = _dismissed("high", "image-size", "some_future_reason", patched="2.0.3")
    _, payload = alerts_for("o/r", run=_runner(0, json.dumps([alert])))
    assert payload == []


def test_an_auto_dismissed_alert_is_not_treated_as_a_dismissal():
    alert = _alert("high", "image-size", patched="2.0.3")
    alert["state"] = "auto_dismissed"
    _, payload = alerts_for("o/r", run=_runner(0, json.dumps([alert])))
    assert payload == []


def test_a_fixed_alert_is_not_reported_even_if_the_api_returns_one():
    alert = _alert("high", "image-size", patched="2.0.3")
    alert["state"] = "fixed"
    _, payload = alerts_for("o/r", run=_runner(0, json.dumps([alert])))
    assert payload == []


# --- the org-wide cross-check (Cycle 548) ---
#
# The per-repo sweep printed "No open security alerts" with
# SokratesAI/agora on the answered list at 21:33 on 2026-08-27, while that
# repo's brace-expansion alert was open and reported by eleven later calls.
# I never reproduced the empty answer, so nothing here claims a cause. What
# these cover is the consequence: a per-repo read that comes back exit 0
# and empty is indistinguishable from a clean repo, and the only way to
# tell them apart is a second reading over a different route.


def _org_alert(repo, number, package="brace-expansion"):
    alert = _alert("high", package, patched="2.1.4")
    alert["number"] = number
    alert["repository"] = {"full_name": repo}
    return alert


def _summarised_org_alerts(*raw):
    """The org view in the shape `org_alerts` actually returns.

    The raw payloads above are what GitHub sends; everything downstream of
    `org_alerts` sees the `_summarise` shape. Building the fixture by
    running the module's own parser is deliberate -- a hand-written dict
    here would drift from what production produces, and the fold below
    feeds straight into `fix_landed` and the report, which read every one
    of those fields.
    """
    state, alerts = security_alerts.org_alerts("o", run=_runner(0, json.dumps(list(raw))))
    assert state == OK
    return alerts


def test_the_org_view_carries_the_repo_and_the_alert_number():
    body = json.dumps([_org_alert("o/a", 2), _org_alert("o/b", 7, "left-pad")])
    state, alerts = security_alerts.org_alerts("o", run=_runner(0, body))
    assert state == OK
    assert {(a["repo"], a["number"]) for a in alerts} == {("o/a", 2), ("o/b", 7)}


def test_an_alert_the_per_repo_sweep_missed_is_a_disagreement():
    # The exact shape of what happened: the repo answered ok with nothing
    # in it, and the org-wide list has an alert on it.
    results = {"o/a": (OK, [])}
    org_views = {"o": (OK, [dict(_org_alert("o/a", 2), repo="o/a")])}
    lines, disagreed = security_alerts.cross_check(results, org_views)
    assert disagreed is True
    assert any("the per-repo sweep did not" in line for line in lines)
    assert any("#2" in line for line in lines)


def test_the_two_views_agreeing_says_nothing_at_all():
    results = {"o/a": (OK, [{"number": 2}])}
    org_views = {"o": (OK, [dict(_org_alert("o/a", 2), repo="o/a")])}
    assert security_alerts.cross_check(results, org_views) == ([], False)


def test_an_alert_only_the_per_repo_sweep_saw_is_also_a_disagreement():
    results = {"o/a": (OK, [{"number": 2}])}
    org_views = {"o": (OK, [])}
    lines, disagreed = security_alerts.cross_check(results, org_views)
    assert disagreed is True
    assert any("the org-wide list did not" in line for line in lines)


def test_a_repo_the_sweep_could_not_read_is_not_a_disagreement():
    # An archived repo, or one whose alerts are disabled for this token, is
    # absent from the sweep by design. Reporting that as a contradiction
    # would make the check red on day one and forever, which is the same as
    # off.
    results = {"o/a": (DISABLED, "Dependabot alerts are disabled")}
    org_views = {"o": (OK, [dict(_org_alert("o/a", 2), repo="o/a")])}
    assert security_alerts.cross_check(results, org_views) == ([], False)


def test_a_repo_outside_the_sweep_entirely_is_not_a_disagreement():
    results = {"o/a": (OK, [])}
    org_views = {"o": (OK, [dict(_org_alert("o/archived", 2), repo="o/archived")])}
    assert security_alerts.cross_check(results, org_views) == ([], False)


def test_an_unreadable_org_view_leaves_the_sweep_unconfirmed():
    results = {"o/a": (OK, [])}
    org_views = {"o": (ERROR, "HTTP 403")}
    lines, disagreed = security_alerts.cross_check(results, org_views)
    assert disagreed is True
    assert any("COULD NOT CROSS-CHECK" in line for line in lines)


def test_a_full_page_of_org_alerts_is_unreadable_rather_than_complete():
    body = json.dumps(
        [_org_alert("o/a", n) for n in range(security_alerts._ORG_ALERT_PAGE)]
    )
    state, message = security_alerts.org_alerts("o", run=_runner(0, body))
    assert state == ERROR
    assert "truncated" in message


def test_a_dismissed_org_alert_with_no_patch_is_filtered_the_same_way():
    # The org view has to apply `_still_counts` too, or every alert this
    # loop has deliberately dismissed reads as one the per-repo sweep lost.
    alert = _org_alert("o/a", 2)
    alert["state"] = "dismissed"
    alert["dismissed_reason"] = "tolerable_risk"
    alert["security_vulnerability"]["first_patched_version"] = None
    state, alerts = security_alerts.org_alerts("o", run=_runner(0, json.dumps([alert])))
    assert state == OK and alerts == []


def test_main_never_exits_clean_when_the_two_views_disagree(monkeypatch, capsys):
    monkeypatch.setattr(
        security_alerts, "_repos_to_sweep", lambda: (["o/a"], [], [], False)
    )
    monkeypatch.setattr(security_alerts, "alerts_for", lambda repo: (OK, []))
    monkeypatch.setattr(security_alerts, "verify_landed", lambda results: None)
    monkeypatch.setattr(security_alerts, "_orgs_from_workspace", lambda: ["o"])
    # Built before `org_alerts` is patched: the helper runs the real one.
    org_view = _summarised_org_alerts(_org_alert("o/a", 2))
    monkeypatch.setattr(security_alerts, "org_alerts", lambda org: (OK, org_view))
    # 2, not 1: the alert the org view holds is folded back into the sweep
    # (Cycle 583), so there is something to act on rather than only an
    # instrument that cannot be believed. It said 1 and printed "No open
    # security alerts" until 2026-08-28, which is the bug that fold fixes.
    assert security_alerts.main([]) == 2
    out = capsys.readouterr().out
    assert "No open security alerts" not in out
    assert "the per-repo sweep did not" in out


# --- the org-only alert is put back into the sweep (Cycle 583) ---
#
# The cross-check above fired for real on 2026-08-28 at 15:33, on the same
# repo and the same alert as 08-27. It worked, and the report still led
# with "No open security alerts" and still named the repo on the answered
# list, because the warning is printed after a report built only from the
# per-repo sweep. The second view has to be a source, not just an audit.


def test_an_org_only_alert_is_put_back_into_the_repo_it_belongs_to():
    results = {"o/a": (OK, [])}
    org_views = {"o": (OK, _summarised_org_alerts(_org_alert("o/a", 2)))}
    folded = security_alerts.fold_in_org_only(results, org_views)
    assert folded == [("o/a", 2)]
    assert [a["number"] for a in results["o/a"][1]] == [2]
    assert results["o/a"][1][0]["org_only"] is True
    # The restored alert must look like every other one, or the report and
    # `fix_landed` would need a second shape to know about.
    assert "repo" not in results["o/a"][1][0]
    assert results["o/a"][1][0]["package"] == "brace-expansion"


def test_the_headline_cannot_read_clean_while_the_org_view_holds_an_alert():
    results = {"o/a": (OK, [])}
    org_views = {"o": (OK, _summarised_org_alerts(_org_alert("o/a", 2)))}
    before, _ = format_report({"o/a": (OK, [])})
    assert any("No open security alerts" in line for line in before)
    security_alerts.fold_in_org_only(results, org_views)
    lines, code = format_report(results)
    blob = "\n".join(lines)
    assert "No open security alerts" not in blob
    assert code == 2
    assert "brace-expansion" in blob


def test_a_folded_alert_says_the_per_repo_route_missed_it():
    results = {"o/a": (OK, [])}
    org_views = {"o": (OK, _summarised_org_alerts(_org_alert("o/a", 2)))}
    security_alerts.fold_in_org_only(results, org_views)
    lines, _ = format_report(results)
    assert any("seen only in the org-wide alert list" in line for line in lines)


def test_an_alert_both_views_have_is_not_added_twice():
    results = {"o/a": (OK, [{"number": 2, "package": "brace-expansion"}])}
    org_views = {"o": (OK, _summarised_org_alerts(_org_alert("o/a", 2)))}
    assert security_alerts.fold_in_org_only(results, org_views) == []
    assert len(results["o/a"][1]) == 1


def test_a_repo_outside_the_sweep_is_not_folded_in():
    # Same boundary as the cross-check: an org alert on a repo that was
    # never swept is not a missing alert, it is a repo outside the sweep,
    # and inventing a results row for it would report an archived repo as
    # something this loop can fix.
    results = {"o/a": (OK, [])}
    org_views = {"o": (OK, _summarised_org_alerts(_org_alert("o/archived", 2)))}
    assert security_alerts.fold_in_org_only(results, org_views) == []
    assert results == {"o/a": (OK, [])}


def test_a_repo_the_sweep_could_not_read_is_not_folded_into():
    results = {"o/a": (DISABLED, "Dependabot alerts are disabled")}
    org_views = {"o": (OK, _summarised_org_alerts(_org_alert("o/a", 2)))}
    assert security_alerts.fold_in_org_only(results, org_views) == []
    assert results["o/a"] == (DISABLED, "Dependabot alerts are disabled")


def test_an_unreadable_org_view_folds_nothing_in():
    results = {"o/a": (OK, [])}
    org_views = {"o": (ERROR, "gh exited 1")}
    assert security_alerts.fold_in_org_only(results, org_views) == []
    assert results["o/a"][1] == []


def test_a_folded_alert_gets_the_same_already_fixed_measurement(monkeypatch):
    # This is what actually happened on 08-28: the missing alert was real
    # and its fix was already on the default branch. It has to reach
    # `fix_landed` like any other alert, or the report would call a patched
    # branch actionable and every cycle would re-derive that it is not.
    results = {"o/a": (OK, [])}
    org_views = {"o": (OK, _summarised_org_alerts(_org_alert("o/a", 2)))}
    security_alerts.fold_in_org_only(results, org_views)
    lockfile = json.dumps(
        {"packages": {"node_modules/brace-expansion": {"version": "2.1.4"}}}
    )
    security_alerts.verify_landed(results, run=_runner(0, lockfile))
    lines, code = format_report(results)
    blob = "\n".join(lines)
    assert code == 0
    assert "ALREADY FIXED ON THE DEFAULT BRANCH" in blob
    assert "seen only in the org-wide alert list" in blob


def test_main_folds_the_org_view_in_and_still_reports_the_disagreement(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        security_alerts,
        "_repos_to_sweep",
        lambda run=None: (["o/a"], [], [], False),
    )
    monkeypatch.setattr(security_alerts, "_orgs_from_workspace", lambda: ["o"])
    monkeypatch.setattr(security_alerts, "alerts_for", lambda repo, run=None: (OK, []))
    # Built before `org_alerts` is patched: the helper runs the real one.
    org_view = _summarised_org_alerts(_org_alert("o/a", 2))
    monkeypatch.setattr(
        security_alerts, "org_alerts", lambda org, run=None: (OK, org_view)
    )
    monkeypatch.setattr(
        security_alerts, "fix_landed", lambda repo, alert, run=None: (False, "")
    )
    code = security_alerts.main([])
    blob = capsys.readouterr().out
    assert code == 2
    assert "OPEN SECURITY ALERTS" in blob
    assert "No open security alerts" not in blob
    assert "the per-repo sweep did not" in blob


def test_main_verifies_a_folded_alert_rather_than_reporting_it_unmeasured(
    monkeypatch, capsys
):
    # Order, specifically: the fold has to happen before `verify_landed`,
    # or an alert only the org view saw would always be actionable because
    # nothing ever measured it. That is how 08-28's real case would have
    # been reported -- an already-patched branch called a live finding.
    monkeypatch.setattr(
        security_alerts,
        "_repos_to_sweep",
        lambda run=None: (["o/a"], [], [], False),
    )
    monkeypatch.setattr(security_alerts, "_orgs_from_workspace", lambda: ["o"])
    monkeypatch.setattr(security_alerts, "alerts_for", lambda repo, run=None: (OK, []))
    org_view = _summarised_org_alerts(_org_alert("o/a", 2))
    monkeypatch.setattr(
        security_alerts, "org_alerts", lambda org, run=None: (OK, org_view)
    )
    monkeypatch.setattr(
        security_alerts,
        "fix_landed",
        lambda repo, alert, run=None: (True, "default branch resolves 2.1.4"),
    )
    # 1, not 2: nothing to fix, but the per-repo route lied, so the
    # instrument still says it cannot be believed.
    assert security_alerts.main([]) == 1
    out = capsys.readouterr().out
    assert "ALREADY FIXED ON THE DEFAULT BRANCH" in out
    assert "OPEN SECURITY ALERTS" not in out
    assert "the per-repo sweep did not" in out
