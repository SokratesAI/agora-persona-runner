"""One board, read out of the record store in `parse_board`'s shape.

This is the seam issue #203's switchover commit hangs on. Twenty-one
modules call `nova_boards.parse_board(markdown)` today and every one of
them wants the same four keys back: `captures`, `captureReplies`,
`items` and `details`. `board_document` can turn a single document into
each of those pieces and `board_store` can fetch the documents, but
nothing joined the two, so each of the twenty-one would otherwise have
written its own join -- twenty-one chances to sort captures the wrong
way or to read a row document as a capture.

It is deliberately **not** a facade over the markdown. `board-records.md`
rules that out (*"straight to records, no facade phase"*): the danger in
this migration is two live sources of truth, and an accessor that can
still fall back to a parser is exactly how you get one. Everything here
reads CouchDB or it raises.

Two joins live here and nowhere else:

**Rows and captures share a key range.** `board_store.read_rows` selects
on the literal prefix `board:<board>:`, and a capture's id is
`board:<board>:capture:<captureId>` -- inside that range on purpose, so
one query fetches both. Telling them apart is therefore this module's
job, and it is done on the document's own `type` field rather than on
the id, because `type` is what the CouchDB views key on and a document
that disagrees with its own type is a bug worth raising rather than
silently filing under the other kind.

**A row carries `projectId`/`milestoneId`; `parse_board` carried names.**
The registry is the only place the two are joined, and a row pointing at
an id the registry does not hold raises rather than falling back to the
default project. That fallback is right for a row with *no* project --
`from_document` does it, for a row nobody has re-filed -- and wrong for a
dangling id, which is precisely the orphan stable ids exist to prevent.
Rendering it as `Nova` would put the orphan on his board looking re-filed.
"""

from agora_runner import board_document, board_store


class RecordError(ValueError):
    """A stored document contradicts the board it was read from."""


class UnmigratedStore(RecordError):
    """The store has never been written, so it cannot answer for a board.

    It subclasses `RecordError` on purpose. Every reader issue #203 moves
    already catches that to mean *"this sweep did not see that board"* and
    exits non-zero, which is the right answer here too -- so the guard
    reaches all twenty-one without twenty-one edits, and a reader that
    forgets it inherits the safe behaviour rather than the silent one.
    """


def split_documents(docs):
    """One board's documents -> `(rows, captures)`, order preserved.

    Told apart by `type`. An unknown type raises: `read_rows` promises
    everything in the board's key range, so a third kind of document
    appearing there is a naming decision somebody made without reading
    `board_store.REGISTRY_ID`'s comment, and guessing which list it
    belongs in would hide that.
    """
    rows, captures = [], []
    for doc in docs:
        kind = doc.get("type")
        if kind == board_document.DOCUMENT_TYPE:
            rows.append(doc)
        elif kind == board_document.CAPTURE_DOCUMENT_TYPE:
            captures.append(doc)
        else:
            raise RecordError(
                f"document {doc.get('_id')!r} has type {kind!r}, which is "
                f"neither {board_document.DOCUMENT_TYPE!r} nor "
                f"{board_document.CAPTURE_DOCUMENT_TYPE!r}")
    return rows, captures


def _names(registry, doc):
    """`(project_name, milestone_name)` for one row, from the registry."""
    project_id = doc.get("projectId")
    milestone_id = doc.get("milestoneId")
    project_name = milestone_name = None
    if project_id is not None:
        entry = registry.get("projects", {}).get(project_id)
        if entry is None:
            raise RecordError(
                f"row {doc.get('_id')!r} points at project {project_id!r}, "
                "which the registry does not hold")
        project_name = entry.get("name")
    if milestone_id is not None:
        entry = registry.get("milestones", {}).get(milestone_id)
        if entry is None:
            raise RecordError(
                f"row {doc.get('_id')!r} points at milestone "
                f"{milestone_id!r}, which the registry does not hold")
        milestone_name = entry.get("name")
    return project_name, milestone_name


def contents(board, store=board_store):
    """One board's records -> exactly what `parse_board` returned.

    `{"captures": [...], "captureReplies": [[...]], "items": [...],
    "details": {number: markdown}}`, with `items` in `board_store.sort_key`
    order and the two capture lists in `captures_map`'s.

    `store` is injected so a test can hand over a fake without a CouchDB;
    it needs `read_rows` and `read_registry`.

    **An unmigrated store raises rather than reading as a clean board.**
    `read_rows` answers `[]` for a board that has never been written and for
    a board whose every row was closed, and those are the same value; every
    reader below this seam would print "no drift", rank nothing, and exit 0
    on either. The registry tells them apart, because `board_migrate` writes
    it on every run and `read_registry` only omits `_rev` when the document
    is genuinely absent -- so "no revision" means *nothing has ever been
    migrated*, while an empty board under a stored registry is a real,
    reportable empty board and still returns.
    """
    registry = store.read_registry()
    if registry.get("_rev") is None:
        raise UnmigratedStore(
            "the record store has never been written, so it cannot answer "
            f"for board {board!r}: the registry document has no revision. Run "
            "`python3 -m tools.board_migrate --board <board> --file <md> "
            "--apply` first")
    rows, captures = split_documents(store.read_rows(board))
    items = []
    for doc in rows:
        project_name, milestone_name = _names(registry, doc)
        items.append(board_document.from_document(
            doc, project_name=project_name, milestone_name=milestone_name))
    return {
        **board_document.captures_map(captures),
        "items": items,
        "details": board_document.details_map(rows),
    }
