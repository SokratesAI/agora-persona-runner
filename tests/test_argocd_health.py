"""`tools.argocd_health` — the verdicts, and the one judgement it makes.

The tests that matter here are the ones about *not* raising: an
Application held `Degraded` by a Job that a later run of the same CronJob
succeeded past is the live case this tool was written for
(`sokratesai-infra`, Degraded three days, nine such Jobs), and calling it
actionable would make every cycle re-derive it.
"""

import datetime
import json
import subprocess

import pytest

from tools import argocd_health


NOW = datetime.datetime(2026, 8, 27, 1, 30, tzinfo=datetime.timezone.utc)


def app(name, sync="Synced", health="Healthy", since="", cronjobs=()):
    return {
        "metadata": {"name": name, "namespace": "argocd"},
        "status": {
            "sync": {"status": sync},
            "health": {"status": health, "lastTransitionTime": since},
            "resources": [
                {"kind": "CronJob", "namespace": ns, "name": cj}
                for ns, cj in cronjobs
            ],
        },
    }


def job(name, cronjob, created, failed=False, succeeded=False, namespace="agents"):
    return {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "creationTimestamp": created,
            "ownerReferences": [{"kind": "CronJob", "name": cronjob}],
        },
        "status": {"failed": 1 if failed else 0, "succeeded": 1 if succeeded else 0},
    }


def fake_kubectl(apps=(), jobs=(), fail_on=None, stdout=None):
    """A `subprocess.run` that answers the two queries the tool makes."""
    def runner(args, **kwargs):
        which = "applications" if "applications" in args else "jobs"
        if fail_on == which:
            return subprocess.CompletedProcess(args, 1, "", "Error from server (Forbidden)")
        if stdout is not None and which == stdout[0]:
            return subprocess.CompletedProcess(args, 0, stdout[1], "")
        items = apps if which == "applications" else jobs
        return subprocess.CompletedProcess(args, 0, json.dumps({"items": list(items)}), "")
    return runner


def report_for(apps, jobs):
    parsed, why = argocd_health.read_applications(fake_kubectl(apps=apps, jobs=jobs))
    assert why is None
    by_owner, why = argocd_health.read_jobs(fake_kubectl(apps=apps, jobs=jobs))
    assert why is None
    return argocd_health.report(parsed, by_owner, NOW)


# --- the clean case -------------------------------------------------

def test_every_application_synced_and_healthy_is_status_zero():
    lines, status = report_for([app("agora-config"), app("reloader")], [])
    assert status == 0
    assert any("ok      agora-config: Synced, Healthy" in l for l in lines)


def test_it_names_how_many_applications_it_swept():
    lines, _ = report_for([app("a"), app("b"), app("c")], [])
    assert any("Read 3 ArgoCD Application(s)" in l for l in lines)


def test_progressing_is_not_a_finding():
    # A sync in flight is the normal state right after every merge. A
    # checker that fired on it would fire on its own cycle's deploy.
    _, status = report_for([app("x", health="Progressing")], [])
    assert status == 0


# --- the two verdicts stay separate ---------------------------------

def test_out_of_sync_is_reported_even_when_healthy():
    lines, status = report_for([app("x", sync="OutOfSync")], [])
    assert status == 2
    assert any(l.startswith("OUT OF SYNC  x") for l in lines)
    assert not any("UNHEALTHY" in l for l in lines)


def test_degraded_is_reported_even_when_synced():
    lines, status = report_for([app("x", health="Degraded")], [])
    assert status == 2
    assert any(l.startswith("UNHEALTHY  x: Degraded") for l in lines)
    assert not any("OUT OF SYNC" in l for l in lines)


def test_missing_and_unknown_count_as_unhealthy():
    for health in ("Missing", "Unknown", "Suspended"):
        _, status = report_for([app("x", health=health)], [])
        assert status == 2, health


# --- the judgement: stale job failures ------------------------------

def test_a_failure_a_later_run_succeeded_past_does_not_raise_the_status():
    lines, status = report_for(
        [app("infra", health="Degraded", cronjobs=[("agents", "rss")])],
        [job("rss-1", "rss", "2026-08-24T08:00:00Z", failed=True),
         job("rss-2", "rss", "2026-08-26T08:00:00Z", succeeded=True)],
    )
    assert status == 0
    assert any(l.startswith("STALE JOB FAILURE  infra") for l in lines)
    assert any("rss-1 failed 2026-08-24T08:00:00Z" in l for l in lines)


def test_a_failure_with_nothing_newer_that_worked_does_raise_it():
    lines, status = report_for(
        [app("infra", health="Degraded", cronjobs=[("agents", "rss")])],
        [job("rss-1", "rss", "2026-08-24T08:00:00Z", succeeded=True),
         job("rss-2", "rss", "2026-08-26T08:00:00Z", failed=True)],
    )
    assert status == 2
    assert any("rss-2 failed and rss has not succeeded since" in l for l in lines)


def test_one_live_failure_beside_stale_ones_still_raises():
    # The whole Application is only quiet when *every* failing Job it holds
    # has been succeeded past. A stale failure must never mask a live one.
    _, status = report_for(
        [app("infra", health="Degraded",
             cronjobs=[("agents", "rss"), ("agents", "gen")])],
        [job("rss-1", "rss", "2026-08-24T08:00:00Z", failed=True),
         job("rss-2", "rss", "2026-08-26T08:00:00Z", succeeded=True),
         job("gen-1", "gen", "2026-08-26T09:00:00Z", failed=True)],
    )
    assert status == 2


def test_degraded_with_no_failing_job_at_all_says_it_cannot_explain_it():
    lines, status = report_for(
        [app("infra", health="Degraded", cronjobs=[("agents", "rss")])],
        [job("rss-1", "rss", "2026-08-26T08:00:00Z", succeeded=True)],
    )
    assert status == 2
    assert any("nothing in its tracked resources explains it" in l for l in lines)


def test_success_is_compared_by_timestamp_not_by_name():
    # The generated suffix counts unix minutes, so it happens to sort
    # correctly — reading the schedule out of the string would work today
    # and is not what the field is for.
    stale, live = argocd_health.stale_job_failures(
        [("agents", "rss")],
        {("agents", "rss"): [
            {"name": "zzz", "created": "2026-08-20T00:00:00Z", "failed": True,
             "succeeded": False},
            {"name": "aaa", "created": "2026-08-26T00:00:00Z", "failed": False,
             "succeeded": True},
        ]},
    )
    assert [r["job"] for r in stale] == ["zzz"]
    assert live == []


def test_a_job_owned_by_no_cronjob_is_not_read_as_one():
    body = {"items": [{
        "metadata": {"name": "helm-install", "namespace": "kube-system",
                     "creationTimestamp": "2026-08-01T00:00:00Z"},
        "status": {"failed": 1},
    }]}
    by_owner, why = argocd_health.read_jobs(
        fake_kubectl(stdout=("jobs", json.dumps(body))))
    assert why is None
    assert by_owner == {}


# --- unreadable never reads as clean --------------------------------

def test_kubectl_refused_on_applications_is_status_one(capsys):
    assert argocd_health.main([], runner=fake_kubectl(fail_on="applications"), now=NOW) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


def test_kubectl_refused_on_jobs_is_status_one(capsys):
    assert argocd_health.main(
        [], runner=fake_kubectl(apps=[app("x")], fail_on="jobs"), now=NOW) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


def test_no_applications_at_all_is_no_instrument_not_a_clean_bill(capsys):
    assert argocd_health.main([], runner=fake_kubectl(apps=[]), now=NOW) == 1
    assert "no Applications at all" in capsys.readouterr().out


def test_output_that_is_not_json_is_status_one(capsys):
    assert argocd_health.main(
        [], runner=fake_kubectl(stdout=("applications", "<html>")), now=NOW) == 1
    assert "not JSON" in capsys.readouterr().out


# --- the age beside the verdict -------------------------------------

def test_the_age_is_printed_beside_an_unhealthy_verdict():
    lines, _ = report_for(
        [app("x", health="Degraded", since="2026-08-24T09:31:00Z")], [])
    assert any("Degraded, 2d" in l for l in lines)


def test_an_unreadable_timestamp_costs_the_age_and_not_the_verdict():
    lines, status = report_for([app("x", health="Degraded", since="whenever")], [])
    assert status == 2
    assert any(l.startswith("UNHEALTHY  x: Degraded") for l in lines)


@pytest.mark.parametrize("since,expected", [
    ("2026-08-27T01:00:00Z", "30m"),
    ("2026-08-26T20:30:00Z", "5h"),
    ("2026-08-24T09:31:00Z", "2d"),
])
def test_age_units(since, expected):
    assert argocd_health._age(since, NOW) == expected
