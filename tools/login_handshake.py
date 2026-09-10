"""He answered the login reminder. Is a link owed, and to which message?

    python3 -m tools.login_handshake

His ask, Telegram 2026-09-10 11:51 Oslo, after the fourth hand-run login of
the morning finally landed:

    "Now let this be the standard way we refresh. Send me a message on
    telegram when its time, 5days before expiery then i respond with 'ok' or
    something similar. Then you send me the link. Ok? Remember this until
    next time and schedule for this to happen."

Half of that already ran. `tools.credential_recovery --notify` is in
`tools.preflight` on a daily cadence and pages him at
`OWNER_REQUESTED_LEAD_HOURS` -- 120 hours, the five days he asked for, chosen
from that same ask. What nothing did was read his answer: his reply landed in
`tools.telegram_inbox` as an ordinary unread message, indistinguishable from a
capture, and a link only ever got minted because a cycle happened to be
looking. This is the read side, so the handshake he described is a thing the
loop does rather than a thing a cycle remembers.

**It decides, it does not mint.** Exit 2 means a link is owed and the command
to send it is printed; nothing here talks to Anthropic and nothing here writes
a credential. That is deliberate rather than unfinished: minting invalidates
nothing since agora-persona-runner#972 but the *exchange* has minutes to live,
so the link and `tools.claude_login_link wait` belong in one foreground run by
a cycle that will still be alive when he taps it. A sweep that minted a link
and walked away is the exact failure #971 was built to stop.

**A bare "ok" is the only thing that counts, and that is the whole safety
rule.** He writes long captures on this channel, and a message that merely
*contains* the word ok -- "ok so the board is still wrong" -- is not consent to
anything. `is_go_ahead` matches the whole message after normalising, against a
short list of the ways a person says yes, so a sentence never passes. And the
reply must have arrived *after* the page went out (`tools.notify`'s own state
file records when), or a two-day-old "ok" about something else would mint a
link the day the countdown starts.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone

from tools import credential_recovery, notify, telegram_inbox

#: The whole message, normalised, must be one of these. Short and deliberately
#: not a regex over a sentence -- see the module docstring.
GO_AHEAD = frozenset({
    "ok", "okay", "okey", "k", "yes", "yep", "yeah", "yup", "y",
    "ja", "jepp", "greit", "kjor", "kjør",
    "go", "go ahead", "send", "send it", "send me the link", "sure",
    "do it", "ok go", "ok send", "yes please", "ja takk",
})


def normalise(text):
    """Lowercased, punctuation and emoji stripped, whitespace collapsed."""
    lowered = str(text or "").lower()
    kept = re.sub(r"[^a-z0-9æøå ]+", " ", lowered)
    return " ".join(kept.split())


def is_go_ahead(text):
    """Is this whole message him saying yes?

    Whole-message, never a substring: "ok" passes and "ok but not today"
    does not, because the second one is a conversation and this function's
    answer mints a login link.
    """
    return normalise(text) in GO_AHEAD


def _parsed(stamp):
    """An ISO stamp from the bridge or the notify state, as aware UTC, or None."""
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    try:
        when = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def last_paged(state):
    """When the login page last went out, off `tools.notify`'s own state file.

    Read rather than re-derived: the page and this reader must agree on
    which reminder a reply is answering, and the sender already writes the
    only stamp that can say so.
    """
    return _parsed((state.get(credential_recovery.LOGIN_NOTIFY_KEY) or {}).get("last_sent"))


def go_ahead_since(rows, since):
    """His newest whole-message yes that arrived after `since`, or None.

    `since` is required. A reply cannot answer a message that had not been
    sent, and every row here is unread by construction, so without the
    stamp an old "ok" about anything at all would read as consent.
    """
    if since is None:
        return None
    best = None
    for row in rows or []:
        at = _parsed(row.get("at"))
        if at is None or at < since:
            continue
        if not is_go_ahead(row.get("text")):
            continue
        if best is None or at >= _parsed(best.get("at")):
            best = row
    return best


def due(expiry, now):
    """Is the login close enough that he has been, or is about to be, paged?

    The same threshold `judge` branches on, named once so the caller can
    decide whether reaching for the Telegram bridge is worth it at all.
    """
    if expiry is None:
        return False
    return (expiry - now).total_seconds() / 3600.0 <= credential_recovery.LOGIN_LEAD_HOURS


def judge(expiry, now, paged_at, rows):
    """(exit status, lines). The whole decision, with no I/O in it."""
    if expiry is None:
        return 1, ["NOT JUDGED  when this loop's login expires is unknown, so "
                   "whether a reply of his is owed a link cannot be decided."]
    hours_left = (expiry - now).total_seconds() / 3600.0
    if hours_left > credential_recovery.LOGIN_LEAD_HOURS:
        return 0, ["No login is due -- %.1f day(s) left, and he is paged at %.1f."
                   % (hours_left / 24.0, credential_recovery.LOGIN_LEAD_HOURS / 24.0)]
    if paged_at is None:
        # Not an error and usually a matter of minutes: the countdown is
        # inside the window and `credential_recovery --notify` runs on its own
        # daily cadence, so this is the gap between the deadline arriving and
        # the next sweep sending the page.
        return 0, ["The login is due in %.1f day(s) but the 5-day reminder has "
                   "not gone out yet, so there is nothing of his to answer. "
                   "`python3 -m tools.credential_recovery --notify` sends it."
                   % (hours_left / 24.0)]
    row = go_ahead_since(rows, paged_at)
    if row is None:
        return 0, ["Reminded him %s UTC; no reply of his since then is a plain "
                   "yes, so no link is owed. %d unread message(s) on the channel."
                   % (paged_at.isoformat(timespec="seconds"), len(rows or []))]
    return 2, [
        "HE SAID GO -- message #%s at %s: %r" % (
            row.get("id"), row.get("at"), str(row.get("text") or "")),
        "Send the link and stay for the code, in ONE foreground run:",
        "  python3 -m tools.claude_login_link start --notify --force && \\",
        "    python3 -m tools.claude_login_link wait --timeout 720 --poll 15 \\",
        "      --install /data/claude-home/.claude/.credentials.json",
        "Then, and only once the exchange has landed:",
        "  python3 -m tools.telegram_inbox --ack %s" % row.get("id"),
    ]


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--disk", default=None,
                        help="path to the live .credentials.json (default: under CLAUDE_HOME)")
    parser.add_argument("--state", default=notify.DEFAULT_STATE,
                        help="where tools.notify records when it last paged him")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    now = datetime.now(timezone.utc)

    path = args.disk or credential_recovery.disk_path()
    try:
        live = credential_recovery.read_disk(path)
    except credential_recovery.CredentialError as problem:
        sys.stdout.write("CANNOT READ the live credential at %s: %s\n" % (path, problem))
        return 1
    # `judge_live_refresh` and not `expires_at`: the second reads the access
    # token, which is eight hours wide by design and would put this in the
    # window every afternoon on a system that is working.
    _, expiry = credential_recovery.judge_live_refresh(live, now)

    paged_at = last_paged(notify.load_state(args.state))

    rows = []
    if due(expiry, now) and paged_at is not None:
        # Only fetched once the answer can turn on it, and `due` is what makes
        # that true for 25 days out of 30. The stamp alone is not enough: it is
        # never cleared, so the day after any renewal it still says he was
        # paged, and a reader gated on it would call the bridge on every sweep
        # forever -- turning a bridge outage into a daily exit 1 on a question
        # whose answer could not have changed.
        inbox_status, body, problem = telegram_inbox.read_inbox()
        if inbox_status != 0:
            sys.stdout.write(
                "CANNOT READ the Telegram inbox (%s), so a yes of his may be "
                "sitting unanswered. The deadline itself is unaffected.\n" % problem)
            return 1
        rows = body["messages"]

    status, lines = judge(expiry, now, paged_at, rows)
    for line in lines:
        sys.stdout.write("%s\n" % line)
    return status


if __name__ == "__main__":
    sys.exit(main())
