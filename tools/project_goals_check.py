"""Read `project-goals.md` and `milestone-seats.md` and print what is wrong.

Issue #227 step 1's half that makes the model enforceable rather than
merely written down, and the place the issue's fourth rule is delivered:
*"A milestone that serves nothing is one of exactly two things -- keep-the-
lights-on work, which is legitimate and sits under a KPI rather than a
goal, or work nobody can justify, which is the pruning signal. The orphan
list is a deliverable of this job, not a side effect."*

    python3 -m tools.project_goals_check
    python3 -m tools.project_goals_check --goals /tmp/g.md --seats /tmp/s.md

With no arguments it fetches both documents from the vault with
`vault_tool.py`, which only works from the bridge pod; `--goals`/`--seats`
take paths on disk instead, which is how the tests drive it and how a cycle
checks a draft before writing it back.

**Exit codes are its siblings' contract, not its own.** `2` something in
the model is broken (a fourth key result, a KPI with a target, a `Serves`
pointing at nothing), `1` a document could not be read, `0` nothing to act
on. It said `1` for broken and `2` for unreadable until cycle 1536, exactly
inverted, which is `preflight`'s `{0: ok, 1: UNREADABLE, 2: ACT}` read
backwards -- a real defect would have printed as UNREADABLE and a vault it
could not reach as ACT. An **absent** `project-goals.md` is neither broken
nor unreadable: it is the state before step 2 has written any content, so it
prints that and exits 0.

**The orphan list prints and does not raise**, which is what let this into
`tools.preflight` at all. Both used to be one list and exit `1` together;
today 43 seated milestones serve nothing, 32 of them in the eight projects
the owner scoped out until step 4 of issue #227, so a permanently non-clean
check would have been a permanently unread one. It is also not a finding a
pull request can close -- an orphan is either legitimate keep-the-lights-on
work or a pruning signal for him -- which is the same call `security_alerts`
makes on an already-fixed advisory and `argocd_health` on a stale Job
failure. `--orphans` prints the list on its own for when it is the thing you
came for.

**The orphan list prints under two headings, because it holds two
findings.** Rule 4 names exactly two verdicts and the `Keeps` column
separated one of them; the other one is *why the seat is empty*. Measured
Cycle 1555 against the live documents: 36 orphans, and 32 of them sit
under a project with no objective, no key result and no KPI written at
all -- there is nothing there for a seat to point at, so pruning is not
the question and writing that project's goals is. The 4 under a project
that does have goals are the pruning signal, and printed as one block of
36 under one sentence offering two readings they were unfindable.
`ORPHANS` is the short list; `NO GOALS TO SERVE YET` is the rest.
**Nothing is dropped and the total is unchanged** -- the summary prints
36 with the split beside it -- so this narrows what to read, never what
is reported.

**A KPI out of its own range is a section of its own, and it does not
raise.** Issue #227 defines a KPI as a health number *"with a range rather
than a target, for the things that must stay in bounds while the work
happens"* -- and nothing here compared the current value to those bounds
until cycle 1565, so the range was written down and never read. It is an
inventory rather than a defect for `split_orphans`'s reason: the document is
well formed and the *system* is out of bounds, which is a reading to act on
rather than a file to fix. Measured against the live document the day it
landed: one breach, `nova-kpi-silent-cycles` at 6 against a ceiling of 1.

**The pointers are checked in both directions.** `Serves`/`Keeps` are read
from the seat outwards -- an id that resolves to nothing is a defect, and a
seat naming nothing at all is the orphan list above. `unpointed_goals` reads
them from the goals document inwards: a key result no milestone serves is an
outcome nobody is building toward, and a KPI no milestone keeps is a number
on the scoreboard nobody is accountable for. Neither direction implies the
other -- every pointer in the file can resolve while four of the nine goals
have nothing aimed at them, which is what it read the day it was written.
It is an inventory and does not raise, for the orphan list's reason: which
milestone owns a given number is a judgement, not something a diff closes.

**The task end of the chain is checked the same way, one level down.** A
board row naming a milestone with no seat is a defect and raises; a row
under no milestone at all is an inventory and does not raise. Both boards
are read through the site's own API, and a board it could not read prints
`UNREADABLE: the boards` and exits `1` rather than reporting a clean zero
unplaced tasks -- which reads identically to the best possible answer.
"""

import argparse
import datetime
import json
import sys
import urllib.request

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    MILESTONE_SEATS_PATH, parse_milestone_keeps, parse_milestone_serves,
)
from agora_runner.project_goals import (
    PROJECT_GOALS_PATH, PROJECT_GOALS_TEMPLATE, kpi_breaches, parse_project_goals, problems,
    keeps_problems, serves_problems, split_orphans,
    task_seat_orphans,
    unpointed_goals,
    task_seat_problems,
    objective_periods,
    undecided_goals,
    projects_without_goals,
    writeup_readings,
)

#: The two boards the owner's work sits on, read through the site's own API
#: -- the same store `top_board_rows` ranks, one HTTP call away, and
#: reachable from the bridge pod where this check runs under `preflight`.
#: `board_records` is not used here on purpose: it needs `COUCHDB_*`, which
#: the bridge pod does not set, so it would raise on every cycle.
BOARDS = ("issues", "ideas")
SITE = "http://nova-site.agents.svc.cluster.local:8083"


def report(goals_markdown, seats_markdown, rows=None, today=None):
    """`(lines, exit code)` -- the whole judgement, no I/O.

    Split out from `main` so the tests drive the logic rather than a
    subprocess, and so a caller that already holds both documents (the
    project page, when step 4 draws this) does not fetch them twice.

    `rows` is every board row, both boards, and `None` means **not read**
    rather than "no rows". The two are the same value to every function
    below and opposite findings: an unread board would print a clean zero
    unplaced tasks and look like the best possible answer, which is the
    guaranteed-positive trap. So `None` prints that the task half was not
    evaluated, the way `top_board_rows` refuses to stay silent about a
    maintenance reservation it could not judge.

    `today` is the clock rule 7's monthly check is read against, and it is
    defaulted to this box's date only here, at the edge -- `objective_periods`
    itself refuses to guess, so a test cannot quietly pass against a date it
    never chose.
    """
    sections = parse_project_goals(goals_markdown)
    if not sections:
        return ["project-goals.md holds no project section yet "
                "(issue #227 step 2 writes the content)"], 0
    found = problems(sections)
    serves = parse_milestone_serves(seats_markdown)
    keeps = parse_milestone_keeps(seats_markdown)
    broken = serves_problems(serves, sections) + keeps_problems(keeps, sections)
    prunable, finished, awaiting = split_orphans(serves, sections, keeps, rows)
    orphans = prunable + finished + awaiting
    unpointed = unpointed_goals(serves, keeps, sections)
    unseated = [] if rows is None else task_seat_problems(rows, serves)
    unplaced = [] if rows is None else task_seat_orphans(rows)
    past, undated = objective_periods(
        sections, today or datetime.date.today())
    undecided = undecided_goals(sections)
    breaches = kpi_breaches(sections)
    contradicting, older = writeup_readings(goals_markdown)
    missing_goals, projects_on_boards = (
        ([], 0) if rows is None else projects_without_goals(rows, sections))
    defects = found + broken + unseated
    lines = ["BROKEN" if defects else "MODEL HOLDS"]
    for line in defects:
        lines.append(f"  {line}")
    if prunable:
        lines.append(
            f"ORPHANS ({len(prunable)}) -- issue #227's fourth rule, an "
            "inventory rather than a defect, so it does not raise. The "
            "project has goals and this milestone names none of them, so "
            "this is the list rule 4 is asking for:")
        for line in prunable:
            lines.append(f"  {line}")
    if finished:
        lines.append(
            f"NOTHING LEFT TO KEEP ({len(finished)}) -- also rule 4's "
            "pruning list, and also not a defect, but not a question "
            "either: every row under these is closed, so retiring the "
            "milestone drops no open work:")
        for line in finished:
            lines.append(f"  {line}")
    if awaiting:
        lines.append(
            f"NO GOALS TO SERVE YET ({len(awaiting)}) -- also orphans "
            "under rule 4, and also not a defect, but not a pruning "
            "signal either: nothing is written for these projects to "
            "serve. Writing their goals is the action, not pruning them:")
        for line in awaiting:
            lines.append(f"  {line}")
    if unpointed:
        lines.append(
            f"NOTHING POINTS AT ({len(unpointed)}) -- rule 4 read from the "
            "goals side, an inventory rather than a defect, so it does not "
            "raise:")
        for line in unpointed:
            lines.append(f"  {line}")
    if breaches:
        lines.append(
            f"KPIS OUT OF BOUNDS ({len(breaches)}) -- a guardrail is a range "
            "the system has to stay inside, and these do not. An inventory "
            "rather than a defect, so it does not raise: the document is "
            "well formed and the number is the finding:")
        for line in breaches:
            lines.append(f"  {line}")
    if contradicting:
        lines.append(
            f"WRITE-UP CONTRADICTS ITS OWN NUMBER ({len(contradicting)}) -- "
            "the block says one reading, the paragraph under it says another, "
            "and the paragraph carries no date, so nothing orders the two. An "
            "inventory rather than a defect, so it does not raise: which of "
            "the two is current is a judgement:")
        for line in contradicting:
            lines.append(f"  {line}")
    if older:
        lines.append(
            f"WRITE-UP QUOTES AN EARLIER READING ({len(older)}) -- the "
            "paragraph is dated and `now:` is rewritten on every sweep, so "
            "this is the audit trail of a reading already taken and not a "
            "second claim about today. Printed so prose that has gone stale "
            "stays visible; it is not a question anyone has to answer:")
        for line in older:
            lines.append(f"  {line}")
    if past:
        lines.append(
            f"PAST THEIR MONTH ({len(past)}) -- issue #227's seventh rule, "
            "an inventory rather than a defect, so it does not raise. The "
            "month an objective covers has ended and nobody re-cut it:")
        for line in past:
            lines.append(f"  {line}")
    if undated:
        lines.append(
            f"NO MONTH AT ALL ({len(undated)}) -- also rule 7, and also not "
            "a defect: an objective carrying no period is one nothing can "
            "ever report as stale:")
        for line in undated:
            lines.append(f"  {line}")
    if undecided:
        lines.append(
            f"STILL BEING ARGUED ({len(undecided)} of {len(sections)}) -- "
            "issue #227's sixth rule, an inventory rather than a defect, so "
            "it does not raise. These goals are written and nobody has "
            "settled them with him yet, which is not the same state as a "
            "project having a goal:")
        for line in undecided:
            lines.append(f"  {line}")
    if missing_goals:
        lines.append(
            f"PROJECTS WITH NO GOAL ({len(missing_goals)} of "
            f"{projects_on_boards}) -- issue #227's own title, an inventory "
            "rather than a defect, so it does not raise. These projects have "
            "rows on the boards and no objective anywhere:")
        for line in missing_goals:
            lines.append(f"  {line}")
    if rows is None:
        lines.append("PROJECT COVERAGE NOT EVALUATED -- the boards were not "
                     "read, so nothing is claimed about which projects still "
                     "have no goal")
        lines.append("TASKS NOT EVALUATED -- the boards were not read, so "
                     "nothing is claimed about which milestone a row sits "
                     "under")
    elif unplaced:
        lines.append(
            f"UNPLACED TASKS ({len(unplaced)}) -- the task end of issue "
            "#227's chain, an inventory rather than a defect, so it does "
            "not raise:")
        for line in unplaced:
            lines.append(f"  {line}")
    lines.append(f"{len(sections)} project section(s), "
                 f"{len(serves)} seated milestone(s), "
                 f"{len(defects)} model problem(s), "
                 f"{len(orphans)} orphan(s)"
                 + (f" ({len(prunable)} pruning signal, "
                    f"{len(finished)} with nothing left to keep, "
                    f"{len(awaiting)} awaiting project goals)"
                    if orphans else "") + ", "
                 f"{len(unpointed)} unpointed goal(s), "
                 f"{len(breaches)} KPI(s) out of bounds, "
                 f"{len(contradicting)} write-up(s) contradicting their "
                 "own number, "
                 f"{len(older)} quoting an earlier reading, "
                 f"{len(past)} objective(s) past their month, "
                 f"{len(undated)} undated, "
                 f"{len(undecided)} of {len(sections)} project(s) still "
                 "being argued, "
                 + ("tasks not read"
                    if rows is None
                    else f"{len(unplaced)} unplaced task(s)") + ", "
                 + ("project coverage not read"
                    if rows is None
                    else f"{projects_on_boards - len(missing_goals)} of "
                         f"{projects_on_boards} project(s) have a goal"))
    return lines, 2 if defects else 0


def _read(path):
    try:
        return _pathlib.Path(path).read_text(), True
    except OSError:
        return "", False


def _fetch(path):
    import subprocess
    try:
        done = subprocess.run(
            [sys.executable, "/app/bridge/vault_tool.py", "get", path],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return "", False
    if done.returncode != 0:
        return "", False
    text = done.stdout
    # The vault client answers a missing document with this marker and exit
    # 0. Absent is a real, legitimate state here (nothing written yet), so it
    # is read as an empty document rather than as an unreadable one.
    return ("" if "[not found]" in text[:200] else text), True


def _fetch_rows(site=SITE):
    """Every open and closed row on both boards -> `(rows, ok)`.

    Each row is tagged with the board it came from, because a row number is
    only unique inside one board and the finding has to name which `#227`
    it means.

    Read over HTTP rather than through `board_records` deliberately: this
    check runs inside `tools.preflight` on the bridge pod, which holds
    CouchDB credentials under `CDB_*` while `board_records` reads
    `COUCHDB_*` -- so the record store would raise `UnmigratedStore` here
    every single cycle. The site pod holds the credentials and serves the
    same records.
    """
    rows = []
    for board in BOARDS:
        try:
            with urllib.request.urlopen(
                    f"{site}/api/board?name={board}", timeout=60) as response:
                payload = json.loads(response.read())
        except (OSError, ValueError):
            return [], False
        for item in payload.get("items") or []:
            rows.append({**item, "board": board})
    return rows, True


def _rows_from_file(path):
    """A JSON list of rows on disk -> `(rows, ok)`, for the tests and a draft.

    A payload that is not a list reads as unreadable rather than being
    coerced: `list({...})` on the whole board payload would hand back its
    keys, and a check that then reports every one of them as a task with no
    milestone is worse than one that says it could not read the file.
    """
    try:
        payload = json.loads(_pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return [], False
    if not isinstance(payload, list):
        return [], False
    return payload, True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--goals", help="local project-goals.md instead of a fetch")
    ap.add_argument("--seats", help="local milestone-seats.md instead of a fetch")
    ap.add_argument("--rows", help="a JSON list of board rows instead of a "
                                   "fetch from the site")
    ap.add_argument("--orphans", action="store_true",
                    help="print only the milestones that serve no key "
                         "result, one per line, and exit 0")
    ap.add_argument("--scaffold", action="store_true",
                    help="print an empty project-goals.md and exit -- the "
                         "frontmatter contract, no content")
    args = ap.parse_args(argv)

    if args.scaffold:
        # So the first cycle to write content does not retype the contract
        # line, and cannot quietly write a different one. Printing rather
        # than writing keeps this tool read-only against the vault, which is
        # the contract every other checker here holds.
        print(PROJECT_GOALS_TEMPLATE, end="")
        return 0

    goals, ok_goals = (_read(args.goals) if args.goals
                       else _fetch(PROJECT_GOALS_PATH))
    seats, ok_seats = (_read(args.seats) if args.seats
                       else _fetch(MILESTONE_SEATS_PATH))
    for ok, name in ((ok_goals, "project-goals.md"),
                     (ok_seats, "milestone-seats.md")):
        if not ok:
            print(f"UNREADABLE: {name}")
            return 1
    if args.orphans:
        # Grouped, not one flat alphabetical list: this flag exists for
        # when the orphan list is the thing you came for, and four
        # pruning signals interleaved with thirty-two seats that have
        # nothing to serve yet is the state the split fixed in `report`.
        prunable, finished, awaiting = split_orphans(
            parse_milestone_serves(seats), parse_project_goals(goals),
            parse_milestone_keeps(seats))
        for line in prunable:
            print(line)
        for line in finished:
            print(line)
        for line in awaiting:
            print(line)
        return 0
    # Fetched here rather than beside the two documents so `--orphans`, which
    # is a question about the seats file alone, does not pay for an HTTP call
    # it never reads.
    rows, ok_rows = (_rows_from_file(args.rows) if args.rows
                     else _fetch_rows())
    if not ok_rows:
        print("UNREADABLE: the boards")
        return 1
    lines, code = report(goals, seats, rows)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
