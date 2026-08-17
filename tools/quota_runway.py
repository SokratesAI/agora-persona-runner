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

# The heartbeat interval, in minutes. Only used to turn dark hours into a
# count of wasted wake-ups, which is the form that makes the cost legible.
CADENCE_MINUTES = 40

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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", default=SNAPSHOT)
    ap.add_argument("--history", default=HISTORY)
    ap.add_argument("--window-hours", type=float, default=24)
    ap.add_argument("--cadence-minutes", type=float, default=CADENCE_MINUTES)
    args = ap.parse_args()

    with open(args.snapshot) as fh:
        snap = json.load(fh)

    seven = next(
        (w for w in snap.get("windows", []) if w.get("window") == "seven_day"), None
    )
    if seven is None:
        print("COULD NOT READ: no seven_day window in the snapshot.")
        return 1

    now = snap.get("fetched_at") or dt.datetime.now(dt.timezone.utc).timestamp()
    reset = dt.datetime.fromisoformat(seven["resets_at"])
    hours_to_reset = (reset.timestamp() - now) / 3600

    rate = burn_rate(_read_history(args.history), now, args.window_hours)
    if rate is None:
        print(
            "COULD NOT READ: not enough history to measure a burn rate over the "
            f"last {args.window_hours:.0f}h. pace reads "
            f"{seven.get('pace', '?')}; that is the only instrument here."
        )
        return 1

    state, _, _, _, lines = runway(
        seven["remaining_pct"], hours_to_reset, rate, args.cadence_minutes
    )
    for line in lines:
        print(line)
    return 0 if state == HEALTHY else 2


if __name__ == "__main__":
    raise SystemExit(main())
