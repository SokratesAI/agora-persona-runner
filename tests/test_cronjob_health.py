"""`tools.cronjob_health` — the slot arithmetic, and what it refuses to raise.

The tests that matter here are the ones that pin the verdict to *scheduled
slots* rather than to elapsed minutes. A weekly CronJob is nine days quiet
between successes and healthy the whole time; a five-minute one is broken
after eleven. Any check built on a wall-clock threshold gets one of those
two wrong, so `test_a_weekly_job_quiet_for_six_days_is_healthy` and
`test_a_five_minute_job_quiet_for_the_same_six_days_is_behind` are the pair
that says the design works.
"""

import json
import subprocess

import pytest

from tools import cronjob_health


def cronjob(name, schedule="*/5 * * * *", suspend=False,
            scheduled="", succeeded="", namespace="agents", timezone="Etc/UTC"):
    status = {}
    if scheduled:
        status["lastScheduleTime"] = scheduled
    if succeeded:
        status["lastSuccessfulTime"] = succeeded
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"schedule": schedule, "suspend": suspend, "timeZone": timezone},
        "status": status,
    }


def fake_kubectl(items=(), returncode=0, stderr="", stdout=None):
    """A `subprocess.run` that answers the one query the tool makes."""
    def runner(args, **kwargs):
        if returncode:
            return subprocess.CompletedProcess(args, returncode, "", stderr)
        body = stdout if stdout is not None else json.dumps({"items": list(items)})
        return subprocess.CompletedProcess(args, 0, body, "")
    return runner


def report_for(items):
    rows, why = cronjob_health.read_cronjobs(fake_kubectl(items=items))
    assert why is None
    return cronjob_health.report(rows)


# --- the clean case -------------------------------------------------

def test_a_cronjob_that_succeeded_on_its_newest_slot_is_status_zero():
    lines, status = report_for([
        cronjob("marcus-backup", "20 * * * *",
                scheduled="2026-09-03T19:20:00Z", succeeded="2026-09-03T19:20:07Z"),
    ])
    assert status == 0
    assert any("ok      agents/marcus-backup" in line for line in lines)


def test_it_names_how_many_cronjobs_it_swept():
    lines, _ = report_for([
        cronjob("a", scheduled="2026-09-03T19:50:00Z", succeeded="2026-09-03T19:50:04Z"),
        cronjob("b", scheduled="2026-09-03T19:50:00Z", succeeded="2026-09-03T19:50:04Z"),
    ])
    assert any("Judged 2 CronJob(s)" in line for line in lines)


def test_one_slot_behind_is_the_grace_and_does_not_raise():
    # The run for the newest slot was created this minute and cannot have
    # finished yet. One slot is always explicable; two is not.
    lines, status = report_for([
        cronjob("vault-backup", "50 * * * *",
                scheduled="2026-09-03T19:50:00Z", succeeded="2026-09-03T18:52:03Z"),
    ])
    assert status == 0
    assert any("1 slot(s) behind" in line for line in lines)


# --- the slot arithmetic is the design ------------------------------

def test_a_weekly_job_quiet_for_six_days_is_healthy():
    # `newspaper-suggestions` runs at 23:00 on Saturdays. It is six days
    # stale by the clock and zero slots behind by its own schedule, and a
    # wall-clock threshold cannot tell that from a real failure.
    lines, status = report_for([
        cronjob("newspaper-suggestions", "0 23 * * 6",
                scheduled="2026-08-29T21:00:00Z", succeeded="2026-08-29T21:01:08Z"),
    ])
    assert status == 0
    assert any("0 slot(s) behind" in line for line in lines)


def test_a_five_minute_job_quiet_for_the_same_six_days_is_behind():
    # Same two stamps, different schedule, opposite verdict. This is the
    # pair that says the check reads the schedule and not the calendar.
    lines, status = report_for([
        cronjob("deploy-rollback", "*/5 * * * *",
                scheduled="2026-09-03T19:50:00Z", succeeded="2026-08-29T21:01:08Z"),
    ])
    assert status == 2
    assert any("BEHIND" in line for line in lines)


def test_the_live_failure_this_was_written_for():
    # Measured 21:51 Oslo on 2026-09-03: a second node joined the cluster
    # and went unreachable, and the two CronJobs that matter most both had
    # their newest runs stranded on it.
    lines, status = report_for([
        cronjob("deploy-rollback", "*/5 * * * *",
                scheduled="2026-09-03T19:50:00Z", succeeded="2026-09-03T19:40:05Z"),
        cronjob("nova-alive-ping", "*/5 * * * *", namespace="obsidian",
                scheduled="2026-09-03T19:50:00Z", succeeded="2026-09-03T19:40:06Z"),
    ])
    assert status == 2
    assert sum("2 consecutive run(s) have not succeeded" in l for l in lines) == 2


def test_the_walk_is_capped_and_says_so_rather_than_reporting_a_smaller_number():
    # `heartbeat-liveness` last succeeded on 2026-05-01, which at `*/5` is
    # about 35,000 slots. Counting them all would cost more than the answer
    # is worth, and every slot past the second says the same thing.
    count, capped = cronjob_health.slots_behind(
        "*/5 * * * *",
        cronjob_health._as_datetime("2026-09-01T00:00:00Z"),
        cronjob_health._as_datetime("2026-09-03T19:50:00Z"),
    )
    assert capped is True
    assert count == cronjob_health.MAX_SLOTS
    lines, status = report_for([
        cronjob("stuck", "*/5 * * * *",
                scheduled="2026-09-03T19:50:00Z", succeeded="2026-09-01T00:00:00Z"),
    ])
    assert status == 2
    assert any("at least 100 consecutive run(s)" in line for line in lines)


# --- the verdicts stay separate -------------------------------------

def test_never_scheduled_is_its_own_verdict():
    # Kubernetes has never created a Job for it: the controller does not
    # know about this schedule. Different cause and different first
    # question from a job that used to work and stopped.
    lines, status = report_for([cronjob("orphan", scheduled="", succeeded="")])
    assert status == 2
    assert any("NEVER SCHEDULED  agents/orphan" in line for line in lines)


def test_never_succeeded_is_its_own_verdict():
    lines, status = report_for([
        cronjob("stillborn", scheduled="2026-09-03T19:50:00Z", succeeded=""),
    ])
    assert status == 2
    assert any("NEVER SUCCEEDED  agents/stillborn" in line for line in lines)
    assert not any("BEHIND" in line for line in lines)


# --- what it refuses to raise ---------------------------------------

def test_a_suspended_cronjob_is_not_judged_rather_than_passed():
    # This loop's kubectl is read-only, so no pull request re-enables a
    # CronJob and nothing here marks a suspension deliberate. Raising would
    # put the check red on its first run and every run after it, which is
    # the same as having no check — so it declines to judge instead, in
    # `preflight`'s own caveat form.
    lines, status = report_for([
        cronjob("heartbeat-liveness", suspend=True,
                scheduled="2026-05-01T18:05:00Z", succeeded=""),
    ])
    assert status == 0
    assert any("NOT JUDGED  agents/heartbeat-liveness" in line for line in lines)


def test_a_suspend_is_written_in_the_form_preflight_pulls_out_as_a_caveat():
    # Buried at the tail of a summary sentence in a row marked `ok`, a
    # suspension is invisible in the only report a cycle reads every
    # morning. `preflight` prints a line as a caveat when a stem from
    # CAVEAT_STEMS opens a shouted head, so this asserts against
    # `preflight`'s own predicate rather than against the literal.
    from tools import preflight
    lines, _ = report_for([cronjob("heartbeat-liveness", suspend=True)])
    line = next(l for l in lines if "heartbeat-liveness" in l and l.startswith("NOT"))
    head = preflight.SHOUTED_HEAD.match(line)
    assert head and any(stem in head.group(0) for stem in preflight.CAVEAT_STEMS)


def test_a_suspended_cronjob_rides_on_the_line_preflight_keeps():
    # `preflight` collapses a check that exits 0 to its last line carrying a
    # digit. A suspend is the whole reason this check does not raise, so its
    # name has to be on the summary or it vanishes from the only report a
    # cycle reads every morning.
    lines, _ = report_for([cronjob("heartbeat-liveness", suspend=True)])
    swept = next(l for l in lines if "Judged 1 CronJob(s)" in l)  # noqa: E501
    assert "agents/heartbeat-liveness" in swept


def test_a_suspend_never_hides_a_real_finding_beside_it():
    lines, status = report_for([
        cronjob("heartbeat-liveness", suspend=True),
        cronjob("deploy-rollback", scheduled="2026-09-03T19:50:00Z",
                succeeded="2026-09-03T19:35:00Z"),
    ])
    assert status == 2


# --- unreadable never reads as clean --------------------------------

def test_kubectl_refused_is_status_one():
    rows, why = cronjob_health.read_cronjobs(
        fake_kubectl(returncode=1, stderr="Error from server (Forbidden)"))
    assert rows is None
    assert "Forbidden" in why


def test_no_cronjobs_at_all_is_no_instrument_not_a_clean_sweep():
    # This cluster demonstrably runs CronJobs, so zero of them means the
    # query looked in the wrong place.
    assert cronjob_health.main([], runner=fake_kubectl(items=[])) == 1


def test_an_unparseable_schedule_costs_the_sweep_its_clean_verdict():
    lines, status = report_for([
        cronjob("nonsense", "every tuesday please",
                scheduled="2026-09-03T19:50:00Z", succeeded="2026-09-03T19:45:00Z"),
    ])
    assert status == 1
    assert any("CANNOT JUDGE  agents/nonsense" in line for line in lines)


def test_an_unparseable_schedule_does_not_hide_a_real_finding():
    # Exit 2 outranks exit 1: a partial sweep that found something real
    # must still say so, the same call `pin_drift` makes.
    _, status = report_for([
        cronjob("nonsense", "every tuesday please",
                scheduled="2026-09-03T19:50:00Z", succeeded="2026-09-03T19:45:00Z"),
        cronjob("deploy-rollback", scheduled="2026-09-03T19:50:00Z",
                succeeded="2026-09-03T19:35:00Z"),
    ])
    assert status == 2


def test_kubectl_returning_something_that_is_not_json_is_status_one():
    rows, why = cronjob_health.read_cronjobs(fake_kubectl(stdout="<html>502</html>"))
    assert rows is None
    assert "not JSON" in why


# --- the CronJob's own timeZone, not this process's ------------------

def test_an_oslo_schedule_is_matched_in_oslo_and_not_in_utc():
    # `newspaper-generator` is `0 0 * * *` on Europe/Oslo, which fires at
    # 22:00Z in summer. Matched against a UTC clock the walk looks for
    # midnight UTC and finds a different day's firing.
    # A two-hour window that straddles midnight Oslo and contains no
    # midnight UTC at all. One firing in the CronJob's own zone, none in
    # this process's — so the two readings cannot agree by coincidence.
    succeeded = cronjob_health._as_datetime("2026-07-01T21:00:00Z")
    scheduled = cronjob_health._as_datetime("2026-07-01T23:00:00Z")
    assert cronjob_health.slots_behind(
        "0 0 * * *", succeeded, scheduled, "Europe/Oslo") == (1, False)
    assert cronjob_health.slots_behind(
        "0 0 * * *", succeeded, scheduled, "Etc/UTC") == (0, False)


def test_a_healthy_oslo_job_across_the_dst_change_is_not_reported_behind():
    # Oslo goes CEST -> CET on 2026-10-25. A daily midnight job that
    # succeeded every single day in this window measures 7 slots behind if
    # the cron is matched against UTC, which is well past the grace.
    lines, status = report_for([
        cronjob("newspaper-generator", "0 0 * * *", namespace="agents",
                timezone="Europe/Oslo",
                succeeded="2026-10-27T23:00:05Z", scheduled="2026-10-27T23:00:00Z"),
    ])
    assert status == 0
    assert any("0 slot(s) behind" in line for line in lines)


def test_an_unknown_timezone_costs_the_sweep_its_clean_verdict():
    lines, status = report_for([
        cronjob("nonsense", "0 0 * * *", timezone="Mars/Olympus_Mons",
                scheduled="2026-09-03T19:50:00Z", succeeded="2026-09-02T19:45:00Z"),
    ])
    assert status == 1
    assert any("CANNOT JUDGE  agents/nonsense" in line for line in lines)


def test_an_absent_timezone_is_utc_which_is_what_kubernetes_does():
    lines, status = report_for([
        cronjob("no-zone", "0 0 * * *", timezone="",
                succeeded="2026-07-01T00:00:05Z", scheduled="2026-07-03T00:00:00Z"),
    ])
    assert status == 2
    assert any("2 consecutive run(s)" in line for line in lines)


# --- it is in the morning sweep -------------------------------------

def test_preflight_runs_it():
    from tools import preflight
    assert "cronjob_health" in preflight.CHECKS
    assert preflight.SUBJECT["cronjob_health"][0] == "on-box"
