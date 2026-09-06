"""Record that a low-satisfaction diagnosis actually ran, so it is not re-run.

Milestone M5 of `task-prioritization-redesign.md`. The spec gives his
satisfaction score two behaviours, and the second one is why this file
exists: *"Score ≤2 auto-generates a skip-to-top task: 'diagnose low
satisfaction on [project]'"*, and *"if satisfaction is still ≤2 after a
diagnosis already ran once, surface that persistence visibly rather than
silently re-triggering an identical diagnostic loop."*

`tools.top_board_rows` forces the task; this writes down that it happened.
Without the log the second sentence cannot be honoured at all -- a score he
sets once and does not change would force the same diagnosis every cycle
forever, which is precisely the loop he asked not to have.

**The log is read and written the way `claims.json` is**: this tool takes a
local file, and the caller does the vault `get` and `put` around it in one
`Bash` call. Same reason as the ledger -- the compare-and-swap belongs to
whoever holds the revision, and a tool that fetches and writes on its own
cannot participate in one.

    python3 /app/bridge/vault_tool.py get \
        'projects/sokrates/projects/agora/nova/resources/satisfaction-diagnoses.json' \
        > satisfaction-diagnoses.json
    python3 -m tools.satisfaction_diagnosis record --log satisfaction-diagnoses.json \
        --project Marcus --score 2 --cycle 1099 --found '<what the diagnosis found>'
    python3 /app/bridge/vault_tool.py put \
        'projects/sokrates/projects/agora/nova/resources/satisfaction-diagnoses.json' \
        satisfaction-diagnoses.json

Exit 0 on a write, 1 on a bad argument. `show` prints the log and exits 0
even when it is empty -- "nothing has been diagnosed" is an answer.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import PROJECT_SATISFACTION_MAX
from agora_runner.nova_next import LOW_SATISFACTION_AT, load_diagnoses

#: Rule 7. The stamp is read by a person, on his board, in his timezone.
OSLO = ZoneInfo("Europe/Oslo")

DIAGNOSES_PATH = ("projects/sokrates/projects/agora/nova/resources/"
                  "satisfaction-diagnoses.json")


def read(path):
    """`(raw text, rows)` for the log at `path`. A missing file is empty.

    Missing is the normal state -- nothing has ever been diagnosed -- and
    `vault_tool.py get` writes `[not found: ...]` into the file rather than
    failing, so that string is read as empty here too. `load_diagnoses`
    already treats unparseable text as empty; the raw text comes back so a
    caller can tell the difference if it wants to.
    """
    if not os.path.exists(path):
        return "", {}
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith("[not found:"):
        return text, {}
    return text, load_diagnoses(text)


def record(rows, project, score, cycle, found="", now=None):
    """Add one diagnosis to `rows`, replacing any earlier one for the project.

    **Replacing, not appending.** The only question the log is ever asked is
    "has this project been diagnosed at this level already", and the newest
    answer is the whole of it; keeping the history would make the file grow
    forever for a question nobody asks of it. The score is stored because
    the persistence line quotes it -- a project diagnosed at 1 and now at 2
    has moved, and a reader should be able to see that it did.
    """
    stamp = (now or datetime.now(OSLO)).strftime("%Y-%m-%d %H:%M")
    kept = [r for r in rows.values()
            if (r.get("project") or "").strip().lower() != project.strip().lower()]
    kept.append({"project": project.strip(), "score": int(score),
                 "cycle": int(cycle), "at": stamp, "found": found})
    kept.sort(key=lambda r: (r.get("project") or "").lower())
    return {"diagnoses": kept}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("record", "show"):
        one = sub.add_parser(name)
        one.add_argument("--log", required=True,
                         help=f"local copy of {DIAGNOSES_PATH}")
        if name == "record":
            one.add_argument("--project", required=True)
            one.add_argument("--score", type=int, required=True,
                             help="the score that forced this diagnosis")
            one.add_argument("--cycle", type=int, required=True)
            one.add_argument("--found", default="",
                             help="one line on what the diagnosis actually found")
    args = ap.parse_args(argv)

    text, rows = read(args.log)
    if args.cmd == "show":
        if not rows:
            print(f"no diagnosis recorded ({len(text)} byte(s) read)")
            return 0
        for row in sorted(rows.values(), key=lambda r: r.get("project", "")):
            print(f"{row.get('project')}  scored {row.get('score')} of "
                  f"{PROJECT_SATISFACTION_MAX}  diagnosed by cycle "
                  f"{row.get('cycle')} at {row.get('at')}  {row.get('found', '')}")
        return 0

    # A score outside the band this whole mechanism is about is refused
    # rather than stored. The log's only reader asks "was this project
    # diagnosed while it was low", and a row saying a 5 was diagnosed
    # answers a question nobody asked while looking exactly like one that
    # does -- and it would silently suppress a real forced task later.
    if not 1 <= args.score <= LOW_SATISFACTION_AT:
        print(f"--score must be 1..{LOW_SATISFACTION_AT}: a diagnosis is only "
              f"forced at or below {LOW_SATISFACTION_AT}, so recording one "
              f"above it records something that never happened", file=sys.stderr)
        return 1
    with open(args.log, "w", encoding="utf-8") as fh:
        json.dump(record(rows, args.project, args.score, args.cycle, args.found),
                  fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"recorded: {args.project.strip()} diagnosed at {args.score} of "
          f"{PROJECT_SATISFACTION_MAX} by cycle {args.cycle} -> {args.log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
