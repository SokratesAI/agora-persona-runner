"""Print the highest-rated open rows on the owner's two boards, as one line.

The owner, `comments.md` 2026-08-15, after Cycle 218 went after an infra
question while the only 🔴 Immediately row on either board had been open
three days: *"Why did it skip the issues/ideas with 'immediately'
priority? It seems to me like something is off with your
prioritizations."* Boarded as issue #88.

He is right, and prose is not the fix. `prompt.md` already ranks his
board above the handoff and says so twice, so the gap is not that a
cycle does not know -- it is that a cycle reads "an open item on
the owner's board", sees 52 of them, and the cheapest one wins. A 🔴 pulls
no harder than a ⚪ because nothing ever puts the two side by side.

So this prints the top of the board as a **named row**, once, at the
start of the cycle:

    python3 -m tools.top_board_rows

Take it, or say in the journal entry why you did not. That is the whole
contract, and it is deliberately the weaker of the two fixes #88 names.
The stronger one -- refusing a claim on anything else while a 🔴 is open
-- narrows what a cycle is allowed to work on, which is the kind of rule
that is wrong in a way you cannot see from inside it. That one is
the owner's to approve.

Above the ranking sit the owner's **unprocessed captures** -- the bare
bullets he types above `## Board`, which `prompt.md` step 2 places above
the board, above the handoff and above everything else. They are printed
first and unranked, because a capture has no rating cell to sort on and
because there are never many; see `unboarded_captures`.

Ranking is rating first (Immediately > High > Medium > Low > unrated),
then oldest `Updated` first, then issues before ideas, then row number.
Age is the tiebreak on purpose: two High rows are not equally urgent when one has sat
since 08-04, and "it has been waiting longest" is the only signal left
once the rating is spent.

**Every named line now carries a `[claim: <slug>]`, and a row a live cycle
already holds sinks to the bottom marked 🔒.** The owner is considering moving
the heartbeat from 72 minutes to 18 (`comments.md`, 2026-08-23 13:31), which he says plainly will run cycles in
parallel and that this is wanted. One top row handed to three simultaneous
cycles is three cycles doing it. The ledger and its compare-and-swap are
`agora_runner.nova_claims`, which already existed for handoff slugs; the
board is the list a cycle reads *first*, and it had no slugs at all.

**A comment that says Sokrates relayed it does not jump the queue.** The
owner's ask, relayed on `issues.md` 2026-08-29: *"Sokrates being right
about what [the owner] wants is not the same guarantee as [the owner]
having typed it himself, and the priority system should reflect that
distinction, not collapse it."* The comment API takes `author` as free
text, so a relay arrives signed with his name and every rule here read it
as him. A relayed
comment still counts as waiting and is still listed as owed a reply -- it
just ranks on its own rating instead of above every rating. The signal is
the disclosure sentence Sokrates writes by hand, which proves nothing and
does not need to: it can only ever lower the priority of the text
carrying it. See `nova_boards.is_relayed`.

**A waiting row carries a second slug, `[reply-claim: ...]`, and it is not
the row's.** This tool tells a cycle to answer him *"even if you do not
take it as this cycle's work"* (`prompt.md` step 1a), so replying and
taking the row are different acts and one claim cannot stand for both.
The reply slug is named after the text of his comment rather than the row
number, because `take` refuses a slug that has ever been released as done
-- a row-derived name would make the second question he ever asks on a
row permanently unclaimable. Cycle 343 left this as the last open
collision surface of the three; the other two are closed.

Vault I/O is inside rather than outside, unlike every other tool here,
and that is the point of the tool: an opening read that takes three
commands is one a cycle will skip. `--issues`/`--ideas` take local files
instead, which is how the tests drive it and how the runner pod (which
has no vault client) can use it at all.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    BOARD_PATHS, is_relayed, parse_board, status_key, unanswered_comment_bodies,
)
# The ranking itself lives in `agora_runner` now, not here. The site had to
# be able to import it and could not: `tools/` is not in the image. Same
# functions, one definition -- see `nova_next`'s docstring.
from agora_runner.nova_next import (
    _BLOCKED, _CLOSED, _RANK, _reply_slug, age_key, apply_claims, open_rows,
    rank, row_slug, unboarded_captures,
)
from agora_runner.nova_capture import CAPTURE_TARGETS
from agora_runner.nova_claims import (
    CLAIMS_PATH, ClaimError, finished_claims, held_by, load as load_claims,
    progressed_claims, slug_for_capture,
    slug_for_comment, slug_for_row,
)

VAULT_TOOL = "/app/bridge/vault_tool.py"

# Claim staleness is measured in wall-clock minutes against the `at` stamps
# `tools/claim.py` writes, and those are Oslo (rule 7). Comparing them to a
# naive `now` would raise rather than mislead, which is the safe direction,
# but there is no reason to be within one timezone of correct here.
OSLO = ZoneInfo("Europe/Oslo")

# Taken from `BOARD_PATHS` rather than typed again. Both of these moved on
# 2026-08-12, from `projects/sokrates/projects/agora/` into the owner's own
# `projects/sokrates/projects/nova/`, and `nova_boards` carries the note
# about it. A second hand-typed copy of a path that has already moved once
# is a copy that will be wrong the next time it moves -- and wrong here is
# quiet, because a path that does not resolve reads as a board with no
# rows. `_fetch` makes that loud; not duplicating the string means it does
# not have to.
ISSUES_PATH = BOARD_PATHS["issues"]["edvard"]
IDEAS_PATH = BOARD_PATHS["ideas"]["edvard"]
# The owner's third capture file, and the last one the opening read could only
# reach by hand. It is not a board -- a note is never numbered and never
# rated, so it has no row to rank -- but it carries the same bare-bullet
# contract as the other two, which is why `parse_board`'s capture half reads
# it unchanged. Taken from `CAPTURE_TARGETS` for the same reason the two
# above come from `BOARD_PATHS`: a hand-typed copy of a path that has moved
# once will be wrong the next time it moves.
NOTES_PATH = CAPTURE_TARGETS["notes"]



def _fetch(path):
    """`vault_tool.py get` as text, or `None` if it did not really return one.

    **A missing file is not an error exit.** `vault_tool.py get` prints
    `[not found: <path>]` on stdout and exits **0** (measured, Cycle 220),
    so a return code alone reads a vanished board as an empty one — and an
    empty board contributes no rows, ranks silently, and lets this tool
    print a confident top row chosen from one of two boards. That is the
    exact failure the `COULD NOT READ` line exists to prevent, walking in
    through the door the check was not watching.

    It is not hypothetical either: these two files moved from
    `projects/sokrates/projects/agora/` to `projects/sokrates/projects/nova/`
    on 2026-08-12. The paths come from `BOARD_PATHS` so a move only has to
    be applied once, but "once" still means there is a window, and this is
    what stands between a wrong answer and a loud one inside it.
    """
    try:
        done = subprocess.run([sys.executable, VAULT_TOOL, "get", path],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    if not done.stdout.strip() or done.stdout.lstrip().startswith("[not found:"):
        return None
    return done.stdout


def fetch_claims(path=CLAIMS_PATH):
    """`(ledger_text, readable)` for the claim ledger.

    `_fetch` collapses "the file is not there" and "the read failed" into
    one `None`, and for a board that is right -- neither can be ranked. For
    the ledger the two are opposite answers. **Absent is the normal state**:
    nobody has claimed anything since the last prune, and the correct
    reading is an empty ledger. **Unreadable means the 🔒 marks are missing
    rather than absent**, and a cycle that reads a clean board while another
    cycle holds every row on it is the exact duplication this is here to
    stop. So they are separated, and only the second one is said out loud.
    """
    try:
        done = subprocess.run([sys.executable, VAULT_TOOL, "get", path],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return "", False
    if done.returncode != 0:
        return "", False
    if done.stdout.lstrip().startswith("[not found:"):
        return "", True
    return done.stdout, True


def unread_notes(markdown):
    """`notes.md` -> the notes the owner has left that no cycle has moved.

    The contract is `prompt.md` step 1a's: he writes bare bullets at the
    top, a cycle acts on each and moves it under `## Read` with a line on
    what it did. So "unread" is structural -- everything above the first
    heading -- and `parse_board`'s capture half already finds exactly that,
    frontmatter and cursor bullet excluded.

    A note is not a board row and gets no rating. It is printed with the
    captures rather than ranked, because `rank` sorts on a `Priority` cell
    that a note does not have and never will.
    """
    return [{"board": "note", "priority": "", "text": text,
             # The same two-part reply address the boards get. A note is
             # the one capture target where the reply already renders
             # properly -- the notes page draws an indented bullet as a
             # cycle's own bubble -- so leaving it off here would have
             # withheld the address from the page that reads it best.
             "index": index, "original": text,
             "slug": slug_for_capture(text)}
            for index, text in enumerate(parse_board(markdown or "")["captures"])]


def _reply_claim(row):
    """`  [reply-claim: <slug>]`, or nothing if this row cannot name one.

    `row_slug` derives a missing row slug from the board and the number,
    because it can. This cannot: a reply slug is named after the text of
    the comment, so a row assembled without the markdown it came from has
    no way back to it, and inventing one from the number alone would hand
    two cycles the same name for two different comments -- the exact
    failure the hash is there to prevent.

    Printing nothing is therefore the honest answer, and the guarantee is
    kept where it can be: `open_rows` and `closed_rows_waiting` stamp
    `replySlug` on every waiting row they build, which is pinned by a
    test. Only a row built by hand reaches this fallback.
    """
    slug = row.get("replySlug")
    return f"  [reply-claim: {slug}]" if slug else ""


def closed_rows_waiting(markdown, board):
    """Closed rows whose write-up still ends on one of his comments.

    `open_rows` computes `waiting` for every row and then throws away
    every closed one, so a question asked on a row already marked ✅ Done
    was read out of the file and discarded in the same function. Sokrates
    reported the consequence rather than the cause, `issues.md`
    2026-08-23: a comment left on `ideas #63` on 08-22 flagging that the
    row's Done status looked premature sat through **nine cycles**
    (328-336) with no reply and no change, because *"step 1's read
    genuinely skips comment threads on Done items"*.

    A comment is not a status. Closing a row says the work is finished;
    it says nothing about whether he has been answered, and the case
    where the two disagree is the one that matters most -- a comment on a
    Done row is very often *"this is not actually done"*, which is
    exactly what #63's said.

    These are returned separately and never ranked. `rank` names the row
    a cycle should take, and a closed row is not work at any rating; the
    thing owed here is a reply, which `render` asks for by name. Folding
    them into `rows` would have put a Done row at the top of the pick
    list, which is the opposite failure and just as wrong.
    """
    waiting = unanswered_comment_bodies(markdown or "")
    return [{
        "board": board,
        "number": item["number"],
        "title": item["title"],
        "status": item["status"],
        "updated": item["updated"],
        "waiting": True,
        "relayed": is_relayed(waiting.get(item["number"], "")),
        "replySlug": _reply_slug(board, item["number"], waiting),
    } for item in parse_board(markdown or "")["items"]
        if (item["done"] or item["statusKey"] in _CLOSED)
        and item["number"] in waiting]


def _line(row):
    rating = row["priority"] or "(unrated)"
    # Ahead of the rating, because it is ahead of it in the sort. A marker
    # that explains the order is worth more than one appended as a footnote
    # to a line whose position it already decided.
    if row.get("waiting"):
        # `(relayed)` rides on the same mark rather than getting its own,
        # because it modifies exactly what that mark means: a reply is
        # owed, and this one did not move the row up the list.
        relayed = " (relayed)" if row.get("relayed") else ""
        waiting = (f"🔒 REPLY HELD by cycle {row['replyHeldBy']}  "
                   if row.get("replyHeldBy")
                   else f"💬 UNANSWERED{relayed}  ")
    else:
        waiting = ""
    if row.get("statusKey") == _BLOCKED:
        waiting += "⏸ ON EDVARD  "
    if row.get("heldBy"):
        waiting = f"🔒 HELD by cycle {row['heldBy']}  " + waiting
    # The reply slug is printed only while the reply is actually owed, and
    # it is printed next to the row slug rather than instead of it: they
    # buy different things, and a cycle that wants to answer him without
    # taking the row needs to be able to see which is which.
    reply = (_reply_claim(row)
             if row.get("waiting") and not row.get("replyHeldBy") else "")
    return (f"{row['board']} #{row['number']}  {waiting}{rating}  {row['status']}"
            f"  (updated {row['updated']})  {row['title']}"
            f"{_claim_tag(row)}{reply}")


def _claim_tag(item):
    """`[claim: <slug>]`, or why that command would be refused.

    A slug the ledger already records as `done` is not claimable again,
    so printing the take command beside it is an instruction that cannot
    work. The cycle types it, gets exit 2, and is then holding the one
    fact the line should have carried in the first place -- and exit 2
    reads as "somebody is doing this", which is a different answer from
    "somebody already did this" and is acted on differently.

    So the outcome the releasing cycle wrote is printed inline. That is
    the sentence that decides whether there is still work here, and
    fetching it costs a cycle a shell call and a detour through the
    ledger. Deliberately *not* a reason to skip the item: `prompt.md`
    ranks captures above everything, and a spent claim is a fact about
    the ledger, never a fact about the work.
    """
    # `release --outcome` is free shell text, and this tool's whole output
    # is one item per line -- a newline in there would split the row and
    # read as a second board entry. Same shape as the Outcome pill that
    # rendered as a title the first time a cycle wrote a long one.
    spent = item.get("spentClaim")
    if spent:
        outcome = " ".join((spent.get("outcome") or "no outcome recorded").split())
        return (f"  [⛔ claim spent by cycle {spent['cycle']}: {outcome}"
                f" — work it without claiming]")
    progress = item.get("progressClaim")
    if progress:
        # The take command stays, because `take` really does grant this
        # one -- that is the entire difference from the branch above, and
        # printing ⛔ here would recreate the bug in reverse: a claimable
        # row read as unclaimable.
        outcome = " ".join((progress.get("outcome") or "no outcome recorded").split())
        return (f"  [claim: {row_slug(item)}]"
                f"  🔁 cycle {progress['cycle']} left this open: {outcome}")
    return f"  [claim: {row_slug(item)}]"


def apply_finished(items, finished):
    """Stamp `spentClaim` on anything whose slug is already `done`.

    Separate from `apply_claims` because the two answers are separate:
    `heldBy` sinks a row in the ranking (somebody is on it this minute),
    and this one changes nothing about the order (nobody is on it, and
    whether it is finished is a judgement the reader makes from the
    outcome text).
    """
    for item in items:
        item["spentClaim"] = finished.get(row_slug(item))


def apply_progress(items, progressed):
    """Stamp `progressClaim` on anything a cycle stopped on without finishing.

    Like `apply_finished` this changes nothing about the ranking -- a row
    somebody left open is not a row to skip, it is a row that comes with a
    note saying what is already done. It is a separate pass from
    `apply_finished` for the same reason that one is separate from
    `apply_claims`: the three states are three different answers and only
    one of them makes the take command unrunnable.
    """
    for item in items:
        item["progressClaim"] = progressed.get(row_slug(item))


def _capture_line(capture):
    # A note carries no rating cell, so printing "(unrated)" beside one
    # would invite a cycle to go and rate something that has nowhere to
    # put a rating.
    held = f"🔒 HELD by cycle {capture['heldBy']}  " if capture.get("heldBy") else ""
    # Printed ahead of the rating for the same reason `_line` prints the
    # comment mark there: it is part of why the line sits where it does.
    held += "↩ RELAYED, not typed by him  " if capture.get("relayed") else ""
    claim = _claim_tag(capture)
    if capture["board"] == "note":
        return f"notes.md  {held}{capture['text']}{claim}"
    rating = capture["priority"] or "(unrated)"
    text = capture["text"]
    return f"{capture['board']}s.md  {held}{rating}  {text}{claim}"


def _capture_reply_help(captures):
    """How to answer a capture where he wrote it, with the address filled in.

    Six consecutive handoffs filed *"there is no way to reply on an
    unboarded capture"* -- this tool ranks his bare bullets above every
    boarded row, and `/api/board/comment` is keyed by a row number that a
    capture does not have. `POST /api/capture/comment` is that route
    (Cycle 430), and it takes `index` + `original` instead, which are
    exactly the two fields the page uses for Edit and Delete.

    Printed with the real values rather than as a shape to fill in,
    because a cycle that has to derive the index will do what the last six
    did and write the answer into its journal entry instead.
    """
    out = ["  Answer one where he wrote it — POST http://nova-site.agents.svc.cluster.local:8083/api/capture/comment",
           "  with {\"target\": \"issues\"|\"ideas\"|\"notes\", \"index\": N, \"original\": \"<his bullet, verbatim>\", \"text\": \"...\"}."]
    for capture in captures:
        board = "notes" if capture["board"] == "note" else capture["board"] + "s"
        # `original`, whole -- not `text`. `text` is stripped of the rating
        # glyph and of a `DONE (Cycle N):` marker for display, and a
        # truncation would be worse still: the route matches the bullet
        # exactly, so anything shortened here comes back 409 and the cycle
        # does what the last six did and answers in its journal instead.
        out.append(f"     target {board}, index {capture['index']}  ->  {capture['original']}")
    out.append("  No line break in `text` — it is one indented bullet under his. "
               "Replying is not taking it: claim the row separately if you work it.")
    return out


def _capture_board_help(captures):
    """How to move a capture out of the box and onto the board.

    The owner, `issues.md` 2026-08-27, rated 🔴 Immediately: *"You should
    immediately board 'not boarded yet' ideas and issues. Of you are not
    able to start the work on it, mark it as backlog and give it a
    priority. ... I see so many cycles just letting them be unstaged,
    comments them and moves on."*

    That is the block above, working exactly as written and stopping one
    step short. `_capture_reply_help` prints the filled-in call for
    *answering* a capture, so answering is one copied line; boarding it
    was a hand edit to his document, so every cycle did the cheap correct
    thing and the box grew to twelve. Same shape as `add_row`'s own
    docstring one layer down -- *"a missing button, not a habit"* -- and
    the same fix: print the button.

    **Highest index first, and the line says so**, because
    `capture_entries` renumbers as soon as one is removed and a cycle
    working top-down boards the wrong bullets from the second call on.
    """
    if not captures:
        return []
    out = ["  Board one — python3 -m tools.board_capture --file <his file on disk> "
           "--index N --priority low|medium|high|immediate "
           "--status backlog|in-progress|done|blocked-on-edvard --dated MM-DD",
           "  It adds the row AND cuts the bullet, so the item is in one place. "
           "Board highest --index first: the indices renumber after each cut."]
    for capture in sorted(captures, key=lambda c: -c["index"]):
        board = "notes" if capture["board"] == "note" else capture["board"] + "s"
        out.append(f"     --index {capture['index']}  ({board})  ->  {capture['text'][:70]}")
    out.append("  The caller owns the compare-and-swap: vault_tool.py get --rev-file, "
               "then put --if-rev-file, in ONE Bash call.")
    return out


def _claim_footer(rows, captures, claims_readable):
    """What to type to take what this tool just named, and who has what.

    The instruction is printed unconditionally rather than only when
    something is held, because the whole mechanism fails open: a cycle that
    only claims when it sees somebody else's claim never claims first, and
    every cycle is somebody's first.
    """
    out = []
    if not claims_readable:
        out.append("  ⚠ CLAIMS LEDGER UNREADABLE — the 🔒 marks are missing, not "
                   "absent. Another cycle may be on any row above.")
    held = [i for i in list(captures) + list(rows) if i.get("heldBy")]
    if held:
        # `row_slug`, not `i['slug']` -- the whole point of that helper is that
        # a line printing no claim name is the one outcome that leaves a row
        # unclaimable, and reading the key directly here quietly opted out of
        # its own guarantee. Reviewer finding, PR #301.
        out.append(f"  {len(held)} item(s) held by a live cycle right now: "
                   + ", ".join(f"{row_slug(i)} (cycle {i['heldBy']})" for i in held))
    out.append("  Claim before you work — cycles overlap now: "
               "python3 -m tools.claim take --ledger claims.json "
               "--item <claim slug> --cycle <N>  (see prompt.md step 2)")
    # The page prints 🔁 for a row somebody left open and never named the
    # word that produces one. A reader who learns the state here and not
    # the flag has to go and find it. Reviewer finding on runner#313.
    out.append("  Release it with --done if you finished it, or --progress "
               "--outcome '<what is left>' if you did not; there is no default.")
    return out


def render(rows, runners_up=3, captures=(), closed_waiting=(), claims_readable=True):
    """The captures first, then the ranked board. Never one without the other.

    The alternative the handoff offered was refusing to rank at all while
    a capture is open. That throws away the board to make a point, and
    this loop has a rule against exactly that shape -- keep the data
    whole and let the presentation carry the priority. So both are
    printed, and the "take this" sentence moves onto the captures when
    there are any, because that is where the contract actually points.
    """
    out = []
    if captures:
        # Held captures sink within the section for the same reason held rows
        # sink within the ranking. The section is otherwise unsorted, so this
        # is the only ordering it has ever had.
        # Relayed captures sink above the held ones and below the typed
        # ones. The section still outranks the board -- an unprocessed
        # capture is unprocessed whoever typed it -- so this orders within
        # the section rather than removing anything from it.
        captures = sorted(captures, key=lambda c: (
            1 if c.get("heldBy") else 0, 1 if c.get("relayed") else 0))
        relayed = sum(1 for c in captures if c.get("relayed"))
        # The old header said "FROM EDVARD" of every bullet in the section,
        # which is the collapse his ask names: Sokrates relaying him
        # accurately is still not him. The count is spelled out rather than
        # the header softened for all of them, because most of these really
        # are his and reading them as second-hand would be the same error
        # pointing the other way.
        note = f", {relayed} of them relayed by Sokrates" if relayed else ""
        out.append(f"UNPROCESSED CAPTURES FROM EDVARD ({len(captures)}{note}) — "
                   "these outrank every row below. Take one, or say why not:")
        out.extend("  -> " + _capture_line(c) for c in captures)
        out.append("")
        out.extend(_capture_reply_help(captures))
        out.append("")
        out.extend(_capture_board_help(captures))
        out.append("")
    ranked = rank(rows)
    if not ranked:
        out.append("TOP OF EDVARD'S BOARD — no open rows on either board.")
    else:
        header = ("TOP OF EDVARD'S BOARD — below the captures above:" if captures else
                  "TOP OF EDVARD'S BOARD — take this, or say in your journal why you did not:")
        out.append(header)
        out.append("  -> " + _line(ranked[0]))
        rest = ranked[1:1 + runners_up]
        if rest:
            out.append("  next:")
            out.extend("     " + _line(r) for r in rest)
    # Every waiting row, not just the ones that fit in the runners-up window.
    # Answering him is cheap and the list is short; a row that is waiting and
    # ranked fifth is exactly the one that goes unanswered for three days.
    #
    # Closed rows join this list rather than getting one of their own,
    # because what is owed is identical -- a reply -- and the failure being
    # fixed is a second place a cycle has to remember to look. They carry
    # their status so nobody reads one as a pick; `closed_rows_waiting`
    # keeps them out of the ranking, which is where that distinction is
    # enforced rather than described.
    #
    # A comment another cycle has claimed the reply to is dropped from the
    # list rather than listed with a mark. Everywhere else in this tool a
    # held item is named and sunk, because a row is a *pick* and a cycle
    # that cannot see it cannot notice the claim is wrong. This is not a
    # pick: the line is an instruction to go and type a reply, and printing
    # that instruction next to "somebody else is typing it" is how two
    # replies land. The `🔒 REPLY HELD` mark on the ranked line above is
    # where it stays visible.
    waiting = [r for r in ranked if r.get("waiting") and not r.get("replyHeldBy")]
    held_replies = [r for r in list(ranked) + list(closed_waiting)
                    if r.get("waiting") and r.get("replyHeldBy")]
    # A closed row never reaches `_line`, so this list is the only place its
    # relay mark can appear. Without it the mark would exist on the open half
    # of the fix and silently not on the closed half -- which is the shape
    # `closed_rows_waiting` itself was built to end.
    def mark(row):
        return " (relayed)" if row.get("relayed") else ""
    named = [f"{r['board']} #{r['number']}{mark(r)}{_reply_claim(r)}"
             for r in waiting]
    named += [f"{r['board']} #{r['number']} ({r['status']}){mark(r)}"
              f"{_reply_claim(r)}"
              for r in closed_waiting if not r.get("replyHeldBy")]
    if named:
        out.append(f"  {len(named)} row(s) waiting on a reply from you: "
                   + ", ".join(named))
        out.append("  Reply on the row (POST /api/board/comment, author Nova) "
                   "even if you do not take it as this cycle's work — but claim "
                   "the reply-claim slug first, or two cycles answer him twice.")
    if held_replies:
        out.append(f"  {len(held_replies)} reply(ies) already being written by a "
                   "live cycle: "
                   + ", ".join(f"{r['board']} #{r['number']} (cycle {r['replyHeldBy']})"
                               for r in held_replies))
    if any(not r.get("replyHeldBy") for r in closed_waiting):
        out.append("  The closed ones still need one. A comment on a finished "
                   "row is often 'this is not actually done' — read it before "
                   "you assume the status settles it.")
    # Named, not hidden. The whole reason this status exists is that a row
    # only the owner can finish was being skipped in silence; sinking it in the
    # ranking without saying so would automate the silence instead of the
    # skip. If one of these has in fact become actionable, the fix is to
    # change its status back, and a cycle can only notice that if it can see
    # the row.
    blocked = [r for r in ranked if r.get("statusKey") == _BLOCKED]
    if blocked:
        # "Nothing for a cycle to build on these" used to end this block, and
        # four cycles filed it as a false positive over a ranking they had
        # just been told to take from -- 393, 395, 397 and 398, with 399 and
        # 400 seeing it again. It was never wrong -- "these" meant
        # the blocked rows -- but the pronoun sat two lines under the
        # ranking, and the blocked rows are usually *not* in the printed
        # ranking, because being blocked is what sinks them below the
        # runners-up window. So the only rows the sentence was about were
        # the ones the reader could not see, and the only rows the reader
        # could see were the ones it was not about. Cycle 400 read it as a
        # verdict on four ranked rows while it was talking about `issue #94`
        # -- with `idea #94`, a different board and a different row, sitting
        # second in the ranking directly above it.
        #
        # So: no pronoun, and the rows are printed in full rather than as
        # bare numbers, because a bare number is ambiguous across two boards
        # that share a numbering space.
        out.append(f"  {len(blocked)} row(s) ranked down as blocked on Edvard. "
                   "Nothing for a cycle to build on the row(s) listed here — "
                   "this is not a verdict on the ranking above:")
        out.extend("     " + _line(r) for r in blocked)
        out.append("  If one is actually actionable now, set its status back.")
    out.extend(_claim_footer(ranked, captures, claims_readable))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--issues", help="local issues.md instead of a vault fetch")
    ap.add_argument("--ideas", help="local ideas.md instead of a vault fetch")
    # A local run has to name all three. Naming two and letting the third
    # fall through to the vault is what CI caught: it is green on this box,
    # where `vault_tool.py` exists, and exits 1 anywhere else -- a test that
    # passes for a reason that has nothing to do with what it asserts.
    ap.add_argument("--notes", help="local notes.md instead of a vault fetch")
    ap.add_argument("--claims", help="local claims.json instead of a vault fetch")
    ap.add_argument("--cycle", type=int,
                    help="your own cycle number, so your own claims are not "
                         "reported back to you as somebody else's")
    ap.add_argument("--runners-up", type=int, default=3)
    args = ap.parse_args(argv)

    if args.claims:
        with open(args.claims, encoding="utf-8") as fh:
            claims_text, claims_readable = fh.read(), True
    else:
        claims_text, claims_readable = fetch_claims()
    try:
        ledger = load_claims(claims_text)
        live = held_by(ledger, datetime.now(OSLO))
        finished = finished_claims(ledger)
        progressed = progressed_claims(ledger)
    except ClaimError as exc:
        # A ledger that will not parse is unreadable, not empty. Saying so
        # and carrying on beats refusing to print the board at all: the
        # ranking is still correct, it is only the 🔒 marks that are gone.
        print(f"claims ledger will not parse: {exc}", file=sys.stderr)
        live, finished, progressed, claims_readable = {}, {}, {}, False

    rows = []
    captures = []
    closed_waiting = []
    missing = []
    for board, local, path in (("issue", args.issues, ISSUES_PATH),
                               ("idea", args.ideas, IDEAS_PATH)):
        if local:
            with open(local, encoding="utf-8") as fh:
                text = fh.read()
        else:
            text = _fetch(path)
        # A board that could not be read is said out loud rather than
        # silently ranked as empty -- a top row chosen from one of two
        # boards is exactly the wrong answer wearing the right shape.
        if text is None:
            missing.append(path)
            continue
        rows.extend(open_rows(text, board))
        captures.extend(unboarded_captures(text, board))
        closed_waiting.extend(closed_rows_waiting(text, board))

    notes_md = open(args.notes, encoding="utf-8").read() if args.notes \
        else _fetch(NOTES_PATH)
    if notes_md is None:
        # Same treatment as a board: said out loud rather than read as
        # "he has left no notes", which is what silence here would mean.
        missing.append(NOTES_PATH)
    else:
        captures.extend(unread_notes(notes_md))

    apply_claims(rows, live, args.cycle)
    apply_claims(captures, live, args.cycle)
    # Closed rows are never ranked, so they miss the two calls above -- and a
    # reply owed on a Done row is exactly the one `closed_rows_waiting` was
    # built for (idea #63 sat nine cycles). Left out, it is the one comment
    # two cycles could still both answer.
    apply_claims(closed_waiting, live, args.cycle)
    # Rows and captures only: `closed_waiting` is rendered by its own path
    # that prints the board, the number and the reply claim, and never goes
    # through `_line`. Stamping it would be code that reads as coverage and
    # changes nothing on the page.
    for group in (rows, captures):
        apply_finished(group, finished)
        apply_progress(group, progressed)

    print(render(rows, runners_up=args.runners_up, captures=captures,
                 closed_waiting=closed_waiting, claims_readable=claims_readable))
    if missing:
        print("COULD NOT READ: " + ", ".join(missing)
              + " — this ranking is incomplete, read the missing board yourself.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
