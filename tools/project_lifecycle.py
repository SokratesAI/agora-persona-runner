"""Propose one project's lifecycle stage -- for him to approve or decline.

Milestone M5 of idea #260's picking redesign, and the third of that
milestone's three project fields. The spec's own assignment:

> *"lifecycle: I approve / Nova proposes"*

    python3 -m tools.project_lifecycle --file projects.md --project Marcus \
        --propose active

**This writes the `Proposed` cell and can never write the live `Lifecycle`
cell beside it.** That is the approval gate, and there is deliberately no
flag here that skips it: the only thing that moves a stage from proposed to
live is `POST /api/project/lifecycle`, which is a button on his project
page. `tools.project_trl` beside it is the opposite call for the opposite
reason -- a TRL is mine outright, so it has a CLI and no button, and the
satisfaction score is his outright, so it has a button and no CLI. This one
is shared, so it has both halves and each half writes its own cell.

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.project_trl` and `tools.board_size` hold, so the caller
owns the compare-and-swap: `vault_tool.py get --rev-file` before,
`vault_tool.py put --if-rev-file` after.

The refusals: a stage outside the four; an unknown project, because a
lifecycle is a statement about a row that exists and inventing the row
would rate a project he never rated; a file with no table; and `check`
refusing the write when anything other than that one project's proposal
moved -- including its live lifecycle, which is the gate asserted a second
time against the diff rather than against the intent.

`--propose ''` withdraws a proposal I have not had answered yet. That is
not the same as him declining it, and it is a real thing to want: a cycle
that proposes `Retired` and then finds the project has open rows should be
able to take it back rather than wait for him to say no.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import (
    PROJECT_LIFECYCLE_STAGES,
    canonical_lifecycle,
    parse_project_meta,
    propose_project_lifecycle,
)


#: The one key `parse_project_meta` derives from the `Proposed` cell.
#: `lifecycle` is deliberately NOT in here: this tool may not move it, so
#: it belongs on the compared side of `check` where a change is a bug.
_PROPOSAL_KEYS = frozenset({"lifecycleProposed"})


def check(before, after, project, stage):
    """Refuse the write unless that one project's proposal moved, and nothing else.

    Same shape and same reasoning as `tools.project_trl.check`. The one
    thing worth naming: `lifecycle` is compared rather than exempted, so a
    bug that let this tool write a live stage fails here as well as being
    impossible in `propose_project_lifecycle` -- the gate is the whole
    point of the field and one guard for it is not enough.
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
        if moved.get("lifecycleProposed") != stage:
            problems.append(
                f"{project!r} came back proposing "
                f"{moved.get('lifecycleProposed')!r}, asked for {stage!r}"
            )
        if key in old:
            was = {k: v for k, v in old[key].items() if k not in _PROPOSAL_KEYS}
            now = {k: v for k, v in moved.items() if k not in _PROPOSAL_KEYS}
            if was != now:
                problems.append(
                    f"{project!r} changed something other than its proposal"
                )

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
        "--propose",
        required=True,
        help="one of " + ", ".join(PROJECT_LIFECYCLE_STAGES)
        + ", or '' to withdraw a proposal",
    )
    parser.add_argument("--out", help="where to write (default: in place)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    wanted = (args.propose or "").strip()
    if wanted:
        stage = canonical_lifecycle(wanted)
        if stage is None:
            print(
                f"REFUSED: '{args.propose}' is not a lifecycle stage. One of: "
                + ", ".join(PROJECT_LIFECYCLE_STAGES),
                file=sys.stderr,
            )
            return 1
    else:
        stage = ""

    with open(args.file, encoding="utf-8") as handle:
        before = handle.read()

    after = propose_project_lifecycle(before, args.project, stage)
    if after is None:
        print(
            f"REFUSED: {args.project!r} is not in {args.file} -- a lifecycle "
            "is a statement about a project that already has a row",
            file=sys.stderr,
        )
        return 1

    problems = check(before, after, args.project, stage)
    if problems:
        print("REFUSED: the write moved more than that one proposal:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    shown = stage or "(withdrawn)"
    if args.dry_run:
        print(f"would propose {shown} for {args.project}")
        return 0

    target = args.out or args.file
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(after)
    print(f"{args.project} -> proposed {shown}; wrote {target}. He approves it "
          "on the project page -- this tool cannot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
