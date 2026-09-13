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

**Exit codes, and the middle one is the point.** `0` clean, `1` something
in the model is broken (a fourth key result, a KPI with a target, a `Serves`
pointing at nothing), `2` a document could not be read. Unreadable is not
folded into clean: *"a check that never ran must not read as a check that
came back clean"*, which is `preflight`'s contract one level up. An
**absent** `project-goals.md` is neither -- it is the state before step 2
has written any content, so it prints that and exits 0.

Not registered in `tools.preflight` yet, deliberately: it would report the
same "nothing written yet" every cycle until step 2 lands, and a check that
always says the same thing teaches a cycle to skip reading it.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    MILESTONE_SEATS_PATH, parse_milestone_serves,
)
from agora_runner.project_goals import (
    PROJECT_GOALS_PATH, PROJECT_GOALS_TEMPLATE, parse_project_goals, problems,
    serves_problems,
)


def report(goals_markdown, seats_markdown):
    """`(lines, exit code)` -- the whole judgement, no I/O.

    Split out from `main` so the tests drive the logic rather than a
    subprocess, and so a caller that already holds both documents (the
    project page, when step 4 draws this) does not fetch them twice.
    """
    sections = parse_project_goals(goals_markdown)
    lines = []
    if not sections:
        return ["project-goals.md holds no project section yet "
                "(issue #227 step 2 writes the content)"], 0
    found = problems(sections)
    serves = parse_milestone_serves(seats_markdown)
    orphans = serves_problems(serves, sections)
    lines.append(f"{len(sections)} project section(s), "
                 f"{len(serves)} seated milestone(s)")
    for line in found + orphans:
        lines.append(f"  {line}")
    return lines, 1 if (found or orphans) else 0


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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--goals", help="local project-goals.md instead of a fetch")
    ap.add_argument("--seats", help="local milestone-seats.md instead of a fetch")
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
    for ok, name in ((ok_goals, "project-goals.md"), (ok_seats, "milestone-seats.md")):
        if not ok:
            print(f"UNREADABLE: {name}")
            return 2
    lines, code = report(goals, seats)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
