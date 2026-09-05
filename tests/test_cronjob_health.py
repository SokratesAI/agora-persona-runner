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


#: Every case below that is not about a CronJob's age wants one old enough to
#: judge, so the default creation stamp is months before `NOW`.
OLD = "2026-01-01T00:00:00Z"

#: The clock every report in this file is judged against, injected rather than
#: read, so a test about age cannot drift into a test about today's date.
NOW = cronjob_health._as_datetime("2026-09-04T19:00:00Z")


def cronjob(name, schedule="*/5 * * * *", suspend=False,
            scheduled="", succeeded="", namespace="agents", timezone="Etc/UTC",
            created=OLD):
    status = {}
    if scheduled:
        status["lastScheduleTime"] = scheduled
    if succeeded:
        status["lastSuccessfulTime"] = succeeded
    return {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "creationTimestamp": created,
        },
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


def report_for(items, now=NOW):
    rows, why = cronjob_health.read_cronjobs(fake_kubectl(items=items))
    assert why is None
    return cronjob_health.report(rows, now=now)


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


# --- too young to judge ---------------------------------------------

def test_a_cronjob_younger_than_its_first_slot_is_not_never_scheduled():
    # Measured Cycle 920 against the live cluster: `agents/agora-backup` was
    # created 2026-09-04T03:26:35Z on `40 3 * * *` Europe/Oslo, so its first
    # firing was not due until the next morning — and it read `NEVER
    # SCHEDULED`, the verdict reserved for a controller that has never heard
    # of the schedule, for the whole of the day in between.
    lines, status = report_for([
        cronjob("agora-backup", "40 3 * * *", timezone="Europe/Oslo",
                created="2026-09-04T03:26:35Z", scheduled="", succeeded=""),
    ])
    assert status == 0
    assert not any("NEVER SCHEDULED" in line for line in lines)
    assert any("NOT JUDGED  agents/agora-backup" in line for line in lines)
    assert any("too young" in line for line in lines)


def test_the_same_cronjob_raises_once_its_slot_has_come_and_gone():
    # The grace is one slot, not a day. Two mornings after creation with the
    # status still empty, the controller really has not created a Job for it
    # and the loud verdict is the right one.
    lines, status = report_for([
        cronjob("agora-backup", "40 3 * * *", timezone="Europe/Oslo",
                created="2026-09-04T03:26:35Z", scheduled="", succeeded=""),
    ], now=cronjob_health._as_datetime("2026-09-06T19:00:00Z"))
    assert status == 2
    assert any("NEVER SCHEDULED  agents/agora-backup" in line for line in lines)


def test_a_first_run_still_in_flight_is_not_never_succeeded():
    # One slot has come round and the Job it created has not finished. That
    # is the same in-flight case `GRACE_SLOTS` exists for on the BEHIND
    # branch, and it must not read as broken from birth.
    lines, status = report_for([
        cronjob("newborn", "*/5 * * * *", created="2026-09-04T18:56:00Z",
                scheduled="2026-09-04T19:00:00Z", succeeded=""),
    ])
    assert status == 0


def test_a_cronjob_with_no_creation_stamp_is_still_judged():
    # Without an age there is no grace to grant, and a watchdog fails loud:
    # an unreadable creation stamp must not buy a CronJob a free pass.
    lines, status = report_for([
        cronjob("orphan", scheduled="", succeeded="", created=""),
    ])
    assert status == 2
    assert any("NEVER SCHEDULED  agents/orphan" in line for line in lines)


def test_a_young_cronjob_is_named_on_the_line_preflight_keeps():
    # `preflight` collapses a check that exits 0 to its last line carrying a
    # digit. A CronJob that was declined rather than passed has to survive
    # that collapse, the same way a suspended one does.
    lines, status = report_for([
        cronjob("agora-backup", "40 3 * * *", timezone="Europe/Oslo",
                created="2026-09-04T03:26:35Z"),
    ])
    assert status == 0
    swept = [line for line in lines if "Judged 1 CronJob(s)" in line]
    assert len(swept) == 1
    assert "agents/agora-backup" in swept[0]
    assert "not judged" in swept[0]


# --- the second axis: is the job pinned to the node its volume is on? -------
#
# Added Cycle 938, after `agents/agora-backup`'s first scheduled firing aborted
# with `expected exactly one *_agents_agora-data directory under /storage,
# found 0`. It was pinned to server1; the volume had moved to server2 the day
# before. The schedule half of this check was perfectly happy about it.

def _backup_cronjob(pinned="server1", claim="agents_agora-data"):
    return {
        "metadata": {"namespace": "agents", "name": "agora-backup",
                     "creationTimestamp": "2026-08-01T00:00:00Z"},
        "spec": {
            "schedule": "40 3 * * *",
            "jobTemplate": {"spec": {"template": {"spec": {
                "nodeSelector": {"kubernetes.io/hostname": pinned},
                "containers": [{
                    "name": "backup",
                    "env": [{"name": "PYTHONUNBUFFERED", "value": "1"},
                            {"name": "CLAIM", "value": claim}],
                }],
            }}}},
        },
        "status": {"lastScheduleTime": "2026-09-05T01:40:00Z",
                   "lastSuccessfulTime": "2026-09-05T01:41:00Z"},
    }


def test_read_cronjobs_carries_the_pin_and_the_declared_claim():
    rows, why = cronjob_health.read_cronjobs(
        fake_kubectl(items=[_backup_cronjob()]))
    assert why is None
    assert rows[0]["pinned_node"] == "server1"
    assert rows[0]["claim"] == "agents_agora-data"


def test_read_claim_nodes_maps_a_mounted_claim_to_its_node():
    pods = {"items": [
        {"metadata": {"namespace": "agents", "name": "agora-1"},
         "spec": {"nodeName": "server2",
                  "volumes": [{"name": "data", "persistentVolumeClaim":
                               {"claimName": "agora-data"}}]}},
        # An unscheduled Pod names no node and must say nothing about where
        # its volume is -- otherwise a Pending Pod erases a real answer.
        {"metadata": {"namespace": "infra", "name": "pending-1"},
         "spec": {"volumes": [{"name": "d", "persistentVolumeClaim":
                               {"claimName": "whatsapp-bridge-auth"}}]}},
    ]}
    nodes, why = cronjob_health.read_claim_nodes(fake_kubectl(items=pods["items"]))
    assert why is None
    assert nodes == {"agents_agora-data": "server2"}


def test_a_pin_that_disagrees_with_the_volume_raises():
    rows, _ = cronjob_health.read_cronjobs(
        fake_kubectl(items=[_backup_cronjob(pinned="server1")]))
    lines, status = cronjob_health.report(
        rows,
        now=cronjob_health._as_datetime("2026-09-05T01:45:00Z"),
        claim_nodes={"agents_agora-data": "server2"})
    assert status == 2
    wrong = [ln for ln in lines if ln.startswith("PINNED TO THE WRONG NODE")]
    assert len(wrong) == 1
    assert "agents/agora-backup" in wrong[0]
    assert "server1" in wrong[0] and "server2" in wrong[0]


def test_a_pin_that_agrees_with_the_volume_does_not_raise():
    rows, _ = cronjob_health.read_cronjobs(
        fake_kubectl(items=[_backup_cronjob(pinned="server2")]))
    lines, status = cronjob_health.report(
        rows,
        now=cronjob_health._as_datetime("2026-09-05T01:45:00Z"),
        claim_nodes={"agents_agora-data": "server2"})
    assert status == 0
    assert not [ln for ln in lines if "WRONG NODE" in ln]
    assert "1 of them name both a node and the claim they copy" in lines[-1]


def test_an_unmounted_claim_prints_and_does_not_raise():
    # infra/whatsapp-bridge is parked at zero replicas, so no Pod mounts its
    # volume and the pin cannot be compared. That is an ordinary state and a
    # check that is red forever on one is a check nobody reads.
    rows, _ = cronjob_health.read_cronjobs(
        fake_kubectl(items=[_backup_cronjob(pinned="server1")]))
    lines, status = cronjob_health.report(
        rows,
        now=cronjob_health._as_datetime("2026-09-05T01:45:00Z"),
        claim_nodes={})
    assert status == 0
    assert [ln for ln in lines if ln.startswith("CANNOT SEE")]
    assert "could not be compared: agents/agora-backup" in lines[-1]


def test_a_cronjob_with_no_claim_is_not_judged_on_its_pin():
    row = _backup_cronjob(pinned="server1")
    row["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"] = []
    rows, _ = cronjob_health.read_cronjobs(fake_kubectl(items=[row]))
    verdict, _detail = cronjob_health.judge_pin(rows[0], {"x": "server2"})
    assert verdict is None


# --- the volume's own node, which does not need a running Pod ---------

def _local_path_pv(name, namespace, claim, node, hostnames=None):
    """A local-path PersistentVolume as the API server actually returns one."""
    values = [node] if hostnames is None else list(hostnames)
    return {
        "metadata": {"name": name},
        "spec": {
            "claimRef": {"namespace": namespace, "name": claim},
            "nodeAffinity": {"required": {"nodeSelectorTerms": [
                {"matchExpressions": [{
                    "key": "kubernetes.io/hostname",
                    "operator": "In",
                    "values": values,
                }]},
            ]}},
        },
    }


def three_query_kubectl(cronjobs=(), pods=(), pvs=(), pv_stderr=""):
    """A `subprocess.run` that answers cronjobs, pods and pv separately.

    The tool makes three different reads and the failure this file cares about
    is one of them being refused while the others answer, so a fake that hands
    the same body to all three could not express it.
    """
    def runner(args, **kwargs):
        if "cronjobs" in args:
            body = json.dumps({"items": list(cronjobs)})
        elif "pods" in args:
            body = json.dumps({"items": list(pods)})
        elif "pv" in args:
            if pv_stderr:
                return subprocess.CompletedProcess(args, 1, "", pv_stderr)
            body = json.dumps({"items": list(pvs)})
        else:
            raise AssertionError(f"unexpected kubectl query: {args}")
        return subprocess.CompletedProcess(args, 0, body, "")
    return runner


def test_read_pv_nodes_places_a_bound_volume_by_its_hostname_affinity():
    pvs = [
        _local_path_pv("pvc-1", "infra", "whatsapp-bridge-auth", "server2"),
        # An unbound PersistentVolume backs no claim, so no CronJob names it.
        {"metadata": {"name": "pvc-spare"}, "spec": {"nodeAffinity": {
            "required": {"nodeSelectorTerms": [{"matchExpressions": [{
                "key": "kubernetes.io/hostname", "operator": "In",
                "values": ["server1"]}]}]}}}},
        # Two hostnames is not a local-path volume pinned to one node's disk,
        # so it must answer nothing rather than resolve to the first value.
        _local_path_pv("pvc-2", "agents", "roaming", "server1",
                       hostnames=["server1", "server2"]),
    ]
    nodes, why = cronjob_health.read_pv_nodes(
        fake_kubectl(items=pvs))
    assert why is None
    assert nodes == {"infra_whatsapp-bridge-auth": "server2"}


def test_the_volume_places_a_pin_that_no_running_pod_could_place():
    # This is the whole point of reading PersistentVolumes: infra/whatsapp-bridge
    # is parked at zero replicas, so nothing mounts its claim and the Pod-derived
    # reading is silent. Before this, the tool printed CANNOT SEE here and a
    # volume move would have broken the backup with nobody told.
    job = _backup_cronjob(pinned="server1", claim="infra_whatsapp-bridge-auth")
    job["metadata"]["name"] = "whatsapp-auth-backup"
    runner = three_query_kubectl(
        cronjobs=[job],
        pods=[],
        pvs=[_local_path_pv("pvc-1", "infra", "whatsapp-bridge-auth", "server2")])
    assert cronjob_health.main([], runner=runner) == 2

    rows, _ = cronjob_health.read_cronjobs(fake_kubectl(items=[job]))
    pv_nodes, _ = cronjob_health.read_pv_nodes(
        fake_kubectl(items=[_local_path_pv(
            "pvc-1", "infra", "whatsapp-bridge-auth", "server2")]))
    lines, _ = cronjob_health.report(rows, now=cronjob_health._as_datetime(
        "2026-09-05T01:45:00Z"), claim_nodes=pv_nodes)
    wrong = [ln for ln in lines if ln.startswith("PINNED TO THE WRONG NODE")]
    assert len(wrong) == 1
    assert "whatsapp-auth-backup" in wrong[0]
    assert not [ln for ln in lines if ln.startswith("CANNOT SEE")]


def test_a_refused_pv_read_falls_back_to_pods_and_says_so(capsys):
    # This is what the tool did before platform-config#685 granted the read, and
    # it has to stay working: the fallback answer is real, so a refusal must not
    # turn a pin that agrees into a failure.
    job = _backup_cronjob(pinned="server2", claim="agents_agora-data")
    pod = {"metadata": {"namespace": "agents", "name": "agora-1"},
           "spec": {"nodeName": "server2",
                    "volumes": [{"name": "d", "persistentVolumeClaim":
                                 {"claimName": "agora-data"}}]}}
    runner = three_query_kubectl(
        cronjobs=[job], pods=[pod],
        pv_stderr='Error from server (Forbidden): persistentvolumes is '
                  'forbidden at the cluster scope')
    status = cronjob_health.main([], runner=runner)
    out = capsys.readouterr().out
    assert "COULD NOT READ" in out
    assert "Forbidden" in out
    assert "says nothing about a volume no Pod is mounting" in out
    assert not [ln for ln in out.splitlines() if "WRONG NODE" in ln]
    assert status == 0


def test_the_volume_outranks_a_pod_that_disagrees_with_it():
    # A Pod's nodeName is where a Pod landed; the PersistentVolume's affinity is
    # where the directory is. They can only disagree if the Pod-derived reading
    # is stale, so the volume has to win rather than the merge order deciding.
    job = _backup_cronjob(pinned="server1", claim="agents_agora-data")
    stale_pod = {"metadata": {"namespace": "agents", "name": "old"},
                 "spec": {"nodeName": "server1",
                          "volumes": [{"name": "d", "persistentVolumeClaim":
                                       {"claimName": "agora-data"}}]}}
    runner = three_query_kubectl(
        cronjobs=[job], pods=[stale_pod],
        pvs=[_local_path_pv("pvc-1", "agents", "agora-data", "server2")])
    assert cronjob_health.main([], runner=runner) == 2
