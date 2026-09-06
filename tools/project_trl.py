"""Set one project's TRL -- how proven it is under real conditions.

Milestone M5 of idea #260's picking redesign, and the one of that
milestone's three fields that is mine to set. The spec's own assignment:

> *"TRL (who sets it: **Nova, directly, no approval gate**) ... No approval
> gate, because this is a technical self-assessment, same ownership as
> code-quality calls Nova already makes without asking permission."*

    python3 -m tools.project_trl --file projects.md --project Marcus \
        --trl hardened

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.board_size`, `tools.board_priority` and `tools.board_status`
hold, so the caller owns the compare-and-swap: `vault_tool.py get
--rev-file` before, `vault_tool.py put --if-rev-file` after.

Because the field is mine, there is deliberately **no control on his
project page** -- drawing him a button for this would assert he owns a
number the spec says I do. The page renders the meter and nothing more.

The refusals, each one a way this could hand him a wrong table: a level
outside the five (`--trl` accepts a name or a 1-5 number, nothing else); an
unknown project, because a readiness score is a statement about a row that
exists and inventing the row would rate a project he never rated; a file
with no table in it; and `check` refusing the write when anything other
than that one project's TRL moved.

`--trl ''` is legal and clears the cell back to unassessed, which is a real
state and the one every project is in until a cycle looks -- it is
different from `Concept`, which is a judgement that the project is at the
bottom of the scale.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    PROJECT_TRL_LEVELS,
    canonical_trl,
    parse_project_meta,
    set_project_trl,
)


#: The one key `parse_project_meta` derives from the TRL cell. Naming it is
#: what lets `check` be an equality test on everything else.
_TRL_KEYS = frozenset({"trl"})


def check(before, after, project, trl):
    """Refuse the write unless that one project's TRL moved, and nothing else.

    Same shape and same reasoning as `tools.board_size.check`, against
    `parse_project_meta` instead of `parse_board` because this is his
    project table rather than a board. There is no forgiveness here at all
    -- this tool writes no note and stamps no date, so a moved `Updated`
    cell is a bug rather than a side effect.
    """
    problems = []
    key = (project or "").strip().lower()
    old = parse_project_meta(before)
    new = parse_project_meta(after)

    if key not in old:
        problems.append(f"{project!r} was not in the table to begin with")
    if key not in new:
        problems.append(f"{project!r} is not in the table afterwards")
    else:
        moved = new[key]
        if moved.get("trl") != trl:
            problems.append(
                f"{project!r} came back as {moved.get('trl')!r}, asked for {trl!r}"
            )
        if key in old:
            was = {k: v for k, v in old[key].items() if k not in _TRL_KEYS}
            now = {k: v for k, v in moved.items() if k not in _TRL_KEYS}
            if was != now:
                problems.append(f"{project!r} changed something other than its TRL")

    if len(new) != len(old):
        problems.append(
            f"row count went {len(old)} -> {len(new)}, expected no change"
        )
    for other, was in old.items():
        if other == key:
            continue
        now = new.get(other)
        if now is None:
            problems.append(f"{was['project']!r} fell out of the table")
        elif now != was:
            problems.append(f"{was['project']!r} changed underneath the write")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="projects.md on disk")
    parser.add_argument("--project", required=True, help="the project name")
    parser.add_argument(
        "--trl",
        required=True,
        help="one of " + ", ".join(PROJECT_TRL_LEVELS) + ", or 1-5, or '' to clear",
    )
    parser.add_argument("--out", help="where to write (default: in place)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    wanted = (args.trl or "").strip()
    if wanted:
        level = canonical_trl(wanted)
        if level is None:
            print(
                f"REFUSED: '{args.trl}' is not a TRL. One of: "
                + ", ".join(PROJECT_TRL_LEVELS)
                + ", or 1-5",
                file=sys.stderr,
            )
            return 1
    else:
        level = ""

    with open(args.file, encoding="utf-8") as handle:
        before = handle.read()

    after = set_project_trl(before, args.project, level)
    if after is None:
        print(
            f"REFUSED: {args.project!r} is not in {args.file} -- a TRL is a "
            "statement about a project that already has a row",
            file=sys.stderr,
        )
        return 1

    problems = check(before, after, args.project, level)
    if problems:
        print("REFUSED: the write moved more than that one TRL:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    shown = level or "(unassessed)"
    if args.dry_run:
        print(f"would set {args.project} to {shown}")
        return 0

    target = args.out or args.file
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(after)
    print(f"{args.project} -> {shown}; wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
