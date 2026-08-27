"""A workflow that never starts has no run to judge, so nothing judged it.

Every test here is pure: no network, no `gh`, no clock. That is the same
choice `tests/test_deadman_check.py` makes for the same reason -- a check
that watches for an absence cannot be tested against the presence.
"""

from datetime import datetime, timezone

from tools import schedule_health as sh


NOW = datetime(2026, 8, 27, 15, 40, tzinfo=timezone.utc)


def test_cron_interval_reads_every_form_github_accepts():
    assert sh.cron_interval_minutes("*/30 * * * *") == 30
    # The case a `*/n` special case would have got wrong: same cadence,
    # written as an explicit pair to dodge the top of the hour.
    assert sh.cron_interval_minutes("7,37 * * * *") == 30
    assert sh.cron_interval_minutes("37 0 * * *") == 24 * 60
    assert sh.cron_interval_minutes("0 6 * * 0") == 7 * 24 * 60
    assert sh.cron_interval_minutes("0 3 1 * *") is not None


def test_cron_matches_sunday_is_zero_not_six():
    # 2026-08-30 is a Sunday. Python's weekday() calls it 6; cron calls it 0,
    # and getting that backwards silently moves every weekly job a day.
    sunday = datetime(2026, 8, 30, 6, 0, tzinfo=timezone.utc)
    monday = datetime(2026, 8, 31, 6, 0, tzinfo=timezone.utc)
    assert sh.cron_matches("0 6 * * 0", sunday)
    assert not sh.cron_matches("0 6 * * 0", monday)
    assert sh.cron_matches("0 6 * * 1", monday)


def test_a_cron_that_is_not_five_fields_is_an_error_not_a_pass():
    for bad in ("* * * *", "*/0 * * * *", "99 * * * *", "* * * * * *"):
        try:
            sh.cron_interval_minutes(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not have parsed")


def test_crons_are_read_only_from_the_schedule_block():
    source = """name: x
on:
  schedule:
    - cron: "*/30 * * * *"  # every half hour
    - cron: '0 6 * * 0'
  workflow_dispatch:
jobs:
  build:
    steps:
      - run: echo "cron: not-a-schedule"
"""
    assert sh.crons_in(source) == ["*/30 * * * *", "0 6 * * 0"]


def test_crons_in_a_file_with_no_schedule_is_empty_not_an_error():
    assert sh.crons_in("name: x\non:\n  push:\n    branches: [main]\n") == []


def test_never_fired_is_its_own_verdict_not_overdue():
    entry = {
        "cron": "*/30 * * * *",
        "interval": 30,
        "last_scheduled": None,
        # Seven hours old, thirteen firing opportunities, nothing.
        "created_at": "2026-08-27T08:45:01Z",
    }
    verdict, note = sh.verdict_for(entry, NOW)
    assert verdict == "never"
    assert "no scheduled run ever" in note


def test_a_brand_new_workflow_is_not_yet_a_finding():
    entry = {
        "cron": "*/30 * * * *",
        "interval": 30,
        "last_scheduled": None,
        "created_at": "2026-08-27T15:00:00Z",
    }
    assert sh.verdict_for(entry, NOW)[0] == "ok"


def test_overdue_needs_to_be_past_the_grace_not_merely_late():
    # GitHub is documented as running schedules late at the top of the hour,
    # so a half-hourly job 45 minutes late is GitHub behaving normally.
    late = {
        "cron": "*/30 * * * *",
        "interval": 30,
        "last_scheduled": "2026-08-27T14:55:00Z",
        "created_at": "2026-08-01T00:00:00Z",
    }
    assert sh.verdict_for(late, NOW)[0] == "ok"
    dead = dict(late, last_scheduled="2026-08-27T13:00:00Z")
    assert sh.verdict_for(dead, NOW)[0] == "overdue"


def test_a_daily_job_gets_days_of_grace_not_hours():
    entry = {
        "cron": "37 0 * * *",
        "interval": 24 * 60,
        "last_scheduled": "2026-08-26T00:37:00Z",
        "created_at": "2026-08-01T00:00:00Z",
    }
    assert sh.verdict_for(entry, NOW)[0] == "ok"
    assert sh.verdict_for(dict(entry, last_scheduled="2026-08-22T00:37:00Z"), NOW)[0] == "overdue"


def test_a_workflow_with_no_creation_time_is_unreadable_not_healthy():
    entry = {
        "cron": "*/30 * * * *",
        "interval": 30,
        "last_scheduled": None,
        "created_at": "",
    }
    assert sh.verdict_for(entry, NOW)[0] == "unreadable"


def _stub(pages):
    """A `gh` stand-in that answers by matching the first arg it recognises."""

    def run(args):
        joined = " ".join(args)
        for needle, payload in pages.items():
            if needle in joined:
                return 0, payload, ""
        return 1, "", f"no stub for {joined}"

    return run


def test_sweep_excludes_manual_dispatches_from_the_measurement():
    import base64

    source = base64.b64encode(
        b'name: d\non:\n  schedule:\n    - cron: "*/30 * * * *"\n  workflow_dispatch:\n'
    ).decode()
    run = _stub(
        {
            "actions/workflows --paginate": (
                '{"workflows": [{"path": ".github/workflows/nova-deadman.yaml",'
                '"name": "nova-deadman", "state": "active",'
                '"created_at": "2026-08-27T08:45:01Z"}]}'
            ),
            "contents/.github/workflows/nova-deadman.yaml": source,
            # `--event schedule` is why this is empty: the workflow has two
            # workflow_dispatch runs and they must not count.
            "run list": "[]",
        }
    )
    results, errors = sweep_at(run)
    assert errors == []
    assert [r["verdict"] for r in results] == ["never"]
    report, status = sh.format_report(results, errors, ["SokratesAI/agora-persona-runner"])
    assert status == 2
    assert "NEVER FIRED" in report
    assert "Manual dispatches do not count" in report


def sweep_at(run):
    results, errors, _lateness = sh.sweep(
        ["SokratesAI/agora-persona-runner"], run=run, now=NOW
    )
    return results, errors


def test_an_unreadable_repo_never_reads_as_clean():
    results, errors, _lateness = sh.sweep(
        ["SokratesAI/x"], run=lambda args: (1, "", "boom"), now=NOW
    )
    assert results == []
    assert errors and "boom" in errors[0]
    assert sh.format_report(results, errors, ["SokratesAI/x"])[1] == 1


def test_a_healthy_schedule_exits_zero():
    import base64

    source = base64.b64encode(
        b'name: m\non:\n  schedule:\n    - cron: "37 0 * * *"\n'
    ).decode()
    run = _stub(
        {
            "actions/workflows --paginate": (
                '{"workflows": [{"path": ".github/workflows/agentics-maintenance.yml",'
                '"name": "m", "state": "active", "created_at": "2026-08-01T00:00:00Z"}]}'
            ),
            "contents/": source,
            "run list": '[{"createdAt": "2026-08-27T00:37:12Z"}]',
        }
    )
    results, errors = sweep_at(run)
    report, status = sh.format_report(results, errors, ["SokratesAI/agora-persona-runner"])
    assert status == 0, report
    assert results[0]["verdict"] == "ok"


def test_a_disabled_workflow_is_not_reported_as_a_dead_schedule():
    run = _stub(
        {
            "actions/workflows --paginate": (
                '{"workflows": [{"path": ".github/workflows/old.yaml", "name": "old",'
                '"state": "disabled_manually", "created_at": "2026-01-01T00:00:00Z"}]}'
            )
        }
    )
    results, errors = sweep_at(run)
    assert results == [] and errors == []


def test_a_workflow_with_no_file_in_the_repo_is_skipped_not_unreadable():
    # `dynamic/dependabot/update-graph` is GitHub's own, has no file, and
    # 404s on a contents read. Five repos here carry one.
    run = _stub(
        {
            "actions/workflows --paginate": (
                '{"workflows": [{"path": "dynamic/dependabot/update-graph",'
                '"name": "Dependabot Updates", "state": "active",'
                '"created_at": "2026-01-01T00:00:00Z"}]}'
            )
        }
    )
    results, errors = sweep_at(run)
    assert results == [] and errors == []
    assert sh.format_report(results, errors, ["SokratesAI/agora-persona-runner"])[1] == 0


def test_several_crons_on_one_workflow_are_judged_at_the_loosest():
    """`nova-deadman` declares three cadences and GitHub does not say which fired.

    Judged per cron, the 30-minute rung is `overdue` forever the moment the
    daily rung is the only one firing — a permanent red on a healthy
    workflow, which is the failure this module exists to avoid. One run
    inside the daily window is the whole evidence there is, so the daily
    window is the honest verdict.
    """
    import base64

    source = base64.b64encode(
        b'name: d\non:\n  schedule:\n'
        b'    - cron: "7,37 * * * *"\n'
        b'    - cron: "23 */6 * * *"\n'
        b'    - cron: "53 4 * * *"\n'
        b'  workflow_dispatch:\n'
    ).decode()
    run = _stub(
        {
            "actions/workflows --paginate": (
                '{"workflows": [{"path": ".github/workflows/nova-deadman.yaml",'
                '"name": "nova-deadman", "state": "active",'
                '"created_at": "2026-08-01T00:00:00Z"}]}'
            ),
            "contents/": source,
            # 2h47m before NOW: far past the 30-minute rung’s 120m window,
            # comfortably inside the daily one's.
            "run list": '[{"createdAt": "2026-08-27T12:53:00Z"}]',
        }
    )
    results, errors = sweep_at(run)
    assert errors == []
    assert len(results) == 1, "one entry per workflow, not per cron"
    assert results[0]["verdict"] == "ok"
    assert results[0]["interval"] == 1440
    assert results[0]["crons"] == ["7,37 * * * *", "23 */6 * * *", "53 4 * * *"]
    assert "judged at the loosest" in results[0]["note"]
    assert sh.format_report(results, errors, ["SokratesAI/agora-persona-runner"])[1] == 0


def test_a_cron_that_cannot_be_judged_does_not_take_its_workflow_with_it():
    """One nonsense cron is reported; the sibling it shares a file with is still judged."""
    import base64

    source = base64.b64encode(
        b'name: d\non:\n  schedule:\n'
        b'    - cron: "not-a-cron"\n'
        b'    - cron: "*/30 * * * *"\n'
    ).decode()
    run = _stub(
        {
            "actions/workflows --paginate": (
                '{"workflows": [{"path": ".github/workflows/nova-deadman.yaml",'
                '"name": "nova-deadman", "state": "active",'
                '"created_at": "2026-08-01T00:00:00Z"}]}'
            ),
            "contents/": source,
            "run list": "[]",
        }
    )
    results, errors = sweep_at(run)
    assert len(errors) == 1 and "not-a-cron" in errors[0]
    assert len(results) == 1
    assert results[0]["verdict"] == "never"
    assert results[0]["interval"] == 30


def _deadman_stub(created_at, runs):
    """The real `nova-deadman` file — three rungs — with a settable history."""
    import base64

    source = base64.b64encode(
        b'name: d\non:\n  schedule:\n'
        b'    - cron: "7,37 * * * *"\n'
        b'    - cron: "23 */6 * * *"\n'
        b'    - cron: "53 4 * * *"\n'
        b'  workflow_dispatch:\n'
    ).decode()
    return _stub(
        {
            "actions/workflows --paginate": (
                '{"workflows": [{"path": ".github/workflows/nova-deadman.yaml",'
                '"name": "nova-deadman", "state": "active",'
                f'"created_at": "{created_at}"}}]}}'
            ),
            "contents/": source,
            "run list": runs,
        }
    )


def test_a_multi_cron_workflow_that_never_fired_is_judged_at_the_tightest():
    """The alarm meant to survive this box read `ok` while it had never once run.

    `nova-deadman` declares a 30-minute, a 6-hourly and a daily rung. On
    2026-08-28 GitHub had started it zero times in 829 minutes — roughly 27
    missed firings of the tight rung — and this check called it healthy,
    because the daily rung is allowed 4320m. The loosest rule exists to stop
    a dead tight rung being inferred from a live loose one; with no run at
    all there is no inference to make, every rung is silent, and the tightest
    is the one a run was owed on first.
    """
    # Created 829m before NOW: past the 30-minute rung's 120m window, and
    # nowhere near the daily rung's 4320m.
    run = _deadman_stub("2026-08-27T01:51:00Z", "[]")
    results, errors = sweep_at(run)
    assert errors == []
    assert len(results) == 1
    assert results[0]["verdict"] == "never", results[0]["note"]
    assert results[0]["tightest"] == 30
    assert "every 30m" in results[0]["note"]
    assert "judged at the tightest" in results[0]["note"]
    assert sh.format_report(results, errors, ["SokratesAI/agora-persona-runner"])[1] == 2


def test_the_first_scheduled_run_on_any_rung_clears_the_red():
    """The red has to be actionable, not permanent — one run and it goes quiet.

    Same workflow, same age, one scheduled run an hour ago. That run could
    have come from any rung and GitHub does not say which, so the loosest is
    the only honest window again and the verdict is `ok`.
    """
    run = _deadman_stub("2026-08-27T01:51:00Z", '[{"createdAt": "2026-08-27T14:40:00Z"}]')
    results, errors = sweep_at(run)
    assert errors == []
    assert results[0]["verdict"] == "ok"
    assert "judged at the loosest" in results[0]["note"]
    assert sh.format_report(results, errors, ["SokratesAI/agora-persona-runner"])[1] == 0


def test_a_never_fired_multi_cron_workflow_inside_the_tight_window_is_not_yet_a_finding():
    """Tightest does not mean impatient: a workflow younger than its own tight
    window has not missed anything yet, and reporting it would be the day-one
    red this module refuses to ship."""
    # 60m old against the 30-minute rung's 120m window.
    run = _deadman_stub("2026-08-27T14:40:00Z", "[]")
    results, errors = sweep_at(run)
    assert errors == []
    assert results[0]["verdict"] == "ok"
    assert sh.format_report(results, errors, ["SokratesAI/agora-persona-runner"])[1] == 0


# --- the floor is measured, not documented (Cycle 555) -------------------


def test_lateness_is_measured_against_the_minute_the_cron_asked_for():
    # `37 0 * * *` owed a run at 00:37; GitHub started it at 02:10, which is
    # 93 minutes late. That is a real reading off this org's own history.
    late = sh.lateness_minutes(["37 0 * * *"], 1440, ["2026-08-26T02:10:27Z"])
    assert late == [93]


def test_a_run_that_cannot_be_attributed_to_an_occurrence_is_dropped():
    # A Friday-only cron and a run on a Tuesday: within one interval there is
    # no occurrence to attribute it to, so it contributes nothing rather than
    # inventing a lateness.
    assert sh.lateness_minutes(["7 5 * * 5"], 60, ["2026-08-25T05:07:00Z"]) == []


def test_the_floor_never_drops_below_the_documented_ninety():
    assert sh.measured_floor([]) == 90
    assert sh.measured_floor([("a", 4), ("a", 11)]) == 90
    assert sh.measured_floor([("a", 4), ("a", 574)]) == 574


def test_a_workflow_is_never_judged_against_its_own_lateness():
    # My reviewer's finding: without this a workflow that fires chronically
    # late puts its own worst run into the floor it is then judged against.
    samples = [("late-one", 574), ("other", 36)]
    assert sh.measured_floor(samples) == 574
    assert sh.measured_floor(samples, without="late-one") == 90
    assert sh.measured_floor(samples, without="other") == 574


def test_a_run_later_than_the_documented_grace_is_not_a_finding_when_the_account_is_that_late():
    # 574 minutes late is normal for this account, so a daily schedule that
    # last fired 200 minutes ago is healthy under the measured floor and
    # would still be healthy under the documented one -- the case that
    # actually separates them is the tight cron below.
    entry = {
        "cron": "*/30 * * * *",
        "interval": 30,
        "tightest": 30,
        "last_scheduled": "2026-08-27T00:00:00Z",
    }
    now = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)  # 240m ago
    assert sh.verdict_for(entry, now)[0] == "overdue"
    assert sh.verdict_for(entry, now, floor=574)[0] == "ok"


def test_the_report_says_where_the_floor_came_from():
    results = []
    text, status = sh.format_report(
        results, [], ["SokratesAI/x"], {"samples": [36, 92, 574], "floor": 574}
    )
    assert "measured, not documented" in text
    assert "36m to 574m" in text
    assert "median 92m" in text
    assert "twice that and 574 minutes" in text


def test_the_report_says_so_when_it_had_nothing_to_measure():
    text, _status = sh.format_report([], [], ["SokratesAI/x"], {"samples": [], "floor": 90})
    assert "documented best-effort" in text
    assert "measured, not documented" not in text


def test_the_sweep_actually_collects_the_lateness_it_judges_with():
    # The wiring, not the helper: a sweep that computed a floor and never fed
    # it any runs would report the documented 90 forever and look identical.
    import base64

    source = base64.b64encode(
        b'name: m\non:\n  schedule:\n    - cron: "37 0 * * *"\n'
    ).decode()
    run = _stub(
        {
            "actions/workflows --paginate": (
                '{"workflows": [{"path": ".github/workflows/agentics-maintenance.yml",'
                '"name": "m", "state": "active", "created_at": "2026-08-01T00:00:00Z"}]}'
            ),
            "contents/": source,
            "run list": (
                '[{"createdAt": "2026-08-27T00:37:12Z"},'
                ' {"createdAt": "2026-08-26T02:10:27Z"}]'
            ),
        }
    )
    _results, errors, lateness = sh.sweep(
        ["SokratesAI/agora-persona-runner"], run=run, now=NOW
    )
    assert errors == []
    assert lateness["samples"] == [0, 93]
    assert lateness["floor"] == 93
