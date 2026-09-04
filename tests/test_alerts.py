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


# --- what is allowed to reach his phone --------------------------------

def _found(**kw):
    base = {
        "base": "http://p",
        "rule_count": 6,
        "pools": {"kubelet": []},
        "active": [{"health": "up"}],
        "missing": [],
        "unhealthy": [],
        "firing": [],
        "pending": [],
    }
    base.update(kw)
    return base


def _alert(name, severity, **labels):
    labels = {"alertname": name, "severity": severity, **labels}
    return {"labels": labels, "state": "firing", "activeAt": "2026-09-04T06:00:00Z"}


def test_a_quiet_cluster_pages_nobody():
    worth, urgent, text = alerts.paging(_found())
    assert worth is False and urgent is False and text == ""


def test_a_high_severity_alert_is_allowed_to_wake_him():
    worth, urgent, text = alerts.paging(_found(firing=[_alert("NodeDiskCritical", "high")]))
    assert worth is True and urgent is True
    assert "NodeDiskCritical" in text


def test_a_medium_severity_alert_is_worth_telling_him_but_not_at_night():
    worth, urgent, text = alerts.paging(_found(firing=[_alert("NodeSwapNearlyFull", "medium")]))
    assert worth is True and urgent is False


def test_a_down_scrape_target_is_urgent_on_its_own():
    # His words were "if one server is down"; a kubelet that stopped answering
    # is that case, and TargetDown cannot fire for a job discovering nothing.
    found = _found(unhealthy=[{"scrapePool": "kubelet", "scrapeUrl": "http://n2/metrics",
                               "lastError": "connection refused", "health": "down"}])
    worth, urgent, text = alerts.paging(found)
    assert worth is True and urgent is True
    assert "http://n2/metrics" in text


def test_no_targets_at_all_is_urgent():
    worth, urgent, _ = alerts.paging(_found(active=[]))
    assert worth is True and urgent is True


def test_a_scrape_job_with_no_targets_is_reported_but_not_urgent():
    worth, urgent, text = alerts.paging(_found(missing=["agora"]))
    assert worth is True and urgent is False
    assert "agora" in text


def test_a_pending_alert_never_pages():
    found = _found(pending=[_alert("NodeMemoryCritical", "high")])
    assert alerts.paging(found)[0] is False


def test_a_broken_instrument_does_not_page():
    # It still exits 1 in the report. Waking him because Prometheus is
    # unreachable teaches him to ignore the channel.
    assert alerts.paging({"unreadable": "COULD NOT READ", "base": "http://p"})[0] is False
    assert alerts.paging({"no_rules": True, "base": "http://p"})[0] is False


def test_the_page_key_ignores_the_duration_and_tracks_the_problem():
    # Two readings of the same outage eighteen minutes apart must share a key,
    # or dedupe never fires and he gets 80 messages a day.
    one = _found(firing=[_alert("NodeDiskCritical", "high")])
    two = _found(firing=[dict(_alert("NodeDiskCritical", "high"), activeAt="2026-09-04T07:00:00Z")])
    assert alerts._page_key(one) == alerts._page_key(two)


def test_a_new_problem_gets_a_new_page_key():
    one = _found(firing=[_alert("NodeDiskCritical", "high")])
    two = _found(firing=[_alert("NodeDiskCritical", "high"), _alert("ContainerRestartLoop", "high")])
    assert alerts._page_key(one) != alerts._page_key(two)


def test_report_renders_a_reading_it_was_handed_without_querying(monkeypatch):
    monkeypatch.setattr(alerts, "collect", lambda base: (_ for _ in ()).throw(AssertionError("re-queried")))
    status, lines = alerts.report("http://p", _found(firing=[_alert("NodeDiskCritical", "high")]))
    assert status == 2
    assert any("FIRING" in line for line in lines)


def test_a_target_that_goes_down_pages_once_not_twice():
    # The real lifecycle of one machine going down: collect() reports the
    # unhealthy target immediately, and TargetDown's `for: 10m` makes the same
    # outage appear a second time as a firing alert one cycle later. Two keys
    # for one incident means the six-hour dedupe never sees the repeat.
    target = {"scrapePool": "kubelet", "scrapeUrl": "http://n2/metrics",
              "lastError": "connection refused", "health": "down"}
    just_down = _found(unhealthy=[target])
    ten_minutes_later = _found(unhealthy=[target], firing=[_alert("TargetDown", "high")])
    assert alerts._page_key(just_down) == alerts._page_key(ten_minutes_later)
    # and the message does not say it twice either
    assert alerts.paging(ten_minutes_later)[2].count("TargetDown") == 0


def test_targetdown_still_pages_when_no_target_is_reported_unhealthy():
    # The suppression is only ever a de-duplication of one fact. With nothing
    # in `unhealthy` there is nothing to duplicate, and the alert must stand.
    found = _found(firing=[_alert("TargetDown", "high")])
    worth, urgent, text = alerts.paging(found)
    assert worth is True and urgent is True and "TargetDown" in text


def test_notify_is_called_with_the_paging_verdict(monkeypatch, capsys):
    # The wire-up itself: alerts --notify must hand tools.notify the key,
    # the urgency and the text that `paging`/`_page_key` produced.
    from tools import notify as notify_tool

    found = _found(firing=[_alert("NodeDiskCritical", "high")])
    monkeypatch.setattr(alerts, "collect", lambda base: found)
    seen = {}

    def fake(text, key, urgent):
        seen.update(text=text, key=key, urgent=urgent)
        return 0, "sent, 40 character(s)"

    monkeypatch.setattr(notify_tool, "notify", lambda text, key, urgent: fake(text, key, urgent))
    code = alerts.main(["--notify"])
    assert code == 2
    assert seen["key"] == alerts._page_key(found)
    assert seen["urgent"] is True
    assert "NodeDiskCritical" in seen["text"]
    assert "telegram: sent" in capsys.readouterr().out


def test_without_the_flag_nothing_is_sent(monkeypatch):
    from tools import notify as notify_tool

    monkeypatch.setattr(alerts, "collect", lambda base: _found(firing=[_alert("NodeDiskCritical", "high")]))
    monkeypatch.setattr(notify_tool, "notify",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent without --notify")))
    assert alerts.main([]) == 2
