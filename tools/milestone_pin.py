"""Pin a milestone to a position in its project's list, or unpin it.

Milestone M4 of idea #260's picking redesign, and the override half of it.
`tools.board_milestone` puts a row into a named milestone and
`nova_next.milestone_ranks` orders those milestones by importance over
size; this is the one thing that outranks that formula. His words in the
spec: *"I want the ability to reorder tasks and milestones but the default
is that you do it"* -- so the formula is the default and a pin is him
saying otherwise about one milestone.

    python3 -m tools.milestone_pin --file milestones.md \
        --project Nova --milestone 'Picking and planning' \
        --position 1 --dated 09-07

    python3 -m tools.milestone_pin --file milestones.md \
        --project Nova --milestone 'Picking and planning' --position 0

**`--position 0` removes the row**, which is the "until he removes it" half
of the spec and the only way back to the computed order.

**It takes a path on disk and knows nothing about the vault**, the same
contract `tools.board_size` and `tools.board_row` hold (`board_milestone`
held it until #203 converted it onto the record store), so the caller owns the compare-and-swap: `vault_tool.py get
--rev-file` before, `vault_tool.py put --if-rev-file` after. `board_put`
refuses a file that is not one of the two boards, so this one goes back
with the plain vault client.

**`--boards` is what stops a pin naming nothing.** A milestone exists only
as a cell on the board rows carrying its name, so a typo in `--milestone`
writes a row that resolves to no milestone and is silently ignored by the
ranking forever. Pass the boards and the name is checked against the open
rows before anything is written; the check is opt-in rather than mandatory
because this module deliberately does not know where the boards live, and
`--position 0` skips it, since removing a pin whose milestone has already
been renamed away is exactly when you most need to.

The pair is matched case-insensitively, the same way `milestone_ranks`
keys it -- `nova` and `Nova` are one project.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import set_milestone_pin
from agora_runner.nova_next import open_rows


def known_milestones(paths):
    """`{(project, milestone) lowercased}` over the open rows of `paths`."""
    found = set()
    for path in paths or []:
        with open(path, encoding="utf-8") as fh:
            for row in open_rows(fh.read(), "idea"):
                milestone = (row.get("milestone") or "").strip()
                if milestone:
                    found.add(((row.get("project") or "").strip().lower(),
                               milestone.lower()))
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, help="local milestones.md")
    ap.add_argument("--project", required=True)
    ap.add_argument("--milestone", required=True)
    ap.add_argument("--position", required=True, type=int,
                    help="1-based position within the project, or 0 to unpin")
    ap.add_argument("--dated", default="", help="MM-DD for the Updated cell")
    ap.add_argument("--boards", nargs="*", default=[],
                    help="board files to check the milestone name against")
    args = ap.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as fh:
            markdown = fh.read()
    except FileNotFoundError:
        # Not an error: the file is written whole on the first pin, so
        # before he has pinned anything there is nothing to read.
        markdown = ""

    if args.position and args.boards:
        wanted = (args.project.strip().lower(), args.milestone.strip().lower())
        if wanted not in known_milestones(args.boards):
            print("no open row carries milestone "
                  f"{args.milestone!r} in project {args.project!r} -- "
                  "nothing would be pinned", file=sys.stderr)
            return 2

    updated = set_milestone_pin(markdown, args.project, args.milestone,
                                args.position, args.dated)
    if updated is None:
        print("refused: check the project, the milestone and the position",
              file=sys.stderr)
        return 2
    with open(args.file, "w", encoding="utf-8") as fh:
        fh.write(updated)
    if args.position:
        print(f"pinned {args.project} / {args.milestone} at {args.position}")
    else:
        print(f"unpinned {args.project} / {args.milestone}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
