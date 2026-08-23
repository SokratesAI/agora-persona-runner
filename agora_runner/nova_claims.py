"""Who is already working on a handoff item, so two cycles do not both do it.

Edvard, `issues.md` #74: *"The messages you leave to the next cycle might
not scale with the 4 cycles a day as they might overlap and two or more
cycles might read the note left from a previous cycle and then do the
same work in confliction."*

Half of that was already fixed and the wrong half. Every vault write a
cycle makes now carries the revision it read at, so the second writer is
*refused* rather than quietly winning -- which stops the **record** of the
work being lost and does nothing at all about the work being done twice.
Two cycles that both read "1. Confirm the deploy" both confirm the
deploy, and neither ever finds out.

A claim is the missing half: a cycle takes an item before it starts, and
a cycle that wakes into an overlapping window reads the ledger and sees
the item is taken. The atomicity comes from CouchDB, not from here --
`vault_tool.py get --rev-file` / `put --if-rev-file` is a
compare-and-swap, and losing it exits 3. This module is the pure decision
in the middle: given the ledger text and a request, say granted or
refused and hand back the new ledger.

Vault I/O is deliberately outside, the same as `nova_retro`: the ledger
comes in as text and goes out as text, so `tools/claim.py` runs from
either pod with whichever vault client that pod actually has.

The ledger is a JSON object with one key:

    {"claims": [
      {"item": "confirm-deploy-171", "cycle": 189, "state": "open",
       "at": "2026-08-14T11:12:00+02:00", "note": "handoff item 1"}
    ]}

`item` is a slug the cycle that *wrote* the handoff assigns, because the
numbers in **Next cycle** are renumbered every rewrite and a number is
therefore not a name. `prompt.md` step 7 is what puts the slug in the
handoff; this module only ever compares the strings it is given.
"""

import hashlib
import json
import re
from datetime import datetime

#: Where the ledger lives. It was hand-typed in `tools/claim.py`'s docstring
#: and nowhere else, which was fine while one module read it; Cycle 342 added
#: a second reader, and two hand-typed copies of a vault path is the shape
#: `prompt.md` step 2 says to delete rather than guard.
CLAIMS_PATH = "projects/sokrates/projects/agora/nova/resources/claims.json"

#: A claim goes stale after this long, and a later cycle may take it over.
#: 45 minutes because that is the hard turn cap -- measured Cycle 82, and a
#: turn that overruns is killed with no reply posted -- so a cycle whose
#: claim is older than this is not slow, it is dead. Without an expiry a
#: single killed cycle would fence off one handoff item forever, which is
#: strictly worse than the duplication this file exists to stop: nobody
#: would ever notice, because an unclaimable item looks exactly like an
#: item somebody else is handling.
CLAIM_TTL_MINUTES = 45

#: Completed claims older than this are dropped when the ledger is
#: written. A day at the current cadence is somewhere under forty rows,
#: and the point of keeping any of them is that a cycle waking mid-window
#: can tell "already done" from "never claimed" -- which stops mattering
#: once every cycle that could still be holding the stale handoff has
#: finished.
DONE_KEEP_HOURS = 24

#: Slugs are lowercase, hyphen-separated, and long enough to mean
#: something. The rule exists because the whole mechanism is string
#: equality: `Confirm-Deploy` and `confirm-deploy` are two claims on one
#: item, and both cycles would be told they had it.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,47}$")

OPEN = "open"
DONE = "done"

#: What `vault_tool.py get` prints for a path that holds no document. It
#: exits 0 and prints this, so the first cycle to claim anything is handed
#: a file containing a sentence rather than an empty one. Anchored at both
#: ends: a real ledger cannot start with it, and "absent" here means
#: "start a new ledger", which would delete every live claim.
_ABSENT_RE = re.compile(r"\[not found: [^\]]*\]\s*$")


class ClaimError(Exception):
    """The ledger, or the request, is not something we can act on."""


def load(text):
    """Parse ledger text. Absent or blank is an empty ledger, not an error."""
    text = (text or "").strip()
    if not text or _ABSENT_RE.match(text):
        return {"claims": []}
    try:
        ledger = json.loads(text)
    except ValueError as exc:
        raise ClaimError(f"ledger will not parse as JSON: {exc}") from exc
    if not isinstance(ledger, dict) or not isinstance(ledger.get("claims"), list):
        raise ClaimError("ledger must be an object with a 'claims' list")
    for row in ledger["claims"]:
        if not isinstance(row, dict) or "item" not in row or "cycle" not in row:
            raise ClaimError(f"claim row is missing 'item' or 'cycle': {row!r}")
    return ledger


def dumps(ledger):
    return json.dumps(ledger, indent=2, ensure_ascii=False) + "\n"


def _minutes_between(earlier, later):
    """Minutes from `earlier` to `later`, fractional, both aware datetimes."""
    return (later - earlier).total_seconds() / 60.0


def _parse_at(row):
    try:
        return datetime.fromisoformat(row["at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ClaimError(f"claim on {row.get('item')!r} has no readable 'at': {exc}")


def find(ledger, item):
    for row in ledger["claims"]:
        if row["item"] == item:
            return row
    return None


def is_stale(row, now, ttl_minutes=CLAIM_TTL_MINUTES):
    """True when an open claim is older than a cycle can possibly be."""
    if row.get("state") != OPEN:
        return False
    return _minutes_between(_parse_at(row), now) > ttl_minutes


def prune(ledger, now, keep_hours=DONE_KEEP_HOURS):
    """Drop completed claims nobody can still be racing. Mutates and returns."""
    kept = []
    for row in ledger["claims"]:
        if row.get("state") == DONE and _minutes_between(_parse_at(row), now) > keep_hours * 60:
            continue
        kept.append(row)
    ledger["claims"] = kept
    return ledger


def take(ledger, item, cycle, now, note=None, ttl_minutes=CLAIM_TTL_MINUTES):
    """Claim `item` for `cycle`. Returns (granted, message).

    Granted is False for an item another live cycle holds and for one that
    is already finished -- those are the two answers that make the caller
    pick something else. Re-taking your own open claim is granted and
    changes nothing, so a cycle that loses a write conflict and retries is
    not told it lost to itself.
    """
    if not SLUG_RE.match(item or ""):
        raise ClaimError(
            f"{item!r} is not a claim slug: lowercase, digits and hyphens, 3-48 chars"
        )
    if not isinstance(cycle, int) or cycle <= 0:
        raise ClaimError(f"cycle must be a positive integer, got {cycle!r}")

    existing = find(ledger, item)
    if existing is not None:
        if existing.get("state") == DONE:
            return False, (
                f"{item} was finished by cycle {existing['cycle']} at {existing['at']}"
            )
        if existing["cycle"] == cycle:
            return True, f"{item} was already yours (cycle {cycle}), unchanged"
        if not is_stale(existing, now, ttl_minutes):
            held = int(_minutes_between(_parse_at(existing), now))
            return False, (
                f"{item} is held by cycle {existing['cycle']}, claimed {held} min ago "
                f"at {existing['at']} -- pick something else"
            )
        ledger["claims"].remove(existing)
        taken_over = existing["cycle"]
    else:
        taken_over = None

    row = {"item": item, "cycle": cycle, "state": OPEN, "at": now.isoformat()}
    if note:
        row["note"] = note
    if taken_over is not None:
        row["took_over_from"] = taken_over
    ledger["claims"].insert(0, row)
    prune(ledger, now)
    if taken_over is not None:
        return True, (
            f"{item} claimed by cycle {cycle} -- taken over from cycle {taken_over}, "
            f"whose claim was older than {ttl_minutes} min"
        )
    return True, f"{item} claimed by cycle {cycle}"


def release(ledger, item, cycle, now, outcome=None):
    """Mark `item` finished. Returns (ok, message).

    Only the holder may release, and a claim taken over by a later cycle
    is no longer yours -- saying so is the point, because it is the one
    moment the loop can notice the duplication happened at all.
    """
    existing = find(ledger, item)
    if existing is None:
        return False, f"{item} is not claimed by anyone -- nothing to release"
    if existing["cycle"] != cycle:
        return False, (
            f"{item} is held by cycle {existing['cycle']}, not {cycle} -- refusing to release"
        )
    if existing.get("state") == DONE:
        return True, f"{item} was already released at {existing['at']}"
    existing["state"] = DONE
    existing["at"] = now.isoformat()
    if outcome:
        existing["outcome"] = outcome
    prune(ledger, now)
    return True, f"{item} released by cycle {cycle}"


def slug_for_row(board, number):
    """The claim slug for a row on one of Edvard's two boards.

    A handoff item's slug is written by hand by the cycle that wrote the
    handoff. A board row cannot work that way: two overlapping cycles both
    read the row out of the vault and neither wrote it, so the only slug
    they can agree on is one derived from the row itself. `board` is
    `"issue"` or `"idea"`, exactly the strings `top_board_rows` already
    tags its rows with, and the two boards are separately numbered -- so
    the board has to be in the slug or `issue #7` and `idea #7` are one
    claim.
    """
    return f"{board}-{int(number)}"


def slug_for_capture(text):
    """The claim slug for one of Edvard's unboarded captures.

    A capture has no number -- it is a bare bullet he typed -- so the text
    is the only identity it has. Whitespace is normalised first because the
    same bullet read twice can come back wrapped differently, and a slug
    that changes with the wrapping would let both cycles claim it.

    Truncated to 12 hex characters rather than the full digest: this is a
    name two cycles have to agree on within one 45-minute window, not a
    defence against anyone constructing a collision, and a cycle that has
    to read the slug off a terminal line is better served by a short one.
    """
    normalised = " ".join((text or "").split())
    return "capture-" + hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:12]


def slug_for_comment(board, number, text):
    """The claim slug for replying to one comment of his on one row.

    Replying is not the same job as taking the row, and `prompt.md` says
    so: *"Reply on the row ... even if you do not take it as this cycle's
    work."* So the two cannot share a slug -- a cycle that claims
    `issue-7` to reply would fence off the row itself, and a cycle that
    takes the row would silently also claim the reply.

    The row number is in the name so it can be read off a terminal line,
    and the hash is what makes it a *comment* claim rather than a row one.
    `take` refuses a slug that has ever been released as done, which is
    right for a row and fatal for a thread: without the hash, the second
    question Edvard asked on `issue #7` would be permanently unclaimable
    because the first one was answered.

    **That is a guarantee about distinct questions, not about all second
    questions**, and the gap is worth naming rather than rounding off. The
    body this hashes starts at the `**Edvard, MM-DD:**` marker, so the same
    sentence on a different day is a different slug -- but the same sentence
    on the *same* day, asked again after a reply that did not satisfy him,
    hashes the same and is refused until `prune` drops the finished claim
    after `DONE_KEEP_HOURS`. Reviewer finding on runner#304, recorded rather
    than coded: the fix is a timestamp finer than the day, and the day is
    the finest thing `append_detail_note` writes.

    Truncated to 8 hex characters rather than `slug_for_capture`'s 12 for
    the same *reason* it truncates at all -- this is a name two cycles have
    to agree on inside one 45-minute window, printed on a line a cycle has
    to retype. Shorter than a capture's because `board` and `number` are
    already in the name and a capture has nothing but its text.
    """
    normalised = " ".join((text or "").split())
    digest = hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:8]
    return f"reply-{board}-{int(number)}-{digest}"


def held_by(ledger, now, ttl_minutes=CLAIM_TTL_MINUTES):
    """`{item: cycle}` for every claim a cycle could still be working on.

    Open and not yet stale. A `done` claim is deliberately not in here:
    "somebody finished this" and "somebody is doing this right now" are
    different answers, and only the second one is a reason to look at a
    different row this minute.
    """
    live = {}
    for row in ledger["claims"]:
        if row.get("state") == OPEN and not is_stale(row, now, ttl_minutes):
            live.setdefault(row["item"], row["cycle"])
    return live


def summarise(ledger, now, ttl_minutes=CLAIM_TTL_MINUTES):
    """One line per claim, newest first, for a cycle reading the ledger."""
    lines = []
    for row in ledger["claims"]:
        if row.get("state") == DONE:
            state = "done"
        elif is_stale(row, now, ttl_minutes):
            state = "stale"
        else:
            state = "open"
        note = f" — {row['note']}" if row.get("note") else ""
        lines.append(f"{state:<5} {row['item']}  cycle {row['cycle']}  {row['at']}{note}")
    return "\n".join(lines) if lines else "no claims"
