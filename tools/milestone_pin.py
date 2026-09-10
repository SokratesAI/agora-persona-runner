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
contract `tools.board_milestone`, `tools.board_size` and `tools.board_row`
hold, so the caller owns the compare-and-swap: `vault_tool.py get
--rev-file` before, `vault_tool.py put --if-rev-file` after. `board_put`
refuses a file that is not one of the two boards, so this one goes back
with the plain vault client.

**The milestone-name check is what stops a pin naming nothing, and it is
on by default now.** A milestone exists only as a cell on the board rows
carrying its name, so a typo in `--milestone` writes a row that resolves
to no milestone and is silently ignored by the ranking forever. The check
used to be opt-in, and the reason it was opt-in was that this module did
not know where the boards lived -- you had to hand it their paths with
`--boards`. Issue #203's record store removes that: `--boards` now names
boards in the store (`issue`, `idea`) rather than files on disk, so the
check needs nothing from the caller and defaults to both. `--position 0`
still skips it, since removing a pin whose milestone has already been
renamed away is exactly when you most need to.

**A store this cannot read refuses the pin rather than skipping the
check.** "I looked and the milestone is not there" and "I could not look"
are opposite findings and the old opt-in spelled them the same way -- no
`--boards`, no complaint. `--no-check` is the deliberate way past it, and
it says so in the refusal.

The pair is matched case-insensitively, the same way `milestone_ranks`
keys it -- `nova` and `Nova` are one project.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records
from agora_runner.nova_boards import set_milestone_pin
from agora_runner.nova_next import open_rows_from_contents

# Both of his boards. A milestone is a project/name pair and a project
# spans the two, so checking only one of them would refuse a real name.
CHECKED_BOARDS = ("issue", "idea")


def known_milestones(boards):
    """`{(project, milestone) lowercased}` over the open rows of `boards`.

    `boards` is an iterable of the four-key mapping `board_records.contents`
    returns, not a list of paths -- this reader is converted onto the record
    store (issue #203), so it never sees markdown and never opens a file.
    The board label handed to `open_rows_from_contents` only decides the row
    slugs, which this discards, so one label for both boards is correct here
    rather than merely convenient.

    A milestone carried only by *closed* rows is deliberately not known: a
    pin orders a list of things left to do, and pinning a finished milestone
    would put an empty heading at the top of his project drawer.
    """
    found = set()
    for contents in boards or ():
        for row in open_rows_from_contents(contents, "idea"):
            milestone = (row.get("milestone") or "").strip()
            if milestone:
                found.add(((row.get("project") or "").strip().lower(),
                           milestone.lower()))
    return found


def store_milestones(names, store=None):
    """`known_milestones` over the named boards in the record store.

    Split out from `main` so a test can reach the read without a CouchDB,
    and so the refusal below has exactly one thing to catch.

    `store=None` rather than the real store as a default argument: a default
    binds its value at import, so `board_records.board_store` written there
    would be the object this module captured and a test replacing it would
    be replacing something nothing reads.
    """
    store = store or board_records.board_store
    return known_milestones(
        [board_records.contents(name, store=store) for name in names])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, help="local milestones.md")
    ap.add_argument("--project", required=True)
    ap.add_argument("--milestone", required=True)
    ap.add_argument("--position", required=True, type=int,
                    help="1-based position within the project, or 0 to unpin")
    ap.add_argument("--dated", default="", help="MM-DD for the Updated cell")
    ap.add_argument("--boards", nargs="*", default=list(CHECKED_BOARDS),
                    help="boards in the record store to check the milestone "
                         "name against (names, not paths)")
    ap.add_argument("--no-check", action="store_true",
                    help="pin without checking the milestone name exists")
    args = ap.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as fh:
            markdown = fh.read()
    except FileNotFoundError:
        # Not an error: the file is written whole on the first pin, so
        # before he has pinned anything there is nothing to read.
        markdown = ""

    if args.position and args.boards and not args.no_check:
        wanted = (args.project.strip().lower(), args.milestone.strip().lower())
        try:
            found = store_milestones(args.boards)
        except Exception as exc:  # noqa: BLE001 -- see the module docstring
            # Every way this can fail -- an unmigrated store, a capture
            # misfiled into the row range, CouchDB not answering -- ends in
            # the same place: the check did not run, so it must not read as
            # one that came back clean.
            print(f"cannot check the milestone name against the record "
                  f"store: {exc} -- pass --no-check to pin anyway",
                  file=sys.stderr)
            return 2
        if wanted not in found:
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
