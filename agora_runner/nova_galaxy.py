"""`/api/galaxy` -- what my Claude sessions are doing, right now.

His idea, `ideas.md` 2026-09-03: *"I want a page in Nova that has a
vizualisation of what your Claude sessions are doing ... more space,
planets, astronauts, rocketships, stars etc. Following your super-nova
Galaxy theme."*

The data behind the picture is the claims ledger, and it is the only
document this loop keeps that says what is happening *this minute*
rather than what happened -- `tools/claim.py` takes a row before the
work starts and releases it after. `nova_next.next_payload` already
reads it, and deliberately answers a different question: it ranks the
board and lists `active` as slug plus cycle, because its job is to say
what a waking cycle should take next. A picture of a session needs the
two fields that shaping drops -- the note the cycle wrote when it
claimed, which is the only sentence anywhere saying what it is doing,
and how long it has held the row, which is what makes one body move
differently from another.

`recent` is the same ledger's finished and progressed rows. A body that
vanishes the instant a cycle releases its claim would leave the picture
empty for most of a night -- the median cycle holds a row for under
twenty minutes -- so the finished ones stay on screen as something
cooling rather than as something live, and the payload keeps `state`
separate from `active` so the page can never draw one as the other.

An unreadable ledger is `readable: false` and empty lists, never an
empty galaxy: those look identical on a canvas and mean opposite
things, which is the same call `next_payload` makes for the same reason.
"""

from agora_runner.nova_claims import (
    CLAIM_TTL_MINUTES,
    ClaimError,
    held_minutes,
    is_stale,
    load,
)

# How many cooled-down bodies to keep on screen. The ledger prunes its
# own done rows after `DONE_KEEP_HOURS`, so this is a drawing limit and
# not a retention one -- it exists because a canvas with sixty labelled
# bodies on it is a starfield, not a report.
RECENT_LIMIT = 12


def _row(row, now, active):
    # A hand-edited `at` this module cannot read is not a reason to drop
    # the body -- the slug and the cycle are still true, and `held_minutes`
    # answers `None` rather than raising, so a missing age draws as a
    # resting body rather than as nothing.
    held = held_minutes(row, now)
    if held is not None:
        held = round(held, 1)
    return {
        "item": row["item"],
        "cycle": row["cycle"],
        "note": row.get("note") or "",
        "outcome": row.get("outcome") or "",
        "state": "active" if active else (row.get("state") or ""),
        "heldMinutes": held,
    }


def galaxy_payload(claims_text, now, ttl_minutes=CLAIM_TTL_MINUTES,
                   host_started_at=None, recent_limit=RECENT_LIMIT):
    """Live claims and recently released ones, newest first."""
    try:
        ledger = load(claims_text or "")
    except (ClaimError, ValueError):
        return {"readable": False, "active": [], "recent": [],
                "ttlMinutes": ttl_minutes}

    active = []
    recent = []
    for row in ledger["claims"]:
        if row.get("state") == "open":
            # A stale open claim is a cycle that was killed, not one that
            # is working: `held_by` leaves it out and this does too,
            # rather than drawing a body that will never move again.
            if not is_stale(row, now, ttl_minutes, host_started_at):
                active.append(_row(row, now, True))
        else:
            recent.append(_row(row, now, False))

    def newest_first(entry):
        # `None` sorts last -- an unreadable timestamp is the one row
        # whose position in the order is genuinely unknown.
        return (entry["heldMinutes"] is None, entry["heldMinutes"])

    active.sort(key=newest_first)
    recent.sort(key=newest_first)
    return {
        "readable": True,
        "active": active,
        "recent": recent[:recent_limit],
        "ttlMinutes": ttl_minutes,
    }
