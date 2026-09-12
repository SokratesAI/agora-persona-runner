"""`/api/alerts` -- idea #122, the Sentinel's findings on a page he can open.

The assertions that matter here are all one shape: **an empty alert list is
produced by a healthy cluster, by a dead scrape, and by a rules file that
failed to load, and the page must not draw those the same way.** That is
`prompt.md`'s negative-result-guaranteed-in-advance rule, and it is the only
reason this module is not a passthrough of Prometheus's own JSON.
"""

import json
import pathlib
import urllib.error

import pytest

from agora_runner import nova_alerts

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _alerts(*alerts):
    return {"data": {"alerts": list(alerts)}}


def _rules(count):
    return {"data": {"groups": [{"rules": [{"name": "r%d" % i} for i in range(count)]}]}}


def _alert(state="firing", **labels):
    base = {"alertname": "PodCrashLooping", "severity": "warning"}
    base.update(labels)
    return {
        "state": state,
        "labels": base,
        "annotations": {"summary": "a pod is restarting"},
        "activeAt": "2026-09-12T01:02:03Z",
    }


def test_a_firing_alert_carries_its_name_text_and_where():
    payload = nova_alerts.summarise(
        _alerts(_alert(namespace="infra", pod="whatsapp-bridge-1")), _rules(6)
    )
    assert payload["reachable"] is True
    assert len(payload["firing"]) == 1
    one = payload["firing"][0]
    assert one["name"] == "PodCrashLooping"
    assert one["severity"] == "warning"
    assert one["text"] == "a pod is restarting"
    assert one["where"] == ["namespace infra", "pod whatsapp-bridge-1"]
    assert one["since"] == "2026-09-12T01:02:03Z"


def test_pending_is_kept_separate_from_firing():
    """`for:` has not elapsed, so a pending alert may still be a spike.

    Merging them would make the count on the page a number he learns to
    ignore, which is the whole failure the row is about.
    """
    payload = nova_alerts.summarise(
        _alerts(_alert(state="firing"), _alert(state="pending")), _rules(6)
    )
    assert len(payload["firing"]) == 1
    assert len(payload["pending"]) == 1
    assert payload["firing"][0]["state"] == "firing"
    assert payload["pending"][0]["state"] == "pending"


def test_a_state_that_is_neither_is_dropped_rather_than_counted_as_firing():
    payload = nova_alerts.summarise(_alerts(_alert(state="inactive")), _rules(6))
    assert payload["firing"] == []
    assert payload["pending"] == []


def test_quiet_with_rules_loaded_is_not_blind():
    payload = nova_alerts.summarise(_alerts(), _rules(6))
    assert payload["reachable"] is True
    assert payload["firing"] == []
    assert payload["rules"] == 6
    assert payload["blind"] is False


def test_zero_rules_is_blind_not_quiet():
    """The failure this whole module exists to keep visible.

    Prometheus stays Running and green with a rules file that failed to
    load, serving an empty alert list forever. From the alert list alone
    that is indistinguishable from a calm cluster.
    """
    payload = nova_alerts.summarise(_alerts(), {"data": {"groups": []}})
    assert payload["reachable"] is True
    assert payload["firing"] == []
    assert payload["blind"] is True


def test_unreachable_never_reports_quiet():
    payload = nova_alerts.summarise(None, None, error="connection refused")
    assert payload["reachable"] is False
    assert payload["error"] == "connection refused"
    assert payload["blind"] is True
    assert payload["firing"] == []


def test_a_dead_prometheus_is_an_unreachable_payload_not_an_exception():
    """This is a page, not a check: it must render something, never 500."""

    def boom(path):
        raise urllib.error.URLError("connection refused")

    payload = nova_alerts.alerts_payload(get=boom)
    assert payload["reachable"] is False
    assert "refused" in payload["error"]


def test_malformed_json_is_unreachable_rather_than_a_crash():
    def rubbish(path):
        raise ValueError("Expecting value: line 1 column 1")

    payload = nova_alerts.alerts_payload(get=rubbish)
    assert payload["reachable"] is False


def test_an_alert_with_no_annotations_still_names_its_rule():
    bare = {"state": "firing", "labels": {"alertname": "TargetDown"}}
    payload = nova_alerts.summarise(_alerts(bare), _rules(6))
    assert payload["firing"][0]["name"] == "TargetDown"
    assert payload["firing"][0]["text"] == ""
    assert payload["firing"][0]["where"] == []


def test_description_is_used_when_there_is_no_summary():
    """Our own rules write `summary`; upstream rule packs write
    `description`. A card that only read one would be blank for the other."""
    alert = {
        "state": "firing",
        "labels": {"alertname": "KubeNodeNotReady"},
        "annotations": {"description": "server2 has been NotReady for 15m"},
    }
    payload = nova_alerts.summarise(_alerts(alert), _rules(6))
    assert payload["firing"][0]["text"] == "server2 has been NotReady for 15m"


def test_rules_are_counted_across_every_group():
    two_groups = {"data": {"groups": [{"rules": [1, 2]}, {"rules": [3]}]}}
    assert nova_alerts.summarise(_alerts(), two_groups)["rules"] == 3


def test_the_page_route_and_the_api_route_are_both_registered():
    """Three files have to agree for this page to exist at all.

    `tests/test_nav_is_grouped.py` already pins the menu link against
    `PAGE_ROUTES`; what nothing else covers is that the API route the view
    fetches is actually served.
    """
    site = (ROOT / "agora_runner" / "nova_site.py").read_text()
    assert '"/alerts",' in site
    assert 'if path == "/api/alerts":' in site
    assert "from agora_runner.nova_alerts import alerts_payload" in site


def test_the_browser_router_and_the_view_agree_on_the_name():
    app = (ROOT / "agora_runner" / "nova_public" / "app.js").read_text()
    assert 'if (path === "/alerts") return { view: "alerts"' in app
    assert 'if (here.view === "alerts") {' in app
    assert "function renderAlerts(payload)" in app
    assert "function loadAlerts()" in app


def test_every_state_word_is_spelled_out_beside_its_colour():
    """`personality.md`: if a reader has to know a colour code to know what
    I said, I have not said it."""
    app = (ROOT / "agora_runner" / "nova_public" / "app.js").read_text()
    assert "Firing" in app
    assert "Pending" in app
    css = (ROOT / "agora_runner" / "nova_public" / "style.css").read_text()
    assert ".alert-firing { border-left-color: var(--danger); }" in css
    assert ".alert-pending { border-left-color: var(--warn); }" in css


def test_the_alerts_stylesheet_uses_variables_this_sheet_actually_defines():
    """A `var(--muted)` typo renders as no colour at all and nothing fails.

    The first draft of this sheet used exactly that name; it is not in
    `:root`, so the two secondary lines on every card would have inherited
    body text and looked deliberate.
    """
    css = (ROOT / "agora_runner" / "nova_public" / "style.css").read_text()
    block = css[css.rindex("/* `/alerts` -- idea #122."):]
    import re

    used = set(re.findall(r"var\((--[a-z-]+)\)", block))
    defined = set(re.findall(r"^\s*(--[a-z-]+):", css, re.M))
    assert used <= defined, sorted(used - defined)
