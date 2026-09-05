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
from datetime import datetime, timezone

ORG = "SokratesAI"

#: GitHub Free's included Actions minutes per month for private repositories.
#: Public-repository minutes are free and are not drawn from this.
FREE_PLAN_MINUTES = 2000

#: Days of the month that have to be behind us before a run rate is worth
#: extrapolating. Three is the point at which a single anomalous day stops
#: dominating the average.
MIN_DAYS_FOR_PROJECTION = 3


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


def month_progress(now):
    """Return (elapsed_days, days_in_month) as floats, elapsed including today."""
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elapsed = (now - start).total_seconds() / 86400.0
    return elapsed, float(days_in_month)


def projected_overrun(used, allowance, elapsed_days, days_in_month, net=0.0,
                      min_days=MIN_DAYS_FOR_PROJECTION):
    """`(kind, reason)` -- is the private-minute allowance past, or heading past, its ceiling?

    `kind` is `"charged"` (GitHub has already billed for it), `"spent"` (the
    allowance is gone but nothing is owed yet), `"projected"` (the run rate
    lands past the allowance before the month ends) or `None` (nothing to
    act on). The three raising kinds are exactly the three `main` prints
    below, and they live here rather than inline because `tools.cadence_control`
    has to ask the same question before it speeds this loop up -- a second
    copy of the arithmetic is the duplication `prompt.md` step 2 says to
    stop building.
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
    rate = used / elapsed_days
    projected = used + rate * remaining_days
    if projected > allowance:
        headroom = allowance - used
        days_left = headroom / rate if rate > 0 else float("inf")
        return ("projected",
                f"{rate:.0f} minute(s)/day projects to {projected:.0f} against the "
                f"{allowance}-minute allowance, and the remaining {headroom:.0f} "
                f"minute(s) last {days_left:.1f} more day(s) of the "
                f"{remaining_days:.1f} left in the month")
    return (None,
            f"{rate:.0f} minute(s)/day projects to {projected:.0f}, inside the "
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
    except RuntimeError as exc:
        return (True, f"the Actions allowance could not be read ({exc})")
    private, _public, _unknown, net = split_minutes(items, visibility)
    used = sum(private.values())
    elapsed, days_in_month = month_progress(now)
    kind, reason = projected_overrun(used, allowance, elapsed, days_in_month, net)
    return (kind is not None, reason)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org", default=ORG)
    parser.add_argument("--allowance", type=int, default=FREE_PLAN_MINUTES)
    parser.add_argument("--min-days", type=float, default=MIN_DAYS_FOR_PROJECTION)
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    try:
        items = fetch_usage(args.org, now.year, now.month)
        visibility = fetch_visibility(args.org)
    except RuntimeError as exc:
        print(f"UNREADABLE  {exc}")
        print("Could not measure the allowance; this is not a clean result.")
        return 1

    private, public, unknown, net = split_minutes(items, visibility)
    used = sum(private.values())
    elapsed, days_in_month = month_progress(now)
    remaining_days = days_in_month - elapsed

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

    status = 0

    # The rule itself lives in `projected_overrun` above, because
    # `tools.cadence_control` asks the same question before it makes this
    # loop run more often. What stays here is the wording.
    kind, verdict = projected_overrun(used, args.allowance, elapsed, days_in_month, net,
                                      args.min_days)
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
        rate = used / elapsed
        projected = used + rate * remaining_days
        print(f"Run rate {rate:.0f} minute(s)/day projects to {projected:.0f} by month end.")
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
