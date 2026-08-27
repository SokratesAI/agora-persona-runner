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
    return sh.sweep(["SokratesAI/agora-persona-runner"], run=run, now=NOW)


def test_an_unreadable_repo_never_reads_as_clean():
    results, errors = sh.sweep(["SokratesAI/x"], run=lambda args: (1, "", "boom"), now=NOW)
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
