"""Write a board's markdown document into the record store, once (issue #203).

    python3 -m tools.board_migrate --board issue --file issues.md
    python3 -m tools.board_migrate --board issue --file issues.md --apply
    python3 -m tools.board_migrate --board issue --file issues.md --status
    python3 -m tools.board_migrate --board issue --file issues.md --resync --apply

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
that wants the store brought back into line with his markdown runs
`--resync` (see `resync`), which keeps the id of every bullet he has not
edited. A cycle that genuinely wants to start over from nothing empties the
board first, which is one line and is the documented undo:

    python3 -c "from agora_runner import board_store as s; s.write_rows('issue', []); s.write_captures('issue', []); s.delete_layout('issue')"

**All three, not the first one.** A seed writes his rows, his write-ups,
his own capture bullets and the layout of his document, and they live in
three key ranges that no single writer may reach across. Emptying the rows
alone leaves the captures and the layout behind, which is the exact state
the next run refuses -- so a half-undo reads as a half-migration and the
board cannot be re-seeded without finishing it. `delete_layout` is the one
delete here rather than an empty write on purpose: `[]` is a *valid*
layout, and storing it renders his board as its frontmatter, his capture
box and nothing else.

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

from agora_runner import (  # noqa: E402
    board_document, board_records, board_store, board_view, entity_id,
    rank_key)
from tools import board_migration_preflight as preflight  # noqa: E402


class MigrationRefused(RuntimeError):
    """The store is not in a state this run can safely write into."""


def captures(markdown, board, registry, reuse=None):
    """His own bullets -> capture documents, minted into `registry`.

    A board file is not only its two tables. The bullets above the first
    heading are the box he types into, and they live in their own key range
    (`capture:<board>:`) precisely so that a migration writing the rows
    alone cannot touch them -- which is exactly what a migration writing the
    rows alone *did*: it left them out, so a generated view rendered off the
    store would hand his board back with the bullets deleted.

    Rank is the bullet's position in his file, so the order he wrote them in
    survives the trip -- as a `rank_key`, the shape every row and every
    capture writer uses. It was `index + 1` until Cycle 1391, and one whole
    number beside one key on the same board is a TypeError in
    `captures_in_order` on every read of it.

    Ids come from `entity_id.mint_capture`, which is the
    one id here that is not seeded from a name -- his words are the thing he
    edits, so a slug of them would orphan the replies underneath.

    **`reuse` is `{his words: [capture id, ...]}` and it is what makes a
    re-seed safe.** `mint_capture` is deliberately not idempotent -- its own
    docstring says a caller re-running a migration mints a second set of ids
    and that this is not something it can defend against -- so the defence
    has to live here, in the caller. A bullet whose text is byte-identical to
    one already stored keeps that bullet's id, so the replies underneath stay
    attached and `nova_site`'s Edit route goes on addressing the same
    document. Anything with no match mints, which is the honest answer: he
    edited the words, and an edited bullet is a new one as far as any id
    seeded from a name is concerned.

    Matching is on the exact text and ids are consumed in the order they were
    stored, so two identical bullets keep their two ids rather than both
    taking the first. `None` means seed -- mint everything -- which is what
    the first migration of a board wants.
    """
    pool = {}
    for text, held in (reuse or {}).items():
        pool[text] = list(held)
    docs = []
    bullets = list(preflight.board_captures(markdown))
    keys = rank_key.sequence(len(bullets))
    for (text, under), key in zip(bullets, keys):
        held = pool.get(text)
        capture_id = (held.pop(0) if held
                      else entity_id.mint_capture(registry, board))
        docs.append(board_document.to_capture_document(
            text, board, capture_id, rank=key, replies=under))
    return docs


def plan(markdown, board, registry, reuse=None):
    """The documents a run would write, minted into `registry` in place.

    Separate from `migrate` so the dry run and the real run compose through
    exactly one code path -- a dry run that built its report a second way
    would be reporting on a migration nobody is about to perform.

    Four things, not two. `board_view.render_document` draws a board file
    from the rows, the write-ups, his capture bullets *and* the layout, and
    a seed that stores the first two only is not a seed of his document:
    measured on the live boards, rendering `issues.md` without its layout
    drops 19,653 words of his `## Processed captures` archive and
    `ideas.md` drops 6,469 including its whole `## Discarded` table.
    """
    items = preflight.board_items(markdown)
    details = preflight.board_details(markdown)
    docs, _projects, _milestones = preflight.records(
        items, board, registry, details=details)
    capture_docs = captures(markdown, board, registry, reuse=reuse)
    layout = board_view.document_layout(markdown)
    return docs, details, capture_docs, layout


def migrate(markdown, board, apply=False, store=board_store):
    """Compose `markdown` into records and, with `apply`, store them.

    Returns a report dict. Raises `MigrationRefused` when the board already
    holds records, before anything is minted or written.
    """
    if board not in board_document.BOARDS:
        raise MigrationRefused(
            f"board must be one of {board_document.BOARDS}, not {board!r}")

    held = store.stored_documents(board)
    if held:
        raise MigrationRefused(
            f"{board} already holds {len(held)} record(s); empty the board "
            "first if you mean to migrate it again")
    # Checked separately from the rows because they are separate key ranges
    # and either can be occupied on its own. A store holding captures and no
    # rows is what a half-finished migration leaves behind, and reading only
    # `stored_documents` there would call it empty and mint a second id for
    # every bullet he has written.
    held_captures = store.stored_capture_documents(board)
    if held_captures:
        raise MigrationRefused(
            f"{board} already holds {len(held_captures)} capture(s); empty "
            "the board first if you mean to migrate it again")
    if store.read_layout(board) is not None:
        raise MigrationRefused(
            f"{board} already holds a layout; empty the board first if you "
            "mean to migrate it again")

    registry = store.read_registry()
    docs, details, capture_docs, layout = plan(markdown, board, registry)

    # `projects` and `milestones` are the registry's totals *after* this
    # run, not what this run minted. For the switchover that is the useful
    # number -- the store starts empty and the two are the same -- but the
    # second board of a pair reports the first board's ids in its count too,
    # and reading that as "the idea board has 11 projects" is wrong.
    report = {
        "board": board,
        "rows": len(docs),
        "details": len(details),
        "projects": len(registry.get("projects") or {}),
        "milestones": len(registry.get("milestones") or {}),
        "captures": len(capture_docs),
        "layout_blocks": len(layout),
        "applied": bool(apply),
        "written": 0,
        "stored": 0,
        "captures_stored": 0,
        "layout_stored": 0,
    }
    if not apply:
        return report

    store.write_registry(registry)
    written = store.write_rows(board, docs)
    if written.get("failures"):
        raise MigrationRefused(
            f"{len(written['failures'])} row(s) failed to write; the store "
            "now holds a partial migration and must be emptied before a retry")
    report["written"] = written.get("written") or 0
    report["stored"] = len(store.stored_documents(board))

    # After the rows on purpose. `write_rows` prunes its own key range and
    # cannot reach `capture:<board>:`, so the order is not a correctness
    # requirement -- but a crash between the two leaves the rows stored and
    # the captures absent, which is the state this run already knows how to
    # refuse, where the mirror image is a store that looks migrated to
    # nothing and holds his bullets.
    stored_captures = store.write_captures(board, capture_docs)
    if stored_captures.get("failures"):
        raise MigrationRefused(
            f"{len(stored_captures['failures'])} capture(s) failed to write; "
            "the store now holds a partial migration and must be emptied "
            "before a retry")
    report["captures_stored"] = len(store.stored_capture_documents(board))
    store.write_layout(board, layout)
    report["layout_stored"] = len(store.read_layout(board) or [])
    return report


def stored_capture_ids(board, store=board_store):
    """His stored bullets -> `{text: [capture id, ...]}`, in stored order.

    Read as a *list* per text rather than one id, because two bullets can
    hold the same words -- he writes `-` placeholders, and a repeated
    sentence is his to repeat. Collapsing them would hand both copies the
    same id and `board_store._bulk_write` refuses a batch with a duplicate
    in it, which is the right refusal in the wrong place: the caller would
    have lost a bullet before the store ever saw it.
    """
    held = store.stored_capture_documents(board)
    by_text = {}
    # `captures_in_order`, not `rank or 0`: with string ranks one unranked
    # capture made that key compare `0` with a str. Pre-sorted by id so ties
    # keep the order they always had.
    by_id = sorted(held.values(), key=lambda doc: doc.get("_id") or "")
    for doc in board_document.captures_in_order(by_id):
        by_text.setdefault(doc.get("text"), []).append(doc.get("captureId"))
    return by_text


def resync(markdown, board, apply=False, store=board_store):
    """Rewrite a board that already holds records, from `markdown`.

    `migrate` above is the one-way door: it refuses a board that already
    holds records because `write_rows` prunes, so a second run against a
    store somebody edited would delete their edits. That refusal is right
    and stays. What it leaves missing is the other half -- **until the
    switchover lands, his markdown is still the source of truth and it
    changes every hour**, so the seed goes stale and the only documented
    repair is emptying all three key ranges by hand and re-seeding, which
    re-mints every capture id and orphans the replies under his bullets.
    `tools.board_migrate --status` can already say a board has DRIFTED; this
    is what answers it.

    The direction is the whole contract: **markdown in, store out.** That is
    correct today and becomes wrong the moment `nova_site` reads the store,
    because from then on the store is truth and this would overwrite it with
    a generated view. It is a migration-window tool and it should be deleted
    with the window, not kept as a sync.

    Two refusals, both narrower than `migrate`'s:

    - **An unmigrated store**, because a resync of a board nobody seeded is
      a seed, and a seed is `migrate`'s job with `migrate`'s report. Sending
      it here would mean two commands that both first-write a board.
    - **Markdown with no rows.** A board file is fetched over the vault
      tool, and an oversized read comes back as a ~2KB preview rather than
      an error -- so "his board has no rows" is what a truncated fetch looks
      like, and pruning on it would empty his board in the store. Neither of
      his boards has ever been empty. A genuinely empty board is emptied
      with the three-call undo in this module's docstring.

    Capture ids survive an unchanged bullet; see `captures`. **Row ranks do
    not survive anything**, and that is the one thing here worth knowing
    before issue #202 starts: `preflight.records` mints a fresh
    `rank_key.sequence` from the markdown's own row order on every run, so a
    resync re-ranks the whole board off the file. That is right today --
    markdown is truth and no row on either of his boards carries a position
    -- and it is exactly wrong the day something writes a drag-reorder into
    the store, because this would put every row back where the file says. It
    is the same boundary as the direction rule above and it arrives sooner:
    the read flip is what makes the store truth, but #202 is what first puts
    something in it that the markdown cannot say.
    """
    if board not in board_document.BOARDS:
        raise MigrationRefused(
            f"board must be one of {board_document.BOARDS}, not {board!r}")

    registry = store.read_registry()
    if registry.get("_rev") is None:
        raise MigrationRefused(
            f"{board} has never been migrated, so there is nothing to "
            "resync; seed it with --apply first")

    reuse = stored_capture_ids(board, store=store)
    docs, details, capture_docs, layout = plan(
        markdown, board, registry, reuse=reuse)
    if not docs:
        raise MigrationRefused(
            f"the markdown for {board} parses to no rows at all; refusing to "
            "prune a board down to nothing. A truncated vault read looks "
            "exactly like this -- check the file's size before retrying")

    held_ids = {ident for ids in reuse.values() for ident in ids}
    kept = sum(1 for doc in capture_docs if doc["captureId"] in held_ids)
    report = {
        "board": board,
        "rows": len(docs),
        "details": len(details),
        "projects": len(registry.get("projects") or {}),
        "milestones": len(registry.get("milestones") or {}),
        "captures": len(capture_docs),
        "captures_kept": kept,
        "captures_minted": len(capture_docs) - kept,
        "layout_blocks": len(layout),
        "applied": bool(apply),
        "written": 0,
        "deleted": 0,
        "stored": 0,
        "captures_written": 0,
        "captures_deleted": 0,
        "captures_stored": 0,
        "layout_stored": 0,
    }
    if not apply:
        return report

    # Same order as `migrate` and for the same reason: the registry first,
    # so no stored row can point at a project id the store has never held.
    store.write_registry(registry)
    written = store.write_rows(board, docs)
    if written.get("failures"):
        raise MigrationRefused(
            f"{len(written['failures'])} row(s) failed to write; the store "
            f"now holds a partial resync of {board} and --status will say so")
    report["written"] = written.get("written") or 0
    report["deleted"] = written.get("deleted") or 0
    report["stored"] = len(store.stored_documents(board))

    stored_captures = store.write_captures(board, capture_docs)
    if stored_captures.get("failures"):
        raise MigrationRefused(
            f"{len(stored_captures['failures'])} capture(s) failed to write; "
            f"the store now holds a partial resync of {board} and --status "
            "will say so")
    report["captures_written"] = stored_captures.get("written") or 0
    report["captures_deleted"] = stored_captures.get("deleted") or 0
    report["captures_stored"] = len(store.stored_capture_documents(board))
    store.write_layout(board, layout)
    report["layout_stored"] = len(store.read_layout(board) or [])
    return report


# `differences`, `layout_differences` and their helpers moved into
# `agora_runner.board_publish` (Cycle 1397): the site draws his board file
# after every write now and `tools/` is not in its image. Imported back under
# the same names, so `board_migrate.differences` still answers.
from agora_runner.board_publish import (  # noqa: E402,F401
    _differing_keys, _head, differences, layout_differences)


def status(markdown, board, store=board_store):
    """Does the live store still answer what this markdown parses to?

    Returns `(verdict, problems)` and writes nothing at all. Three
    verdicts: `NEVER MIGRATED`, `AGREES`, `DRIFTED`.

    This is the check the switchover needs and `migrate` structurally
    cannot give: `migrate` refuses a board that already holds records, so
    the only board it can say anything about is one nobody has seeded. The
    seeded board is the one that can go wrong. **`board_records.contents`
    refuses an unmigrated store loudly and answers a stale one silently**,
    so from the moment a board is seeded the `UnmigratedStore` refusal that
    protects every unconverted reader is gone and nothing replaces it --
    his boards are still served from markdown, so every edit he makes
    drifts the store further from what he sees, with no instrument that can
    tell. This is that instrument, and it is read-only on purpose: it is
    safe to run against production on any cycle.

    All four key ranges, not the three `contents` covers. `UnmigratedStore`
    is caught and reported as a verdict rather than raised, because "never
    written" is an answer to this question; any other `RecordError` is a
    store that exists and cannot be read, which is not, and it propagates.
    """
    try:
        got = board_records.contents(board, store=store)
    except board_records.UnmigratedStore as exc:
        return "NEVER MIGRATED", [str(exc)]
    problems = differences(preflight.board_contents(markdown), got)
    problems += layout_differences(markdown, board, store.read_layout(board))
    return ("DRIFTED" if problems else "AGREES"), problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--board", required=True,
                        choices=sorted(board_document.BOARDS),
                        help="which board the file is")
    parser.add_argument("--file", required=True, metavar="FILE",
                        help="the board markdown file")
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without it nothing is stored")
    parser.add_argument("--status", action="store_true",
                        help="read-only: does the live store still agree "
                             "with this markdown?")
    parser.add_argument("--resync", action="store_true",
                        help="rewrite an already-seeded board from this "
                             "markdown, keeping the ids of unchanged "
                             "captures; needs --apply to write")
    args = parser.parse_args(argv)

    # Refused rather than silently preferring one, because the two modes
    # differ on whether the run writes -- and a caller who asked for both
    # cannot be assumed to have meant the writing one.
    if args.status and args.apply:
        print("REFUSED: --status is read-only; do not pass --apply with it")
        return 2
    # Same rule one door along. `--status` reads and `--resync` writes, so a
    # run carrying both has asked for opposite things about the same board
    # and there is no reading of it that is obviously what the caller meant.
    if args.status and args.resync:
        print("REFUSED: --status is read-only; do not pass --resync with it")
        return 2

    with open(args.file, encoding="utf-8") as handle:
        markdown = handle.read()

    if args.status:
        verdict, problems = status(markdown, args.board)
        print(f"board: {args.board}")
        print(f"status: {verdict}")
        for problem in problems:
            print(f"  {problem}")
        return 0 if verdict == "AGREES" else 2

    run = resync if args.resync else migrate
    try:
        report = run(markdown, args.board, apply=args.apply)
    except MigrationRefused as exc:
        print(f"REFUSED: {exc}")
        return 2

    # Printed off the report's own keys rather than a per-mode list, so a
    # field added to one report cannot go unprinted. The order is fixed by
    # the report dicts, which are written in the order a reader wants them.
    for key, value in report.items():
        print(f"{key}: {value}")
    if not args.apply:
        print("dry run -- nothing was written; pass --apply to store it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
