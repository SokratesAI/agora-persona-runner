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

#: The cycle stopped working on this and the work is *not* finished.
#:
#: `open` and `done` were the only two answers for eleven days, and the
#: gap between them is where three separate bugs came from. A cycle that
#: did part of an item and stopped had exactly one way to stop holding it
#: -- `release` -- and `release` meant "finished forever", because `take`
#: refuses a `done` slug. Measured Cycle 353: the top capture on Edvard's
#: board and the only 🔴 Immediately row were both `done` while both were
#: still live work. Cycle 343 released one, Cycle 347 the other, both
#: after real but partial progress, and neither was choosing "finished" --
#: there was nothing else to choose.
#:
#: So a progressed claim is not held (nobody is on it this minute) and not
#: spent (`take` grants it). Its whole content is the `outcome` text, which
#: is what tells the next cycle what is left. It is pruned on the same
#: clock as `done`, because it is a breadcrumb rather than a lock and a
#: breadcrumb nobody can still be racing is just a row.
PROGRESSED = "progressed"

#: The states `release` may leave behind. Both mean "I am no longer
#: holding this"; they differ only in whether `take` will grant it again.
RELEASE_STATES = (DONE, PROGRESSED)

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
    """The row's timestamp, as an aware datetime, or `ClaimError`.

    The naive case is checked here rather than left to the subtraction,
    because `fromisoformat("2026-08-15T11:12:00")` *succeeds* and hands back
    a naive datetime that then raises `TypeError` inside `_minutes_between`
    -- outside this guard, so `tools/claim.py` and `tools/put_entry.py`
    print a traceback instead of the one-line message their exit codes
    promise. Nothing in this module writes a naive `at`; the ledger is a
    vault document and a hand-edit is the ordinary case. Reviewer finding
    on runner#314.
    """
    try:
        parsed = datetime.fromisoformat(row["at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ClaimError(f"claim on {row.get('item')!r} has no readable 'at': {exc}")
    if parsed.tzinfo is None:
        raise ClaimError(
            f"claim on {row.get('item')!r} has a naive 'at' ({row['at']!r}); "
            f"every timestamp in this ledger carries an offset"
        )
    return parsed


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
    """Drop claims nobody can still be racing or resuming. Mutates and returns.

    Both released states, not just `done`: a progressed row is a breadcrumb
    for whoever picks the item up next, and once no live cycle could still
    be reading it, it is a row in a file every claim rewrites.

    And an `open` row **that carries no breadcrumb**, on the same clock,
    which it was not until now. The rule this narrows was "never drop an
    open claim however stale", on the grounds that a stale open row is
    evidence a cycle died and dropping it would delete the only record
    anyone was working on the item. That is right about a row with a
    `resumed_after` on it and wrong about the rest. A plain open row says a
    cycle started something and was killed; `take` deletes that same row
    itself the moment anybody claims the slug again while it is stale, so
    the record survived only where nothing wanted the slug -- and the loop
    no longer produces such rows on purpose, because runner#313 withdrew the
    advice to "leave the claim alone and let it go stale" in favour of
    `release --progress`.

    Measured Cycle 355: four such rows, from 15, 21 and 22 August, in a file
    every claim in this loop reads and rewrites. At a 45-minute cap and a
    cadence heading for 18 minutes with three cycles in flight, a killed
    cycle is an ordinary event, so that is a leak with no ceiling.

    `resumed_after` is the exception and it is not a nicety. A cycle that
    resumes progressed work inherits the previous cycle's account of what is
    left, and that account is the whole content of the `progressed` state --
    runner#313's own reviewer found it dying at exactly this seam. Ageing it
    out would hand the next taker a clean row and let it rebuild work that
    was already done, which is the duplication this module exists to stop,
    arriving through a different door. So a breadcrumb row leaks instead;
    that is a smaller and rarer leak than the one being fixed, and it is the
    right direction to fail in. Reviewer finding on runner#314.

    `keep_hours` and not `CLAIM_TTL_MINUTES`: the 45-minute expiry exists so
    a later cycle can *take over* a dead claim and have the handover
    recorded, and pruning at 45 minutes would take that window away.

    A row whose `at` will not parse is kept, not dropped. Every row this
    module writes carries an offset timestamp, so an unreadable one is a
    hand-edit -- and `prune` runs inside `take` and `release`, on every
    item, so raising here would make one bad row fail every claim in the
    loop including `put_entry`'s journal reservation. Keeping it leaves the
    ledger exactly as legible as it was and lets the readers that genuinely
    need that row's date be the ones that complain. Reviewer finding on
    runner#314.
    """
    kept = []
    for row in ledger["claims"]:
        if row.get("state") == OPEN and row.get("resumed_after"):
            kept.append(row)
            continue
        try:
            age = _minutes_between(_parse_at(row), now)
        except ClaimError:
            kept.append(row)
            continue
        if age > keep_hours * 60:
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

    A `PROGRESSED` item is granted, which is the whole reason that state
    exists, and the row it produces carries `resumed_from` and
    `resumed_after` so the previous cycle's account of what it got done
    survives being picked up.
    """
    if not SLUG_RE.match(item or ""):
        raise ClaimError(
            f"{item!r} is not a claim slug: lowercase, digits and hyphens, 3-48 chars"
        )
    if not isinstance(cycle, int) or cycle <= 0:
        raise ClaimError(f"cycle must be a positive integer, got {cycle!r}")

    existing = find(ledger, item)
    taken_over = None
    resumed = None
    carried = None
    if existing is not None:
        if existing.get("state") == DONE:
            return False, (
                f"{item} was finished by cycle {existing['cycle']} at {existing['at']}"
            )
        if existing.get("state") == PROGRESSED:
            # Not held and not spent: the previous cycle said in writing
            # that it stopped and the work did not. Granting it is the
            # whole reason the state exists, and carrying its outcome
            # forward is what stops the breadcrumb dying at the moment
            # somebody picks the item up.
            resumed = existing
            ledger["claims"].remove(existing)
        elif existing["cycle"] == cycle:
            return True, f"{item} was already yours (cycle {cycle}), unchanged"
        elif not is_stale(existing, now, ttl_minutes):
            held = int(_minutes_between(_parse_at(existing), now))
            return False, (
                f"{item} is held by cycle {existing['cycle']}, claimed {held} min ago "
                f"at {existing['at']} -- pick something else"
            )
        else:
            ledger["claims"].remove(existing)
            taken_over = existing["cycle"]
            # Carry any inherited breadcrumb across the takeover. A cycle
            # that resumed a progressed item and was then killed at the
            # turn cap is the canonical case for this state, and it was
            # the one case where the note was silently dropped. Reviewer
            # finding on runner#313.
            carried = existing

    row = {"item": item, "cycle": cycle, "state": OPEN, "at": now.isoformat()}
    if note:
        row["note"] = note
    if taken_over is not None:
        row["took_over_from"] = taken_over
    if resumed is not None:
        row["resumed_from"] = resumed["cycle"]
        if resumed.get("outcome"):
            row["resumed_after"] = resumed["outcome"]
    elif carried is not None and carried.get("resumed_after"):
        row["resumed_from"] = carried.get("resumed_from", carried["cycle"])
        row["resumed_after"] = carried["resumed_after"]
    ledger["claims"].insert(0, row)
    prune(ledger, now)
    if taken_over is not None:
        return True, (
            f"{item} claimed by cycle {cycle} -- taken over from cycle {taken_over}, "
            f"whose claim was older than {ttl_minutes} min"
        )
    if resumed is not None:
        left = " ".join((resumed.get("outcome") or "no outcome recorded").split())
        return True, (
            f"{item} claimed by cycle {cycle} -- resumed from cycle {resumed['cycle']}, "
            f"which left it open: {left}"
        )
    return True, f"{item} claimed by cycle {cycle}"


def release(ledger, item, cycle, now, outcome=None, state=DONE):
    """Stop holding `item`. Returns (ok, message).

    `state` says which kind of stopping this was: `DONE` means the work is
    finished and the slug is spent forever, `PROGRESSED` means the cycle
    stopped and the work did not, so the next cycle may `take` it. That
    choice is the caller's and there is no way to infer it here -- the
    ledger has never known what the item *is*.

    Only the holder may release, and a claim taken over by a later cycle
    is no longer yours -- saying so is the point, because it is the one
    moment the loop can notice the duplication happened at all.

    A progressed claim may be released again as `done` by the same cycle,
    which is the ordinary case of finishing something you had paused. The
    reverse is refused: once a slug is spent, calling it open again would
    re-grant work that really was finished, which is the duplicate this
    whole ledger exists to stop.
    """
    if state not in RELEASE_STATES:
        raise ClaimError(f"release state must be one of {RELEASE_STATES}, got {state!r}")
    if state == PROGRESSED and not (outcome or "").strip():
        # The outcome is the entire content of a progressed row -- without
        # it the board prints "left this open: no outcome recorded", which
        # tells the next cycle that somebody stopped partway and not what
        # is left. That is worse than an unmarked row, because it implies
        # a note exists. Reviewer finding on runner#313.
        raise ClaimError(
            f"{item}: --progress needs an outcome saying what is left; that text is "
            f"the whole reason the state exists"
        )
    existing = find(ledger, item)
    if existing is None:
        return False, f"{item} is not claimed by anyone -- nothing to release"
    if existing["cycle"] != cycle:
        if existing.get("state") == PROGRESSED:
            # Not "held": `held_by` returns nothing for this row and the
            # board prints no 🔒 on it. Take it first -- that is the
            # documented way to finish somebody else's paused work, and it
            # records the handover. Reviewer finding on runner#313.
            return False, (
                f"{item} was left open by cycle {existing['cycle']}, not {cycle} -- "
                f"take it before releasing it"
            )
        return False, (
            f"{item} is held by cycle {existing['cycle']}, not {cycle} -- refusing to release"
        )
    if existing.get("state") == DONE:
        return True, f"{item} was already released at {existing['at']}"
    if existing.get("state") == PROGRESSED and state == PROGRESSED:
        return True, f"{item} was already left open at {existing['at']}"
    existing["state"] = state
    existing["at"] = now.isoformat()
    if outcome:
        existing["outcome"] = outcome
    prune(ledger, now)
    if state == PROGRESSED:
        return True, (
            f"{item} left open by cycle {cycle} -- still claimable, the next cycle "
            f"will be shown your outcome"
        )
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


def finished_claims(ledger):
    """`{item: row}` for every slug `take` will now refuse forever.

    `held_by` is deliberately about "somebody is doing this right now".
    This is the other half, and it exists because the two answers look
    identical from outside the ledger and are acted on differently.

    A `done` slug is not claimable again -- `take` refuses it with the
    same exit 2 it uses for "somebody is doing this", and `prompt.md`
    tells every cycle to accept a 2 without arguing. That is correct for
    a slug whose *work* is finished and wrong for one whose work is not,
    and only the recorded `outcome` can tell them apart. Measured Cycle
    353: `capture-1d1fc76af9de` (Edvard's "switch to Claude 20x at 18:00
    today") and `idea-63` -- the top capture and the only 🔴 Immediately
    row -- were both `done` in the ledger while both were still live
    work, so `top_board_rows` was printing `[claim: ...]` on the two
    highest-priority items on the board and `take` refused both.

    Rows are pruned after `DONE_KEEP_HOURS`, so this window closes on its
    own; the caller's job is to make it visible while it is open rather
    than to reopen the slug, because re-granting a slug a cycle really
    did finish is the duplicate this whole ledger exists to stop.

    A cycle that stops without finishing has `PROGRESSED` now and is in
    `progressed_claims` instead, so a row landing *here* is a cycle
    saying, in one word it had to type, that there is nothing left.
    """
    return {row["item"]: row for row in ledger["claims"] if row.get("state") == DONE}


def progressed_claims(ledger):
    """`{item: row}` for every slug a cycle stopped on without finishing.

    The third answer, and the reason the state exists. `held_by` says
    "somebody is on this right now" and `finished_claims` says "somebody
    already did this"; this one says "somebody did part of this and
    stopped, and here is what they got done". `take` grants these, so a
    caller rendering a line should still print the take command -- what it
    adds is the outcome text, which is the only thing that says what is
    left.
    """
    return {row["item"]: row for row in ledger["claims"]
            if row.get("state") == PROGRESSED}


def summarise(ledger, now, ttl_minutes=CLAIM_TTL_MINUTES):
    """One line per claim, newest first, for a cycle reading the ledger."""
    lines = []
    for row in ledger["claims"]:
        if row.get("state") == DONE:
            state = "done"
        elif row.get("state") == PROGRESSED:
            state = "prog"
        elif is_stale(row, now, ttl_minutes):
            state = "stale"
        else:
            state = "open"
        # The outcome as well as the note, not instead of it: on a
        # `progressed` row the outcome is the entire content -- what got
        # done and what is left -- and a reader of `claim list` who cannot
        # see it has to open the raw JSON. `resumed_after` is here for the
        # same reason: on an open row it is the only record of what the
        # cycle before this one got done.
        parts = [row[k] for k in ("note", "resumed_after", "outcome") if row.get(k)]
        tail = f" — {' | '.join(parts)}" if parts else ""
        lines.append(f"{state:<5} {row['item']}  cycle {row['cycle']}  {row['at']}{tail}")
    return "\n".join(lines) if lines else "no claims"
