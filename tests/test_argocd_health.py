"""`tools.argocd_health` — the verdicts, and the one judgement it makes.

The test that matters most here is that an unhealthy Application always
raises. Cycle 513 wrote the opposite — a `Degraded` was excused when every
failing Job under its CronJobs had been succeeded past — and Cycle 933
measured that a Job under a CronJob is not an immediate child of the
Application and so cannot hold it `Degraded` at all. The live case
(`sokratesai-infra`, Degraded seven days) was four SealedSecrets refusing
to write their Secret, and the old quiet verdict hid them.
"""

import datetime
import json
import subprocess

import pytest

from tools import argocd_health


NOW = datetime.datetime(2026, 8, 27, 1, 30, tzinfo=datetime.timezone.utc)


def app(name, sync="Synced", health="Healthy", since="", cronjobs=(), sealed=()):
    return {
        "metadata": {"name": name, "namespace": "argocd"},
        "status": {
            "sync": {"status": sync},
            "health": {"status": health, "lastTransitionTime": since},
            "resources": [
                {"kind": "CronJob", "namespace": ns, "name": cj}
                for ns, cj in cronjobs
            ] + [
                {"kind": "SealedSecret", "namespace": ns, "name": nm}
                for ns, nm in sealed
            ],
        },
    }


def sealed_secret(name, namespace="infra", synced="True", message=""):
    return {
        "metadata": {"name": name, "namespace": namespace},
        "status": {"conditions": [
            {"type": "Synced", "status": synced, "message": message}]},
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


def _which(args):
    for kind in ("applications", "jobs", "sealedsecrets"):
        if kind in args:
            return kind
    raise AssertionError(f"unexpected kubectl call: {args}")


def fake_kubectl(apps=(), jobs=(), sealed=(), fail_on=None, stdout=None):
    """A `subprocess.run` that answers the two queries the tool makes."""
    def runner(args, **kwargs):
        which = _which(args)
        if fail_on == which:
            return subprocess.CompletedProcess(args, 1, "", "Error from server (Forbidden)")
        if stdout is not None and which == stdout[0]:
            return subprocess.CompletedProcess(args, 0, stdout[1], "")
        items = {"applications": apps, "jobs": jobs, "sealedsecrets": sealed}[which]
        return subprocess.CompletedProcess(args, 0, json.dumps({"items": list(items)}), "")
    return runner


def report_for(apps, jobs, sealed=()):
    runner = fake_kubectl(apps=apps, jobs=jobs, sealed=sealed)
    parsed, why = argocd_health.read_applications(runner)
    assert why is None
    by_owner, why = argocd_health.read_jobs(runner)
    assert why is None
    broken, why = argocd_health.read_sealed_secrets(runner)
    assert why is None
    return argocd_health.report(parsed, by_owner, NOW, broken)


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


# --- the judgement: a stale job failure is history, not a cause ------

def test_a_failure_a_later_run_succeeded_past_is_history_and_still_raises():
    # It used to exit 0 here. A Job owned by a CronJob is created by a
    # controller and is in no application source, so ArgoCD never
    # aggregates it into App health — excusing a Degraded with one is
    # excusing it with something that cannot have caused it.
    lines, status = report_for(
        [app("infra", health="Degraded", cronjobs=[("agents", "rss")])],
        [job("rss-1", "rss", "2026-08-24T08:00:00Z", failed=True),
         job("rss-2", "rss", "2026-08-26T08:00:00Z", succeeded=True)],
    )
    assert status == 2
    assert any(l.startswith("UNHEALTHY  infra") for l in lines)
    assert any("history, not the cause" in l and "1 failed Job(s)" in l
               for l in lines)
    assert not any("STALE JOB FAILURE" in l for l in lines)


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
    assert any("no immediate child of it is measurably unhealthy" in l
               for l in lines)


# --- the cause it can actually name ----------------------------------

def test_a_sealed_secret_that_cannot_write_its_secret_is_named():
    # The live case: sokratesai-infra, Degraded seven days, four
    # SealedSecrets refusing to overwrite a Secret they do not own.
    lines, status = report_for(
        [app("infra", health="Degraded", sealed=[("infra", "repo-read-token")],
             cronjobs=[("agents", "rss")])],
        [job("rss-1", "rss", "2026-08-24T08:00:00Z", failed=True),
         job("rss-2", "rss", "2026-08-26T08:00:00Z", succeeded=True)],
        [sealed_secret("repo-read-token", synced="False",
                       message='failed update: Resource "repo-read-token" '
                               "already exists and is not managed by SealedSecret")],
    )
    assert status == 2
    assert any("SealedSecret infra/repo-read-token is not healthy" in l
               and "already exists" in l for l in lines)
    # Named cause, so it must not also claim it cannot explain itself.
    assert not any("no immediate child" in l for l in lines)


def test_a_synced_sealed_secret_is_not_offered_as_a_cause():
    # The complement. Without it the check would "name a cause" on every
    # Degraded app that happens to own a SealedSecret.
    lines, status = report_for(
        [app("infra", health="Degraded", sealed=[("infra", "repo-read-token")])],
        [],
        [sealed_secret("repo-read-token", synced="True")],
    )
    assert status == 2
    assert not any("SealedSecret" in l for l in lines)
    assert any("no immediate child of it is measurably unhealthy" in l
               for l in lines)


def test_a_broken_sealed_secret_belonging_to_another_app_is_not_borrowed():
    # The cause has to be one of *this* Application's immediate children.
    lines, _ = report_for(
        [app("infra", health="Degraded")],
        [],
        [sealed_secret("repo-read-token", synced="False", message="nope")],
    )
    assert not any("repo-read-token" in l for l in lines)


def test_a_sealed_secret_with_no_conditions_yet_is_not_a_finding():
    # The controller writes conditions on its first pass; an unreconciled
    # SealedSecret is young, not broken.
    runner = fake_kubectl(sealed=[{"metadata": {"name": "s", "namespace": "infra"},
                                   "status": {}}])
    broken, why = argocd_health.read_sealed_secrets(runner)
    assert why is None
    assert broken == {}


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
    # Only reachable when something is unhealthy, which is the only time
    # the Jobs are asked for at all.
    assert argocd_health.main(
        [], runner=fake_kubectl(apps=[app("x", health="Degraded")], fail_on="jobs"),
        now=NOW) == 1
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


# --- the reviewer's findings, pinned ---------------------------------

def test_a_failed_job_with_no_timestamp_is_never_swept_quiet():
    # An empty `creationTimestamp` sorts before every real stamp, so the
    # plain comparison would call any such failure superseded and go
    # silent on it. Nothing establishes that it predates the success.
    lines, status = report_for(
        [app("infra", health="Degraded", cronjobs=[("agents", "rss")])],
        [{"metadata": {"name": "mystery", "namespace": "agents",
                       "creationTimestamp": "",
                       "ownerReferences": [{"kind": "CronJob", "name": "rss"}]},
          "status": {"failed": 1}},
         job("rss-2", "rss", "2026-08-26T08:00:00Z", succeeded=True)],
    )
    assert status == 2
    assert any("mystery failed and rss has not succeeded since" in l for l in lines)


def test_valid_json_that_is_not_an_object_is_unreadable_not_a_traceback(capsys):
    assert argocd_health.main(
        [], runner=fake_kubectl(stdout=("applications", "null")), now=NOW) == 1
    assert "not an object" in capsys.readouterr().out


def test_jobs_are_not_read_at_all_when_every_application_is_healthy():
    # `kubectl get jobs -A` is a cluster-wide read a restricted account can
    # be refused; asking for it on a clean cluster would turn a green answer
    # into COULD NOT READ for data nothing was going to use.
    asked = []

    def runner(args, **kwargs):
        which = _which(args)
        asked.append(which)
        items = [app("x")] if which == "applications" else []
        return subprocess.CompletedProcess(
            args, 0, json.dumps({"items": items}), "")

    assert argocd_health.main([], runner=runner, now=NOW) == 0
    assert asked == ["applications"]


def test_jobs_and_sealed_secrets_are_read_once_something_is_unhealthy():
    asked = []

    def runner(args, **kwargs):
        which = _which(args)
        asked.append(which)
        items = [app("x", health="Degraded")] if which == "applications" else []
        return subprocess.CompletedProcess(
            args, 0, json.dumps({"items": items}), "")

    assert argocd_health.main([], runner=runner, now=NOW) == 2
    assert asked == ["applications", "jobs", "sealedsecrets"]


def test_kubectl_refused_on_sealed_secrets_is_status_one(capsys):
    # Never clean: a refusal here means the one cause this can name was
    # not looked for.
    runner = fake_kubectl(apps=[app("x", health="Degraded")],
                          fail_on="sealedsecrets")
    assert argocd_health.main([], runner=runner, now=NOW) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


# --- the summary line survives the preflight collapse ----------------
#
# `preflight` reports a check as one line, and it picks the last line
# carrying a digit. An Application that is unhealthy with no cause this
# check can name has to ride on *that* line or it is invisible on a
# normal morning. Cycle 810 swept clean here and still wrote
# "sokratesai-infra reports Degraded and I do not know why or since when"
# into the handoff. These pin the line, not the paragraph.


def _unexplained(name="sokratesai-infra", since="2026-08-22T01:30:00Z"):
    return report_for(
        [app(name, health="Degraded", since=since, cronjobs=[("agents", "rss")])],
        [job("rss-1", "rss", "2026-08-20T08:00:00Z", failed=True),
         job("rss-2", "rss", "2026-08-26T08:00:00Z", succeeded=True)],
    )


def test_the_unexplained_application_is_named_on_the_line_preflight_keeps():
    from tools.preflight import summary_line

    lines, status = _unexplained()
    assert status == 2
    kept = summary_line("\n".join(lines))
    assert "sokratesai-infra" in kept
    assert "Degraded" in kept


def test_the_kept_line_carries_how_long_it_has_been_unhealthy():
    # "since when" is the half of the question the handoff could not
    # answer, so the age has to be on the kept line and not only beside
    # the UNHEALTHY heading five lines up.
    from tools.preflight import summary_line

    lines, _ = _unexplained(since="2026-08-22T01:30:00Z")
    assert "5d" in summary_line("\n".join(lines))


def test_an_application_with_a_named_cause_is_not_called_unexplained():
    from tools.preflight import summary_line

    lines, _ = report_for(
        [app("infra", health="Degraded", sealed=[("infra", "tok")])],
        [],
        [sealed_secret("tok", synced="False", message="cannot write")],
    )
    assert not any("no immediate child this check can name" in l for l in lines)
    assert "infra" not in summary_line("\n".join(lines)).split("cause:")[-1]


def test_a_clean_sweep_does_not_claim_anything_is_held():
    # The complement, and it is the one that would rot: a sentence that
    # printed on every run regardless is the footnote problem again.
    from tools.preflight import summary_line

    lines, status = report_for([app("a"), app("b")], [])
    assert status == 0
    kept = summary_line("\n".join(lines))
    assert "Read 2 ArgoCD Application(s)" in kept
    assert "no immediate child" not in kept
    assert not any("An unexplained Degraded raises" in l for l in lines)


def test_two_unexplained_applications_are_both_named():
    lines, status = report_for(
        [app("infra", health="Degraded", cronjobs=[("agents", "rss")]),
         app("other", health="Degraded", cronjobs=[("agents", "gen")])],
        [job("rss-1", "rss", "2026-08-20T08:00:00Z", failed=True),
         job("rss-2", "rss", "2026-08-26T08:00:00Z", succeeded=True),
         job("gen-1", "gen", "2026-08-20T08:00:00Z", failed=True),
         job("gen-2", "gen", "2026-08-26T08:00:00Z", succeeded=True)],
    )
    assert status == 2
    kept = [l for l in lines if "no immediate child this check can name" in l]
    assert len(kept) == 1
    assert "infra" in kept[0] and "other" in kept[0]


# --- the remedy for a SealedSecret blocked on a Secret it does not own ----
#
# Both messages below are verbatim strings the sealed-secrets controller
# writes, not strings built out of the matcher. The first is what
# `argocd/scm-generator-github-token`, `crossplane-system/github-creds`,
# `infra/repo-read-token` and `platform-catalog/github-app-creds` all carried
# on 2026-09-05, read off the live cluster. The second is the controller's
# wrong-key failure, which is a different problem with a different fix.

_UNOWNED = ('failed update: Resource "repo-read-token" already exists and '
            "is not managed by SealedSecret")
_WRONG_KEY = ("no key could decrypt secret (token)")


def test_a_secret_the_controller_does_not_own_names_the_command_that_fixes_it():
    lines, status = report_for(
        [app("infra", health="Degraded", sealed=[("infra", "repo-read-token")])],
        [],
        [sealed_secret("repo-read-token", synced="False", message=_UNOWNED)],
    )
    assert status == 2
    assert any("kubectl annotate secret -n infra repo-read-token "
               "sealedsecrets.bitnami.com/managed=true" in l for l in lines)
    assert any("no pull request fixes this" in l for l in lines)


def test_the_remedy_says_it_overwrites_the_live_secret():
    # Without this the line reads as a chore. It is a credential overwrite
    # nobody here can check first, which is why it is handed over rather
    # than done.
    lines, _ = report_for(
        [app("infra", health="Degraded", sealed=[("infra", "repo-read-token")])],
        [],
        [sealed_secret("repo-read-token", synced="False", message=_UNOWNED)],
    )
    assert any("overwrites the live Secret" in l and "owner's call" in l
               for l in lines)


def test_a_different_sealed_secret_failure_gets_no_remedy():
    # The control. A remedy printed beside a message it does not fit would
    # send a cycle to annotate a Secret whose problem is a decryption key.
    lines, status = report_for(
        [app("infra", health="Degraded", sealed=[("infra", "repo-read-token")])],
        [],
        [sealed_secret("repo-read-token", synced="False", message=_WRONG_KEY)],
    )
    assert status == 2
    # The failure itself is still named — only the remedy is withheld.
    assert any("SealedSecret infra/repo-read-token is not healthy" in l
               and _WRONG_KEY in l for l in lines)
    assert not any("kubectl annotate" in l for l in lines)


def test_the_remedy_names_the_namespace_and_secret_it_was_given():
    # A hardcoded example command would pass every assertion above while
    # telling a cycle to annotate the wrong Secret.
    lines, _ = report_for(
        [app("catalog", health="Degraded",
             sealed=[("platform-catalog", "github-app-creds")])],
        [],
        [sealed_secret("github-app-creds", namespace="platform-catalog",
                       synced="False", message=_UNOWNED)],
    )
    assert any("kubectl annotate secret -n platform-catalog github-app-creds"
               in l for l in lines)
    assert not any("infra" in l for l in lines)


def test_a_healthy_application_never_prints_a_remedy():
    lines, status = report_for([app("infra")], [], [])
    assert status == 0
    assert not any("kubectl annotate" in l for l in lines)
