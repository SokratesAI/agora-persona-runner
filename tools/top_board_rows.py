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

Ranking is rating first (🔴 > 🟠 > 🔵 > ⚪ > unrated), then oldest
`Updated` first, then issues before ideas, then row number. Age is the
tiebreak on purpose: two 🟠 rows are not equally urgent when one has sat
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

from agora_runner.nova_boards import (
    BOARD_PATHS, _CLOSED_STATUS_KEYS, parse_board, unanswered_comments,
)

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
    """
    return sorted(rows, key=lambda r: (
        0 if r.get("waiting") else 1,
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
    return (f"{row['board']} #{row['number']}  {waiting}{rating}  {row['status']}"
            f"  (updated {row['updated']})  {row['title']}")


def render(rows, runners_up=3):
    ranked = rank(rows)
    if not ranked:
        return "TOP OF EDVARD'S BOARD — no open rows on either board."
    out = ["TOP OF EDVARD'S BOARD — take this, or say in your journal why you did not:",
           "  -> " + _line(ranked[0])]
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
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--issues", help="local issues.md instead of a vault fetch")
    ap.add_argument("--ideas", help="local ideas.md instead of a vault fetch")
    ap.add_argument("--runners-up", type=int, default=3)
    args = ap.parse_args(argv)

    rows = []
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

    print(render(rows, runners_up=args.runners_up))
    if missing:
        print("COULD NOT READ: " + ", ".join(missing)
              + " — this ranking is incomplete, read the missing board yourself.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
