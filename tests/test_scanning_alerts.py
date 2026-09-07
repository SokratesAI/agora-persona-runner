"""Tests for `tools.scanning_alerts`.

The load-bearing one is `test_a_matched_secret_is_never_printed`: the whole
reason this tool reduces an alert to named fields instead of passing the
payload through is that GitHub returns the matched credential in it.
"""

import json

import pytest

from tools import scanning_alerts as sa


def _runner(responses):
    """A fake `gh` that answers by substring of the URL it is handed."""

    def run(args):
        url = args[1]
        for needle, reply in responses.items():
            if needle in url:
                return reply
        raise AssertionError(f"no fake response for {url}")

    return run


PUBLIC = (0, "false\n", "")
PRIVATE = (0, "true\n", "")


def test_open_secret_alert_is_reported_without_its_body():
    payload = json.dumps(
        [
            {
                "number": 1,
                "created_at": "2026-08-16T04:55:00Z",
                "html_url": "https://github.com/o/r/security/secret-scanning/1",
                "validity": "unknown",
                "secret_type_display_name": "Amazon AWS Temporary Access Key ID",
                "secret": "ASIA-THIS-MUST-NOT-BE-PRINTED",
            }
        ]
    )
    state, alerts = sa.alerts_for("o/r", "secret", run=_runner({"secret-scanning": (0, payload, "")}))
    assert state == sa.OK
    assert alerts == [
        {
            "number": 1,
            "created_at": "2026-08-16T04:55:00Z",
            "html_url": "https://github.com/o/r/security/secret-scanning/1",
            "validity": "unknown",
            "what": "Amazon AWS Temporary Access Key ID",
        }
    ]


def test_a_matched_secret_is_never_printed():
    """The report may not carry the credential GitHub matched.

    Asserted on the rendered text rather than on the summary dict, because
    the summary is only half the path: a later change that passes the raw
    alert into `format_report` would keep this dict correct and still print
    the secret.
    """
    secret = "ASIA-THIS-MUST-NOT-BE-PRINTED"
    payload = json.dumps(
        [
            {
                "number": 1,
                "created_at": "2026-08-16T04:55:00Z",
                "html_url": "https://github.com/o/r/security/secret-scanning/1",
                "validity": "active",
                "secret_type_display_name": "Amazon AWS Temporary Access Key ID",
                "secret": secret,
            }
        ]
    )
    results, public = sa.sweep(
        ["o/r"],
        run=_runner(
            {
                "secret-scanning": (0, payload, ""),
                "code-scanning": (0, "[]", ""),
            }
        ),
        visibility={"o/r": True},
    )
    lines, code = sa.format_report(results, public)
    text = "\n".join(lines)
    assert secret not in text
    assert "Amazon AWS Temporary Access Key ID" in text
    assert code == 2


def test_scanner_off_on_a_private_repo_does_not_raise():
    off = (1, "", "gh: Secret scanning is disabled on this repository. (HTTP 404)")
    no_analysis = (1, "", "gh: no analysis found (HTTP 404)")
    results, public = sa.sweep(
        ["o/closed"],
        run=_runner({"secret-scanning": off, "code-scanning": no_analysis}),
        visibility={"o/closed": False},
    )
    lines, code = sa.format_report(results, public)
    assert code == 0
    assert any("private repo" in line for line in lines)


def test_scanner_off_on_a_public_repo_raises():
    off = (1, "", "gh: Secret scanning is disabled on this repository. (HTTP 404)")
    results, public = sa.sweep(
        ["o/open"],
        run=_runner({"secret-scanning": off, "code-scanning": (0, "[]", "")}),
        visibility={"o/open": True},
    )
    lines, code = sa.format_report(results, public)
    assert code == 2
    assert any("NO INSTRUMENT" in line for line in lines)


def test_off_and_no_analysis_are_different_states():
    """GitHub answers 403 for "not enabled" and 404 for "nothing analysed".

    They mean different things -- buy the feature, versus turn the scan on --
    so collapsing them into one status would print the wrong fix.
    """
    off = (1, "", "gh: Advanced Security must be enabled for this repository (HTTP 403)")
    empty = (1, "", "gh: no analysis found (HTTP 404)")
    run_off = _runner({"code-scanning": off})
    run_empty = _runner({"code-scanning": empty})
    assert sa.alerts_for("o/r", "code", run=run_off)[0] == sa.OFF
    assert sa.alerts_for("o/r", "code", run=run_empty)[0] == sa.NO_ANALYSIS


def test_an_unreadable_call_is_not_clean():
    boom = (1, "", "gh: Bad credentials (HTTP 401)")
    results, public = sa.sweep(
        ["o/r"],
        run=_runner({"secret-scanning": boom, "code-scanning": (0, "[]", "")}),
        visibility={"o/r": True},
    )
    lines, code = sa.format_report(results, public)
    assert code == 1
    assert any("UNREADABLE" in line for line in lines)


def test_an_open_alert_outranks_an_unreadable_sibling():
    """Exit 2 wins over exit 1, so a broken call cannot hide a live alert."""
    payload = json.dumps([{"number": 3, "rule": {"description": "hardcoded thing"}}])
    boom = (1, "", "gh: Bad credentials (HTTP 401)")
    results, public = sa.sweep(
        ["o/r"],
        run=_runner({"code-scanning": (0, payload, ""), "secret-scanning": boom}),
        visibility={"o/r": True},
    )
    _lines, code = sa.format_report(results, public)
    assert code == 2


def test_clean_repo_exits_zero_and_says_what_it_swept():
    results, public = sa.sweep(
        ["o/r"],
        run=_runner({"code-scanning": (0, "[]", ""), "secret-scanning": (0, "[]", "")}),
        visibility={"o/r": True},
    )
    lines, code = sa.format_report(results, public)
    assert code == 0
    text = "\n".join(lines)
    assert "Nothing to act on." == lines[0]
    assert "o/r" in text
    assert "Swept 1 repo(s)" in text


def test_a_json_object_is_not_read_as_a_list_of_alerts():
    state, message = sa.alerts_for(
        "o/r", "code", run=_runner({"code-scanning": (0, '{"message": "nope"}', "")})
    )
    assert state == sa.ERROR
    assert "list of alerts" in message


def test_visibility_defaults_to_public_when_the_call_fails():
    """A repo GitHub would not place must fail towards raising, not silence."""
    seen = sa.repo_visibility(["o/r"], run=lambda args: (1, "", "boom"))
    assert seen == {"o/r": True}


def test_visibility_reads_private_from_github():
    assert sa.repo_visibility(["o/r"], run=lambda args: PRIVATE) == {"o/r": False}
    assert sa.repo_visibility(["o/r"], run=lambda args: PUBLIC) == {"o/r": True}


def test_code_alert_carries_its_severity():
    payload = json.dumps(
        [
            {
                "number": 9,
                "created_at": "2026-09-01T00:00:00Z",
                "html_url": "https://github.com/o/r/security/code-scanning/9",
                "rule": {"description": "Missing workflow permissions", "security_severity_level": "HIGH"},
            }
        ]
    )
    _state, alerts = sa.alerts_for("o/r", "code", run=_runner({"code-scanning": (0, payload, "")}))
    assert alerts[0]["severity"] == "high"
    assert alerts[0]["what"] == "Missing workflow permissions"


def test_alerts_for_resolves_gh_at_call_time(monkeypatch):
    """A default argument would bind the real `gh` at import."""
    calls = []

    def fake(args):
        calls.append(args)
        return 0, "[]", ""

    monkeypatch.setattr(sa, "_gh", fake)
    assert sa.alerts_for("o/r", "secret")[0] == sa.OK
    assert calls and "secret-scanning" in calls[0][1]


def test_both_classes_are_swept_for_every_repo():
    asked = []

    def run(args):
        asked.append(args[1])
        return 0, "[]", ""

    sa.sweep(["o/a", "o/b"], run=run, visibility={"o/a": True, "o/b": True})
    assert sum("code-scanning" in u for u in asked) == 2
    assert sum("secret-scanning" in u for u in asked) == 2


def test_incomplete_org_enumeration_is_not_clean():
    results, public = sa.sweep(
        ["o/r"],
        run=_runner({"code-scanning": (0, "[]", ""), "secret-scanning": (0, "[]", "")}),
        visibility={"o/r": True},
    )
    _lines, code = sa.format_report(results, public, incomplete=True)
    assert code == 1


@pytest.mark.parametrize("kind", sorted(sa.CLASSES))
def test_no_class_asks_for_a_closed_alert(kind):
    """`state=open` is what makes a count actionable rather than historical."""
    assert "state=open" in sa.CLASSES[kind]["path"]
