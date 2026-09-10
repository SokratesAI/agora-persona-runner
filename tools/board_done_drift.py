"""Does the claim ledger say a board row is finished while the board says it is open?

    python3 -m tools.board_done_drift

**The row at the top of the owner's board was finished work for five
days.** Idea #152 asked for multi-conversation support in the chat modal;
Cycle 759 shipped the second half of it on 2026-09-01 and wrote *"Marking
this Done -- both halves of what you wrote are shipped"* into the row's own
write-up. It never moved the `Status` cell. So `tools.top_board_rows` --
the instrument that decides what every cycle works on -- printed
`-> idea #152 ... take this, or say in your journal why you did not` to
every cycle from 09-01 to 09-06, and each of them had to open the write-up
and re-derive that there was nothing there.

That is not the first time. Idea #162's own write-up carries the
diagnosis, written by Cycle 696 on 08-31: *"I finished this row on 08-29
... and wrote 'Marking this Done' in the note above, and then never moved
this cell. So it sat at Backlog and top_board_rows named it as the top of
your board for two days running."* That cycle checked whether it was a
class, found one other instance, and wrote *"One instance is not a class,
so I did not build a check for it"* -- and then did not move #162's cell
either. It still reads Backlog today, eight days on.

**What makes it a check now is that the signal stopped being prose.**
`prompt.md` step 2 requires a cycle to claim a board row before working it
and to release the claim with `--done` or `--progress`, and it says what
the two mean: a `--done` slug can never be claimed again, `--progress`
means the work is still open. So a `done` claim on `idea-152` is this
loop's own structured assertion that the row is finished, written under
compare-and-swap into a file every cycle rewrites. The `Status` cell is
the same assertion in the place the owner reads. Nothing compared them.

`tools/close_done_captures.py` is this exact reconciliation one file over
-- it closes the owner's *unboarded captures* from the same ledger, built
Cycle 487 after 17 of 21 finished captures sat unmarked in the box he
types into. Boarded rows had no equivalent. **Measured on the live ledger
and both live boards, 2026-09-06 18:12 Oslo: five rows carry a `done`
claim and an open cell** -- idea #188 (Cycle 1055), issues #163, #165 and
#168 (Cycles 1067, 1068, 1070, all within the last two hours) and issue
#131 (Cycle 989). Four of the five were claimed and closed the same day,
so this is the loop's current habit and not an old scar.

**Three buckets, and only one of them raises, because the question is
not "do the two records match" -- it is "will this row be handed to
another cycle as work".** `CLAIMED DONE, CELL OPEN` is the one above and
it raises. `CLAIMED DONE, BLOCKED ON EDVARD` prints and does not raise:
`top_board_rows` ranks a blocked row out of the ranking entirely -- its
own report says *"20 row(s) ranked down as blocked ... Nothing for a
cycle to build on the row(s) listed here"* -- so nothing is being
re-offered, and the state is usually deliberate: issue #131's own claim
outcome says the row was moved there with the full measurement on it. `CELL CLOSED, CLAIM
OPEN` is the mirror -- a row Done on the board whose newest claim was
released `--progress` -- and it does not raise either, because a later
cycle closing a row it did not claim is legitimate and common (issues #30
and #139 are both in that state today and both correctly Done). Both
print, so a bucket that stops being harmless is visible rather than
suppressed.

**The rows come out of the record store, not out of his markdown**
(issue #203, since 2026-09-10). `board_contents` below is the one door.
The claims ledger is the only document this check still fetches, and a
ledger is not a board -- so the two 700KB board files are not read here at
all any more, and a store that will not answer is reported as unreadable
rather than ranked as a board with nothing on it.

**The window is the ledger's, and it is about a day.** `tools.claim prune`
drops claims marked done, so the ledger holds roughly the last 24 hours --
30 board claims when this was written. That is deliberately enough: the
failure this catches is a row sitting at the top of the board for days,
and catching it the same day is the whole fix. A row whose claim has
already been pruned is invisible here and always will be, which is why
#152 and #162 had to be closed by hand rather than found by this.
"""

import argparse
import json
import re
import subprocess
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records  # noqa: E402
from agora_runner.nova_claims import CLAIMS_PATH  # noqa: E402

VAULT_TOOL = "/app/bridge/vault_tool.py"

#: `idea-152` / `issue-88` -- the slug shape `tools.top_board_rows` prints in
#: square brackets and `prompt.md` step 2 tells a cycle to claim. Anything
#: else in the ledger (`journal-seq-1136`, an invented slug for work that came
#: out of my own head) names no row and is skipped rather than guessed at.
SLUG_RE = re.compile(r"^(idea|issue)-(\d+)$")

#: The two `statusKey` values `nova_boards` treats as closed. Kept as a
#: literal rather than imported from `_CLOSED_STATUS_KEYS`, which is private
#: to that module.
CLOSED_KEYS = frozenset({"done", "outdated"})

#: The claim slug says `idea`/`issue`; the key this module files rows under
#: says `ideas`/`issues`; the record store says `idea`/`issue` again. One
#: map here
#: rather than a `[:-1]` at the call site, because "drop the trailing s" is a
#: rule about English that happens to hold for two words.
RECORD_BOARD = {"ideas": "idea", "issues": "issue"}


#: Not closed, but not offered as work either -- `tools.top_board_rows` ranks
#: these out of the ranking, so a `done` claim against one is not the failure
#: this check exists to catch. Reported in its own bucket, never raised.
BLOCKED_KEY = "blocked-on-edvard"


def _fetch(path):
    """`vault_tool.py get` as text, or `None` if it did not really return one.

    Same shape as `roll_health._fetch` and `doc_integrity._fetch`, for the
    same measured reason: `get` prints `[not found: <path>]` on stdout and
    exits 0, so a return code alone reads a vanished document as an empty
    one -- which here would report a board with no rows as a board with no
    drift.
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


def newest_board_claims(ledger):
    """`{(board, number): claim}` -- the newest claim per board row.

    `ledger` is the parsed `claims.json`. A slug can appear more than once:
    a row taken, released `--progress`, then taken again by a later cycle
    leaves two rows in the file, and only the last one says what the loop
    currently believes. Ordered by the claim's own `at` stamp rather than
    by position, because the ledger is appended to by three overlapping
    cycles and position is not time.
    """
    newest = {}
    for claim in (ledger or {}).get("claims", []):
        if not isinstance(claim, dict):
            continue
        found = SLUG_RE.match(str(claim.get("item", "")))
        if not found:
            continue
        key = (found.group(1) + "s", int(found.group(2)))
        previous = newest.get(key)
        if previous is None or str(claim.get("at", "")) >= str(previous.get("at", "")):
            newest[key] = claim
    return newest


def board_contents(board, store=None):
    """One of his boards as the four keys the board parser used to return.

    Issue #203's switchover, for this tool. The rows come out of the record
    store rather than out of a 700KB markdown table read through
    `vault_tool.py get`, so this check no longer fetches either board file
    at all -- the claims ledger is the only document it still reads, and
    that is not a board.

    `board` is the plural name this module keys claims by; `RECORD_BOARD`
    maps it to the singular the store uses.

    `store=None` rather than the real store as a default argument: a
    default binds its value at import, so `board_records.board_store`
    written there would be the object this module captured and a test
    replacing it would be replacing something nothing reads. Same reason as
    `tools.top_board_rows.board_contents` and `tools.milestone_pin`.

    **Every failure raises**, and `check` files the board under
    `unreadable`, which exits 1. An unmigrated store, a board CouchDB will
    not answer for: reading either as a board with no rows would report "no
    drift" over a board this tool never saw, which is the one answer worse
    than no answer here.
    """
    return board_records.contents(RECORD_BOARD[board],
                                  store=store or board_records.board_store)


def check(fetch=_fetch, boards=("ideas", "issues"), contents=board_contents):
    """`(findings, blocked, mirrors, unreadable, swept)`.

    A `findings` entry is `(board, number, status, claim)` -- a row whose
    newest claim is `done` and whose cell is neither closed nor blocked.
    `blocked` is the same tuple for a `done` claim against a row blocked on
    the owner, and `mirrors` for the opposite disagreement; both print and
    neither raises. `unreadable` names the claims ledger if it did not
    come back, and any board the record store would not answer for.
    `swept` is the number of rows that carried a claim at all, so "no drift"
    can never be confused with "no claims in the window".
    """
    findings, blocked, mirrors, unreadable = [], [], [], []
    raw = fetch(CLAIMS_PATH)
    if raw is None:
        return findings, blocked, mirrors, [CLAIMS_PATH], 0
    try:
        ledger = json.loads(raw)
    except ValueError:
        return findings, blocked, mirrors, [CLAIMS_PATH], 0
    claims = newest_board_claims(ledger)
    swept = 0
    for board in boards:
        try:
            items = contents(board)["items"]
        except Exception as exc:  # noqa: BLE001 -- see `board_contents`
            # Named with the reason: an unmigrated store and a CouchDB that
            # will not answer are the same exit code and different fixes.
            unreadable.append(f"{board} board records "
                              f"({exc.__class__.__name__}: {exc})")
            continue
        for item in items:
            claim = claims.get((board, item["number"]))
            if claim is None:
                continue
            swept += 1
            closed = item["statusKey"] in CLOSED_KEYS
            row = (board, item["number"], item["status"], claim)
            if claim.get("state") == "done" and not closed:
                if item["statusKey"] == BLOCKED_KEY:
                    blocked.append(row)
                else:
                    findings.append(row)
            elif claim.get("state") != "done" and closed:
                mirrors.append(row)
    return findings, blocked, mirrors, unreadable, swept


def _line(board, number, status, claim):
    word = "idea" if board == "ideas" else "issue"
    outcome = str(claim.get("outcome") or "").strip()
    return (f"    {word} #{number} — cell reads {status!r}, claim released "
            f"{claim.get('state')!r} by cycle {claim.get('cycle')} at "
            f"{str(claim.get('at', ''))[:16]}"
            + (f" — {outcome}" if outcome else ""))


def report(findings, blocked, mirrors, unreadable, swept, out=sys.stdout):
    for path in unreadable:
        print(f"COULD NOT READ — {path}", file=out)
    if findings:
        print(f"CLAIMED DONE, CELL OPEN — {len(findings)} row(s) this loop "
              "recorded as finished are still on the owner's board as open "
              "work, so `tools.top_board_rows` will offer them again.",
              file=out)
        for finding in findings:
            print(_line(*finding), file=out)
        print("    Fix each with `python3 -m tools.board_status --file <board> "
              "--number <n> --status done --dated <MM-DD> --note '<what closed "
              "it>' --cycle <N>`, inside the usual get --rev-file / put "
              "--if-rev-file pair. If the row is genuinely not finished, the "
              "claim was the wrong half: say so on the row.", file=out)
    if blocked:
        print(f"CLAIMED DONE, BLOCKED ON EDVARD — {len(blocked)} row(s) carry "
              "a done claim and sit blocked on the owner. This prints and "
              "does not raise: `top_board_rows` ranks a blocked row out of "
              "the ranking, so nothing is being re-offered.", file=out)
        for row in blocked:
            print(_line(*row), file=out)
    if mirrors:
        print(f"CELL CLOSED, CLAIM OPEN — {len(mirrors)} row(s) are Done on "
              "the board with a claim that was released --progress. This "
              "prints and does not raise: a later cycle closing a row it "
              "never claimed is normal.", file=out)
        for mirror in mirrors:
            print(_line(*mirror), file=out)
    if unreadable:
        print(f"Could not read {len(unreadable)} document(s) — that is no "
              "instrument, not no drift.", file=out)
        return 1
    if findings:
        print(f"{len(findings)} row(s) drifted, of {swept} board row(s) "
              "carrying a claim in the ledger's window (about a day).",
              file=out)
        return 2
    print(f"No row is offered as work that this loop already recorded as "
          f"finished, over the {swept} board row(s) carrying a claim in the "
          f"ledger's window; {len(blocked)} done claim(s) sit on a blocked "
          f"row and {len(mirrors)} row(s) were closed by a cycle that did "
          "not claim them.", file=out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.parse_args(argv)
    return report(*check())


if __name__ == "__main__":
    sys.exit(main())
