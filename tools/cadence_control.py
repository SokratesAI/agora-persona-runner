"""Move Nova's own heartbeat interval so the seven-day window lands on zero.

the owner's capture, 2026-08-29: *"Dynamic adjustment of the Nova heartbeat
based on token consumption and weekly limit left. We want to spent all of
it and its not a problem with som cycles hitting rate limit at the end,
but it could be beneficial to do som dynamic adjustments"*.

`tools.quota_runway` already computes the interval that would make the
budget last exactly to the reset, and has since Cycle 259. It ends on the
line *"The lever is cadence, and it is the owner's."* That sentence is what
this module deletes. He has now asked for the lever to be pulled
automatically, so the number stops being advice printed at a cycle that
has no reason to act on it.

**No arithmetic is re-implemented here.** `burn_rate`,
`observed_cadence_minutes`, `live_cadence_minutes` and `_needed_cadence`
all stay in `quota_runway` and are imported. This module is the actuator
and nothing else: read the target, decide whether moving is safe, PATCH
the heartbeat. A second copy of that arithmetic is exactly the
duplication `prompt.md` step 2 says to stop building.

**Both directions, because the ask is to spend all of it.** `quota_runway`
only ever talks about slowing down, since it was written the morning the
window was 88% spent. Under-spending is the same failure with the sign
flipped -- quota that resets unspent bought nothing -- and the owner says
outright that hitting the limit near the end is fine. So a HEALTHY window
speeds the loop up.

**The interval is not a free number, and Agora is what taught me.** The
first live run computed 37 minutes and got back
`400: schedule must be ... every@N[m|h]@HH:MM (anchored -- the interval
must divide 24h evenly)`. An anchored `every@` schedule may only use an
interval that divides 1440, so the reachable set between the floor and
the ceiling is {15, 16, 18, 20, 24, 30, 32, 36, 40, 45, 48, 60, 72, 80,
90, 96, 120, 144, 160, 180} and nothing else. Dropping the anchor would
make any integer legal and is the wrong trade: the anchor is what pins
the wake-ups to a repeating offset, so every adjustment would re-phase
the loop. So this **snaps to a legal interval, in the direction the
correction is already going** -- rounding up when slowing down, down when
speeding up -- because rounding the other way lands back inside the
failure the move was made to fix.

Four guards, each with a reason rather than a round number:

  * **A floor of 15 minutes.** Below the median cycle length (~18m,
    measured over 320 cycles for `prompt.md` step 1b) every wake-up starts
    before the last one finished, and the concurrent workspace has a
    fixed number of slots. the owner asked for 18 minutes at 20x and said he
    wanted overlap; he did not ask for unbounded overlap.
  * **A ceiling of 180 minutes.** Slower than that and the loop stops
    being a loop -- his boards get answered three times a day.
  * **A deadband.** A move smaller than 10% of the current interval (or
    3 minutes, whichever is larger) is inside the noise of a burn rate
    measured over 24h, and PATCHing for it re-anchors the schedule for
    nothing.
  * **The anti-thrash guard, which is the one that matters.** A cadence
    change does not show up in the burn rate until the trailing sample has
    rolled over, so the *set* interval and the interval the rate was
    *earned at* disagree for hours afterwards. `quota_runway.runway`
    already documents this -- passing the new cadence to `_needed_cadence`
    "double-counts a cadence change", and on 2026-08-27 that moved a
    suggestion from 29 to 43 minutes having measured nothing new. A
    controller that ran every 30 minutes into that would chase its own
    tail. So: scale the rate by the interval that produced it, and refuse
    to move at all while the two disagree by more than the deadband.

Exit codes match the step 1a checks: **2 means the number a cycle should
act on is outside what this may do by itself** (the floor or the ceiling),
1 means something was unreadable -- which never reads as clean -- and 0
means the cadence is in band, either already or because this just moved
it.

    python3 -m tools.cadence_control            # measure and apply
    python3 -m tools.cadence_control --dry-run  # measure and say what it would do
"""

import argparse
import datetime as dt
import json
import os
import sys

from tools.quota_runway import (
    HISTORY,
    SNAPSHOT,
    _needed_cadence,
    _read_history,
    burn_rate,
    live_cadence_minutes,
    observed_cadence_minutes,
    CADENCE_WINDOW_HOURS,
)

# See the module docstring for why each of these is the number it is.
FLOOR_MINUTES = 15
CEILING_MINUTES = 180
DEADBAND_FRACTION = 0.10
DEADBAND_MINUTES = 3

WINDOW = "seven_day"

# Agora refuses an anchored `every@Nm@HH:MM` whose interval does not divide
# 24h evenly -- measured, not read off a schema: the first live PATCH of 37
# minutes came back 400 with that rule quoted. See the module docstring.
DAY_MINUTES = 24 * 60


def legal_intervals(floor=FLOOR_MINUTES, ceiling=CEILING_MINUTES):
    """Every anchored interval Agora will accept between the floor and the ceiling."""
    return [n for n in range(floor, ceiling + 1) if DAY_MINUTES % n == 0]


def snap(minutes, current_minutes):
    """The nearest legal interval, rounded away from `current_minutes`.

    Rounding towards the current interval would land back inside the
    failure the move was made to fix -- a slow-down that snaps down still
    goes dark, a speed-up that snaps up still leaves quota unspent. So a
    move is always at least as large as asked for, and the deadband is
    what stops that from being a permanent overshoot.
    """
    legal = legal_intervals()
    if minutes > current_minutes:
        return min((n for n in legal if n >= minutes), default=legal[-1])
    return max((n for n in legal if n <= minutes), default=legal[0])


def deadband_for(current_minutes):
    """How far the target has to be from the current interval to be worth a PATCH."""
    return max(DEADBAND_MINUTES, current_minutes * DEADBAND_FRACTION)


def decide(current_minutes, needed_minutes, spend_minutes=None):
    """`(action, minutes, reason)` -- pure, so the policy is testable off a live window.

    `action` is one of `"hold"`, `"move"`, `"floor"`, `"ceiling"`.
    `"floor"`/`"ceiling"` mean the honest target is outside what this tool
    may set by itself: it clamps, moves to the clamp, and reports the
    number a human has to look at.

    `spend_minutes` is the interval the burn rate was earned at. When it
    disagrees with `current_minutes` by more than the deadband, a schedule
    change is still washing through the sample and this holds -- see the
    anti-thrash guard in the module docstring.
    """
    band = deadband_for(current_minutes)

    if spend_minutes and abs(spend_minutes - current_minutes) > band:
        return ("hold", current_minutes,
                f"the heartbeat is set to {current_minutes:.0f} minutes but the burn "
                f"rate was earned at about {spend_minutes:.0f} -- a schedule change is "
                f"still rolling through the sample, so any move now would be scaled "
                f"from a rate that has not caught up yet")

    if needed_minutes < FLOOR_MINUTES:
        return ("floor", FLOOR_MINUTES,
                f"the window would take {needed_minutes:.0f} minutes to spend fully, "
                f"which is below the {FLOOR_MINUTES}-minute floor -- there is quota "
                f"here that this tool may not spend on its own")

    if needed_minutes > CEILING_MINUTES:
        return ("ceiling", CEILING_MINUTES,
                f"spending to the reset needs {needed_minutes:.0f} minutes between "
                f"cycles, past the {CEILING_MINUTES}-minute ceiling -- the loop cannot "
                f"be slowed enough to cover this and will go dark")

    # Snapped before the deadband, not after: the deadband exists to stop a
    # PATCH that buys nothing, and what would actually be set is the legal
    # interval, not the raw target.
    target = snap(needed_minutes, current_minutes)

    if abs(target - current_minutes) < band:
        return ("hold", current_minutes,
                f"{needed_minutes:.0f} minutes wanted, nearest legal interval "
                f"{target} against {current_minutes:.0f} now -- inside the "
                f"{band:.0f}-minute deadband")

    direction = "slower" if target > current_minutes else "faster"
    return ("move", target,
            f"{current_minutes:.0f} -> {target} minutes ({direction}); "
            f"{needed_minutes:.0f} was wanted and {target} is the nearest interval "
            f"that divides 24h")


def nova_every_heartbeat():
    """`(id, schedule, minutes)` for the one `every@` heartbeat that runs cycles.

    `None` when Agora did not answer, or when the number of such heartbeats
    is not exactly one. Two of them is not a case to guess at: the cadence
    entries actually appear at is the shortest of them
    (`cycle_health.nova_cadence_minutes`), but which one to *move* is a
    judgement, and moving the wrong one changes nothing while looking like
    it worked.
    """
    from agora_runner.cycle_health import nova_cycle_heartbeats
    from agora_runner.http_util import agora_get, agora_internal
    from agora_runner.turns import schedule_minutes

    status, body = agora_internal("GET", "/heartbeats")
    if status != 200:
        status, body = agora_get("/heartbeats")
    if status != 200:
        return None
    rows = []
    for hb in nova_cycle_heartbeats(body.get("heartbeats")):
        minutes = schedule_minutes(hb.get("schedule", ""))
        if minutes:
            rows.append((hb.get("id"), hb.get("schedule"), minutes))
    if len(rows) != 1:
        return None
    return rows[0]


def rewrite_schedule(schedule, minutes):
    """`every@30m@16:00` -> `every@37m@16:00`, keeping the anchor.

    The anchor is what pins the wake-ups to a repeating offset, so dropping
    it would silently re-phase the loop on every adjustment.
    """
    body = schedule[len("every@"):]
    _amount, sep, anchor = body.partition("@")
    return f"every@{int(minutes)}m" + (sep + anchor if sep else "")


def apply_schedule(heartbeat_id, schedule):
    """PATCH the heartbeat on the **public** app. `(ok, detail)`.

    :8080 rather than the internal :8081 for the same reason
    `heartbeat_health`'s fix line uses it: the internal API does not carry
    the heartbeat's schedule fields the way the app his browser writes
    against does.
    """
    from agora_runner.http_util import agora_public

    status, body = agora_public("PATCH", f"/heartbeats/{heartbeat_id}",
                                {"schedule": schedule})
    if status not in (200, 204):
        return False, f"HTTP {status}: {json.dumps(body)[:300]}"
    return True, schedule


def _window(snapshot, name=WINDOW):
    for w in snapshot.get("windows", []):
        if w.get("window") == name:
            return w
    return None


def main_argv(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="measure and print, change nothing")
    ap.add_argument("--snapshot", default=SNAPSHOT)
    ap.add_argument("--history", default=HISTORY)
    ap.add_argument("--window-hours", type=float, default=24.0,
                    help="trailing window the burn rate is measured over")
    args = ap.parse_args(argv)

    if not os.path.exists(args.snapshot):
        print(f"UNREADABLE: no quota snapshot at {args.snapshot}")
        return 1
    with open(args.snapshot) as fh:
        snapshot = json.load(fh)

    window = _window(snapshot)
    if not window:
        print(f"UNREADABLE: the snapshot carries no {WINDOW} window")
        return 1

    resets_at = window.get("resets_at")
    if not resets_at:
        print(f"UNREADABLE: the {WINDOW} window carries no reset time")
        return 1
    now = dt.datetime.now(dt.timezone.utc)
    hours_to_reset = (dt.datetime.fromisoformat(resets_at) - now).total_seconds() / 3600.0
    if hours_to_reset <= 0:
        print("UNREADABLE: the reset time is in the past -- the snapshot is stale")
        return 1

    history = _read_history(args.history)
    pct_per_day = burn_rate(history, now.timestamp(), args.window_hours, WINDOW)
    if not pct_per_day or pct_per_day <= 0:
        print(f"UNREADABLE: no measurable burn over the last {args.window_hours:.0f}h, "
              f"so there is no rate to scale")
        return 1

    live = nova_every_heartbeat()
    if live is None:
        print("UNREADABLE: could not read exactly one `every@` Nova heartbeat from Agora")
        return 1
    heartbeat_id, schedule, current_minutes = live

    # What the rate was *earned* at, which is not what the schedule says
    # for some hours after a change. See the anti-thrash guard above.
    spend_minutes, _support, _sampled = observed_cadence_minutes(
        history, now.timestamp(), CADENCE_WINDOW_HOURS
    )

    remaining_pct = window.get("remaining_pct")
    needed = _needed_cadence(remaining_pct, hours_to_reset, pct_per_day,
                             spend_minutes or current_minutes)
    action, minutes, reason = decide(current_minutes, needed, spend_minutes)

    print(f"{remaining_pct:.0f}% of the seven-day window left, {hours_to_reset:.1f}h to "
          f"the reset, burning {pct_per_day:.1f}%/day at {spend_minutes or current_minutes:.0f} "
          f"minutes between cycles.")
    print(f"Spending it exactly to the reset needs about {needed:.0f} minutes; the "
          f"heartbeat is set to {current_minutes:.0f} ({schedule}).")

    if action == "hold":
        print(f"HOLD: {reason}")
        return 0

    if args.dry_run:
        print(f"WOULD SET {minutes} minutes: {reason}")
        return 2 if action in ("floor", "ceiling") else 0

    ok, detail = apply_schedule(heartbeat_id, rewrite_schedule(schedule, minutes))
    if not ok:
        print(f"UNREADABLE: could not PATCH heartbeat {heartbeat_id} -- {detail}")
        return 1
    print(f"SET {detail} on heartbeat {heartbeat_id}: {reason}")
    return 2 if action in ("floor", "ceiling") else 0


def main():
    return main_argv()


if __name__ == "__main__":
    sys.exit(main())
