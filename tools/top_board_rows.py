"""Print the highest-rated open rows on Edvard's two boards, as one line.

Edvard, `comments.md` 2026-08-15, after Cycle 218 went after an infra
question while the only 🔴 Immediately row on either board had been open
three days: *"Why did it skip the issues/ideas with 'immediately'
priority? It seems to me like something is off with your
prioritizations."* Boarded as issue #88.

He is right, and prose is not the fix. `prompt.md` already ranks his
board above the handoff and says so twice, so the gap is not that a
cycle does not know -- it is that a cycle reads "an open item on
Edvard's board", sees 52 of them, and the cheapest one wins. A 🔴 pulls
no harder than a ⚪ because nothing ever puts the two side by side.

So this prints the top of the board as a **named row**, once, at the
start of the cycle:

    python3 -m tools.top_board_rows

Take it, or say in the journal entry why you did not. That is the whole
contract, and it is deliberately the weaker of the two fixes #88 names.
The stronger one -- refusing a claim on anything else while a 🔴 is open
-- narrows what a cycle is allowed to work on, which is the kind of rule
that is wrong in a way you cannot see from inside it. That one is
Edvard's to approve.

Above the ranking sit Edvard's **unprocessed captures** -- the bare
bullets he types above `## Board`, which `prompt.md` step 2 places above
the board, above the handoff and above everything else. They are printed
first and unranked, because a capture has no rating cell to sort on and
because there are never many; see `unboarded_captures`.

Ranking is rating first (Immediately > High > Medium > Low > unrated),
then oldest `Updated` first, then issues before ideas, then row number.
Age is the tiebreak on purpose: two High rows are not equally urgent when one has sat
since 08-04, and "it has been waiting longest" is the only signal left
once the rating is spent.

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

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    BLOCKED_STATUS, BOARD_PATHS, _CLOSED_STATUS_KEYS, parse_board,
    split_capture_done, split_capture_priority, status_key,
    unanswered_comments,
)
from agora_runner.nova_capture import CAPTURE_TARGETS

VAULT_TOOL = "/app/bridge/vault_tool.py"

# Taken from `BOARD_PATHS` rather than typed again. Both of these moved on
# 2026-08-12, from `projects/sokrates/projects/agora/` into Edvard's own
# `projects/sokrates/projects/nova/`, and `nova_boards` carries the note
# about it. A second hand-typed copy of a path that has already moved once
# is a copy that will be wrong the next time it moves -- and wrong here is
# quiet, because a path that does not resolve reads as a board with no
# rows. `_fetch` makes that loud; not duplicating the string means it does
# not have to.
ISSUES_PATH = BOARD_PATHS["issues"]["edvard"]
IDEAS_PATH = BOARD_PATHS["ideas"]["edvard"]
# Edvard's third capture file, and the last one the opening read could only
# reach by hand. It is not a board -- a note is never numbered and never
# rated, so it has no row to rank -- but it carries the same bare-bullet
# contract as the other two, which is why `parse_board`'s capture half reads
# it unchanged. Taken from `CAPTURE_TARGETS` for the same reason the two
# above come from `BOARD_PATHS`: a hand-typed copy of a path that has moved
# once will be wrong the next time it moves.
NOTES_PATH = CAPTURE_TARGETS["notes"]

# Ranking order, best first. Unrated sorts last rather than first: a
# blank cell means nobody has looked, which is a reason to rate it, not
# a reason to work on it ahead of something Edvard called High.
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


def unread_notes(markdown):
    """`notes.md` -> the notes Edvard has left that no cycle has moved.

    The contract is `prompt.md` step 1a's: he writes bare bullets at the
    top, a cycle acts on each and moves it under `## Read` with a line on
    what it did. So "unread" is structural -- everything above the first
    heading -- and `parse_board`'s capture half already finds exactly that,
    frontmatter and cursor bullet excluded.

    A note is not a board row and gets no rating. It is printed with the
    captures rather than ranked, because `rank` sorts on a `Priority` cell
    that a note does not have and never will.
    """
    return [{"board": "note", "priority": "", "text": text}
            for text in parse_board(markdown or "")["captures"]]


def unboarded_captures(markdown, board):
    """The bare bullets above `## Board` -- Edvard talking, unfiled.

    These outrank every row this tool ranks. `prompt.md` step 2 puts an
    unprocessed capture above a live incident, above the board and above
    the handoff; step 1c calls them *"the strongest signal you will get
    all cycle"*. This tool nonetheless could not see them, because
    `open_rows` asked `parse_board` for `items` and dropped the
    `captures` key sitting beside it in the same return value.

    That is not a theoretical gap. Cycle 241 ran this tool, took the row
    it named, and three of Edvard's captures were sitting above the board
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
    for bullet in parse_board(markdown or "")["captures"]:
        done, rest = split_capture_done(bullet)
        if done:
            continue
        priority, text = split_capture_priority(rest)
        captures.append({"board": board, "priority": priority, "text": text})
    return captures


def open_rows(markdown, board):
    """Open rows of one board file, each tagged with which board it is on."""
    rows = []
    waiting = set(unanswered_comments(markdown or ""))
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
        })
    return rows


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

    **And a row blocked on Edvard sinks below every actionable one,
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
        0 if r.get("waiting") else 1,
        1 if r.get("statusKey") == _BLOCKED else 0,
        _RANK.get(r["priorityKey"], len(_RANK)),
        age_key(r["updated"]),
        0 if r["board"] == "issue" else 1,
        r["number"],
    ))


def _line(row):
    rating = row["priority"] or "(unrated)"
    # Ahead of the rating, because it is ahead of it in the sort. A marker
    # that explains the order is worth more than one appended as a footnote
    # to a line whose position it already decided.
    waiting = "💬 UNANSWERED  " if row.get("waiting") else ""
    if row.get("statusKey") == _BLOCKED:
        waiting += "⏸ ON EDVARD  "
    return (f"{row['board']} #{row['number']}  {waiting}{rating}  {row['status']}"
            f"  (updated {row['updated']})  {row['title']}")


def _capture_line(capture):
    # A note carries no rating cell, so printing "(unrated)" beside one
    # would invite a cycle to go and rate something that has nowhere to
    # put a rating.
    if capture["board"] == "note":
        return f"notes.md  {capture['text']}"
    rating = capture["priority"] or "(unrated)"
    text = capture["text"]
    return f"{capture['board']}s.md  {rating}  {text}"


def render(rows, runners_up=3, captures=()):
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
        out.append(f"UNPROCESSED CAPTURES FROM EDVARD ({len(captures)}) — "
                   "these outrank every row below. Take one, or say why not:")
        out.extend("  -> " + _capture_line(c) for c in captures)
        out.append("")
    ranked = rank(rows)
    if not ranked:
        out.append("TOP OF EDVARD'S BOARD — no open rows on either board.")
        return "\n".join(out)
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
    waiting = [r for r in ranked if r.get("waiting")]
    if waiting:
        out.append(f"  {len(waiting)} row(s) waiting on a reply from you: "
                   + ", ".join(f"{r['board']} #{r['number']}" for r in waiting))
        out.append("  Reply on the row (POST /api/board/comment, author Nova) "
                   "even if you do not take it as this cycle's work.")
    # Named, not hidden. The whole reason this status exists is that a row
    # only Edvard can finish was being skipped in silence; sinking it in the
    # ranking without saying so would automate the silence instead of the
    # skip. If one of these has in fact become actionable, the fix is to
    # change its status back, and a cycle can only notice that if it can see
    # the row.
    blocked = [r for r in ranked if r.get("statusKey") == _BLOCKED]
    if blocked:
        out.append(f"  {len(blocked)} row(s) ranked down as blocked on Edvard: "
                   + ", ".join(f"{r['board']} #{r['number']}" for r in blocked))
        out.append("  Nothing for a cycle to build on these. If one is "
                   "actually actionable now, set its status back.")
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
    ap.add_argument("--runners-up", type=int, default=3)
    args = ap.parse_args(argv)

    rows = []
    captures = []
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

    notes_md = open(args.notes, encoding="utf-8").read() if args.notes \
        else _fetch(NOTES_PATH)
    if notes_md is None:
        # Same treatment as a board: said out loud rather than read as
        # "he has left no notes", which is what silence here would mean.
        missing.append(NOTES_PATH)
    else:
        captures.extend(unread_notes(notes_md))

    print(render(rows, runners_up=args.runners_up, captures=captures))
    if missing:
        print("COULD NOT READ: " + ", ".join(missing)
              + " — this ranking is incomplete, read the missing board yourself.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
