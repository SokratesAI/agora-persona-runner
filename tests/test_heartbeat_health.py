"""`tools.heartbeat_health` --- the check that would have caught the Sentinel.

The case that put this module here, written as a test so it stays caught:
`K3s Sentinel` sat `enabled: false` from 2026-08-12 to 2026-08-26 and the
owner found it, not an instrument.
"""

from datetime import datetime, timedelta, timezone

from tools import heartbeat_health as hh


NOW = datetime(2026, 8, 26, 10, 45, tzinfo=timezone.utc)


def _hb(**kw):
    row = {
        "name": "Nova",
        "schedule": "every@20m@16:20",
        "enabled": True,
        "lastRunAt": (NOW - timedelta(minutes=5)).isoformat(),
        "createdAt": "2026-07-30T07:51:54.524Z",
    }
    row.update(kw)
    return row


# --- interval derivation -------------------------------------------------


def test_daily_and_every_and_cron_intervals():
    assert hh.interval_seconds("daily@12:00")[0] == 24 * 3600
    assert hh.interval_seconds("every@20m@16:20")[0] == 20 * 60
    assert hh.interval_seconds("every@6h")[0] == 6 * 3600
    assert hh.interval_seconds("cron@0 7 * * 1")[0] == 7 * 86400
    # Tue/Thu/Sat is three turns a week, so a turn is a bit over two days.
    assert hh.interval_seconds("cron@0 6 * * 2,4,6")[0] == int(7 * 86400 / 3)
    assert hh.interval_seconds("cron@0 6 * * *")[0] == 24 * 3600


def test_a_schedule_it_cannot_read_returns_no_interval_rather_than_a_guess():
    for shape in ["", "yearly@jan", "cron@0 6 1 * *", "cron@0 6 * * mon", "every@xm@1"]:
        seconds, note = hh.interval_seconds(shape)
        assert seconds is None, shape
        assert note


# --- the Sentinel case ---------------------------------------------------


def test_a_heartbeat_switched_off_with_nothing_saying_so_is_reported():
    row = _hb(
        name="K3s Sentinel",
        schedule="daily@12:00",
        enabled=False,
        lastRunAt="2026-08-12T15:26:13.363771+00:00",
    )
    verdict = hh.judge(row, NOW)
    assert verdict["verdict"] == "off"
    report, status = hh.format_report([verdict], None)
    assert status == 2
    assert "OFF — K3s Sentinel" in report
    assert "2026-08-12 15:26 UTC" in report


def test_a_heartbeat_whose_name_says_it_is_off_is_context_not_a_finding():
    row = _hb(
        name="Workflow trial — Cycle 402 (disabled, manual only)",
        schedule="daily@23:59",
        enabled=False,
    )
    verdict = hh.judge(row, NOW)
    assert verdict["verdict"] == "off_marked"
    report, status = hh.format_report([verdict], None)
    assert status == 0
    assert "OFF —" not in report


# --- overdue is a separate failure from off ------------------------------


def test_an_enabled_heartbeat_that_missed_two_turns_is_overdue():
    row = _hb(lastRunAt=(NOW - timedelta(minutes=61)).isoformat())
    verdict = hh.judge(row, NOW)
    assert verdict["verdict"] == "overdue"
    _report, status = hh.format_report([verdict], None)
    assert status == 2


def test_one_missed_turn_is_a_scheduler_under_load_not_a_stop():
    row = _hb(lastRunAt=(NOW - timedelta(minutes=35)).isoformat())
    assert hh.judge(row, NOW)["verdict"] == "ok"


def test_off_and_overdue_are_reported_as_different_things():
    rows = [
        hh.judge(_hb(name="A", enabled=False), NOW),
        hh.judge(_hb(name="B", lastRunAt=(NOW - timedelta(hours=4)).isoformat()), NOW),
    ]
    report, status = hh.format_report(rows, None)
    assert status == 2
    assert "OFF — A" in report
    assert "OVERDUE — B" in report


# --- a turn that has not come round yet ----------------------------------


def test_a_never_run_heartbeat_is_measured_from_its_creation_not_flagged():
    """The Monday goal review was created after Monday 07:00 and has no
    lastRunAt. It is not late; its first turn has not happened."""
    row = _hb(
        name="Nova — goals & reprioritise (Mon)",
        schedule="cron@0 7 * * 1",
        lastRunAt=None,
        createdAt="2026-08-24T21:05:21.253Z",
    )
    assert hh.judge(row, NOW)["verdict"] == "ok"


def test_a_never_run_heartbeat_old_enough_to_have_missed_two_turns_is_overdue():
    row = _hb(
        schedule="daily@12:00",
        lastRunAt=None,
        createdAt=(NOW - timedelta(days=5)).isoformat(),
    )
    assert hh.judge(row, NOW)["verdict"] == "overdue"


# --- unreadable never reads as clean -------------------------------------


def test_a_schedule_it_cannot_judge_exits_one_rather_than_zero():
    verdict = hh.judge(_hb(schedule="every@xm@1"), NOW)
    assert verdict["verdict"] == "unjudged"
    report, status = hh.format_report([verdict], None)
    assert status == 1
    assert "NOT JUDGED" in report


def test_a_failed_fetch_is_exit_one_and_says_it_is_no_instrument():
    report, status = hh.format_report([], "could not read http://x/heartbeats: boom")
    assert status == 1
    assert "COULD NOT READ" in report
    assert "no instrument" in report


def test_an_empty_heartbeat_list_is_not_a_clean_sweep():
    _report, status = hh.format_report([], None)
    assert status == 1


def test_a_healthy_sweep_is_exit_zero():
    rows = [hh.judge(_hb(), NOW), hh.judge(_hb(name="K3s", schedule="daily@12:00"), NOW)]
    report, status = hh.format_report(rows, None)
    assert status == 0
    assert "Read 2 heartbeat(s)" in report


# --- fetch keeps "no rows" and "could not ask" apart ----------------------


def test_fetch_reports_an_error_rather_than_an_empty_list_when_it_cannot_ask():
    def boom(_url, timeout=None):
        raise OSError("no route")

    rows, error = hh._fetch(opener=boom)
    assert rows == []
    assert "no route" in error


def test_fetch_reads_a_plain_list_body():
    class _Resp:
        def read(self):
            return b'[{"name":"K3s Sentinel","enabled":false}]'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    rows, error = hh._fetch(opener=lambda _u, timeout=None: _Resp())
    assert error is None
    assert rows[0]["name"] == "K3s Sentinel"
