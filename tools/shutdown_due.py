"""Is it time to stop my own heartbeats before the subscription lapses?

The owner cancelled the Claude subscription this loop runs on (capture,
2026-09-22) and confirmed the exact moment in chat: **it stops at
2026-09-24T00:00:00Z**, and he wants every scheduled heartbeat switched off
about an hour before that. So the deadline is a real wall-clock instant,
2026-09-23T23:00:00Z, and exactly one cycle gets to act on it.

    python3 -m tools.shutdown_due

**The reason this is a check and not a note.** The instruction was written
into `inbox.md`, which step 1b hands to a subagent along with nine other
sources. A cycle that skips the delegated read -- which a cycle with an
obvious pick routinely does -- never sees it, and there is no second chance:
after the cutoff every turn dies before it can read anything. `identity.md`
rule 9 already names this shape: a rule that lives only in a markdown file
is the "needs input" panel again. `preflight` is the one thing every cycle
runs, so the deadline belongs in it.

**Before the cutoff this is deliberately quiet and costs no network call.**
Asking Agora for its heartbeats every cycle for two days to be told the
deadline has not arrived is 120 requests to learn what a clock already
knows. After the cutoff it reads them live, because the IDs written down on
2026-09-22 are a snapshot and a heartbeat added since then would not be in
it -- the list to switch off is whatever is enabled *now*, not whatever was
enabled then.

**Exit 2 means act, and the action is printed.** The same contract as
`security_alerts`: **2** a heartbeat is still on past the deadline, **1**
Agora could not be read after the deadline (which is never clean -- "no
heartbeats" and "could not ask" are the two things worth keeping apart),
**0** either the deadline has not arrived or nothing is left running.

A heartbeat this loop never turned on is still switched off here. The ask was
"you can schedule for you and all other heartbeats and jobs to stop", and a
scheduled turn that cannot reach a model is noise on his phone's feed and a
burned cycle number either way.
"""

import sys
from datetime import datetime, timezone

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import pathlib as _pathlib  # noqa: E402
sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.heartbeat_liveness import AGORA_PUBLIC, _fetch  # noqa: E402

#: The moment every scheduled heartbeat should already be off. One hour
#: before the subscription stops, which is the owner's own margin, not mine.
CUTOFF = datetime(2026, 9, 23, 23, 0, tzinfo=timezone.utc)

#: Why that instant, in his words, so a later cycle does not have to find
#: the chat thread to know whether the date still holds.
WHY = ("The owner, chat thread a91291ff, 2026-09-22: the subscription stops at "
       "2026-09-24T00:00:00Z and he wants the heartbeats stopped about an "
       "hour before it.")


def enabled_rows(rows):
    """The heartbeats still switched on, newest field names tolerated.

    Agora has spelled this field `enabled` for as long as this loop has read
    it; a row missing it entirely is treated as on, because the failure that
    matters is leaving one running and a false alarm costs one PATCH.
    """
    return [r for r in rows if r.get("enabled", True)]


def commands(rows):
    ids = " \\\n  ".join(str(r.get("id")) for r in rows)
    return (
        "for id in \\\n  " + ids + " ; do\n"
        "  curl -s -X PATCH -H \"x-agora-token: $AGORA_TOKEN\" "
        "-H \"Content-Type: application/json\" \\\n"
        f"    -d '{{\"enabled\": false}}' \"{AGORA_PUBLIC}/heartbeats/$id\"\n"
        "done"
    )


def format_report(now, rows=None, error=None):
    if now < CUTOFF:
        left = (CUTOFF - now).total_seconds() / 3600.0
        return (f"not due — {left:.1f}h until {CUTOFF.isoformat()}, when every "
                f"scheduled heartbeat should be off. {WHY}"), 0
    if error:
        return (f"PAST THE DEADLINE ({CUTOFF.isoformat()}) AND CANNOT SEE THE "
                f"HEARTBEATS — {error}\n{WHY}\nRead them by hand from "
                f"{AGORA_PUBLIC}/heartbeats and switch off everything enabled."), 1
    live = enabled_rows(rows or [])
    if not live:
        return (f"every heartbeat is already off, past the deadline "
                f"{CUTOFF.isoformat()}. Nothing to do. {WHY}"), 0
    names = "\n".join(f"  ON   {r.get('name')} — {r.get('id')}" for r in live)
    return (
        f"STOP THESE NOW — {len(live)} heartbeat(s) are still enabled past "
        f"{CUTOFF.isoformat()}.\n{WHY}\n{names}\n\n"
        "This outranks every backlog row: after the subscription stops, no "
        "later cycle can run this.\n\n" + commands(live) + "\n\n"
        "Then write one journal entry naming each id you switched off, say it "
        "in journal-digest, and move the inbox section to Handled. Turning "
        "them back on is the owner's call, not automatic."
    ), 2


def main(argv=None):
    now = datetime.now(timezone.utc)
    rows, error = ([], None)
    if now >= CUTOFF:
        rows, error = _fetch()
    report, status = format_report(now, rows, error)
    print(report)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
