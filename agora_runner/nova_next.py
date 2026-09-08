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

import json
import re

from agora_runner.nova_boards import (
    BLOCKED_STATUS, PROJECT_SATISFACTION_MAX, _CLOSED_STATUS_KEYS,
    boarded_capture_rows,
    capture_match_key, is_relayed, parse_board, parse_project_meta,
    near_miss_done_marker, parse_milestone_pins, split_capture_done,
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

    **A capture already sitting on this board as a row is not one of these
    either**, and that is `boardedAs`. `board_capture` cuts the bullet as
    it writes the row, so the two can only both exist when somebody boarded
    by hand -- which happened twice on 2026-09-05, giving idea #258 and
    issue #189 a closed row and a bare bullet saying the same sentence.
    Cycle 1007 woke to both of them printed above the whole board under
    *"these outrank every row below"*, and both were finished work. A row
    whose status is closed drops the bullet exactly as a DONE marker does;
    an open row keeps it and stamps it, because that work really is open
    and the reader should be told where it already lives rather than have
    it hidden.
    """
    captures = []
    parsed = parse_board(markdown or "")
    boarded = boarded_capture_rows(parsed["items"])
    # `index` counts every bullet in the list, including the finished ones
    # skipped below, because it is the address `/api/capture/comment`
    # resolves against and that route reads the same list unfiltered. A
    # position taken after filtering would answer the wrong bullet.
    for index, bullet in enumerate(parsed["captures"]):
        done, rest = split_capture_done(bullet)
        if done:
            continue
        priority, text = split_capture_priority(rest)
        row = boarded.get(capture_match_key(text))
        # A capture that is already a row on this same board is not
        # unprocessed, whatever the bullet still says. Closed rows drop out
        # here for the same reason a `DONE (Cycle N):` marker does -- see the
        # docstring -- and an open row stays, stamped, because the work is
        # genuinely still open and a cycle may want to take it.
        if row and (row["done"] or row["statusKey"] in _CLOSED):
            continue
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
                         # Which row on this same board carries this exact
                         # sentence as its title, if any. `board_capture`
                         # cuts the bullet as it adds the row, so this is
                         # only ever non-None when somebody boarded by hand.
                         "boardedAs": row,
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
            # The two cells `milestone_ranks` divides one by the other.
            # Carried for the same reason `project` is: the ranking has to
            # be computed off one read of one board, and a tier that reads
            # a field this function drops is a tier that silently does
            # nothing -- which is exactly what happened on the first run
            # of M4, and what the end-to-end test now pins.
            "size": item.get("size") or "",
            "sizeKey": item.get("sizeKey") or "",
            "milestone": (item.get("milestone") or "").strip(),
            # The hand-set seat inside that milestone. Carried for the same
            # reason `milestone` is, and the comment above is the warning:
            # `rank` reads this, so dropping it here would leave the whole
            # ordering tier doing nothing with no symptom on the page.
            "order": item.get("order"),
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


#: The rating that means "ahead of the project order", not "the most
#: urgent row inside its project". The redesign spec calls this tier
#: skip-to-top and says it sits above the ordered project list; today the
#: only lever that exists for it is the row's own Immediately tag, and the
#: spec says so in as many words -- *"the row-level Immediately tag is the
#: real, working lever today"*. So the key below reads that tag rather
#: than inventing a second field for a mechanism nobody can set yet.
_SKIP_TO_TOP = "immediate"


def project_ranks(markdown):
    """`projects.md` -> `{lowercased project name: rank}`, best first.

    The owner rated his projects on 2026-09-01 through the app's project
    picker and **nothing in the picking code has ever read the file.** A
    cycle ranked purely on the row's own tag, flattened across every
    project, so a Medium row in Marcus (which he rates Immediately) and a
    Medium row in a project he rates Low competed as equals. He named that
    disconnect himself as the reason for the redesign in
    `projects/sokrates/projects/nova/task-prioritization-redesign.md`, and
    this is that note's milestone M1.

    An unrated project sorts **last**, not first, which is the same rule
    `_RANK` already applies to an unrated row and for the same reason: a
    project with no row in that file is one nobody has looked at, which is
    a reason to rate it rather than a reason to work on it ahead of one he
    called Immediately. It is deliberately not the same as Low -- the
    file's own contract line says so -- but for ordering there is nowhere
    below Low to put it.

    **A project he has placed by hand outranks every rating**, which is
    milestone M3 of that same note. His words: *"the list of projects... is
    an ordered list where the top one has the highest priority"*. So a
    placed project ranks by its position, and a project with no position
    falls in behind the whole placed list by its rating, exactly as M1
    left it. A file he has never ordered has no positions in it at all and
    therefore behaves the way it did yesterday -- the ordering only starts
    mattering once he has actually said something with it.
    """
    meta = parse_project_meta(markdown or "")
    placed = {name: m["order"] for name, m in meta.items() if m.get("order")}
    # Every unplaced project sorts below every placed one. `max` rather
    # than `len(placed)`: he can hand-edit the cells, and a list numbered
    # 1, 2, 9 must not put an unplaced project between 2 and 9.
    floor = max(placed.values()) if placed else 0
    return {name: (placed[name] if name in placed
                   else floor + 1 + _RANK.get(m["priorityKey"], len(_RANK)))
            for name, m in meta.items()}


#: Job size in whatever unit the divide needs. The ratios are what matter,
#: not the units: an XL is five S's of work, which is the ordinary reading
#: of a t-shirt scale and the one the owner used when he asked for it
#: (*"we might do the smaller ones first"*). An unsized row is absent from
#: this map on purpose -- see `milestone_ranks` for why it is not defaulted.
_SIZE_COST = {"s": 1.0, "m": 2.0, "l": 3.0, "xl": 5.0}

#: Cost of delay, the numerator of the divide. **Deliberately not
#: `len(_RANK) - rank`**, which is the obvious reading of the ratings and
#: is wrong here: on a linear 4/3/2/1 scale against a 1..5 size scale, a
#: single trivial Low row scores 2.0 and a three-row Immediately milestone
#: scores 1.67, so the trivial one wins -- which is the precise failure the
#: spec names (*"size alone would let a trivial milestone nobody needs jump
#: ahead of an important large one"*). It fails that way because the
#: ratings are not linear: Immediately is not twice High, it is the label
#: that means drop the others. So the numerator is spread over the same
#: kind of scale as the denominator. An unrated row scores 1 rather than 0
#: -- zero would make a whole unrated milestone score exactly zero however
#: small it is, which is a stronger statement than "nobody has rated this".
_IMPORTANCE = {"immediate": 13.0, "high": 5.0, "medium": 2.0, "low": 1.0,
               "": 1.0}


def milestone_ranks(rows, pins=None):
    """Open rows -> `{(project, milestone): rank}`, best first, per project.

    Milestone M4 of `task-prioritization-redesign.md`, and the tier that
    sits between the project order and the row's own rating. The spec is
    explicit that this tier is **computed** rather than hand-ordered, and
    that the formula is real WSJF: *"Rank by whatever importance signal the
    milestone carries, divided by its rolled-up size ... size alone would
    let a trivial milestone nobody needs jump ahead of an important large
    one."*

    Importance is the **best rating any open row in the milestone carries**
    and size is the **sum** of its rows' sizes -- rolled up from the rows in
    both cases, which is what the spec asks for on size (*"rolled up from
    their rows rather than separately guessed"*) and the honest reading of
    importance: a milestone containing the one thing he called Immediately
    is an Immediately milestone, and it does not become less urgent by also
    containing three Low rows. Max on one axis and sum on the other is
    deliberate rather than an oversight: importance does not accumulate,
    work does.

    **A milestone with no sized rows at all sorts last**, behind every
    milestone that has one, and is not given a default size. That is the
    same rule `project_ranks` applies to an unrated project and `_RANK`
    applies to an unrated row, for the same reason: an unestimated
    milestone is one nobody has looked at, which is a reason to size it
    rather than a reason to work on it. Defaulting it would be inventing
    the number the divide is most sensitive to.

    **A milestone with some sized rows counts only those.** The alternative
    -- treating an unsized row as free -- makes a half-estimated milestone
    look smaller than a fully estimated one, so the ranking would reward
    not sizing things. Counting only what is known makes it a floor, which
    is the honest direction to be wrong in.

    This returned `{}` for the live files on the day it shipped, because
    every board was ungrouped then and nothing reshuffled -- the same call
    M1 and M3 made about their own fields. That is history now: cycles
    1105, 1108 and 1109 grouped 199 of the 201 open rows across all
    eleven projects -- the two left are Agora rows marked `Outdated`, on
    which an estimate of remaining work would mean nothing -- so this
    returns 57 milestones against the live boards (measured 2026-09-07)
    and the tier is what orders them. A cycle reading the old sentence
    would think the milestone tier was still inert.

    **`pins` is checked after the formula and overrides it**, and it is
    the only state at this tier that is not recomputed from the rows --
    `nova_boards.parse_milestone_pins` reads it out of `milestones.md`.
    The spec puts the override here rather than in the formula on purpose:
    a pin is a decision of his about one milestone, not a thumb on the
    scale that changes what the others score. See `_apply_pins` for what
    "position" means when the pin and the list disagree.
    """
    best = {}
    for row in rows or []:
        name = (row.get("milestone") or "").strip()
        if not name:
            continue
        key = ((row.get("project") or "").strip().lower(), name.lower())
        importance = _IMPORTANCE.get(row.get("priorityKey") or "", 1.0)
        cost = _SIZE_COST.get(row.get("sizeKey") or "")
        carried = best.setdefault(key, {"importance": 0.0, "cost": 0.0})
        carried["importance"] = max(carried["importance"], importance)
        if cost:
            carried["cost"] += cost
    scored = []
    for key, carried in best.items():
        cost = carried["cost"]
        # `(1, 0)` for an unsized milestone: sorted() puts it behind every
        # scored one whatever its importance, which is the rule above.
        scored.append((key, (0, -(carried["importance"] / cost))
                       if cost else (1, 0)))
    scored.sort(key=lambda pair: (pair[1], pair[0]))
    order = [key for key, _ in scored]
    return {key: position
            for position, key in enumerate(_apply_pins(order, pins))}


def _apply_pins(order, pins):
    """Move each pinned milestone to the position he pinned it to.

    The pin is applied **inside its own project** and nowhere else, which
    is what a drag in a per-project list means: position 1 is the top of
    that project's milestones, not the top of all 57. The slots the
    project's milestones occupy in the global list are left exactly where
    they were and only their contents are permuted, so pinning one
    milestone in Marcus cannot reorder Nova's -- and with no pins at all
    this returns `order` unchanged, so the computed ranking is the same
    function it was before pins existed.

    **A pin past the end of its project's list clamps rather than
    disappears.** Milestones close, so a `4` he set when the project had
    four of them means "last" once it has two, and the alternative is a
    decision of his silently ceasing to apply. A pin naming a milestone no
    open row carries is absent from `order` and is therefore ignored here;
    it stays in the file, because the milestone may come back.

    Two pins in one project are applied in ascending order of the position
    he asked for, so the result does not depend on dict iteration order.
    """
    if not pins:
        return order
    wanted = {key: pins[key] for key in order if key in pins}
    if not wanted:
        return order
    out = list(order)
    for project in {key[0] for key in wanted}:
        slots = [i for i, key in enumerate(out) if key[0] == project]
        group = [out[i] for i in slots]
        for key in sorted((k for k in wanted if k[0] == project),
                          key=lambda k: (wanted[k], k)):
            group.remove(key)
            # `list.insert` past the end appends, which is the clamp --
            # a `min()` here would be dead code that reads as the rule.
            group.insert(wanted[key] - 1, key)
        for slot, key in zip(slots, group):
            out[slot] = key
    return out


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


def rank(rows, projects=None, milestones=None):
    """Best pick first. See the module docstring for why age is the tiebreak.

    **`projects` is `project_ranks(projects_markdown)`, and it sits between
    the skip-to-top tier and the row's own rating.** That placement is the
    redesign spec's picking order, not a new opinion: expedite, then
    skip-to-top, then the project order, then the row inside it. Passing
    nothing keeps the flat cross-project ranking every caller had before,
    which is what the site's own project page wants -- it has already
    picked the project, so ordering by project inside it would order
    nothing.

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
        # Skip-to-top: ahead of the project order, which is the whole of
        # what the tier means. A row he has called Immediately is one he
        # wants next regardless of which project it belongs to, and
        # ranking it inside its project would be the flat behaviour this
        # change replaces, wearing the new shape.
        0 if r["priorityKey"] == _SKIP_TO_TOP else 1,
        (projects or {}).get((r.get("project") or "").lower(), len(_RANK)),
        # **The milestone tier, inside the project the tier above just
        # chose.** `milestones` is `milestone_ranks(rows)` and passing
        # nothing keeps every caller's previous ordering, exactly the way
        # `projects` does -- the site's own project page has already picked
        # a project and wants the flat list inside it.
        #
        # An ungrouped row sorts **behind every grouped one in its own
        # project** and not behind the whole board: the key is the
        # (project, milestone) pair, so `len(milestones)` is past the last
        # milestone anywhere but every row of a better-ranked project has
        # already been separated by the line above. Same rule as an
        # unsized milestone and an unrated row -- ungrouped sinks.
        (milestones or {}).get(
            ((r.get("project") or "").strip().lower(),
             (r.get("milestone") or "").strip().lower()),
            len(milestones or {})),
        # **The hand-set position inside the milestone, and the rating only
        # underneath it.** His capture of 2026-09-08: *"Convert the old
        # priority to the ordered list so high is at the top and low is at
        # the bottom."* Same layering as the project tier three keys up --
        # a seat he set by hand is a decision and a rating is a
        # description -- and the same fallback, so a group he has never
        # dragged ranks exactly as it does today. An unplaced row sinks
        # below every placed one **in its own milestone** rather than below
        # the whole board: the tier above has already separated the groups,
        # so an unplaced row only ever competes with its own neighbours.
        (0, r["order"]) if r.get("order") else (1, 0),
        _RANK.get(r["priorityKey"], len(_RANK)),
        age_key(r["updated"]),
        0 if r["board"] == "issue" else 1,
        r["number"],
    ))


def next_payload(issues_markdown, ideas_markdown, claims_text, now, top=5,
                 projects_markdown="", milestones_markdown=""):
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

    `projects_markdown` is `projects.md`, his own rating of the projects
    themselves, and it orders the board between the skip-to-top tier and
    the row rating -- see `rank`. Passing nothing is the flat ranking this
    function had before, so a caller that has not got the file still gets
    an answer rather than an exception; the tool that prints this for a
    cycle says out loud when it could not read it.

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
    # His pins go in here, not just into `tools.top_board_rows`. The
    # picker read `milestones.md` from the day the store shipped and
    # this call did not, so a milestone he pinned moved the terminal
    # ranking and left the page he pinned it on showing the old one --
    # two answers to one question, which is the drift this module
    # exists to avoid. Defaulting to `""` keeps every caller that has
    # no pins to pass byte-identical to what it was.
    ranked = rank(rows, project_ranks(projects_markdown),
                  milestone_ranks(rows,
                                  parse_milestone_pins(milestones_markdown)))

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


#: The satisfaction score at or below which the spec forces a diagnosis.
#: `task-prioritization-redesign.md`, the Satisfaction section: *"Score ≤2
#: auto-generates a skip-to-top task: 'diagnose low satisfaction on
#: [project].'"* Two, not one, and it is his number rather than a threshold
#: I picked -- the field is five points wide and he named the bottom two of
#: them as the range where something is wrong enough to drop other work.
LOW_SATISFACTION_AT = 2


def load_diagnoses(text):
    """The diagnosis log -> `{lowercased project: newest row}`.

    The log is the durable half of the spec's second sentence about this
    field: *"If satisfaction is still ≤2 after a diagnosis already ran
    once, surface that persistence visibly rather than silently
    re-triggering an identical diagnostic loop."* Without a record of what
    already ran, a low score forces the same diagnosis every cycle
    forever, which is the loop that sentence exists to forbid.

    **It is deliberately not the claim ledger**, which is the obvious
    place and the wrong one: `tools/claim.py` prunes a done row after
    `DONE_KEEP_HOURS`, so a diagnosis would stop having happened about a
    day after it did, and the persistence rule would then fire as a fresh
    alarm. A record that expires cannot answer a question about the past.

    Malformed text reads as an empty log rather than raising. A log that
    will not parse means "I do not know what has already run", and the
    honest behaviour then is to show the forced task -- the caller says
    out loud that the log was unreadable, and a diagnosis run twice costs
    an hour where one never run costs a project he has told me is bad.
    """
    try:
        data = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for row in data.get("diagnoses") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("project") or "").strip()
        if not name:
            continue
        out[name.lower()] = row
    return out


def diagnosis_slug(project):
    """A project name -> the claim slug for diagnosing it.

    Its own namespace rather than the project name alone: `marcus` would
    collide with any board row that ever gets that slug, and the two are
    different work. Spaces become dashes for the same reason every other
    slug here has none -- the ledger's `--item` is a shell argument.
    """
    return "diagnose-" + re.sub(r"[^a-z0-9]+", "-",
                                (project or "").strip().lower()).strip("-")


def low_satisfaction(project_meta, diagnoses=None):
    """Projects he has scored `LOW_SATISFACTION_AT` or below, worst first.

    Each entry carries `diagnosed`, which is the log row for a diagnosis
    that already ran, or `None`. A caller renders those two differently on
    purpose: an undiagnosed one is the forced task the spec asks for, and a
    diagnosed one is the *persistence* the spec asks to be surfaced rather
    than re-run.

    **Unrated is not low.** `parse_project_satisfaction_cell` returns `0`
    for a project he has never scored, and `0 <= 2` is true, so reading the
    number without this guard would force a diagnosis on every project on
    the board the day the column appears. That distinction is the reason
    the field keeps unrated and 1 apart at the cell, in the payload and on
    screen; collapsing it here would throw that away one layer down.

    **It does not look at TRL and it never will.** The spec: *"It must NOT
    auto-touch TRL or lifecycle; a satisfaction drop doesn't imply the
    engineering got less proven."*
    """
    diagnoses = diagnoses or {}
    out = []
    for key, meta in (project_meta or {}).items():
        score = meta.get("satisfaction") or 0
        if not score or score > LOW_SATISFACTION_AT:
            continue
        out.append({"project": meta.get("project") or key,
                    "score": score,
                    "max": PROJECT_SATISFACTION_MAX,
                    "slug": diagnosis_slug(meta.get("project") or key),
                    "diagnosed": diagnoses.get(key)})
    return sorted(out, key=lambda d: (d["score"], d["project"].lower()))


#: The projects the maintenance reservation forces the project tier onto.
#: Two names rather than one because the boards use both and they mean the
#: same kind of hour: `Infra` is the cluster and the box, `Maintenance` is
#: this loop's own upkeep. Lowercased to match `project_ranks`, which keys
#: on the lowercased project name.
MAINTENANCE_PROJECTS = ("infra", "maintenance")

#: Milestone M6 of `task-prioritization-redesign.md`: every Nth cycle the
#: project tier is forced to maintenance regardless of where those projects
#: sit in the owner's hand-ordered list.
#:
#: **N is 5, and the spec's own note about it was measured from one
#: instrument and is wrong.** It says *"1 in 5 would be a raise, not a
#: floor"*, from 13.3% of the 98 journal entries in cycles 965-1067 naming
#: an Infra or Maintenance board row -- and names its own blind spot in the
#: next paragraph: 36.7% of those cycles name no board row at all and were
#: never classified. Classified, cycle 1102, over cycles 965-1101 (128
#: entries): 14 of the 91 boarded ones are Infra or Maintenance (15.4%),
#: and **34 of the 37 unboarded ones are maintenance in substance** -- a
#: disk filling, an instrument reading the wrong node, a CI workflow red on
#: main, a roller that would not roll. That is 48 of 128, **37.5%**. So one
#: cycle in five is a floor well under current behaviour rather than a tax
#: on top of it, which is what a reservation is for: it does not cap the
#: other four cycles, it only stops a run of feature work from crowding
#: maintenance out entirely.
MAINTENANCE_EVERY = 5


def maintenance_reserved(cycle, every=MAINTENANCE_EVERY):
    """Is `cycle` one of the reserved maintenance cycles?

    `None` is not reserved, and that is deliberate rather than a default:
    a caller that does not know which cycle it is cannot know whether this
    one is reserved, and firing on a guess would force maintenance at a
    rate nobody chose. `top_board_rows` says so on the page instead.
    """
    if cycle is None:
        return False
    if every <= 0:
        raise ValueError("the reservation cadence must be a positive number "
                         "of cycles")
    return cycle % every == 0


def maintenance_queue(rows):
    """The rows a reserved cycle could actually take.

    A row another cycle holds is not in the queue, and neither is one
    blocked on the owner: the reservation exists to spend an hour on
    maintenance, and a row no cycle can take is not an hour of anything.
    That is the same rule `rank` applies to those two flags one tier down;
    applying it here as well is what makes "falls through when the queue is
    empty" mean *empty of work* rather than *empty of rows*.
    """
    return [r for r in rows
            if (r.get("project") or "").strip().lower() in MAINTENANCE_PROJECTS
            and not r.get("heldBy")
            and r.get("statusKey") != _BLOCKED]


def reserve_maintenance(projects, rows, cycle, every=MAINTENANCE_EVERY):
    """Force the project tier onto maintenance, or say why it did not.

    Returns `(ranks, note)`. `note` is `None` when nothing was forced and a
    sentence when something was, or when the reservation was due and fell
    through -- the caller prints it. **The reservation is a rewrite of the
    project rank map and nothing else**, which is exactly the tier the spec
    puts it in: an expedite or a skip-to-top row still wins, because those
    keys sort above the project one in `rank`, and the ordering *inside*
    maintenance is untouched.

    **Falling through when the queue is empty is the spec's rule, and it is
    reported rather than silent.** A reserved cycle that finds no
    maintenance row should carry on with the ordinary pick; a cycle that is
    never told the reservation fired at all cannot tell that apart from a
    reservation that does not work.
    """
    if not maintenance_reserved(cycle, every):
        return projects, None
    queue = maintenance_queue(rows)
    if not queue:
        return projects, (
            f"🔧 RESERVED MAINTENANCE CYCLE (every {every}th, this is cycle "
            f"{cycle}) — but no open, unheld, unblocked row is filed under "
            + " or ".join(p.capitalize() for p in MAINTENANCE_PROJECTS)
            + ", so the ranking below is the ordinary one.")
    ranks = dict(projects or {})
    # Below every forced project and above every other one, whatever the
    # owner's order says. Ranks can be any comparable number, so going
    # *under* the existing floor keeps his order intact underneath rather
    # than renumbering a file this function must not touch.
    floor = min(ranks.values()) if ranks else 0
    for offset, name in enumerate(MAINTENANCE_PROJECTS):
        ranks[name] = floor - len(MAINTENANCE_PROJECTS) + offset
    return ranks, (
        f"🔧 RESERVED MAINTENANCE CYCLE (every {every}th, this is cycle "
        f"{cycle}) — the project tier is forced to "
        + " and ".join(p.capitalize() for p in MAINTENANCE_PROJECTS)
        + f", ahead of the owner's project order. {len(queue)} row(s) are in "
        "that queue. Take the top row below; it is a maintenance row.")


def project_milestones(rows, project, pins=None):
    """One project's milestones, in the order `milestone_ranks` puts them.

    The rendering half of milestone M4 of
    `task-prioritization-redesign.md`. `milestone_ranks` above computes a
    position for all 57 milestones across every project and applies his
    pins to it; `tools.top_board_rows` and the picker read that map, and
    until this function existed nothing drew it. So he could pin a
    milestone -- `tools.milestone_pin`, or `POST /api/milestone/pin` --
    without ever having seen the list he was pinning inside, which is a
    control with no dial next to it.

    The order comes straight out of `milestone_ranks`, filtered to this
    project and sorted by the rank it assigned, so the page and the
    picker can never disagree about where a milestone sits. Computing a
    per-project order here instead would be the second answer to one
    question that this repo keeps paying for.

    `pin` is the 1-based position he pinned this milestone to, or `0` for
    one he has never pinned -- the same `0`-means-unset the project order
    uses in `projectPriority`, and the same `0` that `POST
    /api/milestone/pin` accepts as "back to the computed order". A pin is
    reported as he wrote it, not as it landed: `_apply_pins` clamps a pin
    past the end of a shrinking list, and showing the clamped number
    would quietly rewrite his file on the next press of a button that
    echoes what the page displayed.

    `name` is the spelling on the rows rather than the lowercased key,
    because that is what he typed into the cell and what the board shows;
    the first row that names it wins, the same rule `board_projects`
    applies to a project name.
    """
    wanted = (project or "").strip().lower()
    if not wanted:
        return []
    ranks = milestone_ranks(rows, pins)
    spelling = {}
    counts = {}
    for row in rows or []:
        name = (row.get("milestone") or "").strip()
        if not name:
            continue
        if (row.get("project") or "").strip().lower() != wanted:
            continue
        key = (wanted, name.lower())
        spelling.setdefault(key, name)
        counts[key] = counts.get(key, 0) + 1
    out = []
    for key in sorted(spelling, key=lambda k: (ranks.get(k, len(ranks)), k)):
        out.append({
            "name": spelling[key],
            "open": counts[key],
            "pin": int((pins or {}).get(key) or 0),
        })
    return out
