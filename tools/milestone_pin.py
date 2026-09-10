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
        --position 1 --dated 09-07 --board idea

    python3 -m tools.milestone_pin --file milestones.md \
        --project Nova --milestone 'Picking and planning' --position 0

**`--position 0` removes the row**, which is the "until he removes it" half
of the spec and the only way back to the computed order.

**`--file` is the pins document and it is still a path on disk**, the
same contract `tools.board_row` and `tools.board_priority` hold, so the
caller owns the compare-and-swap: `vault_tool.py get --rev-file` before,
`vault_tool.py put --if-rev-file` after. `board_put` refuses a file that
is not one of the two boards, so this one goes back with the plain vault
client.

**`--board` is what stops a pin naming nothing.** A milestone exists only
as a cell on the board rows carrying its name, so a typo in `--milestone`
writes a row that resolves to no milestone and is silently ignored by the
ranking forever. Name the boards to check against -- `issue`, `idea`, or
both -- and the name is checked against their open rows before anything is
written. `--position 0` skips the check, since removing a pin whose
milestone has already been renamed away is exactly when you most need to.

**The check reads the records, never a board file.** It took `--boards`
with paths on disk until #203 converted it, and that flag is the reason
`agora_runner/nova_next.py` could not come off `parse_board`: it was the
one caller left handing `open_rows` a string it had read itself, and the
migration gate does not count it, because this module never says
`parse_board` in its own text.

The pair is matched case-insensitively, the same way `milestone_ranks`
keys it -- `nova` and `Nova` are one project.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records, board_store
from agora_runner.board_document import BOARDS
from agora_runner.nova_boards import set_milestone_pin
from agora_runner.nova_next import open_rows_from_contents


def known_milestones(boards, store=board_store):
    """`{(project, milestone) lowercased}` over the open rows of `boards`.

    `boards` are names -- `issue`, `idea` -- not paths, and each one is
    read once out of the record store. `store` is injected so a test can
    hand over a fake without a CouchDB, the same seam every other
    converted writer holds.
    """
    found = set()
    for board in boards or []:
        contents = board_records.contents(board, store=store)
        for row in open_rows_from_contents(contents, board):
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
    ap.add_argument("--board", nargs="*", default=[], choices=list(BOARDS),
                    help="boards to check the milestone name against")
    args = ap.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as fh:
            markdown = fh.read()
    except FileNotFoundError:
        # Not an error: the file is written whole on the first pin, so
        # before he has pinned anything there is nothing to read.
        markdown = ""

    if args.position and args.board:
        wanted = (args.project.strip().lower(), args.milestone.strip().lower())
        try:
            known = known_milestones(args.board, store=board_store)
        except board_records.RecordError as problem:
            # A board that has never been migrated answers `[]` for its
            # rows, which is the same value a board whose rows were all
            # closed answers with. `contents` is the only thing that can
            # tell them apart, and taken quietly this would refuse every
            # pin forever with "no open row carries it".
            print(f"REFUSED: {problem}", file=sys.stderr)
            return 1
        if wanted not in known:
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
