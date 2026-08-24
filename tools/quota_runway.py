"""Say when this loop goes dark, and for how long.

Cycle 259, from a live reading. The seven-day window was 88% spent with
58 hours still to run on it, and the only instrument any cycle had was
`pace` -- used share divided by elapsed share, 1.344 that morning.

`pace` is a good number and it answers the wrong question. It says the
week is running hot as a ratio, which a cycle then has to turn into a
decision about how big a pick to take. It cannot say *when* the budget
hits zero, and it cannot say what happens between that moment and the
reset. Those two are the whole question, because the answer is not
"cycles get smaller" -- cycles do not get smaller, they stop. At a
40-minute cadence a heartbeat still fires 36 times a day into an empty
window, and none of those wake-ups can do any work. What one of them
*costs* is not known -- no cycle has ever observed one and lived to
write it down -- so this module reports the hours and the count and
deliberately claims no figure for the waste.

The measured numbers that morning: 12% remaining against 18%/day, so
about 16 hours of runway and then **42 hours dark -- 63 heartbeats that
would wake with nothing.** Nothing in the loop was reporting that, and
`pace` at 1.344 reads as "take a smaller pick", which does not touch it.

    python3 -m tools.quota_runway

`runway` is pure and takes the four numbers; `main` reads them off
`quota-snapshot.json` and `quota-history.jsonl`, the two files the
warning hook already maintains. The split is so the arithmetic is
testable without a live window.

The burn rate is measured off the history rather than assumed, because
the assumed one has been wrong at every cadence change -- `prompt.md`
carried a figure derived at a 6-hourly heartbeat through three of them.
"""

import argparse
import datetime as dt
import json
import os

SNAPSHOT = "/data/claude-home/quota-snapshot.json"
HISTORY = "/data/claude-home/quota-history.jsonl"

# The last-resort fallback, not the truth. `nova_cadence_minutes()` in
# `agora_runner.cycle_health` is the truth and `main` asks it first --
# The owner has changed the cadence five times since 2026-08-08, so a
# constant here would be exactly the mistake this module's docstring
# accuses `prompt.md` of. It is only used when that lookup has no honest
# answer (Agora unreachable, or a schedule with no single interval) and
# the loop's own wake-ups are too sparse to measure one either.
CADENCE_MINUTES = 40

# How far back to look when measuring the cadence off real wake-ups.
# Deliberately not `--window-hours`, which is the burn-rate window: the
# burn rate wants a recent slope and the cadence wants enough gaps for a
# mode. 24h is too few -- the live file had 37 gaps in the last 24h and
# 65 in 48h, and the 24h sample was the only one where a stray short gap
# came close to outvoting the real interval.
CADENCE_WINDOW_HOURS = 48

# Where the cadence in the output came from, in descending order of how
# much it should be trusted.
SCHEDULE = "schedule"  # Agora, asked live -- what the heartbeat is set to
OBSERVED = "observed"  # measured from the loop's own logged wake-ups
ASSUMED = "assumed"  # CADENCE_MINUTES, which has been stale before

HEALTHY = "HEALTHY"
TIGHT = "TIGHT"
DARK = "DARK"


def runway(remaining_pct, hours_to_reset, pct_per_day, cadence_minutes=CADENCE_MINUTES):
    """How long the budget lasts, and what is left over.

    `pct_per_day` is the measured burn rate. A rate at or below zero
    means the window is not being spent at all, which is reported as
    unbounded runway rather than divided by.

    Returns `(state, hours_of_runway, dark_hours, cycles_lost, lines)`.
    `TIGHT` is the band where the budget runs out inside one cadence
    interval of the reset -- close enough that it is not worth acting on,
    and worth distinguishing from `DARK` so the check does not cry wolf.
    """
    lines = []

    if remaining_pct <= 0:
        cycles = int(hours_to_reset * 60 // cadence_minutes)
        lines.append(
            f"{DARK}: the window is already spent, and it does not reset for "
            f"{hours_to_reset:.1f}h. About {cycles} heartbeats will wake with "
            f"nothing between now and then."
        )
        return DARK, 0.0, hours_to_reset, cycles, lines

    if pct_per_day <= 0:
        lines.append(
            f"{HEALTHY}: {remaining_pct:.0f}% remaining and no measurable burn "
            f"over the sample, so nothing to project. Re-read once cycles have "
            f"been running for a few hours."
        )
        return HEALTHY, float("inf"), 0.0, 0, lines

    hours_of_runway = remaining_pct / pct_per_day * 24
    dark_hours = hours_to_reset - hours_of_runway

    if dark_hours <= 0:
        lines.append(
            f"{HEALTHY}: {remaining_pct:.0f}% remaining at {pct_per_day:.1f}%/day "
            f"lasts {hours_of_runway:.1f}h, and the window resets in "
            f"{hours_to_reset:.1f}h. The budget outlives the window."
        )
        return HEALTHY, hours_of_runway, 0.0, 0, lines

    cycles_lost = int(dark_hours * 60 // cadence_minutes)

    if cycles_lost < 1:
        lines.append(
            f"{TIGHT}: {remaining_pct:.0f}% remaining at {pct_per_day:.1f}%/day "
            f"lasts {hours_of_runway:.1f}h against {hours_to_reset:.1f}h to the "
            f"reset -- short by {dark_hours * 60:.0f} minutes, less than one "
            f"cadence interval. Not worth acting on."
        )
        return TIGHT, hours_of_runway, dark_hours, cycles_lost, lines

    lines.append(
        f"{DARK}: {remaining_pct:.0f}% remaining at {pct_per_day:.1f}%/day lasts "
        f"{hours_of_runway:.1f}h, and the window does not reset for "
        f"{hours_to_reset:.1f}h."
    )
    lines.append(
        f"  So the loop goes dark for about {dark_hours:.0f}h -- roughly "
        f"{cycles_lost} heartbeats firing into an empty window. What one of "
        f"those actually costs has never been measured; what is certain is "
        f"that none of them can do any work."
    )
    lines.append(
        "  The lever is cadence, and it is Edvard's. Slowing the heartbeat "
        f"enough to stretch {remaining_pct:.0f}% over {hours_to_reset:.1f}h means "
        f"about {_needed_cadence(remaining_pct, hours_to_reset, pct_per_day, cadence_minutes):.0f} "
        "minutes between cycles."
    )
    return DARK, hours_of_runway, dark_hours, cycles_lost, lines


def _needed_cadence(remaining_pct, hours_to_reset, pct_per_day, cadence_minutes):
    """The interval that would make the budget last exactly to the reset.

    Cost is linear in cadence because every cycle is a cold session --
    the setup is paid per wake-up, not per hour -- so halving the rate
    means doubling the interval.
    """
    needed_rate = remaining_pct / (hours_to_reset / 24)
    return cadence_minutes * pct_per_day / needed_rate


def burn_rate(rows, now, window_hours=24, key="seven_day"):
    """Measured %/day over the trailing window, or None if unmeasurable.

    Rows are the parsed lines of `quota-history.jsonl`, in any order. A
    reset inside the sample would read as a large negative step and make
    the slope meaningless, so samples before the newest reset are
    dropped rather than averaged through.
    """
    usable = sorted(
        (r for r in rows if key in r and "at" in r), key=lambda r: r["at"]
    )
    if len(usable) < 2:
        return None

    start = 0
    for i in range(1, len(usable)):
        if usable[i][key] < usable[i - 1][key] - 20:
            start = i
    usable = usable[start:]

    seg = [r for r in usable if r["at"] >= now - window_hours * 3600]
    if len(seg) < 2:
        return None

    elapsed_hours = (seg[-1]["at"] - seg[0]["at"]) / 3600
    if elapsed_hours <= 0:
        return None
    return (seg[-1][key] - seg[0][key]) / elapsed_hours * 24


def observed_cadence_minutes(rows, now, window_hours=48, bucket=5):
    """`(minutes, support, sampled)` measured off the loop's own wake-ups.

    The warning hook writes a `"boundary": "start"` row into
    `quota-history.jsonl` every time a session begins, so the file the
    burn rate already comes from is also a log of when this loop woke
    up. That makes the realised cadence measurable from the bridge pod,
    which is the pod that cannot ask Agora what the schedule says.

    It is the **mode** of the gaps and not the median, because not every
    start is a heartbeat -- the owner talking to Nova directly opens a
    session too, and that inserts an extra start which splits one
    interval into two short ones without changing their sum. Those land
    all over the low buckets while the scheduled ones pile up on one, so
    the mode survives them and the median does not: measured live on
    2026-08-17, the mode read 60 over every window from 12h to 168h
    while the 24h median read 46.6 against a real 60-minute heartbeat.

    Gaps rounding to zero are dropped. That is the one assumption here
    and it is narrow: two starts inside half a bucket are a re-entry
    within one session, not two wake-ups, because the shortest cadence
    the owner has ever set is 40 minutes and a turn alone is capped at 45.

    Returns `(None, 0, sampled)` when the sample cannot support a mode --
    a winner backed by a single gap is not a measurement.
    """
    starts = sorted(
        r["at"]
        for r in rows
        if r.get("boundary") == "start" and isinstance(r.get("at"), (int, float))
    )
    seg = [t for t in starts if t >= now - window_hours * 3600]
    gaps = [round((b - a) / 60 / bucket) * bucket for a, b in zip(seg, seg[1:])]
    gaps = [g for g in gaps if g > 0]
    if len(gaps) < 4:
        return None, 0, len(gaps)

    # Ties break toward the interval seen most recently, because that is
    # the one still in force: a cadence change makes the new interval and
    # the old one draw for a while, and reporting the old one through
    # that whole stretch is the failure this measurement exists to end.
    counts, last_seen = {}, {}
    for i, g in enumerate(gaps):
        counts[g] = counts.get(g, 0) + 1
        last_seen[g] = i
    best = max(counts, key=lambda g: (counts[g], last_seen[g]))
    if counts[best] < 2:
        return None, 0, len(gaps)
    return best, counts[best], len(gaps)


def live_cadence_minutes(rows=(), now=None, window_hours=CADENCE_WINDOW_HOURS):
    """`(minutes, source)` -- the heartbeat interval, and where it came from.

    Kept out of `runway` so the arithmetic stays pure and testable. The
    schedule lookup already exists for `cycle_health`; importing it
    rather than writing a second one is the point -- a cadence that
    lives in two places is a cadence that will disagree with itself.

    **The source is not decoration.** Cycle 259 shipped this with a
    silent fallback and was wrong within the minute: the bridge pod
    cannot reach Agora, so `nova_cadence_minutes()` returns `None`
    there, and the tool cheerfully reported a wake-up count computed
    from a stale 40 while the real cadence was 60. A cycle runs this
    from the bridge pod, so the silent path *was* the normal path.

    Cycle 260 made that path measure instead of assume. Announcing the
    assumption was the right first move and it was not the fix -- the
    normal path still handed a cycle a number that was wrong by half an
    hour and told it to go and check by hand. `OBSERVED` is a weaker
    answer than `SCHEDULE` and it is a real one: it says what the loop
    did rather than what it was told to do, and those differ whenever a
    cycle overruns or a heartbeat is missed.
    """
    scheduled = None
    try:
        from agora_runner.cycle_health import nova_cadence_minutes

        scheduled = nova_cadence_minutes()
    except Exception:
        scheduled = None
    if scheduled:
        return scheduled, SCHEDULE

    if now is None:
        now = dt.datetime.now(dt.timezone.utc).timestamp()
    seen, _, _ = observed_cadence_minutes(rows, now, window_hours)
    if seen:
        return seen, OBSERVED
    return CADENCE_MINUTES, ASSUMED


def _read_history(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def main_argv(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", default=SNAPSHOT)
    ap.add_argument("--history", default=HISTORY)
    ap.add_argument("--window-hours", type=float, default=24)
    ap.add_argument(
        "--cadence-minutes",
        type=float,
        default=None,
        help="override the live heartbeat lookup",
    )
    args = ap.parse_args(argv)

    with open(args.snapshot) as fh:
        snap = json.load(fh)

    seven = next(
        (w for w in snap.get("windows", []) if w.get("window") == "seven_day"), None
    )
    if seven is None:
        print("COULD NOT READ: no seven_day window in the snapshot.")
        return 1

    now = snap.get("fetched_at") or dt.datetime.now(dt.timezone.utc).timestamp()

    # An empty `resets_at` is a real value, not a malformed one: the
    # bridge writes `block.get("resets_at") or ""` and the upstream API
    # returns nothing for it at the reset instant itself. There is one
    # such row in quota-history.jsonl, logged during the 2026-08-12
    # seven-day reset. Parsing it raises, and the reset instant is
    # exactly when somebody would be asking this question.
    stamp = (seven.get("resets_at") or "").strip()
    if not stamp:
        print(
            "COULD NOT READ: the snapshot has no reset time for the seven_day "
            "window, which is what the bridge writes at the reset instant "
            "itself. Re-read in a minute; the window has almost certainly "
            "just rolled over."
        )
        return 1
    try:
        reset = dt.datetime.fromisoformat(stamp)
    except ValueError:
        print(f"COULD NOT READ: cannot parse the seven_day reset time {stamp!r}.")
        return 1
    hours_to_reset = (reset.timestamp() - now) / 3600

    history = _read_history(args.history)
    rate = burn_rate(history, now, args.window_hours)
    if rate is None:
        print(
            "COULD NOT READ: not enough history to measure a burn rate over the "
            f"last {args.window_hours:.0f}h. pace reads "
            f"{seven.get('pace', '?')}; that is the only instrument here."
        )
        return 1

    cadence, source = args.cadence_minutes, SCHEDULE
    if cadence is None:
        cadence, source = live_cadence_minutes(history, now, CADENCE_WINDOW_HOURS)

    state, _, _, _, lines = runway(
        seven["remaining_pct"], hours_to_reset, rate, cadence
    )
    for line in lines:
        print(line)
    for line in _cadence_note(source, cadence, history, now):
        print(line)
    return 0 if state == HEALTHY else 2


def _cadence_note(source, cadence, history, now):
    """What to say about where the cadence came from. Nothing, if it is live."""
    if source == SCHEDULE:
        return []
    if source == OBSERVED:
        _, support, sampled = observed_cadence_minutes(
            history, now, CADENCE_WINDOW_HOURS
        )
        return [
            f"  NOTE: could not reach Agora for the heartbeat interval, so the "
            f"{cadence:.0f} minutes above is measured from this loop's own "
            f"wake-ups -- the most common gap in {support} of {sampled} starts "
            f"logged over the last {CADENCE_WINDOW_HOURS:.0f}h. That is what the "
            f"loop did, not what it was told to do; the two differ if a "
            f"heartbeat has been missed or the schedule has just changed."
        ]
    return [
        f"  NOTE: could not reach Agora for the heartbeat interval, and this "
        f"loop's own wake-up log was too sparse to measure one, so the wake-up "
        f"count and the suggested interval above assume {cadence:.0f} minutes. "
        f"Check the heartbeat yourself before quoting either number."
    ]


def main():
    return main_argv()


if __name__ == "__main__":
    raise SystemExit(main())
