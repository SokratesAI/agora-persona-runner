"""Write a board's markdown rows into the record store, once (issue #203).

    python3 -m tools.board_migrate --board issue --file issues.md
    python3 -m tools.board_migrate --board issue --file issues.md --apply

Issue #203 replaces the two markdown tables with one record per row. Eight
primitives for that are merged and none of them is wired; this is the ninth
and it is the step that actually moves the data. `board_migration_preflight`
already composes the same records and `--round-trip` already writes them --
and then deletes them again, deliberately, because its job is to prove the
trip is safe and leave nothing behind. This one keeps them.

**The default writes nothing.** `--apply` is the only path that stores
anything, and without it the tool composes the documents, counts them and
prints exactly what a run would write. That split is here rather than in the
caller because the switchover commit runs this against the live boards,
where a dry run is the last cheap moment to read the numbers.

**A board that already holds records is refused, and no `--force` exists.**
`board_store.write_rows` tombstones every row the caller did not send, so a
second run against a store somebody has since edited would silently delete
their edits and put back what the markdown said. Refusing is not caution
about re-running: re-running *this* command with *this* markdown is harmless,
and the case that is not harmless is indistinguishable from it here. A cycle
that genuinely wants to start over empties the board first, which is one
line and is the documented undo:

    python3 -c "from agora_runner import board_store; board_store.write_rows('issue', []); board_store.write_captures('issue', [])"

Both lines, not one: rows and captures are two key ranges and emptying one
leaves the other standing, which is the half-migrated state the refusal
above exists to catch.

That is also the restore point for the migration itself. The markdown files
are not touched by this tool -- the 21 modules that still parse them keep
working until the switchover commit lands -- so until that commit, undoing
the migration is emptying the store and nothing else.

**The registry is read from the store, not minted fresh.** `preflight`
composes against `entity_id.new_registry()` because it is asking a question
about shape and throws its answer away. Ids are permanent, so the migration
has to mint into whatever has already been minted, and write the registry
back at the revision it read at -- a `RegistryConflict` means another writer
got there first and is answered by re-reading and re-running, never by
resending. It is written *before* the rows, because a row referring to a
project id that was never stored is an orphan, while a stored project no row
points at yet is merely unused.
"""
import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import (board_document, board_store, entity_id,  # noqa: E402
                          nova_boards, rank_key)
from tools import board_migration_preflight as preflight  # noqa: E402


class MigrationRefused(RuntimeError):
    """The store is not in a state this run can safely write into."""


def plan(markdown, board, registry):
    """The documents a run would write, minted into `registry` in place.

    Separate from `migrate` so the dry run and the real run compose through
    exactly one code path -- a dry run that built its report a second way
    would be reporting on a migration nobody is about to perform.
    """
    items = preflight.board_items(markdown)
    details = preflight.board_details(markdown)
    docs, _projects, _milestones = preflight.records(
        items, board, registry, details=details)
    return docs, details, capture_plan(markdown, board, registry)


def capture_plan(markdown, board, registry):
    """His own bullets -> capture documents, minted into `registry` in place.

    The rows go through `preflight.records`; captures have no preflight,
    because they were the piece none of the eight primitives had anywhere to
    put. Composed here, in file order, from the one parser that knows what a
    capture is (`nova_boards.capture_entries` -- his words split from the
    replies cycles wrote under them, which is the split the Edit route needs).

    **Every capture gets a rank, and that is the part with teeth.**
    `board_store.read_captures` is deliberately unsorted and `_all_docs`
    answers in lexical id order, so `cap_10` comes back before `cap_2`;
    `board_document.captures_map` puts ranked captures first in rank order
    and unranked ones in the order they arrived. A migration that minted no
    rank would therefore store his list correctly and hand it back reordered
    the moment a board passed ten captures -- silently, with every capture
    still present, which is the shape of bug nobody reports as data loss.
    The file states the order; `rank_key.sequence` is how a stated order
    becomes one a later drag can insert into.
    """
    entries = nova_boards.capture_entries(markdown)
    keys = rank_key.sequence(len(entries))
    return [
        board_document.to_capture_document(
            text, board, entity_id.mint_capture(registry, board),
            rank=key, replies=replies)
        for (_start, _end, text, replies), key in zip(entries, keys)
    ]


def migrate(markdown, board, apply=False, store=board_store):
    """Compose `markdown` into records and, with `apply`, store them.

    Returns a report dict. Raises `MigrationRefused` when the board already
    holds records, before anything is minted or written.
    """
    if board not in board_document.BOARDS:
        raise MigrationRefused(
            f"board must be one of {board_document.BOARDS}, not {board!r}")

    # Both key ranges, because both are written with `prune=True` and a
    # board holding captures but no rows is exactly what a half-finished
    # earlier run leaves behind -- the state a rows-only check calls clean.
    held = dict(store.stored_documents(board))
    held.update(store.stored_capture_documents(board))
    if held:
        raise MigrationRefused(
            f"{board} already holds {len(held)} record(s); empty the board "
            "first if you mean to migrate it again")

    registry = store.read_registry()
    docs, details, captures = plan(markdown, board, registry)

    # `projects` and `milestones` are the registry's totals *after* this
    # run, not what this run minted. For the switchover that is the useful
    # number -- the store starts empty and the two are the same -- but the
    # second board of a pair reports the first board's ids in its count too,
    # and reading that as "the idea board has 11 projects" is wrong.
    report = {
        "board": board,
        "rows": len(docs),
        "details": len(details),
        "captures": len(captures),
        "projects": len(registry.get("projects") or {}),
        "milestones": len(registry.get("milestones") or {}),
        "applied": bool(apply),
        "written": 0,
        "stored": 0,
    }
    if not apply:
        return report

    # The registry carries the capture high-water mark this run just moved,
    # so it is written in the same pass as the captures themselves. A run
    # that stored the registry and not the captures is the partial migration
    # `board_records.contents` cannot detect: the registry is what it reads
    # to answer "has this board been migrated", so it would certify a board
    # whose captures never arrived.
    store.write_registry(registry)
    written = store.write_rows(board, docs)
    if written.get("failures"):
        raise MigrationRefused(
            f"{len(written['failures'])} row(s) failed to write; the store "
            "now holds a partial migration and must be emptied before a retry")
    wrote_captures = store.write_captures(board, captures)
    if wrote_captures.get("failures"):
        raise MigrationRefused(
            f"{len(wrote_captures['failures'])} capture(s) failed to write; "
            "the store now holds a partial migration and must be emptied "
            "before a retry")
    report["written"] = written.get("written") or 0
    report["captures_written"] = wrote_captures.get("written") or 0
    report["stored"] = len(store.stored_documents(board))
    report["captures_stored"] = len(store.stored_capture_documents(board))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--board", required=True,
                        choices=sorted(board_document.BOARDS),
                        help="which board the file is")
    parser.add_argument("--file", required=True, metavar="FILE",
                        help="the board markdown file")
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without it nothing is stored")
    args = parser.parse_args(argv)

    with open(args.file, encoding="utf-8") as handle:
        markdown = handle.read()

    try:
        report = migrate(markdown, args.board, apply=args.apply)
    except MigrationRefused as exc:
        print(f"REFUSED: {exc}")
        return 2

    for key in ("board", "rows", "details", "captures", "projects",
                "milestones", "applied", "written", "captures_written",
                "stored", "captures_stored"):
        if key in report:
            print(f"{key}: {report[key]}")
    if not args.apply:
        print("dry run -- nothing was written; pass --apply to store it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
