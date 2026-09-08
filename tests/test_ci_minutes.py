"""Guards for tools.ci_minutes.

The defect this tool exists around is the one the first hand-run made:
summing every `Minutes` row in the billing endpoint counts public
repositories, which are not billed, and reports a crisis every month.
Most of these tests are about that split rather than about arithmetic.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from tools import ci_minutes


def _row(repo, minutes, unit="Minutes", net=0.0):
    return {
        "repositoryName": repo,
        "quantity": minutes,
        "unitType": unit,
        "netAmount": net,
        "grossAmount": minutes * 0.006,
    }


VISIBILITY = {"secret-repo": True, "open-repo": False}


def test_public_repository_minutes_are_not_billable():
    # The whole reason this tool is not a one-line sum: 1,620 of September's
    # 2,241 minutes were a public repo and drew nothing from the allowance.
    items = [_row("open-repo", 1620), _row("secret-repo", 330)]
    private, public, unknown, net = ci_minutes.split_minutes(items, VISIBILITY)
    assert sum(private.values()) == 330
    assert sum(public.values()) == 1620
    assert unknown == {}


def test_unlisted_repository_is_unknown_rather_than_public():
    # "I could not tell whether these are billed" must not read as "free".
    items = [_row("deleted-repo", 500)]
    private, public, unknown, _net = ci_minutes.split_minutes(items, VISIBILITY)
    assert dict(unknown) == {"deleted-repo": 500}
    assert private == {} and public == {}


def test_storage_rows_are_not_minutes():
    items = [_row("secret-repo", 0.07, unit="GigabyteHours"), _row("secret-repo", 12)]
    private, _public, _unknown, _net = ci_minutes.split_minutes(items, VISIBILITY)
    assert sum(private.values()) == 12


def _run(monkeypatch, items, now, argv=(), prior=()):
    # Answers for `now`'s own month and hands `prior` to the month before it,
    # because the trailing window reaches into the previous calendar month for
    # the first week of every month. A fake that answered every month with the
    # same rows would count the same minutes twice; one that answered the
    # previous month with nothing would make merging it in unobservable.
    previous = (now.replace(day=1) - timedelta(days=1))

    def _usage(org, year, month, gh=None):
        if (year, month) == (now.year, now.month):
            return items
        if (year, month) == (previous.year, previous.month):
            return list(prior)
        return []

    monkeypatch.setattr(ci_minutes, "fetch_usage", _usage)
    monkeypatch.setattr(ci_minutes, "fetch_visibility", lambda org: VISIBILITY)

    class _FrozenNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(ci_minutes, "datetime", _FrozenNow)
    return ci_minutes.main(list(argv))


DAY5 = datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)     # 4.25 days elapsed
DAY2 = datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)     # 1.0 day elapsed


def test_projection_over_the_allowance_raises(monkeypatch, capsys):
    # 365 minutes in 4.25 days projects to ~2,570 against a 2,000 allowance --
    # the live September 2026 reading this tool was written on.
    assert _run(monkeypatch, [_row("secret-repo", 365)], DAY5) == 2
    assert "past the 2000-minute allowance" in capsys.readouterr().out


def test_projection_inside_the_allowance_is_clean(monkeypatch, capsys):
    assert _run(monkeypatch, [_row("secret-repo", 100)], DAY5) == 0
    assert "Nothing to act on" in capsys.readouterr().out


def test_early_month_run_rate_is_printed_but_not_judged(monkeypatch, capsys):
    # 200 minutes on day one extrapolates to 6,000, which is a forecast from
    # one day and must not raise.
    assert _run(monkeypatch, [_row("secret-repo", 200)], DAY2) == 0
    out = capsys.readouterr().out
    assert "below the 3-day floor" in out
    assert "ACT" not in out


def test_money_already_charged_raises_even_under_the_allowance(monkeypatch, capsys):
    # netAmount is the ground truth; a plan whose allowance is smaller than the
    # constant here has to be caught by the charge rather than by the estimate.
    assert _run(monkeypatch, [_row("secret-repo", 50, net=1.25)], DAY5) == 2
    assert "charged $1.25" in capsys.readouterr().out


def test_public_minutes_alone_do_not_raise(monkeypatch, capsys):
    # 5,000 free minutes is not a finding at any point in the month.
    assert _run(monkeypatch, [_row("open-repo", 5000)], DAY5) == 0
    assert "5000 minute(s) across 1 public repo(s)" in capsys.readouterr().out


def test_unlisted_repository_makes_the_run_unreadable(monkeypatch, capsys):
    # An otherwise-clean month with an unresolvable repo is exit 1, never 0.
    assert _run(monkeypatch, [_row("secret-repo", 10), _row("ghost", 4)], DAY5) == 1
    assert "in no listing" in capsys.readouterr().out


def test_unreadable_endpoint_is_exit_one(monkeypatch, capsys):
    def _boom(org, y, m, gh=None):
        raise RuntimeError("gh api ...: HTTP 403")

    monkeypatch.setattr(ci_minutes, "fetch_usage", _boom)
    assert ci_minutes.main([]) == 1
    assert "not a clean result" in capsys.readouterr().out


def test_fetch_helpers_do_not_bind_gh_as_a_default(monkeypatch):
    # A default argument binds at import, so replacing `_gh` on the module would
    # be ignored and the real `gh api` would run inside the test suite.
    calls = []

    def _fake(path, org):
        calls.append(path)
        return {"usageItems": []} if "billing" in path else []

    monkeypatch.setattr(ci_minutes, "_gh", _fake)
    ci_minutes.fetch_usage("Org", 2026, 9)
    ci_minutes.fetch_visibility("Org")
    assert len(calls) == 2


def test_month_progress_counts_today_as_partial():
    elapsed, days = ci_minutes.month_progress(DAY5)
    assert days == 30
    assert elapsed == pytest.approx(4.25)


# --- the verdict cadence_control reads --------------------------------------
# Cycle 977. `projected_overrun` and `allowance_pressure` were extracted so
# `tools.cadence_control` could ask this module whether the loop may be sped
# up. They were reachable only through `main` and through a canned string in
# the cadence tests, so neither had a test of its own.

def test_projected_overrun_charged_outranks_everything():
    kind, reason = ci_minutes.projected_overrun(10, 2000, 15.0, 30.0, net=4.66)
    assert kind == "charged"
    assert "4.66" in reason


def test_projected_overrun_spent_when_used_is_past_the_allowance():
    kind, _ = ci_minutes.projected_overrun(2400, 2000, 15.0, 30.0)
    assert kind == "spent"


def test_projected_overrun_is_not_judged_below_the_day_floor():
    # 400 minutes in 1.5 days extrapolates to 8000, and is still not a finding.
    kind, reason = ci_minutes.projected_overrun(400, 2000, 1.5, 30.0)
    assert kind is None
    assert "not judged" in reason


def test_projected_overrun_raises_on_the_run_rate():
    kind, reason = ci_minutes.projected_overrun(396, 2000, 4.6, 30.0)
    assert kind == "projected"
    assert "2000-minute allowance" in reason


def test_projected_overrun_is_clean_inside_the_allowance():
    kind, reason = ci_minutes.projected_overrun(100, 2000, 10.0, 30.0)
    assert kind is None
    assert "inside" in reason


def _gh_stub(private_minutes):
    def gh(path, org):
        if "usage" in path or "billing" in path:
            return {"usageItems": [
                {"repositoryName": "platform-config", "product": "actions",
                 "quantity": private_minutes, "netAmount": 0.0, "unitType": "Minutes"},
            ]}
        return [{"name": "platform-config", "private": True}]
    return gh


def test_allowance_pressure_blocks_when_the_rate_projects_over():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    blocked, reason = ci_minutes.allowance_pressure(now=now, gh=_gh_stub(396))
    assert blocked is True, reason
    assert "2000-minute allowance" in reason


def test_allowance_pressure_is_clear_on_a_small_bill():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    blocked, reason = ci_minutes.allowance_pressure(now=now, gh=_gh_stub(20))
    assert blocked is False, reason


def _gh_by_month(rows_by_month):
    """A `gh` stub that answers each billing month separately, with dates.

    `_gh_stub` above hands the same undated rows to every month, which makes
    the trailing window refuse the series outright -- so it can pin nothing
    about which months were fetched or whether they were merged.
    """
    def gh(path, org):
        if "usage" in path or "billing" in path:
            year = int(path.split("year=")[1].split("&")[0])
            month = int(path.split("month=")[1].split("&")[0])
            return {"usageItems": rows_by_month.get((year, month), [])}
        return [{"name": "secret-repo", "private": True}]
    return gh


def test_allowance_pressure_counts_the_previous_month_inside_its_window():
    # `tools.cadence_control` asks this function whether the loop may be made to
    # run more often, and it never passes `--trailing-days` -- so the reach back
    # into the previous month has to work here and not only in `main`. 700
    # minutes on 31 August is inside a seven-day window taken on 7 September.
    rows = {
        (2026, 9): [_dated("secret-repo", q, d) for q, d in
                    ((41, "2026-09-01"), (23, "2026-09-02"), (89, "2026-09-03"),
                     (172, "2026-09-04"), (89, "2026-09-05"), (6, "2026-09-06"),
                     (7, "2026-09-07"))],
        (2026, 8): [_dated("secret-repo", 700, "2026-08-31")],
    }
    blocked, reason = ci_minutes.allowance_pressure(now=MONDAY, gh=_gh_by_month(rows))
    assert blocked is True, reason
    assert "160 minute(s)/day" in reason           # (420 + 700) / 7


def test_allowance_pressure_is_clear_on_the_same_week_without_that_burn():
    # The control: identical September, empty August, and the seven-day window
    # reads 60 minutes/day. A stub that answered August with September's rows
    # would make the test above pass for the wrong reason.
    rows = {
        (2026, 9): [_dated("secret-repo", q, d) for q, d in
                    ((41, "2026-09-01"), (23, "2026-09-02"), (89, "2026-09-03"),
                     (172, "2026-09-04"), (89, "2026-09-05"), (6, "2026-09-06"),
                     (7, "2026-09-07"))],
    }
    blocked, reason = ci_minutes.allowance_pressure(now=MONDAY, gh=_gh_by_month(rows))
    assert blocked is False, reason
    assert "60 minute(s)/day" in reason


# --- the per-day series, and the step change a month-to-date average cannot see ---


def _dated(repo, minutes, date):
    row = _row(repo, minutes)
    row["date"] = f"{date}T00:00:00Z"
    return row


def test_daily_series_counts_only_private_minutes():
    items = [
        _dated("secret-repo", 172, "2026-09-04"),
        _dated("open-repo", 900, "2026-09-04"),          # public: free, not in the series
        _dated("secret-repo", 0.07, "2026-09-04"),
        _dated("secret-repo", 6, "2026-09-06"),
    ]
    items[2]["unitType"] = "GigabyteHours"
    by_day, undated = ci_minutes.daily_private_minutes(items, VISIBILITY)
    assert dict(by_day) == {"2026-09-04": 172, "2026-09-06": 6}
    assert undated == 0


def test_undated_minutes_are_reported_rather_than_dropped():
    # A row with no date would otherwise vanish from the series and make a busy
    # day look quiet, which is the one direction this must never fail in.
    by_day, undated = ci_minutes.daily_private_minutes([_row("secret-repo", 50)], VISIBILITY)
    assert dict(by_day) == {}
    assert undated == 50


def test_trailing_rate_refuses_an_incomplete_series():
    rate, reason = ci_minutes.trailing_rate({}, 50, DAY9)
    assert rate is None
    assert "no date" in reason


def test_trailing_rate_excludes_today():
    # Today is partial: counting it reads as a drop every morning.
    by_day = {"2026-09-06": 6, "2026-09-07": 6, "2026-09-08": 6, "2026-09-09": 1}
    rate, reason = ci_minutes.trailing_rate(by_day, 0, DAY9, window=3)
    assert rate == 6
    assert "2026-09-09" not in reason


def test_trailing_rate_reads_a_missing_day_as_zero():
    # The endpoint emits no row for a day with no usage, so absent is a real zero.
    rate, _reason = ci_minutes.trailing_rate({"2026-09-06": 9}, 0, DAY9, window=3)
    assert rate == 3


def test_trailing_rate_refuses_to_reach_past_what_was_fetched():
    # An absent day is a real zero only inside the span somebody looked at.
    # Default `earliest` is the first of this month, which is all a caller that
    # fetched one month may claim.
    rate, reason = ci_minutes.trailing_rate({"2026-09-01": 40}, 0, DAY2)
    assert rate is None
    assert "back to 2026-09-01" in reason


def test_trailing_rate_uses_a_day_before_the_month_when_it_was_fetched():
    # The complement: the same window is answerable once `earliest` says the
    # previous month is covered, and a day absent from *that* span is a zero.
    rate, reason = ci_minutes.trailing_rate(
        {"2026-08-31": 14}, 0, DAY2, window=2, earliest=date(2026, 8, 1))
    assert rate == 7
    assert "2026-08-31" in reason


def test_resolve_rate_falls_back_to_the_month_average_and_says_so():
    rate, label = ci_minutes.resolve_rate({}, 50, DAY9, used=400, elapsed_days=8.0)
    assert rate == 50
    assert label.startswith("month to date")


DAY9 = datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc)     # 8.25 days elapsed

#: September 2026 as it actually happened: `platform-config` lost its
#: `pull_request` trigger late on the 5th and the private burn fell 172 -> 6.
STEP_CHANGE = [
    _dated("secret-repo", 41, "2026-09-01"),
    _dated("secret-repo", 23, "2026-09-02"),
    _dated("secret-repo", 89, "2026-09-03"),
    _dated("secret-repo", 172, "2026-09-04"),
    _dated("secret-repo", 89, "2026-09-05"),
    _dated("secret-repo", 6, "2026-09-06"),
    _dated("secret-repo", 5, "2026-09-07"),
    _dated("secret-repo", 4, "2026-09-08"),
    _dated("secret-repo", 1, "2026-09-09"),
]


#: A ceiling the real September burn projects past on the month average alone.
#: The data stays as measured; only the allowance moves, which is what
#: `--allowance` is for.
STEP_CHANGE_ALLOWANCE = 800

#: Far enough past the 5 September step that a seven-day window is entirely on
#: the far side of it. Cycle 1170 widened the window from three days to a whole
#: week, so the point at which the trailing rate has cleared a step moved with
#: it -- that is the cost of the wider window and it belongs in the fixture
#: rather than in a comment somewhere else.
DAY13 = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)   # 12.25 days elapsed


def test_the_month_average_still_projects_an_overrun_on_the_step_change():
    # The precondition for the test below: without the trailing window this
    # data raises, so the clean verdict there is the change and not the fixture.
    used = sum(r["quantity"] for r in STEP_CHANGE)
    elapsed, days_in_month = ci_minutes.month_progress(DAY13)
    kind, _reason = ci_minutes.projected_overrun(used, STEP_CHANGE_ALLOWANCE, elapsed,
                                                 days_in_month)
    assert kind == "projected"


def test_a_burn_that_has_already_stopped_is_not_projected_forward(monkeypatch, capsys):
    status = _run(monkeypatch, STEP_CHANGE, DAY13,
                  argv=["--allowance", str(STEP_CHANGE_ALLOWANCE)])
    out = capsys.readouterr().out
    assert status == 0, out
    assert "2026-09-04     172" in out          # the series is printed whole
    assert "the last 7 complete day(s)" in out


def test_the_series_is_printed_even_when_the_projection_raises(monkeypatch, capsys):
    hot = STEP_CHANGE[:5] + [_dated("secret-repo", 172, d) for d in
                             ("2026-09-06", "2026-09-07", "2026-09-08")]
    status = _run(monkeypatch, hot, DAY9)
    out = capsys.readouterr().out
    assert status == 2
    assert "Billable minutes by day" in out


#: 2026-09-07 was a Monday, and the three days behind it were Friday, Saturday
#: and Sunday. This is that week as it actually happened, and the row for the
#: Monday itself is the partial day the projection must not read.
WEEKEND_WINDOW = [
    _dated("secret-repo", 41, "2026-09-01"),
    _dated("secret-repo", 23, "2026-09-02"),
    _dated("secret-repo", 89, "2026-09-03"),
    _dated("secret-repo", 172, "2026-09-04"),   # Friday
    _dated("secret-repo", 89, "2026-09-05"),    # Saturday
    _dated("secret-repo", 6, "2026-09-06"),     # Sunday
    _dated("secret-repo", 7, "2026-09-07"),     # Monday, partial
]

MONDAY = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)   # 6.75 days elapsed


def test_a_three_day_window_on_a_monday_is_one_weekday_and_a_weekend(monkeypatch, capsys):
    # The precondition, and the live reading Cycle 1170 took: three days back
    # from a Monday is Friday plus the weekend, so one busy weekday sets the
    # rate for the rest of the month. 172 + 89 + 6 reads as 89 minutes/day.
    status = _run(monkeypatch, WEEKEND_WINDOW, MONDAY, argv=["--trailing-days", "3"])
    out = capsys.readouterr().out
    assert status == 2, out
    assert "past the 2000-minute allowance" in out


def test_the_default_window_is_a_whole_week(monkeypatch, capsys):
    # Same data, same day, seven days: five weekdays and two weekend days
    # whichever day it is asked, and the same minutes read as 60/day.
    status = _run(monkeypatch, WEEKEND_WINDOW, MONDAY)
    out = capsys.readouterr().out
    assert status == 0, out
    assert "the last 7 complete day(s)" in out
    assert "2026-08-31" in out       # it reached into the previous month to fill


def test_usage_before_month_fetches_back_as_far_as_the_window_needs(monkeypatch):
    asked = []

    def _usage(org, year, month, gh=None):
        asked.append((year, month))
        return [_dated("secret-repo", 14, "2026-08-31")]

    monkeypatch.setattr(ci_minutes, "fetch_usage", _usage)
    extra, earliest = ci_minutes.usage_before_month("O", MONDAY, 7)
    assert asked == [(2026, 8)]
    assert earliest == date(2026, 8, 1)
    assert len(extra) == 1


def test_usage_before_month_keeps_reaching_back_for_a_long_window(monkeypatch):
    # `--trailing-days` is settable, and one step back is only enough for a
    # window shorter than a month. With 45 days behind 7 September the loop has
    # to reach August and then July, and say July is where the span starts.
    asked = []

    def _usage(org, year, month, gh=None):
        asked.append((year, month))
        return []

    monkeypatch.setattr(ci_minutes, "fetch_usage", _usage)
    _extra, earliest = ci_minutes.usage_before_month("O", MONDAY, 45)
    assert asked == [(2026, 8), (2026, 7)]
    assert earliest == date(2026, 7, 1)


def test_usage_before_month_costs_no_call_when_the_window_fits(monkeypatch):
    # The complement, and the common case: three weeks into the month there is
    # nothing to reach back for, and a second billing call would be waste.
    asked = []
    monkeypatch.setattr(ci_minutes, "fetch_usage",
                        lambda org, y, m, gh=None: asked.append((y, m)) or [])
    later = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    extra, earliest = ci_minutes.usage_before_month("O", later, 7)
    assert asked == []
    assert extra == []
    assert earliest == date(2026, 9, 1)


def test_minutes_from_the_previous_month_inside_the_window_are_counted(monkeypatch, capsys):
    # The complement of the test above, and what makes reaching back worth the
    # extra call: 31 August is inside a seven-day window taken on 7 September,
    # so a burn there sets the rate. Drop it and the same run reads 60/day and
    # comes back clean.
    status = _run(monkeypatch, WEEKEND_WINDOW, MONDAY,
                  prior=[_dated("secret-repo", 700, "2026-08-31")])
    out = capsys.readouterr().out
    assert status == 2, out
    assert "2026-08-31     700" in out          # printed in the series, not just used
    assert "160 minute(s)/day" in out           # (420 + 700) / 7


def test_an_unlisted_repo_in_the_previous_month_is_unreadable(monkeypatch, capsys):
    # `daily_private_minutes` reads an unlisted repo as not-private and drops
    # it, so its minutes would leave the rate silently low -- the direction
    # that turns "I could not read this" into "nothing to act on".
    status = _run(monkeypatch, WEEKEND_WINDOW, MONDAY,
                  prior=[_dated("ghost", 300, "2026-08-31")])
    out = capsys.readouterr().out
    assert status == 1, out
    assert "inside the trailing window but in an earlier month" in out
    assert "ghost (300m)" in out


def test_a_listed_repo_in_the_previous_month_is_not_unreadable(monkeypatch, capsys):
    # The control: the same shape of row from a repo that *is* in the listing
    # counts toward the rate and raises nothing.
    # The allowance is lifted clear of the projection on purpose: this test is
    # about the UNREADABLE line, and it must not pass or fail on arithmetic.
    status = _run(monkeypatch, WEEKEND_WINDOW, MONDAY,
                  argv=["--allowance", "9000"],
                  prior=[_dated("secret-repo", 300, "2026-08-31")])
    out = capsys.readouterr().out
    assert status == 0, out
    assert "earlier month" not in out
    assert "103 minute(s)/day" in out           # (420 + 300) / 7


def test_a_raising_run_names_the_parked_board_row(monkeypatch, capsys):
    # Cycle 1195 parked idea #74 because five cycles in a row re-measured the
    # same dead burn from the top of the maintenance queue. Parking it is only
    # honest if the instrument that measures its condition brings it back.
    hot = STEP_CHANGE[:5] + [_dated("secret-repo", 172, d) for d in
                             ("2026-09-06", "2026-09-07", "2026-09-08")]
    status = _run(monkeypatch, hot, DAY9)
    out = capsys.readouterr().out
    assert status == 2, out
    assert ci_minutes.PARKED_ROW in out
    assert "Set it back to Backlog." in out


def test_a_clean_run_says_nothing_about_the_parked_row(monkeypatch, capsys):
    # The mirror, and the one that matters: a row named on every run is noise,
    # and noise is what got #74 walked past five times.
    status = _run(monkeypatch, STEP_CHANGE, DAY13,
                  argv=["--allowance", str(STEP_CHANGE_ALLOWANCE)])
    out = capsys.readouterr().out
    assert status == 0, out
    assert ci_minutes.PARKED_ROW not in out


def test_the_parked_row_line_is_not_the_last_line_carrying_a_digit(monkeypatch, capsys):
    # `tools.preflight` collapses a check to its last line carrying a digit.
    # `idea #74` carries one, so printing the note last would make the row
    # number the summary and hide the reading it is derived from.
    hot = STEP_CHANGE[:5] + [_dated("secret-repo", 172, d) for d in
                             ("2026-09-06", "2026-09-07", "2026-09-08")]
    _run(monkeypatch, hot, DAY9)
    out = capsys.readouterr().out
    digit_lines = [ln for ln in out.splitlines() if any(c.isdigit() for c in ln)]
    assert ci_minutes.PARKED_ROW not in digit_lines[-1]
    assert "billable minute(s) used in" in digit_lines[-1]


def test_parked_row_note_is_keyed_off_every_raising_verdict():
    # All three raising verdicts mean private minutes cost something again,
    # which is the row's whole premise -- so none of them may be silent.
    for kind in ("charged", "spent", "projected"):
        assert ci_minutes.parked_row_note(kind)
    assert ci_minutes.parked_row_note(None) is None


def test_the_parked_row_is_spelled_the_way_the_boards_spell_it():
    # Every other test here reads the constant, so it would agree with any
    # spelling at all. `tools.top_board_rows` prints `idea #74` and a journal
    # entry's `Board:` field is refused without the word and the hash, so the
    # one thing that makes this line findable is the literal.
    assert ci_minutes.PARKED_ROW == "idea #74"
