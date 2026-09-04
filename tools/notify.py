"""Should this message reach the owner's phone right now, and has it already?

`tools.telegram` sends. It has no opinion about *when*, and until now nothing
in this loop sent him anything automatically, so it did not need one. That
changed on 2026-09-04, when he asked for exactly that:

    "make Nova connect to it so that important alerts get sent to me like if
    one server is down or if Nova really needs something from me immediately."

and set the constraint in the same breath:

    "I can't answer anything between 22:00 and 07:00 Oslo time as I'm asleep."

So this module is the policy layer and `tools.telegram` stays the transport.
Everything here is a decision about whether to send, made before the bytes go
anywhere:

**Quiet hours, 22:00 to 07:00 Oslo.** A routine message inside that window is
held, not sent, and the caller is told (exit 3) so it can put the thing
somewhere he reads in the morning instead. `--urgent` breaks through, and that
is deliberate rather than a hedge: the class of thing he named -- a server
down -- is the one class where waking him is the point. If he would rather
sleep through a dead box too, the fix is one flag at the call site, and I would
rather be told that than sit on an outage until 07:00.

**Deduplication.** A cycle wakes every eighteen minutes and reads the same
firing alert every time. Without a memory that is 80 identical messages a day,
which is a channel he stops reading -- the ignored-status-panel failure
again, on his phone. A `--key` is remembered in a small JSON file with the time it was last
sent, and the same key inside `--dedupe-hours` (default 6) is held. The key is
the caller's judgement about what counts as "the same problem"; it is not
inferred from the text, because a message whose body carries a duration
changes on every read and would defeat itself.

**A held message is not a failure.** Exit 3 means the gate worked. Exit 2 is
the bridge refusing, exit 1 is not being able to reach it -- the same contract
`tools.telegram` already uses, with 3 added on top so a caller can tell
"deliberately not sent" from "tried and failed".

Two limits, printed here rather than discovered later. The state file is on
one pod's disk, so two cycles overlapping can each send once for one key --
the failure is a duplicate message, which is the safe direction. And if the
Oslo timezone cannot be resolved at all, the hour is unknown: a routine
message is held and an urgent one is sent, because guessing wrong in the
other direction means sitting on an outage.

    python3 -m tools.notify --key nas-down --text 'the NAS stopped answering' --urgent
    python3 -m tools.notify --key alerts --file /tmp/alerts.txt
    python3 -m tools.notify --key alerts --file /tmp/alerts.txt --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# Repo root on sys.path so `python3 tools/notify.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from tools import telegram  # noqa: E402

QUIET_START_HOUR = 22
QUIET_END_HOUR = 7
DEFAULT_DEDUPE_HOURS = 6.0
DEFAULT_STATE = "/data/claude-home/nova-notify-state.json"

HELD = 3


def oslo_now():
    """Now in Oslo, or None when the timezone database cannot answer.

    None is a real third state and callers must handle it: the bridge image
    is Debian and resolves this fine, but the same code on an Alpine base
    raises `ZoneInfoNotFoundError`, and an unknown hour must not silently
    read as "not quiet hours".
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Oslo"))
    except Exception:
        return None


def in_quiet_hours(now) -> bool:
    """True inside 22:00-07:00 Oslo. The window wraps midnight."""
    return now.hour >= QUIET_START_HOUR or now.hour < QUIET_END_HOUR


def load_state(path: str) -> dict:
    """The dedupe memory. An unreadable file is an empty memory, on purpose.

    Failing to read it must not stop a message going out -- the worst case of
    an empty memory is one duplicate, and the worst case of raising here is an
    outage nobody hears about.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(path: str, state: dict) -> str | None:
    """None on success, else why it could not be written."""
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
        return None
    except OSError as err:
        return str(err)


def recently_sent(state: dict, key: str, now: datetime, dedupe_hours: float):
    """The last-sent datetime when `key` is still inside its window, else None."""
    # A missing clock must not disable the dedupe: without this the unknown-hour
    # branch of `decide` sends every urgent message and never suppresses a
    # repeat, which is one page every eighteen minutes for one outage.
    now = now if now is not None else datetime.now(timezone.utc)
    stamp = state.get(key, {}).get("last_sent")
    if not stamp:
        return None
    try:
        last = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if last.tzinfo is None or now.tzinfo is None:
        return None
    if now - last < timedelta(hours=dedupe_hours):
        return last
    return None


def decide(key, urgent, now, state, dedupe_hours=DEFAULT_DEDUPE_HOURS):
    """(send?, line). The whole policy, with no I/O in it so it can be tested.

    Order matters: dedupe is checked before quiet hours, so a repeat inside
    the window reports the reason a caller can act on -- "you already told
    him" is a different fix from "he is asleep".
    """
    last = recently_sent(state, key, now, dedupe_hours)
    if last is not None:
        ago = (now - last).total_seconds() / 3600
        return False, (
            f"held: '{key}' was already sent {ago:.1f}h ago, inside the "
            f"{dedupe_hours:g}h window"
        )
    if now is None:
        return (True, "sending: urgent, and the Oslo hour could not be read") if urgent else (
            False,
            "held: the Oslo timezone could not be resolved, so quiet hours "
            "cannot be ruled out and this is not urgent",
        )
    if in_quiet_hours(now):
        if not urgent:
            return False, (
                f"held: {now:%H:%M} Oslo is inside quiet hours "
                f"({QUIET_START_HOUR:02d}:00-{QUIET_END_HOUR:02d}:00) and this is not urgent"
            )
        return True, f"sending: urgent, overriding quiet hours at {now:%H:%M} Oslo"
    return True, f"sending: {now:%H:%M} Oslo is outside quiet hours"


def notify(
    text,
    key,
    urgent=False,
    state_path=DEFAULT_STATE,
    dedupe_hours=DEFAULT_DEDUPE_HOURS,
    now=None,
    send=None,
    url=telegram.DEFAULT_URL,
):
    """(exit status, line). 0 sent, 3 held, 2 refused, 1 unreachable.

    `send` defaults to None rather than to `telegram.send` because a default
    argument is bound once, at import, so a test that replaces
    `telegram.send` afterwards would be replacing something this function
    never looks at again -- the test passes, the real client runs, and the
    owner gets a message from a test run.
    """
    send = telegram.send if send is None else send
    refusal = telegram.check_text(text)
    if refusal:
        return 2, refusal

    when = now if now is not None else oslo_now()
    state = load_state(state_path)
    should_send, line = decide(key, urgent, when, state, dedupe_hours)
    if not should_send:
        return HELD, line

    status, sent_line = send(text, url)
    if status != 0:
        return status, sent_line

    stamp = when if when is not None else datetime.now(timezone.utc)
    if stamp is not None:
        # Recorded even when the Oslo clock could not be read. Without this the
        # unknown-hour branch sends every urgent message and remembers none of
        # them, so one outage pages him every eighteen minutes forever -- the
        # opposite of what the dedupe is for, in the one case nobody would test.
        state[key] = {"last_sent": stamp.isoformat(), "urgent": bool(urgent)}
        problem = save_state(state_path, state)
        if problem:
            # Said out loud rather than swallowed: without the write, the next
            # cycle sends this again, and a caller reading exit 0 would have no
            # way to know why.
            return 0, f"{sent_line} — but the dedupe state did not save: {problem}"
    return 0, sent_line


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key", required=True, help="what counts as 'the same message' for dedupe")
    parser.add_argument("--text", help="the message, or - to read stdin")
    parser.add_argument("--file", help="read the message from this file instead")
    parser.add_argument("--urgent", action="store_true", help="send even inside quiet hours")
    parser.add_argument("--dedupe-hours", type=float, default=DEFAULT_DEDUPE_HOURS)
    parser.add_argument("--state", default=DEFAULT_STATE, help="where the dedupe memory lives")
    parser.add_argument("--url", default=telegram.DEFAULT_URL)
    parser.add_argument("--dry-run", action="store_true", help="decide, print, send nothing")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        text = telegram.read_text(args)
    except (ValueError, OSError) as err:
        print(str(err))
        return 2

    if args.dry_run:
        refusal = telegram.check_text(text)
        if refusal:
            print(refusal)
            return 2
        should_send, line = decide(
            args.key, args.urgent, oslo_now(), load_state(args.state), args.dedupe_hours
        )
        print(line)
        if should_send:
            print(f"would send to {args.url}/send, {len(text.strip())} character(s)")
        return 0 if should_send else HELD

    status, line = notify(
        text,
        args.key,
        urgent=args.urgent,
        state_path=args.state,
        dedupe_hours=args.dedupe_hours,
        url=args.url,
    )
    print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
