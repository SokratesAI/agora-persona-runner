"""Is this month's GitHub Actions allowance going to run out before the month does?

Cycle 950. The number has existed in this loop for three cycles and has
never been measured by anything but a cycle typing `gh api` by hand:
*"359 of 2,000 minutes with 25.75 days of the month left; the newest 24h
spends the rest in 16.6"* has been copied forward in the journal digest
since Cycle 943. That is the shape this loop keeps paying for -- a number
that decides something, living in a paragraph. When the allowance is
gone, every check on every private repo stops running, `merge_pr` refuses
a repo whose checks never started, and the loop finds out by being
blocked rather than by being told.

    python3 -m tools.ci_minutes

**The naive reading of this endpoint is wrong, and it was wrong on me
first.** Summing every `Minutes` row for September 2026 gives 2,241
against a 2,000-minute allowance -- "already over", written up as an
emergency. It is not: 1,620 of those minutes are `agora-persona-runner`,
which is a **public** repository, and Actions minutes on public
repositories are not billed and are not drawn from the allowance at all.
The private total was 364. So the split by repository visibility is not a
refinement of this check, it is the check; without it the tool reports a
crisis every month from about the fourth day.

A repository in the usage data that is in no org listing is **not**
assumed public. It is named and the run exits 1, because "I could not
tell whether these minutes are billed" and "these minutes are free" are
opposite answers that would otherwise look identical.

**`netAmount` is the ground truth and the allowance is the estimate.**
GitHub reports each row's gross cost, the discount the plan absorbed, and
the net actually charged. While the allowance holds, net is 0.00 for
every row. A non-zero net anywhere means the allowance is already spent
and real money is being charged, so that raises on its own and does not
wait for a projection to agree. The 2,000-minute figure is the GitHub
Free plan's included private-repo minutes; it is a constant here rather
than a reading because no API this token can reach publishes it, and
`--allowance` moves it if the plan changes.

**The projection runs off the last few complete days, not off the whole
month, and that is Cycle 1125's correction.** The endpoint carries a `date`
on every row and this tool threw it away, so a month-to-date average was the
only rate it could compute -- and an average cannot see a step change. On
2026-09-05 `platform-config` lost its `pull_request` trigger and its private
burn fell from 172 minutes a day to 6; the next morning the average was still
68/day and still projecting an overrun, entirely off minutes that had already
been spent. `tools.cadence_control` asks this same question before it may make
the loop run more often, so a stale overrun does not just misreport -- it holds
the cadence down on a bill that stopped. The per-day series is printed whole
beside the rate, because a step change is the one thing a single number cannot
show. When the window cannot be filled -- fewer complete days than
`--trailing-days` are readable, or any row with no date -- the month average is
used and is labelled as the fallback, since a window that could not be measured
must never read as a quiet one.

**The window is a week, and Cycle 1170 measured why it has to be.** Cycle 1125
set it to three days and guarded one direction only: a quiet weekend must not be
able to clear a real burn. Three days is a different mix of weekdays and weekend
days depending on which day of the week the tool is run, so the converse went
unguarded -- run it on a Monday and the window is Friday, Saturday, Sunday. On
2026-09-07 that read 172 + 89 + 6 as 89 minutes/day and printed `ACT ... projects
to 2495` against a 2000-minute allowance, off one Friday, while Monday itself sat
at 7 minutes with 85% of the day already gone. Seven days is five weekdays and
two weekend days whichever day it is asked, so the rate stops depending on the
clock: the same data reads 60 minutes/day and projects 1821, inside the
allowance. Filling a seven-day window means reaching into the previous calendar
month for the first week of every month, and the billing endpoint is per-month,
so `usage_before_month` fetches back as far as the window needs. Without it a
quarter of the year falls back to the month-to-date average -- the exact average
the window exists to replace.

**The projection needs enough month behind it to mean anything.** One day
of data extrapolated over thirty is not a forecast, so below
`--min-days` elapsed the run rate is printed and does not raise. Above
it, a projected month-end total over the allowance is the finding, since
that is the point at which nothing was going to notice until the checks
stopped.

Exit status, matching `tools.cli_pin` and `tools.security_alerts` so a
cycle can read it without parsing the text: 0 when the month fits inside
the allowance, 2 when it does not or when money is already being charged,
1 when something was unreadable. "I could not check" never reads as
"nothing here".
"""

import argparse
import calendar
import collections
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ORG = "SokratesAI"

#: GitHub Free's included Actions minutes per month for private repositories.
#: Public-repository minutes are free and are not drawn from this.
FREE_PLAN_MINUTES = 2000

#: Days of the month that have to be behind us before a run rate is worth
#: extrapolating. Three is the point at which a single anomalous day stops
#: dominating the average.
MIN_DAYS_FOR_PROJECTION = 3

#: Complete days of history a trailing window needs before its rate may stand in
#: for the month-to-date average. **Seven, and the whole point is that it is a
#: whole number of weeks.** Cycle 1125 set this to three so a step change could
#: not hide behind a month-to-date average, and wrote beside it that "one quiet
#: day is a weekend and must not be able to clear a real burn on its own" -- but
#: it only guarded that direction. Three days is a different mix of weekdays and
#: weekend days depending on which day of the week you ask, so the converse was
#: unguarded: run it on a Monday and the window is Friday, Saturday, Sunday, and
#: one busy weekday sets the rate for the month. Measured Cycle 1170: 172 + 89 +
#: 6 over that exact window read as 89 minutes/day and projected 2495 against a
#: 2000-minute allowance, while Monday itself sat at 7 minutes with 85% of the
#: day gone. A seven-day window contains five weekdays and two weekend days
#: whichever day it is run, so the rate stops depending on the clock.
TRAILING_WINDOW_DAYS = 7


def _gh(path, org):
    """Return the parsed JSON of a `gh api` call, or raise RuntimeError.

    Both endpoints here answer in one page at this org's size (26 repos, 88
    usage rows). If either ever spills, `--paginate` concatenates the pages as
    separate JSON documents and this raises rather than reading page one as the
    whole answer -- an unreadable run, which never reads as clean.
    """
    proc = subprocess.run(
        ["gh", "api", path, "--paginate"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {path}: {proc.stderr.strip() or 'exit %d' % proc.returncode}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh api {path}: response was not JSON ({exc})")


def fetch_usage(org, year, month, gh=None):
    # Resolved here rather than bound as a default argument: a default binds the
    # function object at import, so a test that replaces `_gh` on the module
    # would be ignored and the real `gh api` would run inside the test.
    gh = gh or _gh
    path = f"/organizations/{org}/settings/billing/usage?year={year}&month={month}"
    body = gh(path, org)
    items = body.get("usageItems")
    if items is None:
        raise RuntimeError(f"gh api {path}: no usageItems key in the response")
    return items


def fetch_visibility(org, gh=None):
    """Map repository name -> True when private. Archived repos are included."""
    gh = gh or _gh
    repos = gh(f"/orgs/{org}/repos?per_page=100", org)
    return {r["name"]: bool(r["private"]) for r in repos}


def usage_before_month(org, now, window, gh=None):
    """`(items, earliest)` -- usage rows from months *before* `now`'s, enough to
    cover a `window`-day trailing window, plus the first date they cover.

    The billing endpoint is per calendar month and a seven-day window reaches
    back into the previous one for the first week of every month -- which is a
    quarter of the year spent falling back to the month-to-date average, the
    exact average the window exists to replace. Returns `([], first of this
    month)` when the window fits inside this month, so the common case costs no
    extra call. `earliest` is what makes the fetched span explicit to
    `trailing_rate`: an absent day is a real zero only where somebody looked.
    """
    extra = []
    earliest = now.date().replace(day=1)
    need = now.date() - timedelta(days=window)
    while need < earliest:
        last_of_prev = earliest - timedelta(days=1)
        extra.extend(fetch_usage(org, last_of_prev.year, last_of_prev.month, gh=gh))
        earliest = last_of_prev.replace(day=1)
    return extra, earliest


def split_minutes(items, visibility):
    """Split Actions *minutes* rows three ways by repository visibility.

    Returns (private, public, unknown, net_charged) where each of the first
    three is a name -> minutes mapping and `net_charged` is the total dollars
    GitHub actually charged for minutes this month.
    """
    private = collections.Counter()
    public = collections.Counter()
    unknown = collections.Counter()
    net = 0.0
    for item in items:
        if item.get("unitType") != "Minutes":
            continue
        name = item.get("repositoryName", "")
        qty = float(item.get("quantity", 0.0))
        net += float(item.get("netAmount", 0.0))
        if name not in visibility:
            unknown[name] += qty
        elif visibility[name]:
            private[name] += qty
        else:
            public[name] += qty
    return private, public, unknown, net


def daily_private_minutes(items, visibility):
    """`(by_day, undated)` -- private Actions minutes per calendar day.

    The billing endpoint carries a `date` on every row and `split_minutes`
    throws it away, which is why this tool could only ever report one number
    for a whole month. A day with no usage has no row rather than a zero row,
    so an absent key is a real zero; `undated` is the minutes on rows with no
    usable date, and a non-zero value means the series has a hole in it.
    """
    by_day = collections.Counter()
    undated = 0.0
    for item in items:
        if item.get("unitType") != "Minutes":
            continue
        name = item.get("repositoryName", "")
        if not visibility.get(name):        # public, or unlisted: not billed here
            continue
        qty = float(item.get("quantity", 0.0))
        date = str(item.get("date") or "")[:10]
        if len(date) == 10:
            by_day[date] += qty
        else:
            undated += qty
    return by_day, undated


def trailing_rate(by_day, undated, now, window=TRAILING_WINDOW_DAYS, earliest=None):
    """`(rate, reason)` -- minutes/day over the last `window` *complete* days.

    Today is excluded because it is a partial day and would read as a drop
    every morning. Returns `(None, reason)` when the window cannot be filled,
    and the caller falls back to the month-to-date average and says so -- a
    window that could not be measured must never read as a quiet one.

    `earliest` is the first date `by_day` is authoritative for, and it has to
    be passed rather than inferred: a day with no usage has no row, so an
    absent key is a real zero and a day nobody fetched is indistinguishable
    from a quiet one. It defaults to the first of `now`'s month, which is what
    a caller that fetched only this month's usage may claim.
    """
    if undated > 0:
        return (None, f"{undated:.0f} billable minute(s) carry no date, so the "
                      f"per-day series is incomplete")
    if earliest is None:
        earliest = now.date().replace(day=1)
    days = []
    for back in range(1, window + 1):
        day = now.date() - timedelta(days=back)
        if day < earliest:
            return (None, f"only {back - 1} complete day(s) back to {earliest.isoformat()} "
                          f"are readable, and the window needs {window}")
        days.append(day.isoformat())
    return (sum(by_day.get(d, 0.0) for d in days) / window,
            "the last %d complete day(s): %s" % (window, ", ".join(reversed(days))))


def resolve_rate(by_day, undated, now, used, elapsed_days,
                 window=TRAILING_WINDOW_DAYS, earliest=None):
    """`(rate, label)` -- the minutes/day figure to project the month end from.

    The trailing window when the series can carry one, because the question is
    what the *rest* of the month costs and only recent days answer that. A
    month-to-date average cannot see a step change: on 2026-09-06 this repo's
    private burn fell from 172 minutes a day to 6 when `platform-config` lost
    its `pull_request` trigger, and the average went on projecting an overrun
    off minutes that had already been spent. Falls back to the average, named
    as such, when the window cannot be filled.
    """
    rate, reason = trailing_rate(by_day, undated, now, window, earliest)
    if rate is None:
        return (used / max(elapsed_days, 1e-9),
                f"month to date; no trailing window because {reason}")
    return (rate, reason)


def month_progress(now):
    """Return (elapsed_days, days_in_month) as floats, elapsed including today."""
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elapsed = (now - start).total_seconds() / 86400.0
    return elapsed, float(days_in_month)


def projected_overrun(used, allowance, elapsed_days, days_in_month, net=0.0,
                      min_days=MIN_DAYS_FOR_PROJECTION, rate=None,
                      rate_label="month to date"):
    """`(kind, reason)` -- is the private-minute allowance past, or heading past, its ceiling?

    `kind` is `"charged"` (GitHub has already billed for it), `"spent"` (the
    allowance is gone but nothing is owed yet), `"projected"` (the run rate
    lands past the allowance before the month ends) or `None` (nothing to
    act on). What is unified here is the **verdict**, not the wording: `main`
    still writes its own three sentences, and it still recomputes `rate` and
    `projected` for the line it prints. That is deliberate and it is also
    the honest limit of this extraction -- the thing two callers now share
    is which of the four answers is right, and a future edit to `main`'s
    prose can still drift from the `reason` returned here.
    """
    remaining_days = days_in_month - elapsed_days
    if net > 0:
        return ("charged",
                f"GitHub has charged ${net:.2f} for Actions minutes this month "
                f"-- the allowance is spent")
    if used > allowance:
        return ("spent",
                f"{used:.0f} minutes is past the {allowance}-minute allowance")
    if elapsed_days < min_days:
        return (None,
                f"{used / max(elapsed_days, 1e-9):.0f} minute(s)/day with only "
                f"{elapsed_days:.1f} day(s) behind it -- below the {min_days:g}-day "
                f"floor, so it is not judged")
    if rate is None:
        rate = used / elapsed_days
    projected = used + rate * remaining_days
    if projected > allowance:
        headroom = allowance - used
        days_left = headroom / rate if rate > 0 else float("inf")
        return ("projected",
                f"{rate:.0f} minute(s)/day ({rate_label}) projects to {projected:.0f} against the "
                f"{allowance}-minute allowance, and the remaining {headroom:.0f} "
                f"minute(s) last {days_left:.1f} more day(s) of the "
                f"{remaining_days:.1f} left in the month")
    return (None,
            f"{rate:.0f} minute(s)/day ({rate_label}) projects to {projected:.0f}, inside the "
            f"{allowance}-minute allowance")


def allowance_pressure(org=ORG, allowance=FREE_PLAN_MINUTES, now=None, gh=None):
    """`(blocked, reason)` -- may this loop be made *faster* against the Actions bill?

    `blocked` is True when the allowance is over or heading over, and also
    when the usage could not be read at all. Unreadable blocks on purpose:
    the caller is about to spend a budget, and `prompt.md` is explicit that
    a check that could not run must never read as one that came back clean.
    Refusing here costs nothing but the status quo -- the cadence simply
    does not move -- while the other direction spends money nobody measured.
    """
    now = now or datetime.now(timezone.utc)
    try:
        items = fetch_usage(org, now.year, now.month, gh=gh)
        visibility = fetch_visibility(org, gh=gh)
        earlier, earliest = usage_before_month(org, now, TRAILING_WINDOW_DAYS, gh=gh)
    except RuntimeError as exc:
        return (True, f"the Actions allowance could not be read ({exc})")
    private, _public, _unknown, net = split_minutes(items, visibility)
    used = sum(private.values())
    elapsed, days_in_month = month_progress(now)
    by_day, undated = daily_private_minutes(items + earlier, visibility)
    rate, label = resolve_rate(by_day, undated, now, used, elapsed,
                               TRAILING_WINDOW_DAYS, earliest)
    kind, reason = projected_overrun(used, allowance, elapsed, days_in_month, net,
                                     rate=rate, rate_label=label)
    return (kind is not None, reason)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org", default=ORG)
    parser.add_argument("--allowance", type=int, default=FREE_PLAN_MINUTES)
    parser.add_argument("--min-days", type=float, default=MIN_DAYS_FOR_PROJECTION)
    parser.add_argument("--trailing-days", type=int, default=TRAILING_WINDOW_DAYS,
                        help="complete days the trailing rate is averaged over")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    try:
        items = fetch_usage(args.org, now.year, now.month)
        visibility = fetch_visibility(args.org)
        earlier, earliest = usage_before_month(args.org, now, args.trailing_days)
    except RuntimeError as exc:
        print(f"UNREADABLE  {exc}")
        print("Could not measure the allowance; this is not a clean result.")
        return 1

    private, public, unknown, net = split_minutes(items, visibility)
    used = sum(private.values())
    elapsed, days_in_month = month_progress(now)
    remaining_days = days_in_month - elapsed
    by_day, undated = daily_private_minutes(items + earlier, visibility)
    rate, rate_label = resolve_rate(by_day, undated, now, used, elapsed,
                                    args.trailing_days, earliest)

    if private:
        print("Billable minutes by repository:")
        for name, minutes in private.most_common():
            print(f"    {minutes:7.0f}  {name}")
    else:
        print("No private repository ran a billable minute this month.")
    print(
        f"Public-repository minutes are free and are not counted here: "
        f"{sum(public.values()):.0f} minute(s) across {len(public)} public repo(s)."
    )
    if by_day:
        # Printed whole rather than summarised: a step change is the one thing
        # a single rate cannot show, and this is the data the rate is made of.
        print("Billable minutes by day (today is partial and is not projected from):")
        first_shown = min(now.date().replace(day=1),
                          now.date() - timedelta(days=args.trailing_days)).isoformat()
        for day in sorted(d for d in by_day if d >= first_shown):
            print(f"    {day}  {by_day[day]:6.0f}")
    if undated:
        print(
            f"UNREADABLE  {undated:.0f} billable minute(s) carry no date, so the per-day "
            f"series above is incomplete and the projection falls back to the month average."
        )

    status = 0

    # The rule itself lives in `projected_overrun` above, because
    # `tools.cadence_control` asks the same question before it makes this
    # loop run more often. What stays here is the wording.
    kind, _verdict = projected_overrun(used, args.allowance, elapsed, days_in_month, net,
                                       args.min_days, rate=rate, rate_label=rate_label)
    if kind == "charged":
        print(f"ACT  GitHub has charged ${net:.2f} for Actions minutes this month -- the allowance is spent.")
        status = 2
    elif kind == "spent":
        print(f"ACT  {used:.0f} minutes is past the {args.allowance}-minute allowance.")
        status = 2

    if elapsed < args.min_days:
        print(
            f"Run rate is {used / max(elapsed, 1e-9):.0f} minute(s)/day, but only {elapsed:.1f} day(s) "
            f"are behind it -- below the {args.min_days:g}-day floor, so it is printed and not judged."
        )
    else:
        projected = used + rate * remaining_days
        print(f"Run rate {rate:.0f} minute(s)/day over {rate_label} projects to "
              f"{projected:.0f} by month end "
              f"(month to date is {used / max(elapsed, 1e-9):.0f} minute(s)/day).")
        if kind == "projected":
            headroom = args.allowance - used
            days_left = headroom / rate if rate > 0 else float("inf")
            print(
                f"ACT  that is past the {args.allowance}-minute allowance, and at this rate the "
                f"remaining {headroom:.0f} minute(s) last {days_left:.1f} more day(s) of the "
                f"{remaining_days:.1f} left in the month."
            )
            status = 2

    if unknown:
        print(
            f"UNREADABLE  {len(unknown)} repo(s) spent minutes and are in no listing of {args.org}, "
            f"so whether those minutes are billed is unknown: "
            + ", ".join(f"{n} ({m:.0f}m)" for n, m in unknown.most_common())
        )
        status = max(status, 1) if status != 2 else status

    # The same contract one month back. `daily_private_minutes` reads an
    # unlisted repository as not-private and drops it, so minutes spent in the
    # part of the window that lies in the previous month would go missing from
    # the rate silently -- and missing minutes make the rate read *low*, which
    # is the direction that turns an unreadable run into a clean one.
    window_unknown = split_minutes(earlier, visibility)[2] if earlier else {}
    if window_unknown:
        print(
            f"UNREADABLE  {len(window_unknown)} repo(s) spent minutes inside the trailing "
            f"window but in an earlier month, and are in no listing of {args.org}, so the "
            f"run rate above is missing them: "
            + ", ".join(f"{n} ({m:.0f}m)" for n, m in window_unknown.most_common())
        )
        status = max(status, 1) if status != 2 else status

    if status == 0:
        print(f"Nothing to act on. Swept {len(private) + len(public)} repo(s) with Actions minutes.")

    # Last, because `tools.preflight` collapses a check to its last line
    # carrying a digit: that line has to be the reading, not a footnote.
    print(
        f"{used:.0f} of {args.allowance} billable minute(s) used in "
        f"{now.year}-{now.month:02d}, {elapsed:.1f} of {days_in_month:.0f} day(s) elapsed."
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
