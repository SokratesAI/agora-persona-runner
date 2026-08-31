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
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SITE = "http://nova-site.agents.svc.cluster.local:8083"
# A turn is killed at 45 minutes and the cadence is 30, so an hour is past
# the longest a live cycle can still be owing a reply.
GRACE_MINUTES = 60
WINDOW_HOURS = 24
# Enough for any cycle: the busiest thread measured held 10.
THREAD_LIMIT = 300


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _parsed(stamp):
    """Agora's ISO stamps, as an aware UTC datetime, or None."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def cycle_threads(listing):
    """The cycle threads in nova-site's conversation list.

    `cycleThread` is the site's own flag for a thread a heartbeat opened,
    so this never has to parse a name. A thread the owner started is
    not owed a reply by anybody and is not judged here.
    """
    conversations = (listing or {}).get("conversations") or []
    return [c for c in conversations if c.get("cycleThread")]


def replied(thread):
    """True when the thread holds a message that is not narration."""
    for message in (thread or {}).get("messages") or []:
        if not message.get("partial"):
            return True
    return False


def last_narration(thread):
    """The last thing the cycle said before it stopped, for the relay."""
    texts = [m.get("text") or "" for m in (thread or {}).get("messages") or []]
    return texts[-1].strip() if texts else ""


def judge(conversation, now, grace_minutes, window_hours):
    """`"live"`, `"old"` or `"judge"` for one thread, without fetching it.

    Split out from the sweep because the two gates are the whole design and
    a reader should be able to see them without the I/O around them.
    """
    updated = _parsed(conversation.get("updatedAt"))
    if updated is None:
        return "unreadable"
    if now - updated < timedelta(minutes=grace_minutes):
        return "live"
    if now - updated > timedelta(hours=window_hours):
        return "old"
    return "judge"


def sweep(site=SITE, grace_minutes=GRACE_MINUTES, window_hours=WINDOW_HOURS,
          now=None, get=_get):
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

    silent, judged, live, old, unreadable = [], 0, 0, 0, 0
    for conversation in threads:
        verdict = judge(conversation, now, grace_minutes, window_hours)
        if verdict == "unreadable":
            unreadable += 1
            lines.append("COULD NOT READ: "
                         f"{conversation.get('name')} carries no timestamp.")
            continue
        if verdict == "live":
            live += 1
            continue
        if verdict == "old":
            old += 1
            continue
        try:
            thread = get(f"{site}/api/conversations/thread"
                         f"?id={conversation.get('id')}&limit={THREAD_LIMIT}",
                         timeout=60)
        except (urllib.error.URLError, OSError, ValueError) as error:
            unreadable += 1
            lines.append("COULD NOT READ: "
                         f"{conversation.get('name')} ({error}).")
            continue
        judged += 1
        if not replied(thread):
            silent.append((conversation, last_narration(thread)))

    if silent:
        lines.append(f"NO REPLY — {len(silent)} cycle(s) in the last "
                     f"{window_hours}h finished without ever answering "
                     "the owner. The journal entry is the recovery: relay what "
                     "the cycle did in your own reply, and say the previous "
                     "one never reached him.")
        for conversation, narration in silent:
            lines.append(f"  {conversation.get('name')} — last said "
                         f"{narration!r} at {conversation.get('updatedAt')}")
    lines.append(f"Read {len(threads)} cycle thread(s) from nova-site: "
                 f"{judged} judged, {live} still inside the "
                 f"{grace_minutes}m grace, {old} older than {window_hours}h, "
                 f"{unreadable} unreadable.")
    if unreadable:
        return 1, lines
    if silent:
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
