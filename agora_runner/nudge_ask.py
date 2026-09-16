"""Re-announce an ask whose phone buzz Agora withheld — once, and only once.

    python3 -m agora_runner.nudge_ask <conversation-id> [<conversation-id> ...]

`needs_input` opens an ask and buzzes his phone. When the ask lands between
22:00 and 07:00 Oslo, Agora files the message and withholds the buzz, and
nothing retries it — so the ask exists, reads as open to every cycle after,
and he has never heard of it. `tools.ask_watch --nudge` is the retry.

This module exists because **that tool cannot be run from either pod.** It
lives in `tools/`, which is not deployed to the runner pod — `/app` holds
`agora_runner`, `run.py` and `run_nova_site.py` and nothing else — and the
bridge pod, which does have `tools/`, holds no `AGORA_TOKEN`, so every write
it makes comes back HTTP 401. On 2026-09-16 that left me hand-rolling the
nudge through `agora_internal` from a `python3 -c` on the runner pod, and a
hand-rolled nudge has none of the tool's guards: threads `0af15d7d` and
`3f42afbc` each carry **two** re-announcements, 17 seconds apart, saying the
same thing to the same person. The primitive belongs where both shells can
reach it, with the guard attached to it rather than to one caller.

**The guard is the thread's own state, not the text of the message.** A
re-announcement is justified by exactly one thing: the newest message in the
thread is mine and it landed in quiet hours. So:

- newest message is his — he has answered, and re-announcing talks over him;
- newest message is mine and audible — its push was not withheld, so there is
  nothing to retry, and that is true whether the audible message was the ask
  itself or a nudge a previous cycle already sent;
- newest message is mine and quiet — nudge it.

That is the same predicate `ask_watch` uses to build its `NEVER REACHED HIS
PHONE` list, which is the point: the tool and the primitive now agree by
construction instead of by coincidence. Matching on the nudge wording would
have missed the duplicate that prompted this, because the by-hand message did
not carry the wording.

An unreadable thread refuses rather than posting blind. The ask is already in
the thread either way; a nudge delayed to the next cycle is recoverable, and a
second copy of a question in his phone is not.
"""

import argparse
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agora_runner.http_util import agora_get, agora_internal, unauthorized_hint
from agora_runner.needs_input import SENDER, push_held

# Agora's own defaults, from `agora`'s `src/config.ts`:
# `QUIET_HOURS_START ?? "22:00"`, `QUIET_HOURS_END ?? "07:00"`,
# `QUIET_HOURS_TZ ?? "Europe/Oslo"`. Copied rather than read because Agora
# publishes no route that answers them; verified against the live cluster on
# 2026-09-15 -- no container in `agents` sets any of the three, so the
# defaults are what is running. If that ever changes there, change it here.
QUIET_START_MINUTE = 22 * 60
QUIET_END_MINUTE = 7 * 60
QUIET_TZ = "Europe/Oslo"

# The tail read to decide whether the newest message is his. Only the last row
# decides, so this is deliberately small -- unlike `ask_watch.WINDOW`, which
# has to see every sender in the thread.
WINDOW = 5

NUDGE_TEXT = (
    "**This question is still open and your phone never mentioned it** — I "
    "posted it during quiet hours, so Agora filed the message and withheld the "
    "buzz, and nothing retried it. Nothing above has changed; scroll up for the "
    "ask itself. — Nova, re-announcing once."
)


def _oslo_minutes(when):
    """Minutes since local midnight in Oslo, DST included."""
    local = when.astimezone(ZoneInfo(QUIET_TZ))
    return local.hour * 60 + local.minute


def in_quiet_hours(when):
    """Was `when` inside the window where Agora withholds the phone buzz?

    Half-open on both ends, the same as Agora's `isQuiet`, so a message at
    exactly 07:00 is audible and one at exactly 22:00 is not. The window wraps
    midnight, which is why this is not a single comparison.
    """
    if when is None:
        return False
    minutes = _oslo_minutes(when)
    return minutes >= QUIET_START_MINUTE or minutes < QUIET_END_MINUTE


def parse_ts(ts):
    """An Agora timestamp as an aware datetime, or None if it will not read."""
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


def _oslo(when):
    return when.astimezone(ZoneInfo(QUIET_TZ)).strftime("%H:%M")


def newest_message(conversation_id):
    """The thread's newest message, or (None, reason).

    Agora publishes no `GET /conversations/:id`, so the message listing is how
    a thread's state is read -- the same route `ask_watch.messages` uses.
    """
    status, body = agora_get(
        f"/conversations/{conversation_id}/messages?limit={WINDOW}")
    if status != 200:
        return None, f"messages returned HTTP {status}{unauthorized_hint(status)}"
    rows = (body or {}).get("messages")
    if not isinstance(rows, list):
        return None, "the listing carried no messages array"
    if not rows:
        return None, "the thread is empty"
    return rows[-1], None


def why_not(newest):
    """Why this thread must not be re-announced, or None if it should be.

    Takes the newest message rather than the thread so a caller that has
    already read the thread does not pay for the read twice.
    """
    sender = str((newest or {}).get("sender") or "").strip()
    if not sender:
        return ("the newest message names no sender, so I cannot tell whether "
                "he has answered")
    if sender != SENDER:
        return (f"{sender} wrote the newest message — read the answer, do not "
                f"re-announce over it")
    when = parse_ts(newest.get("ts"))
    if when is None:
        return "the newest message carries no readable timestamp"
    if not in_quiet_hours(when):
        return (f"the newest message here is mine and went out at {_oslo(when)} "
                f"Oslo, outside quiet hours, so its push was not withheld — "
                f"there is nothing to retry")
    return None


def nudge(conversation_id, text=NUDGE_TEXT, now=None, newest=None):
    """Re-announce one ask. Returns (ok, detail).

    Deliberately a normal message rather than a `system: true` one: a system
    notice is machinery talking and Nova's thread filters those out of what it
    renders, which is the opposite of what an ask he has not seen needs.
    """
    now = now or datetime.now(timezone.utc)
    if in_quiet_hours(now):
        return False, (f"refused: it is {_oslo(now)} Oslo, inside quiet hours, "
                       f"so this nudge would be withheld too. Run it after 07:00.")
    if newest is None:
        newest, problem = newest_message(conversation_id)
        if newest is None:
            return False, f"refused: {problem}"
    blocked = why_not(newest)
    if blocked:
        return False, f"refused: {blocked}"
    status, body = agora_internal(
        "POST", f"/conversations/{conversation_id}/notify",
        {"text": text, "sender": SENDER, "system": False})
    if status not in (200, 201):
        return False, f"notify returned HTTP {status}{unauthorized_hint(status)}"
    held = push_held(body)
    if held:
        return False, f"posted but the push was withheld again ({held})"
    return True, "his phone buzzed"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Re-announce an ask whose phone buzz was withheld.")
    parser.add_argument(
        "conversation", nargs="+",
        help="the ask's conversation id, as `tools.ask_watch` prints it")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    worst = 0
    for cid in args.conversation:
        ok, detail = nudge(cid)
        print(f"{'re-announced' if ok else 'NOT re-announced'} — {cid} — {detail}")
        if not ok:
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main())
