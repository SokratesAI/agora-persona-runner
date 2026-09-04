"""The three states `tools.alerts` has to keep apart.

The one worth the test is the middle: Prometheus answering happily while the
thing it is meant to watch is invisible to it. Both of the ways that happens
here -- no rules loaded, and a scrape job that discovered nothing -- look
exactly like a clean run from the outside, because a rule that never loaded
evaluates nothing and a job with no targets publishes no `up` series for
`up == 0` to match.
"""

import urllib.error

import pytest

from tools import alerts


def _rules(*alert_states):
    return {
        "groups": [
            {
                "name": "cluster",
                "file": "/etc/prometheus/alerts.yml",
                "rules": [
                    {
                        "type": "alerting",
                        "name": "NodeMemoryCritical",
                        "alerts": [
                            {
                                "state": state,
                                "activeAt": "2026-09-04T07:00:00Z",
                                "labels": {
                                    "alertname": "NodeMemoryCritical",
                                    "severity": "high",
                                    "node": "server1",
                                },
                                "annotations": {"summary": "server1 is using 94% of its memory"},
                            }
                            for state in alert_states
                        ],
                    }
                ],
            }
        ]
    }


def _targets(pools=alerts.EXPECTED_POOLS, health="up"):
    return {
        "activeTargets": [
            {
                "scrapePool": pool,
                "scrapeUrl": f"http://{pool}/metrics",
                "health": health,
                "lastError": "" if health == "up" else "context deadline exceeded",
            }
            for pool in sorted(pools)
        ]
    }


def _wire(monkeypatch, rules, targets):
    def fake_get(base, path):
        return rules if path.startswith("/api/v1/rules") else targets

    monkeypatch.setattr(alerts, "_get", fake_get)


def test_unreachable_prometheus_is_not_a_clean_run(monkeypatch):
    def refuse(base, path):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(alerts, "_get", refuse)
    status, lines = alerts.report("http://nowhere:9090")
    assert status == 1
    assert "COULD NOT READ" in lines[0]
    assert not any("Nothing firing" in line for line in lines)


def test_zero_loaded_rules_is_not_a_clean_run(monkeypatch):
    _wire(monkeypatch, {"groups": []}, _targets())
    status, lines = alerts.report()
    assert status == 1
    assert any("ZERO alerting rules" in line for line in lines)


def test_a_firing_alert_raises_and_is_named(monkeypatch):
    _wire(monkeypatch, _rules("firing"), _targets())
    status, lines = alerts.report()
    assert status == 2
    firing = [line for line in lines if line.startswith("FIRING")]
    assert len(firing) == 1
    assert "NodeMemoryCritical" in firing[0]
    assert "node=server1" in firing[0]
    assert "94%" in firing[0]


def test_a_pending_alert_is_printed_but_does_not_raise(monkeypatch):
    _wire(monkeypatch, _rules("pending"), _targets())
    status, lines = alerts.report()
    assert status == 0
    assert any(line.startswith("pending") for line in lines)
    assert not any(line.startswith("FIRING") for line in lines)


def test_a_scrape_job_that_discovered_nothing_raises(monkeypatch):
    """`up == 0` cannot see this: with no targets there is no `up` series."""
    reduced = set(alerts.EXPECTED_POOLS) - {"kubelet-cadvisor"}
    _wire(monkeypatch, _rules(), _targets(pools=reduced))
    status, lines = alerts.report()
    assert status == 2
    named = [line for line in lines if "NO TARGETS" in line]
    assert len(named) == 1 and "kubelet-cadvisor" in named[0]


def test_an_unhealthy_target_raises_with_its_error(monkeypatch):
    _wire(monkeypatch, _rules(), _targets(health="down"))
    status, lines = alerts.report()
    assert status == 2
    assert any("context deadline exceeded" in line for line in lines)


def test_everything_up_and_quiet_is_exit_zero(monkeypatch):
    _wire(monkeypatch, _rules(), _targets())
    status, lines = alerts.report()
    assert status == 0
    assert any("Nothing firing and every target up." in line for line in lines)
    assert any("1 alerting rule(s) loaded" in line for line in lines)


@pytest.mark.parametrize("stamp", ["2026-09-04T07:00:00Z", "not-a-time"])
def test_age_never_throws_on_a_stamp_it_cannot_parse(stamp):
    assert alerts._age(stamp)
