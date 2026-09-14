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

**The task end of the chain is checked the same way, one level down.** A
board row naming a milestone with no seat is a defect and raises; a row
under no milestone at all is an inventory and does not raise. Both boards
are read through the site's own API, and a board it could not read prints
`UNREADABLE: the boards` and exits `1` rather than reporting a clean zero
unplaced tasks -- which reads identically to the best possible answer.
"""

import argparse
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
    PROJECT_GOALS_PATH, PROJECT_GOALS_TEMPLATE, parse_project_goals, problems,
    keeps_problems, serves_orphans, serves_problems, task_seat_orphans,
    task_seat_problems,
)

#: The two boards the owner's work sits on, read through the site's own API
#: -- the same store `top_board_rows` ranks, one HTTP call away, and
#: reachable from the bridge pod where this check runs under `preflight`.
#: `board_records` is not used here on purpose: it needs `COUCHDB_*`, which
#: the bridge pod does not set, so it would raise on every cycle.
BOARDS = ("issues", "ideas")
SITE = "http://nova-site.agents.svc.cluster.local:8083"


def report(goals_markdown, seats_markdown, rows=None):
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
    """
    sections = parse_project_goals(goals_markdown)
    if not sections:
        return ["project-goals.md holds no project section yet "
                "(issue #227 step 2 writes the content)"], 0
    found = problems(sections)
    serves = parse_milestone_serves(seats_markdown)
    keeps = parse_milestone_keeps(seats_markdown)
    broken = serves_problems(serves, sections) + keeps_problems(keeps, sections)
    orphans = serves_orphans(serves, sections, keeps)
    unseated = [] if rows is None else task_seat_problems(rows, serves)
    unplaced = [] if rows is None else task_seat_orphans(rows)
    defects = found + broken + unseated
    lines = ["BROKEN" if defects else "MODEL HOLDS"]
    for line in defects:
        lines.append(f"  {line}")
    if orphans:
        lines.append(
            f"ORPHANS ({len(orphans)}) -- issue #227's fourth rule, an "
            "inventory rather than a defect, so it does not raise:")
        for line in orphans:
            lines.append(f"  {line}")
    if rows is None:
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
                 f"{len(orphans)} orphan(s), "
                 + ("tasks not read"
                    if rows is None
                    else f"{len(unplaced)} unplaced task(s)"))
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
        for line in serves_orphans(parse_milestone_serves(seats),
                                   parse_project_goals(goals),
                                   parse_milestone_keeps(seats)):
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
