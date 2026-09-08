#!/usr/bin/env python3
"""Did the last cycle actually say anything to the owner?

Cycle 722, from a live one. Cycle 721 merged three streaming PRs, wrote
"Both images built green. Writing my reply now." at 15:55:29 Oslo -- and
the bridge Pod it had just built restarted at 15:55:47, eighteen seconds
later, taking the session with it. The journal entry was already written,
the PRs were already merged, and the one thing that reaches the owner's phone
never happened. Nothing anywhere said so. He would have opened the thread,
seen a cycle narrating its way up to a reply, and had to work out for
himself that the reply was not coming.

That is the highest-order failure this loop has -- the whole point of a
cycle is the sentence at the end -- and it is invisible from every angle a
cycle already looks from. `gh pr checks` is green, the merge landed, the
journal entry is in the vault, `workload_health` sees a healthy Pod, and
`cycle_postmortem` counts journal entries, which 721 wrote. Only the
conversation itself carries the absence.

The invariant is one line: **a finished cycle thread contains at least one
message that is not narration.** Narration -- the tool chips and the prose
a cycle streams while it works -- is marked `partial` by
`nova_conversations`; the reply is the message that is not. So a thread
whose every message is `partial` is a cycle that talked to itself for an
hour and then stopped.

Measured before this was written, over the 30 cycle threads nova-site
lists: three violations -- 694, 696 and 721 -- so this is roughly one
cycle in ten, not a freak.

Three things it deliberately does not do:

- It does not judge a thread younger than `--grace-minutes`. A cycle in
  flight has no reply yet *by construction*, and this tool runs from
  inside one of those cycles: without the gate every run would report
  itself.
- It does not look further back than `--window-hours`. A missed reply is
  permanent -- there is no fix to ship, only a relay to make -- so an
  unbounded window would hand every future cycle the same three names
  forever. Preflight collapses a finding it has already seen; this bounds
  it in time as well, which is the honest shape for history that cannot
  be repaired.
- It does not read Agora directly. nova-site is reachable from the bridge
  pod without a token and it is also the surface the owner actually reads, so
  a check that passes here is a check about what he sees rather than about
  what the store holds.

Exit contract, the same one `security_alerts` and `deadman_check` use: 2
means a cycle inside the window finished without replying, 1 means
something could not be read -- which never reads as clean -- and 0 means
every finished cycle in the window spoke.

On exit 2 the recovery is not a code change. The cycle's journal entry
exists; relay what it did in your own reply, and say plainly that the
previous cycle never reached him.

**Each silence now says why, because the two causes need different
answers.** Cycle 813 read three of these in one morning -- 784, 796 and
797 -- and the tool named them and stopped there, which is the shape it
had when 721 was written up: the docstring above already knew a bridge
restart eighteen seconds later was what took 721, and every cycle since
has had to re-derive that by hand. It is one `kubectl` call. So a silence
is now followed by the bridge container death that landed inside the
45-minute turn cap after its last message, or by a line saying no such
death is recorded -- and those mean opposite things. A named kill is an outage and there is
nothing in this repo to fix. An unattributed one is weaker than it looks
and says so: `lastState` carries only the most recent termination of each
container, so it can rule out a *recent* restart and never every restart,
and a cycle killed two restarts ago reads the same as a cycle that reached
the end of its turn and never spoke.

**A rollout is the second cause, and `lastState` is structurally blind to
it** (cycle 1245). Four cycles went silent on 2026-09-08 -- 1217, 1221,
1224 and 1225 -- and all four printed the unattributed line. The bridge
Deployment had started a ReplicaSet at `12:56:06Z`, and cycle 1224's last
message is stamped `12:56:06.858Z`: three of the four last spoke inside
the 45-minute window before it, so a bridge deploy landed on top of them.
The reason no cycle had ever seen this is not that the call is expensive,
it is that a rollout *replaces the Pod* rather than restarting its
container -- the replacement comes up with `restartCount: 0` and an empty
`lastState`, and the outgoing Pod is deleted with its status -- so the one
place attribution looked can never hold it, and no amount of re-reading
`lastState` would have found it. So `bridge_rollouts` reads the
Deployment's ReplicaSet creation instants alongside it. The two answers
are kept apart because they need different responses: a kill is an outage
to absorb, while a rollout is *this loop deploying over itself*, which is
a scheduling problem a cycle can actually act on. The unattributed line
now names both horizons rather than only one.

Either reader can also fail, and that is printed as its own answer rather
than collapsed into the absence, because "I could not look" is not "I
looked and there was nothing" -- and that holds when only *one* of the two
is blind, so an unreadable rollout history never prints as "no rollout is
recorded". Attribution runs only on the branch that
has already decided to exit 2, so it can never turn a silence into a pass
or a pass into a silence.

**The judgement itself moved to `agora_runner.reply_check` and this is now
the CLI over it.** Cycle 724 wired the same rule into `nova-site` as a push
notice (`agora_runner.reply_notice`), because a check that only ever tells
*me* leaves the owner finding out by asking -- which is his issue #105 in
his own words. `tools/` is not copied into the site image, so the notifier
cannot import this module; the shared half lives in `agora_runner/` and both
sides call it rather than each keeping a copy of "every message is partial".
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.error
import urllib.request
import json
from datetime import datetime, timedelta, timezone

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.reply_check import (
    GRACE_MINUTES,
    WINDOW_HOURS,
    cycle_threads,
    find_silences,
    judge,
    last_narration,
    replied,
)

SITE = "http://nova-site.agents.svc.cluster.local:8083"
# Enough for any cycle: the busiest thread measured held 10.
THREAD_LIMIT = 300

# The Pod my own Claude Code session runs inside. A cycle dies when this
# container does.
BRIDGE_NAMESPACE = "agents"
BRIDGE_SELECTOR = "app=agora-claude-bridge"
# A turn is killed at 45 minutes, so a cycle's last message can never be
# older than that when the thing that killed it arrives. Anything further
# back is a different cycle's death, not this one's.
ATTRIBUTION_MINUTES = 45
# `creationTimestamp` is truncated to the second, so a rollout recorded at
# T started somewhere in [T, T+1s) and a message inside that same second can
# still be the rollout's victim. This is the timestamp's precision, not a
# tuned tolerance -- see `attribute_rollout` for why the Pod's 48-minute
# termination grace is the wrong bound to use instead.
_STAMP_RESOLUTION = timedelta(seconds=1)

# Re-exported so a reader of this module sees the whole rule from here and
# `tests/test_reply_health.py` keeps testing it through the tool it names.
__all__ = ["cycle_threads", "judge", "last_narration", "replied", "sweep",
           "main", "SITE", "GRACE_MINUTES", "WINDOW_HOURS", "THREAD_LIMIT",
           "bridge_kills", "attribute", "bridge_rollouts",
           "attribute_rollout", "BRIDGE_NAMESPACE", "BRIDGE_SELECTOR",
           "ATTRIBUTION_MINUTES"]


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _kubectl(args, timeout=30):
    return subprocess.run(["kubectl", *args], capture_output=True, text=True,
                          timeout=timeout, check=True).stdout


def _parse_stamp(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def bridge_kills(run=_kubectl):
    """Every recorded death of the bridge container, newest first.

    Returns `(kills, note)`. `note` is non-empty when the history could not
    be read at all -- which is not the same answer as "no kill happened",
    and the caller says so rather than printing an absence it never
    measured. Only `lastState` is available, so this sees the most recent
    termination of each container and nothing before it; a cycle killed two
    restarts ago is unattributable here and is reported as unattributed.
    """
    try:
        raw = run(["get", "pods", "-n", BRIDGE_NAMESPACE,
                   "-l", BRIDGE_SELECTOR, "-o", "json"])
        payload = json.loads(raw)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return [], f"{type(error).__name__}: {error}"

    kills = []
    for pod in payload.get("items", []):
        name = pod.get("metadata", {}).get("name", "?")
        for status in pod.get("status", {}).get("containerStatuses", []):
            ended = status.get("lastState", {}).get("terminated") or {}
            finished = _parse_stamp(ended.get("finishedAt"))
            if finished is None:
                continue
            kills.append({"pod": name, "at": finished,
                          "reason": ended.get("reason") or "unknown",
                          "exit_code": ended.get("exitCode")})
    kills.sort(key=lambda kill: kill["at"], reverse=True)
    return kills, ""


def attribute(spoke_at, kills):
    """The kill that plausibly took a cycle whose last message was `spoke_at`.

    A kill counts only if it landed *after* that message and within the
    45-minute turn cap, because a cycle that had already been killed cannot
    be killed again and one that spoke an hour earlier was gone regardless.
    """
    if spoke_at is None:
        return None
    for kill in kills:
        gap = kill["at"] - spoke_at
        if timedelta(0) <= gap <= timedelta(minutes=ATTRIBUTION_MINUTES):
            return kill
    return None


def bridge_rollouts(run=_kubectl):
    """Every rollout of the bridge Deployment, newest first.

    Returns `(rollouts, note)` with the same contract as `bridge_kills`: a
    non-empty `note` means the history could not be read, which is not the
    answer "no rollout happened".

    A new ReplicaSet is the instant a rollout starts, which is also the
    instant the outgoing pod is signalled and every turn inside it stops.
    This is a separate reader from `bridge_kills` because it has to be:
    `lastState` describes an *in-place* container restart, and a rollout
    does not restart a container -- it replaces the whole Pod, so the
    replacement comes up with `restartCount: 0` and an empty `lastState`
    and the outgoing Pod is gone with its status. A rollout therefore
    leaves *no* trace in the only place attribution used to look, which is
    why three of the four silences on 2026-09-08 read as unattributed while
    the ReplicaSet created at 12:56:06Z sat one call away.
    """
    try:
        raw = run(["get", "rs", "-n", BRIDGE_NAMESPACE,
                   "-l", BRIDGE_SELECTOR, "-o", "json"])
        payload = json.loads(raw)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return [], f"{type(error).__name__}: {error}"

    rollouts = []
    for item in payload.get("items", []):
        meta = item.get("metadata", {})
        started = _parse_stamp(meta.get("creationTimestamp"))
        if started is None:
            continue
        rollouts.append({"replicaset": meta.get("name", "?"), "at": started})
    rollouts.sort(key=lambda rollout: rollout["at"], reverse=True)
    return rollouts, ""


def attribute_rollout(spoke_at, rollouts):
    """The bridge rollout that plausibly took a cycle that last spoke then.

    Same 45-minute turn cap as `attribute`, with one second of slack on the
    other side. That second is the ReplicaSet timestamp's own resolution
    and nothing else: `creationTimestamp` is truncated to the second, so a
    rollout recorded at `12:56:06Z` actually started somewhere in
    `[12:56:06, 12:56:07)`, and cycle 1224's last message at
    `12:56:06.858Z` sits inside that same second. Without the slack it read
    as unattributed while the rollout that took it was already in the list.

    The bridge's configured `terminationGracePeriodSeconds` is 2880 -- 48
    minutes, deliberately longer than the 45-minute turn cap so a live
    cycle can finish -- and it is emphatically *not* the bound to use here.
    Allowing 48 minutes of slack would attribute nearly every silence to
    any rollout in a 93-minute span, which is a rubber stamp rather than an
    attribution. It is also not what happens: on 2026-09-08 the
    replacement Pod started five seconds after the rollout began, so the
    outgoing container exited on SIGTERM instead of draining and the grace
    bought the turns inside it nothing.
    """
    if spoke_at is None:
        return None
    for rollout in sorted(rollouts, key=lambda rollout: rollout["at"]):
        gap = rollout["at"] - spoke_at
        if -_STAMP_RESOLUTION <= gap <= timedelta(minutes=ATTRIBUTION_MINUTES):
            return rollout
    return None


def _why(silence, kills, unread, rollouts=(), rollouts_unread=""):
    """One line saying whether the bridge took this cycle with it.

    Two readers, and each can fail on its own. Neither failure may be
    reported as an absence: "I could not look" is not "I looked and there
    was nothing", and that holds when only one of the two is blind.
    """
    spoke_at = _parse_stamp(silence.updated_at)
    if spoke_at is None:
        return (f"cannot place {silence.updated_at!r} on a clock, so this one "
                "is unattributed.")
    kill = attribute(spoke_at, kills) if not unread else None
    if kill is not None:
        gap = int((kill["at"] - spoke_at).total_seconds() // 60)
        return (f"killed with the pod: {kill['pod']}'s container terminated "
                f"({kill['reason']}, exit {kill['exit_code']}) at "
                f"{kill['at'].isoformat().replace('+00:00', 'Z')}, {gap}m "
                "after that message.")
    rollout = (attribute_rollout(spoke_at, rollouts)
               if not rollouts_unread else None)
    if rollout is not None:
        return ("rolled out from under it: the bridge Deployment started "
                f"ReplicaSet {rollout['replicaset']} at "
                f"{rollout['at'].isoformat().replace('+00:00', 'Z')}, "
                f"{_when(rollout['at'] - spoke_at)}, which signals the "
                "outgoing Pod and ends every turn inside it. Nothing in the "
                "cycle's own logic failed; a bridge deploy landed on top of "
                "it.")
    if unread and rollouts_unread:
        return ("could not read the bridge Pod's restart history "
                f"({unread}) or its rollout history ({rollouts_unread}), so "
                "whether the bridge took this one is unmeasured rather than "
                "ruled out.")
    if unread:
        return ("no bridge rollout is recorded in the "
                f"{ATTRIBUTION_MINUTES}m after that message, and the Pod's "
                f"restart history could not be read ({unread}) -- so a "
                "container kill is unmeasured here rather than ruled out.")
    if rollouts_unread:
        return ("no bridge container death is recorded in the "
                f"{ATTRIBUTION_MINUTES}m after that message, and the rollout "
                f"history could not be read ({rollouts_unread}) -- so a Pod "
                "replacement is unmeasured here rather than ruled out.")
    return ("unattributed: no bridge container death and no bridge rollout is "
            f"recorded in the {ATTRIBUTION_MINUTES}m after that message. Both "
            "instruments have a horizon: `lastState` holds only the newest "
            "termination of each container, and the ReplicaSet list reaches "
            "back only as far as `revisionHistoryLimit` keeps it"
            f"{_horizon(rollouts, unread, rollouts_unread)}. "
            "This rules out a recent bridge event, not every one.")


def _when(gap):
    """How long after the message, in units a reader can act on.

    Never renders a negative minute count. A rollout inside the same second
    as the message is a timestamp-resolution artefact rather than an event
    that preceded it, and `-1m after that message` read as nonsense.
    """
    seconds = gap.total_seconds()
    if seconds < 0:
        return "inside the same second as that message"
    if seconds < 60:
        return f"{seconds:.0f}s after that message"
    return f"{seconds / 60:.0f}m after that message"


def _horizon(rollouts, unread, rollouts_unread):
    """The measured back-edge of the rollout history, when there is one."""
    if rollouts_unread or not rollouts:
        return ""
    oldest = min(rollout["at"] for rollout in rollouts)
    return f" (oldest on record {oldest.isoformat().replace('+00:00', 'Z')})"


def sweep(site=SITE, grace_minutes=GRACE_MINUTES, window_hours=WINDOW_HOURS,
          now=None, get=_get, run=_kubectl):
    """Report `(status, lines)` over every cycle thread the site lists."""
    now = now or datetime.now(timezone.utc)
    lines = []
    try:
        listing = get(f"{site}/api/conversations")
    except (urllib.error.URLError, OSError, ValueError) as error:
        return 1, [f"COULD NOT READ: nova-site's conversation list ({error})."]

    threads = cycle_threads(listing)
    if not threads:
        return 1, ["COULD NOT READ: nova-site listed no cycle threads at all."]

    def fetch_thread(conversation_id):
        return get(f"{site}/api/conversations/thread"
                   f"?id={conversation_id}&limit={THREAD_LIMIT}", timeout=60)

    found = find_silences(listing, fetch_thread, now=now,
                          grace_minutes=grace_minutes,
                          window_hours=window_hours)
    lines.extend(f"COULD NOT READ: {note}" for note in found.notes)

    if found.silent:
        lines.append(f"NO REPLY \u2014 {len(found.silent)} cycle(s) in the last "
                     f"{window_hours}h finished without ever answering "
                     "the owner. The journal entry is the recovery: relay what "
                     "the cycle did in your own reply, and say the previous "
                     "one never reached them.")
        kills, unread = bridge_kills(run=run)
        rollouts, rollouts_unread = bridge_rollouts(run=run)
        for silence in found.silent:
            lines.append(f"  {silence.name} \u2014 last said "
                         f"{silence.narration!r} at {silence.updated_at}")
            lines.append("      " + _why(silence, kills, unread,
                                          rollouts, rollouts_unread))
    lines.append(f"Read {len(threads)} cycle thread(s) from nova-site: "
                 f"{found.judged} judged, {found.live} still inside the "
                 f"{grace_minutes}m grace, {found.old} older than "
                 f"{window_hours}h, {found.unreadable} unreadable.")
    if found.unreadable:
        return 1, lines
    if found.silent:
        return 2, lines
    return 0, lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", default=SITE)
    parser.add_argument("--grace-minutes", type=int, default=GRACE_MINUTES)
    parser.add_argument("--window-hours", type=int, default=WINDOW_HOURS)
    args = parser.parse_args(argv)
    status, lines = sweep(args.site, args.grace_minutes, args.window_hours)
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
