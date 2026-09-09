"""Do the three merged migration primitives actually compose on a real board?

    python3 -m tools.board_migration_preflight --board issues.md --board ideas.md
    python3 -m tools.board_migration_preflight --board issues.md --assert-clean

Issue #203 turns each board row into a record, and three pieces of that
change are already merged and wired to nothing: `rank_key` (the ordering
key), `entity_id` (the project and milestone ids) and `board_view` (the
generated markdown the backup keeps). Each has its own unit tests and none
of them has ever been run against the other two, so what nobody has
measured is the only thing the migration commit actually does -- take the
rows that exist and hand every one of them an id and a rank.

This runs that composition over real board markdown and reports the
numbers the migration will have to live with. It is a preflight, not a
migration: it writes nothing, and `--assert-clean` is there so the cycle
that takes the commit can find out in one command whether the ground
moved under the primitives since they merged.

What counts as a problem, and why each one would be a bad surprise
*during* the one big commit rather than before it:

- **Two distinct project names sharing one id.** That is two projects
  silently becoming one. It cannot come from case or spacing, because
  merging those is the entire point of `entity_id.normalise` and is
  correct -- it would have to come from slug truncation or from a
  clash-suffix bug, so it is worth a check that names the pair.
- **A rank key that is invalid, out of order, or has an uninsertable
  gap.** `sequence` and `between` are separately tested; what is not
  tested is `between` over the *whole* sequence at the size of a real
  board, which is where a midpoint runs out of room between two adjacent
  keys.

**There is deliberately no "row with no project" check, and the reason is
worth carrying into the migration.** `nova_boards.parse_board` reads an
absent or empty `Project` cell as `DEFAULT_PROJECT`, which is `Nova` --
documented at the cell, and correct for a board where every row predates
the column. So a project-less row never reaches this module, a count of
them would be zero on any input and would report a guard working while
guarding nothing. What it means downstream is the part that matters: the
migration mints `prj_nova` for a row whose cell is blank, and that id is
permanent, so "nobody has re-filed this yet" becomes "filed under Nova"
the moment the records land. That is a decision for the commit, not a
defect for a preflight to fail on.

What it does not check: whether the generated markdown round-trips. That
already has its own coverage in `board_view`, against these same files.
"""
import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import entity_id, nova_boards, rank_key  # noqa: E402


def board_items(markdown):
    """The row dicts in a board, whatever shape `parse_board` hands back."""
    parsed = nova_boards.parse_board(markdown)
    if isinstance(parsed, list):
        return parsed
    for key in ("items", "rows"):
        rows = parsed.get(key)
        if isinstance(rows, list):
            return rows
    return []


def compose(items):
    """Mint an id and a rank for every row; report what would not compose.

    Returns `(report, problems)` -- counts for the log, and a list of
    human-readable strings that is empty when the migration has clean
    ground to stand on.
    """
    registry = entity_id.new_registry()
    problems = []

    projects = {}
    for item in items:
        name = item.get("project")
        if name and name not in projects:
            projects[name] = entity_id.ensure_project(registry, name)

    by_id = {}
    for name, pid in projects.items():
        by_id.setdefault(pid, []).append(name)
    for pid, names in sorted(by_id.items()):
        if len({entity_id.normalise(n) for n in names}) > 1:
            problems.append(f"project id {pid} is shared by {sorted(names)}")

    milestones = {}
    for item in items:
        name = item.get("project")
        stone = item.get("milestone")
        if not (name and stone):
            continue
        pair = (name, stone)
        if pair not in milestones:
            milestones[pair] = entity_id.ensure_milestone(
                registry, projects[name], stone
            )

    keys = rank_key.sequence(len(items))
    for key in keys:
        if not rank_key.is_valid(key):
            problems.append(f"rank key {key!r} is not valid")
            break
    if sorted(keys) != list(keys) or len(set(keys)) != len(keys):
        problems.append("rank keys are not strictly increasing")
    else:
        for before, after in zip(keys, keys[1:]):
            try:
                middle = rank_key.between(before, after)
            except rank_key.RankError as exc:
                problems.append(f"no key fits between {before!r} and {after!r}: {exc}")
                break
            if not before < middle < after:
                problems.append(
                    f"between({before!r}, {after!r}) gave {middle!r}, out of order"
                )
                break

    report = {
        "rows": len(items),
        # Distinct *ids*, not distinct names: merging `Nova` and `nova`
        # into one project is the whole point of `entity_id`, so counting
        # the names would report the migration creating projects it does
        # not create.
        "projects": len(set(projects.values())),
        "milestones": len(set(milestones.values())),
        "rank_keys": len(keys),
    }
    return report, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--board", action="append", default=[], metavar="FILE",
                        help="a board markdown file; repeatable")
    parser.add_argument("--assert-clean", action="store_true",
                        help="exit 2 when anything would not compose")
    args = parser.parse_args(argv)
    if not args.board:
        parser.error("give at least one --board FILE")

    items = []
    for path in args.board:
        with open(path, encoding="utf-8") as handle:
            items.extend(board_items(handle.read()))

    report, problems = compose(items)
    for name, value in report.items():
        print(f"{name}: {value}")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if problems and args.assert_clean:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
