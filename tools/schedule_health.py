"""Is every `on: schedule` workflow in this org actually firing?

Cycle 543. `nova-deadman` is the off-box dead-man's switch built two days
ago: the cluster force-pushes an empty commit to `refs/nova/alive` every
five minutes and this workflow, running on GitHub's own machines, opens
an issue when those pushes stop. It is the one alarm in this estate that
survives the box it watches, and it is the reason the owner was told the
2026-08-24 silent outage could not happen again.

It has never run on its schedule. Not once. `?event=schedule` on the
whole repo answers `total_count: 0`, the workflow was added to `main` at
08:45 UTC and declares `*/30 * * * *`, and thirteen firing opportunities
passed with nothing. The only two runs it has ever had are
`workflow_dispatch` — me, pressing the button, twice.

Nothing here noticed, and the reason is structural rather than careless.
Every other check in this loop asks whether something *ran badly*:
`agentic_health` reads a workflow's newest completed run and judges its
conclusion, `heartbeat_health` reads Agora's schedules, `ci_health` asks
whether a build can reach `main`. **A workflow that never starts has no
run to judge**, so it is absent from every one of them, and absent reads
exactly like quiet. That is the same shape as a gh-aw workflow whose
`noop` week looks like a healthy one — one layer further down.

    python3 -m tools.schedule_health

**What it reads.** Every workflow in every repo this workspace sweeps,
its file on the default branch parsed for `on: schedule:` crons, and the
newest run of that workflow whose event is `schedule`. Three verdicts,
kept apart on purpose:

- `ok` — a scheduled run inside the window the cron implies.
- `OVERDUE` — the workflow has fired before and has not lately.
- `NEVER FIRED` — the workflow has existed longer than its own window
  and GitHub has never scheduled it at all.

`OVERDUE` and `NEVER FIRED` are separate because they have different
causes and different fixes, which is `heartbeat_health`'s `OFF`-versus-
`OVERDUE` lesson and `agentic_health`'s merged-streak lesson arriving
for the third time. A workflow that fired yesterday and not today is a
scheduler that is late; a workflow that has never fired is a scheduler
that does not know about it.

**The grace is generous on purpose and is not a guess.** GitHub
documents the `schedule` event as best-effort and delayed "during
periods of high loads... including the start of every hour", so a
tight window would report GitHub's normal behaviour as an incident
every day. The window is `interval + max(2 * interval, floor)`,
so a half-hourly job is only a finding after two hours and a daily one
after three days — long enough that anything this reports is a real
absence rather than a late arrival.

**The floor is this account's own measured lateness, not the documented
90 minutes** (Cycle 555). Every scheduled run the sweep reads is now
attributed back to the cron minute it was owed to, and the largest gap
that produces becomes the floor. The number 90 came from GitHub's docs
and was the only one written down anywhere in this loop; measured across
the 25 scheduled runs this org has ever had, the *fastest* was 36
minutes late, the median 92, and one landed 574 minutes late. So 90 was
below the median. That cost more than a noisy check: Cycle 554 reasoned
that `nova-deadman`'s 6-hourly rung "cannot be overtaken by a 90-minute
scheduler delay" and concluded the cause was not the cadence, using the
one number in this file that had never been checked against this
account. Only runs that actually happened contribute, so a schedule that
never fires cannot widen its own window, and the report prints the
sample so the floor can be argued with.

**A workflow with several crons is judged at its loosest rung once it
has fired and at its tightest rung while it never has.** GitHub does not
say which cron produced a run, so with a run history the tight rungs are
unjudgeable; with no run at all there is nothing to disambiguate and the
tightest is the one a run was owed on first. `verdict_for` carries the
measurement that put this here.

**Cron interval is measured, not assumed.** Rather than special-casing
`*/n`, this walks forward minute by minute from a reference instant and
takes the gap between the first two matches, so a `7,37 * * * *` and a
`*/30` both come out at thirty minutes and a `37 0 * * *` at a day. It
understands the five standard fields with `*`, `*/n`, `a-b`, `a,b` and
bare numbers, which is everything GitHub accepts; a cron it cannot parse
is reported as unreadable rather than silently treated as healthy.

Exit contract, the same one the other opening checks use: **2 means a
scheduled workflow is not firing**, 1 means something could not be read
(which never reads as clean), 0 means every schedule swept is inside its
own window and the report names what it swept.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# Seventy days, which holds two firings of a monthly cron from any start
# instant. It was eight days first, and `0 6 * * 0` -- the weekly
# architecture critique's own shape -- found one Sunday inside the window
# and none after it, so the interval came back `None` and a healthy weekly
# workflow would have been reported as a cron that never fires. A walk
# limit is a measurement window, and one too short to hold two firings
# measures nothing.
_WALK_LIMIT_MINUTES = 70 * 24 * 60

_FIELD_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]


def _gh(args):
    """Run `gh` and return `(exit_code, stdout, stderr)`."""
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def _field_matches(spec, value, low, high):
    """Does one cron field admit `value`? Raises `ValueError` on nonsense."""
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty cron field")
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            step = int(step_text)
            if step < 1:
                raise ValueError("cron step must be positive")
        if part in ("*", "?"):
            start, end = low, high
        elif "-" in part.lstrip("-"):
            start_text, _, end_text = part.partition("-")
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        if not (low <= start <= high and low <= end <= high and start <= end):
            raise ValueError(f"cron field out of range: {part}")
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def cron_matches(cron, moment):
    """Does this five-field cron fire at `moment` (UTC, second-resolution ignored)?

    Day-of-month and day-of-week are OR'd when both are restricted, which
    is what cron itself does and what GitHub inherits.
    """
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 cron fields, got {len(fields)}")
    minute, hour, dom, month, dow = fields
    values = [
        moment.minute,
        moment.hour,
        moment.day,
        moment.month,
        moment.weekday() + 1 if moment.weekday() < 6 else 0,
    ]
    specs = [minute, hour, dom, month, dow]
    ok = []
    for spec, value, (low, high) in zip(specs, values, _FIELD_RANGES):
        ok.append(_field_matches(spec, value, low, high))
    day_restricted = dom.strip() not in ("*", "?") and dow.strip() not in ("*", "?")
    if day_restricted:
        return ok[0] and ok[1] and ok[3] and (ok[2] or ok[4])
    return all(ok)


def cron_interval_minutes(cron, reference=None):
    """Minutes between the next two firings of `cron`, or `None` if it fires fewer than twice in seventy days.

    Walking is deliberate: it costs a few thousand cheap comparisons and
    handles every form GitHub accepts, where a `*/n` special case would
    quietly get `7,37 * * * *` wrong.
    """
    start = (reference or datetime(2026, 1, 5, tzinfo=timezone.utc)).replace(
        second=0, microsecond=0
    )
    hits = []
    for offset in range(_WALK_LIMIT_MINUTES):
        moment = start + timedelta(minutes=offset)
        if cron_matches(cron, moment):
            hits.append(moment)
            if len(hits) == 2:
                return int((hits[1] - hits[0]).total_seconds() // 60)
    return None


def crons_in(source):
    """Every cron declared under `on: schedule:` in a workflow file.

    A hand-rolled scan rather than a YAML parse, because this package
    carries no YAML dependency and the shape is fixed by GitHub's own
    schema: `- cron: "<expr>"` lines inside the `schedule:` block.
    """
    found = []
    in_schedule = False
    schedule_indent = 0
    for raw in source.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if stripped in ("schedule:", "schedule :"):
            in_schedule = True
            schedule_indent = indent
            continue
        if in_schedule and indent <= schedule_indent and not stripped.startswith("-"):
            in_schedule = False
        if not in_schedule:
            continue
        if "cron:" not in stripped:
            continue
        _, _, value = stripped.partition("cron:")
        found.append(value.strip().strip("'\""))
    return found


def workflows_in(repo, run=None):
    """`(workflows, error)` -- every workflow GitHub knows about in one repo."""
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/actions/workflows", "--paginate"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return [], blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        payload = json.loads(out)
    except ValueError:
        return [], "gh returned something that is not JSON"
    entries = payload.get("workflows") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return [], "gh returned no workflow list"
    return [
        {
            "repo": repo,
            "path": (entry or {}).get("path") or "",
            "name": (entry or {}).get("name") or "",
            "state": (entry or {}).get("state") or "unknown",
            "created_at": (entry or {}).get("created_at") or "",
        }
        for entry in entries
    ], None


def workflow_source(repo, path, run=None):
    """`(text, error)` -- a workflow file's contents on the default branch."""
    code, out, err = (run or _gh)(
        ["api", f"repos/{repo}/contents/{path}", "--jq", ".content"]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    import base64

    try:
        return base64.b64decode(out.strip()).decode("utf-8", "replace"), None
    except Exception as exc:  # noqa: BLE001 -- the reason is the finding
        return None, f"could not decode {path}: {exc}"


def scheduled_runs(workflow, run=None, limit=30):
    """`(created_at strings, newest first, error)` -- runs GitHub started itself.

    `--event schedule` is the whole point: a workflow kept alive by manual
    dispatches looks healthy to any check that reads run history without
    it, which is exactly how `nova-deadman` read as fine.

    More than one run, because the newest answers *is it firing* and the rest
    are the only evidence this loop has about *how late this account's
    scheduler actually is* -- see `grace_minutes`.
    """
    code, out, err = (run or _gh)(
        [
            "run",
            "list",
            "--repo",
            workflow["repo"],
            "--workflow",
            workflow["path"].rsplit("/", 1)[-1],
            "--event",
            "schedule",
            "--limit",
            str(limit),
            "--json",
            "createdAt",
        ]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return [], blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        payload = json.loads(out)
    except ValueError:
        return [], "gh returned something that is not JSON"
    if not isinstance(payload, list):
        return [], "gh returned a JSON object where a list of runs was expected"
    stamps = [(row or {}).get("createdAt") for row in payload]
    return [stamp for stamp in stamps if stamp], None


def newest_scheduled_run(workflow, run=None):
    """`(created_at, error)` -- when this workflow last ran on its schedule."""
    stamps, error = scheduled_runs(workflow, run=run)
    if error:
        return None, error
    return (stamps[0] if stamps else None), None


def _as_datetime(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


DOCUMENTED_FLOOR = 90


def occurrence_at_or_before(crons, moment, interval):
    """The newest minute at or before `moment` that any of `crons` fires on.

    Bounded by one interval on purpose: past that the run cannot honestly be
    attributed to an occurrence, and an unattributable run is dropped from the
    sample rather than guessed at.

    Known limit, stated rather than papered over: this reads the cron the
    workflow declares *today* and the runs it produced over weeks. A cron that
    was edited inside that window has its older runs measured against the new
    minute, which can inflate a sample by up to one interval. Nothing in
    GitHub's run payload says which cron text produced a run, so the honest
    options are this or no measurement at all; the bound is what stops it
    running away.
    """
    moment = moment.replace(second=0, microsecond=0)
    for back in range(int(interval) + 1):
        candidate = moment - timedelta(minutes=back)
        for cron in crons:
            if cron_matches(cron, candidate):
                return candidate
    return None


def lateness_minutes(crons, interval, run_times):
    """How many minutes after its own cron each scheduled run actually started."""
    out = []
    for text in run_times:
        started = _as_datetime(text)
        if started is None:
            continue
        due = occurrence_at_or_before(crons, started, interval)
        if due is None:
            continue
        out.append(int((started - due).total_seconds() // 60))
    return out


def measured_floor(samples):
    """The grace floor this account's own scheduler has earned, in minutes.

    The 90 below is GitHub's documented "best effort, late at the top of the
    hour" and was the only number here until Cycle 555. Measured that night
    across the 25 scheduled runs this org has: the *fastest* was 36 minutes
    late, the median 92, and one landed 574 minutes late. So a check built on
    90 calls this account's normal behaviour an incident, and -- worse, and the
    reason this was found -- a cycle reasoning about whether a run was missed
    reaches for 90 because that is the number written down.

    Only runs that actually happened contribute, so a schedule that never fires
    cannot widen its own window.
    """
    if not samples:
        return DOCUMENTED_FLOOR
    return max(DOCUMENTED_FLOOR, max(samples))


def grace_minutes(interval, floor=DOCUMENTED_FLOOR):
    """How late GitHub is allowed to be before lateness becomes a finding."""
    return max(2 * interval, floor)


def verdict_for(entry, now, floor=DOCUMENTED_FLOOR):
    """`(verdict, note)` for one scheduled workflow. Pure -- no network.

    A workflow with several crons is judged at its **loosest** once it has a
    run history and at its **tightest** when it has none, and the split is
    the whole point. GitHub's run payload does not say which cron produced a
    run, so with runs on the board there is no way to tell a dead tight rung
    from a live loose one, and calling that overdue would be red on day one
    and forever. With *zero* scheduled runs there is nothing to disambiguate:
    every rung is silent, including the tightest, so the tightest is the
    interval at which a run was genuinely owed.

    `nova-deadman` is why this is written down. It declares `7,37 * * * *`,
    `23 */6 * * *` and `53 4 * * *`; GitHub had started it zero times in 829
    minutes on 2026-08-28, which is roughly 27 missed firings of the
    30-minute rung, and this check called it `ok` because the daily rung is
    allowed 4320m. That is the alarm meant to survive this box reporting
    healthy while it has never once run. It goes quiet again the moment any
    rung produces a first scheduled run, so the red is actionable rather
    than permanent.
    """
    last = _as_datetime(entry.get("last_scheduled"))
    created = _as_datetime(entry.get("created_at"))
    if last is not None:
        interval = entry["interval"]
        window = interval + grace_minutes(interval, floor)
        age = int((now - last).total_seconds() // 60)
        detail = (
            f"cron {entry['cron']} every {interval}m; last scheduled run "
            f"{last:%Y-%m-%d %H:%M} UTC, {age}m ago, allowed {window}m"
        )
        return ("overdue" if age > window else "ok"), detail
    interval = entry.get("tightest", entry["interval"])
    window = interval + grace_minutes(interval, floor)
    if created is None:
        return "unreadable", (
            f"cron {entry['cron']} every {interval}m; no scheduled run, and GitHub "
            "gave no creation time to measure the absence against"
        )
    age = int((now - created).total_seconds() // 60)
    detail = (
        f"cron {entry['cron']} every {interval}m; no scheduled run ever, and the "
        f"workflow has existed {age}m, allowed {window}m"
    )
    return ("never" if age > window else "ok"), detail


def sweep(repos, run=None, now=None):
    """`(results, errors)` across every repo -- one entry per scheduled workflow."""
    now = now or datetime.now(timezone.utc)
    results, errors, lateness = [], [], []
    for repo in repos:
        found, error = workflows_in(repo, run=run)
        if error:
            errors.append(f"{repo}: could not list workflows: {error}")
            continue
        for workflow in found:
            if workflow["state"] != "active" or not workflow["path"]:
                continue
            # GitHub synthesises workflows that have no file in the repo --
            # `dynamic/dependabot/update-graph` is one, in five repos here --
            # and asking for their contents answers 404. Reporting that as
            # "could not read" would put a permanent five-line unreadable
            # block on a clean sweep, which is how a real gap gets ignored.
            if not workflow["path"].startswith(".github/workflows/"):
                continue
            source, error = workflow_source(repo, workflow["path"], run=run)
            if error:
                errors.append(f"{repo} {workflow['path']}: {error}")
                continue
            # One entry per *workflow*, not per cron. A workflow may declare
            # several crons, and GitHub's run payload does not say which one
            # produced a run -- so per-cron is a verdict the evidence cannot
            # support. Judging each separately against the one shared run
            # history makes the tightest cron permanently red the moment the
            # loosest is the only one firing, which is exactly the "red on day
            # one and forever is the same as off" failure this module is
            # written to avoid. `nova-deadman` declares three cadences on
            # purpose (see its own file) and would have reported OVERDUE for
            # the rest of its life. So a workflow with runs on the board is
            # judged at its *loosest* cron, and anything tighter firing is a
            # bonus this check cannot see anyway.
            #
            # That reasoning is entirely about telling one rung's runs from
            # another's, and it has no purchase when the run count is zero:
            # nothing has fired, so every rung is silent and the tightest is
            # the one that was owed a run first. A workflow with no scheduled
            # run at all is therefore judged at its *tightest* -- see
            # `verdict_for`, which is where that split lives.
            # A cron this module cannot judge is reported and skipped rather
            # than taking the whole workflow with it: the error already stops
            # the sweep reading as clean, and dropping a workflow that also
            # declares a cron we *can* judge would trade a real verdict for a
            # second copy of the same complaint.
            declared = []
            for cron in crons_in(source or ""):
                try:
                    interval = cron_interval_minutes(cron)
                except ValueError as exc:
                    errors.append(f"{repo} {workflow['path']}: cron {cron!r}: {exc}")
                    continue
                if interval is None:
                    errors.append(
                        f"{repo} {workflow['path']}: cron {cron!r} fires fewer than "
                        "twice in seventy days — too rare for this check to judge"
                    )
                    continue
                declared.append((cron, interval))
            if not declared:
                continue
            stamps, error = scheduled_runs(workflow, run=run)
            if error:
                errors.append(f"{repo} {workflow['path']}: {error}")
                continue
            last = stamps[0] if stamps else None
            loosest = max(interval for _, interval in declared)
            tightest = min(interval for _, interval in declared)
            lateness.extend(
                lateness_minutes([cron for cron, _ in declared], tightest, stamps)
            )
            label = " | ".join(cron for cron, _ in declared)
            if len(declared) > 1:
                label += (
                    " (judged at the tightest)"
                    if last is None
                    else " (judged at the loosest)"
                )
            entry = dict(
                workflow,
                cron=label,
                crons=[cron for cron, _ in declared],
                interval=loosest,
                tightest=tightest,
                last_scheduled=last,
            )
            results.append(entry)
    # Every verdict is judged against one floor and the floor is built from
    # every run this sweep saw, so it has to be a second pass. The alternative
    # -- a floor per workflow -- would let a schedule that fires punctually
    # judge itself punctually while the account is hours behind on everything
    # else, which is the number this loop actually needs.
    floor = measured_floor(lateness)
    for entry in results:
        entry["verdict"], entry["note"] = verdict_for(entry, now, floor)
    return results, errors, {"samples": sorted(lateness), "floor": floor}


def format_report(results, errors, swept, lateness=None):
    """`(text, exit_status)`."""
    lines = []
    never = [r for r in results if r["verdict"] == "never"]
    overdue = [r for r in results if r["verdict"] == "overdue"]
    unreadable = [r for r in results if r["verdict"] == "unreadable"]
    ok = [r for r in results if r["verdict"] == "ok"]

    for entry in never:
        lines.append(f"NEVER FIRED — {entry['repo']} {entry['path']}")
        lines.append(f"      {entry['note']}")
        lines.append(
            "      GitHub has never scheduled this workflow. Manual dispatches do "
            "not count and are excluded from this measurement."
        )
        lines.append(
            f"      https://github.com/{entry['repo']}/actions/workflows/"
            f"{entry['path'].rsplit('/', 1)[-1]}"
        )
    for entry in overdue:
        lines.append(f"OVERDUE — {entry['repo']} {entry['path']}")
        lines.append(f"      {entry['note']}")
    for entry in unreadable:
        lines.append(f"COULD NOT JUDGE — {entry['repo']} {entry['path']}")
        lines.append(f"      {entry['note']}")
    for entry in ok:
        lines.append(f"ok  {entry['repo']}  {entry['path']} — {entry['note']}")
    if not results:
        lines.append("No scheduled workflows found in the repos this sweep could read.")

    if errors:
        lines.append(
            f"COULD NOT READ — {len(errors)}; this is no instrument, not a clean sweep."
        )
        lines.extend(f"  {blob}" for blob in errors)

    lines.append(f"Swept {len(swept)} repo(s): {', '.join(swept)}")
    samples = sorted((lateness or {}).get("samples") or [])
    floor = (lateness or {}).get("floor", DOCUMENTED_FLOOR)
    lines.append(
        "A schedule is judged only against runs GitHub started itself; the window "
        f"is the cron's own interval plus the larger of twice that and {floor} minutes."
    )
    if samples:
        lines.append(
            f"That {floor} is measured, not documented: across the {len(samples)} "
            "scheduled run(s) this sweep could attribute to a cron, GitHub started "
            f"them {min(samples)}m to {max(samples)}m after the minute they asked "
            f"for (median {samples[len(samples) // 2]}m)."
        )
    else:
        lines.append(
            f"No scheduled run could be attributed to a cron, so that {floor} is "
            "GitHub's documented best-effort figure and not this account's measured one."
        )
    if never or overdue:
        return "\n".join(lines), 2
    if errors or unreadable:
        return "\n".join(lines), 1
    return "\n".join(lines), 0


def main(argv=None):
    from tools.security_alerts import _repos_to_sweep

    repos, _unplaceable, notes, incomplete = _repos_to_sweep()
    results, errors, lateness = sweep(repos)
    if incomplete:
        errors.extend(notes)
    report, status = format_report(results, errors, repos, lateness)
    print(report)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
