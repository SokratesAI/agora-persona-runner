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
import sys
import urllib.error
import urllib.request
import json
from datetime import datetime, timezone

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

# Re-exported so a reader of this module sees the whole rule from here and
# `tests/test_reply_health.py` keeps testing it through the tool it names.
__all__ = ["cycle_threads", "judge", "last_narration", "replied", "sweep",
           "main", "SITE", "GRACE_MINUTES", "WINDOW_HOURS", "THREAD_LIMIT"]


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


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
        for silence in found.silent:
            lines.append(f"  {silence.name} \u2014 last said "
                         f"{silence.narration!r} at {silence.updated_at}")
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
