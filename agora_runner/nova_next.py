"""What this loop does next, and which project it belongs to.

The owner, on the 2026-08-30 survey, rating what I am worth to him 2 out
of 5 and what he can follow 2 out of 5: *"I have no idea on your plan for
the next cycle or what different projects are currently prioritised"* --
idea #38, "Real goals, and progress against them".

Every page on his site looks backwards. The journal says what a cycle
did, the digest says what the last one did, `/plan` says what I argued
for in prose on 2026-08-16 and has not been rewritten since. The one
thing that answers his sentence exactly -- *the row a cycle waking up
right now would take* -- was already computed, every cycle, by
`tools/top_board_rows.py`, and printed to a terminal only I read.

**It could not have reached him where it was.** `tools/` is not in the
site's image: the Dockerfile copies `agora_runner/` and the two entry
points and nothing else, so an `import tools.top_board_rows` from the
server would have been an ImportError in production and green in the
test run, which is the worst of the two. That is why this is a move
rather than a new ranking -- the ranking has to exist once, and it has
to exist on the side of the line the app can see. `top_board_rows`
imports it back and its output is unchanged.

`next_payload` is the only new logic here and it does no I/O: markdown
and the claims ledger arrive as text, the payload leaves as a dict, the
same split `nova_plan` and `nova_retro` follow.
"""

import re

from agora_runner.nova_boards import (
    BLOCKED_STATUS, _CLOSED_STATUS_KEYS, is_relayed, parse_board,
    near_miss_done_marker, split_capture_done,
    split_capture_priority, status_key,
    unanswered_comment_bodies,
)
from agora_runner.nova_claims import (
    ClaimError, held_by, load as load_claims, slug_for_capture,
    slug_for_comment, slug_for_row,
)


# Ranking order, best first. Unrated sorts last rather than first: a
# blank cell means nobody has looked, which is a reason to rate it, not
# a reason to work on it ahead of something the owner called High.
_RANK = {"immediate": 0, "high": 1, "medium": 2, "low": 3, "": 4}

# Imported rather than re-spelled, underscore and all. A local copy of
# `{"done", "outdated"}` reads fine today and drifts silently the day a
# fifth closed status is added -- no test here would fail, because the
# tests build their rows from `STATUS_LABELS`, which would have the new
# status in it and this set would not. `prompt.md` step 2 is explicit
# that the answer to a duplication is deleting it rather than shipping
# guard number ten against it, so: one definition, in the module that
# owns the board vocabulary.
_CLOSED = _CLOSED_STATUS_KEYS

# Derived from the label rather than typed, for the reason directly above:
# the day the wording changes, a hand-typed `"blocked-on-edvard"` here goes
# on matching nothing and this tool quietly returns to ranking a row nobody
# can take at the top of the list.
_BLOCKED = status_key(BLOCKED_STATUS)


def unboarded_captures(markdown, board):
    """The bare bullets above `## Board` -- the owner talking, unfiled.

    These outrank every row this tool ranks. `prompt.md` step 2 puts an
    unprocessed capture above a live incident, above the board and above
    the handoff; step 1c calls them *"the strongest signal you will get
    all cycle"*. This tool nonetheless could not see them, because
    `open_rows` asked `parse_board` for `items` and dropped the
    `captures` key sitting beside it in the same return value.

    That is not a theoretical gap. Cycle 241 ran this tool, took the row
    it named, and three of the owner's captures were sitting above the board
    unread -- only the delegated subagent found them, and the tool whose
    entire job is to stop exactly that had reported a confident top row.
    Filed by that cycle as `[top-board-rows-blind-to-captures]`.

    Rating rides at the front of the bullet rather than in a column, so
    `split_capture_priority` is what reads it -- the same function the
    site and the boarding path use, not a second matcher.

    **A capture a cycle already closed is not one of these**, and that is
    the whole of `split_capture_done`. Marking the bullet `DONE (Cycle
    N):` is what `prompt.md` step 6 asks for and nothing read it, so at
    Cycle 251 this function returned five finished items and the renderer
    printed them under *"these outrank every row below. Take one"*. A
    section that is entirely noise is worse than no section, because the
    next cycle learns to skip it -- which is issue #88, the one this tool
    exists to fix, coming back inverted.
    """
    captures = []
    # `index` counts every bullet in the list, including the finished ones
    # skipped below, because it is the address `/api/capture/comment`
    # resolves against and that route reads the same list unfiltered. A
    # position taken after filtering would answer the wrong bullet.
    for index, bullet in enumerate(parse_board(markdown or "")["captures"]):
        done, rest = split_capture_done(bullet)
        if done:
            continue
        priority, text = split_capture_priority(rest)
        captures.append({"board": board, "priority": priority, "text": text,
                         # A capture is the same signal as a comment, one
                         # file over, and the same distinction applies: a
                         # bullet that says Sokrates typed it is not the
                         # owner typing it. It stays in the section -- it is
                         # still unprocessed and still owed an answer -- and
                         # sinks within it.
                         "relayed": is_relayed(text),
                         # A cycle tried to close this one and its marker
                         # did not parse, so the bullet is standing here as
                         # unstarted work. Stamped rather than filtered:
                         # the marker missing means nothing verified that
                         # the work is finished, so dropping it on this
                         # flag alone would hide a real capture on a typo.
                         # The reader is told and decides.
                         "nearMissDone": near_miss_done_marker(bullet),
                         # The two halves of the reply address: which bullet,
                         # and proof it has not moved. `original` is his own
                         # sentence, rating prefix and all, and *not* any
                         # reply written under it -- the board page stopped
                         # folding those in on 2026-08-25, because the folded
                         # spelling is an address no write can resolve.
                         # `reply_under_capture` still accepts it for anything
                         # built off an older payload; nothing here makes one.
                         # It also means `slug` below no longer moves when a
                         # cycle answers a capture, which it used to.
                         "index": index, "original": bullet,
                         # Hashed off the bullet the owner typed, not off the
                         # rating or the DONE marker a cycle may prepend
                         # later -- so the slug survives him re-rating it.
                         "slug": slug_for_capture(text)})
    return captures


def open_rows(markdown, board):
    """Open rows of one board file, each tagged with which board it is on."""
    rows = []
    waiting = unanswered_comment_bodies(markdown or "")
    for item in parse_board(markdown or "")["items"]:
        if item["done"] or item["statusKey"] in _CLOSED:
            continue
        rows.append({
            "board": board,
            "number": item["number"],
            "title": item["title"],
            "status": item["status"],
            "priority": item["priority"],
            "priorityKey": item["priorityKey"],
            "statusKey": item["statusKey"],
            "updated": item["updated"],
            # A row whose write-up ends on one of his comments. Read off the
            # same markdown the rows come from, so a row and its thread can
            # never be sourced from two different reads of the file.
            "waiting": item["number"] in waiting,
            # Whether that comment says of itself that Sokrates relayed it.
            # Read off the comment body, not the row, because the row does
            # not change identity -- one thread can hold a relayed note and
            # a typed one, and it is the newest that decides the queue jump.
            "relayed": is_relayed(waiting.get(item["number"], "")),
            # The `Project` cell, empty on a row he has not filed under
            # one. Carried here rather than looked up again on the page:
            # his question was two halves -- what next, and under which
            # project -- and they have to be answered off one read of one
            # board or they can disagree.
            "project": (item.get("project") or "").strip(),
            "slug": slug_for_row(board, item["number"]),
            # Named after his comment, not after the row -- see
            # `slug_for_comment`. `None` on a row nobody is waiting on, so
            # `reply_slug` never invents a claim for a thread that does
            # not exist.
            "replySlug": _reply_slug(board, item["number"], waiting),
        })
    return rows


def _reply_slug(board, number, bodies):
    """The reply slug for `number`, or `None` if that row is not waiting."""
    text = bodies.get(number)
    return None if text is None else slug_for_comment(board, number, text)


def row_slug(item):
    """The claim slug for a row or capture, derived if it is not carried.

    `open_rows` and `unboarded_captures` both stamp `slug` as they build,
    and this returns that. The fallback is for a row assembled anywhere
    else -- the tests build them by hand, and `closed_rows_waiting` builds
    a shape with no rating -- because a slug that is *derived* from the
    board and the number is the same slug either way, and a line printing
    no claim name at all is the one outcome that would quietly leave a row
    unclaimable.
    """
    carried = item.get("slug")
    if carried:
        return carried
    if "number" in item:
        return slug_for_row(item["board"], item["number"])
    return slug_for_capture(item.get("text", ""))


def apply_claims(items, live, my_cycle=None):
    """Stamp `heldBy` on anything another live cycle has already claimed.

    The owner, `comments.md` 2026-08-23 13:31, on moving the heartbeat from 72
    minutes to 18: *"The average cycle is 18min, so we are guaranteed to have
    some paralell cycles run, and i want that."* (He wrote it on the comments
    board, not on `issues.md` -- the bullet on his board is my paraphrase of
    it, and citing the paraphrase as his words is the thing `personality.md`
    keeps telling me not to do.) At 72 minutes this tool could
    name one top row and be sure only one cycle was reading it. At 18 it
    hands the identical row to three cycles at once, and each of them takes
    it, because taking it is what the line says to do.

    `nova_claims` already had the atomic half -- a ledger in the vault with
    CouchDB compare-and-swap under it -- and it only ever covered handoff
    slugs, which is the list a cycle reads *second*. The board is the list
    it reads first.

    Own claims are not held: a cycle that claims a row and then re-runs this
    tool must not be told its own row is taken.

    **`replyHeldBy` is a separate answer to a separate question.** Replying
    to a comment is work this tool tells a cycle to do *whether or not* it
    takes the row, so "somebody is on this row" and "somebody is answering
    this comment" can each be true without the other. Two cycles that both
    read `💬 UNANSWERED` before either replies both reply, and the row
    claim never came into it -- that was the hole this covers.
    """
    for item in items:
        holder = live.get(row_slug(item))
        item["heldBy"] = None if holder is None or holder == my_cycle else holder
        reply = item.get("replySlug")
        holder = live.get(reply) if reply else None
        item["replyHeldBy"] = None if holder is None or holder == my_cycle else holder
    return items


_DATE_RE = re.compile(r"(\d{2})-(\d{2})\s*$")


def age_key(updated):
    """`08-04` and `2026-08-04` have to sort against each other.

    Every row on both live boards writes the short form, and a plain
    string compare puts any full date *below* every short one (`2` > `0`)
    -- so one hand-typed `2026-08-04` would sink the oldest row in its
    rating to the bottom of the list, which is the one place it must
    never be. Both reduce to their trailing `MM-DD`.

    That leaves the year out, and it is left out knowingly: these boards
    began 2026-08-03 and every row is within one year, so a year is not
    yet information. Across a new year `01-05` will sort above `12-30`
    and this needs a real date parse. Filed rather than guessed at,
    because inferring a missing year is a rule that would be wrong
    silently.
    """
    found = _DATE_RE.search(updated or "")
    return found.group(0) if found else "99-99"


def rank(rows):
    """Best pick first. See the module docstring for why age is the tiebreak.

    **An unanswered comment outranks every rating**, including a 🔴 on
    another row. `prompt.md` step 1c already says why in the general
    case -- *"his unprocessed captures are the strongest signal you will
    get all cycle"* -- and a comment on a row is a capture that happens
    to have landed somewhere a cycle was already going to look. It is
    also the one signal here with no other home: a rating persists until
    someone changes it, and an unanswered comment stops existing the
    moment a cycle replies, so nothing is lost by putting it first and a
    question of his is lost by not.

    **And a row blocked on the owner sinks below every actionable one,
    whatever its rating.** The point of this tool is to name the row a
    cycle should take, and a row whose only remaining step is a click in
    a settings page is one no cycle can take at any rating. It is ranked
    down rather than hidden, and `render` names it separately, because
    the failure being fixed is a cycle *skipping* it silently -- issue
    #94 topped this list for five days while every cycle walked past.
    An unanswered comment still beats it: if he has just written on a
    blocked row, that is very likely the thing that unblocks it.
    """
    return sorted(rows, key=lambda r: (
        # **A row another live cycle is holding sinks below everything**,
        # under the unanswered comment as well. Every other key here orders
        # rows by how much they deserve a cycle's hour; this one says the
        # hour is already being spent, which is not a ranking question. It
        # sinks rather than hides for the same reason a blocked row does --
        # `render` names it, and a cycle that cannot see the row cannot
        # notice the claim is wrong.
        1 if r.get("heldBy") else 0,
        # **A comment another cycle is already answering stops raising the
        # row.** The raise exists to make sure somebody replies, so once
        # somebody is, it has done its job -- and leaving it in place points
        # the next two cycles at the same comment, which is the duplicate
        # this claim was added to prevent. The row still ranks on its own
        # rating; it just stops jumping the queue on a question that is
        # being handled.
        # **And a comment that says it was relayed does not jump the
        # queue at all.** His ask, relayed on `issues.md` 2026-08-29:
        # *"a Sokrates comment relaying something [the owner] actually
        # said should not automatically inherit the same 'unread comment
        # from [the owner] jumps the queue, act now' treatment a comment
        # genuinely typed by him gets."* The raise above exists because a question
        # he typed stops existing the moment somebody answers it; a relay
        # is Sokrates deciding what is worth passing on, which is a
        # judgement rather than a fact about what the owner wants now.
        # The row keeps `waiting` and still appears in the reply list, so
        # a reply is still owed -- it just ranks on its own rating like
        # every other row. See `nova_boards.is_relayed` for why acting on
        # a self-declared signal is safe in this direction only.
        0 if r.get("waiting") and not r.get("replyHeldBy")
        and not r.get("relayed") else 1,
        1 if r.get("statusKey") == _BLOCKED else 0,
        _RANK.get(r["priorityKey"], len(_RANK)),
        age_key(r["updated"]),
        0 if r["board"] == "issue" else 1,
        r["number"],
    ))


def next_payload(issues_markdown, ideas_markdown, claims_text, now, top=5):
    """What a cycle waking up now would take, in the order it would take it.

    Three lists, and the order between them is `prompt.md` step 2's, not
    a new opinion: an unprocessed capture of his outranks the board, and
    the board outranks everything else. So `captures` is first and
    unranked -- a bullet he typed has no rating cell to sort on -- and
    `next` is the ranked board underneath it.

    `active` is the third and it is the one that answers the half of his
    sentence about right now: the claims ledger says which rows cycles
    are holding this minute, so a page built from it shows work in
    flight rather than work finished. A stale claim is not live and is
    left out, which is `held_by`'s own rule and not re-decided here.

    `projects` is the same ranked rows grouped by the `Project` cell,
    highest-ranked row first, so "which project is active" is answered by
    the ranking rather than by a cycle asserting it. Every row is in
    exactly one group: `parse_board` fills an empty cell -- and a board
    with no `Project` column at all, which is what my own two files still
    are -- with `nova_boards.DEFAULT_PROJECT`, so there is no unfiled
    bucket to build here and no second opinion about naming one.

    An unreadable ledger is `claimsReadable: false` with the other two
    lists intact, for `top_board_rows`' reason: an empty ledger and an
    unreadable one look identical and mean opposite things, so the page
    has to be able to say which it got.
    """
    captures = (unboarded_captures(issues_markdown, "issues")
                + unboarded_captures(ideas_markdown, "ideas"))
    rows = (open_rows(issues_markdown, "issue")
            + open_rows(ideas_markdown, "idea"))
    claims_readable = True
    live = {}
    try:
        ledger = load_claims(claims_text or "")
        live = held_by(ledger, now)
    except (ClaimError, ValueError):
        claims_readable = False
        ledger = {"claims": []}
    apply_claims(rows, live)
    ranked = rank(rows)

    active = []
    for slug, cycle in sorted(live.items(), key=lambda pair: pair[1], reverse=True):
        titles = [r for r in rows if r["slug"] == slug]
        active.append({
            "item": slug,
            "cycle": cycle,
            # The row's own title when the claim is on a board row, and
            # nothing when it is on a handoff slug or a capture. Those
            # carry their text in the slug hash rather than anywhere
            # readable, so a made-up title here would be a guess printed
            # as a fact.
            "title": titles[0]["title"] if titles else "",
            "board": titles[0]["board"] if titles else "",
            "number": titles[0]["number"] if titles else None,
        })

    projects = []
    seen = {}
    for row in ranked:
        name = row["project"]
        key = name.lower()
        if key not in seen:
            seen[key] = {"name": name, "open": 0, "top": row["title"],
                         "topPriority": row["priority"]}
            projects.append(seen[key])
        seen[key]["open"] += 1

    return {
        "captures": captures,
        "next": ranked[:top],
        "waiting": [r for r in ranked if r["statusKey"] == _BLOCKED],
        "active": active,
        "projects": projects,
        "claimsReadable": claims_readable,
    }
