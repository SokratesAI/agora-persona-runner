"""Read and write board row records in CouchDB, ordered by rank.

The fifth primitive of issue #203, the owner's decision of 2026-09-08 that
the boards get a real schema (`projects/sokrates/projects/nova/board-records.md`,
`status: approved`). `rank_key`, `entity_id`, `board_view` and
`board_document` between them say what a record *is* and what it renders
back to. None of them touches a database. This is the layer that does, and
it is deliberately the only one: the spec's whole argument is that a
half-migration with two sources of truth is worse than either store alone,
so every reader ends up here or nowhere.

It sits on `ticket_docs` for credentials and HTTP rather than opening a
second connection, and shares that module's database -- the ticket mirror
holds `ticket:<path>:<n>` and this holds `board:<board>:<n>` **and**
`capture:<board>:<id>`, three id namespaces in one database, because
CouchDB is one database per vault and a second one would need its own
credentials, its own backup and its own reason.

The two of those that are one board's are two *separate* `_all_docs`
ranges, and that is not a detail: a capture id under `board:` would be
tombstoned by `write_rows`' prune, so it is outside deliberately, and a
reader that fetches one range and sorts by `type` finds no captures at
all. `read_rows` and `read_captures` are both needed to read one board.

**Nothing calls this yet**, for the same reason the four before it call
nothing: the store, the migration and the 23 markdown readers land in one
change.

## Order is not the order CouchDB hands back

`_all_docs` returns ids in lexical order, and the ids end in a decimal
number, so `board:issue:100` comes back before `board:issue:2`. Every
read here re-sorts in Python and no caller may lean on the wire order.

The sort itself has a trap that a test built from the live boards cannot
see. A `## Done` row carries no rank -- `board_document` leaves the field
off rather than inventing a position -- and the obvious
`sorted(key=lambda doc: doc.get("rank", ""))` puts *every unranked row
first*, because the empty string is smaller than every valid rank key.
Unranked means "nobody has placed this", which belongs at the end, so the
key is a pair: ranked before unranked, then the rank, then the number.
The number tie-break is there so two rows that somehow share a rank still
come back in one fixed order -- a read that is only *mostly* deterministic
is the kind of bug that shows up as a diff in the generated markdown view
and nowhere else.

## The registry is one document, and its write is conditional

`entity_id` mints project and milestone ids into a plain dict and touches
no database. That dict has to survive somewhere or every migration run
mints a fresh set of ids for the same names, so it is stored here, as one
document rather than one per project: a milestone is only valid against a
project id in the *same* snapshot (`ensure_milestone` on an unknown
project is an error), and splitting the two maps would let one half land
and the other fail.

The write sends the revision the caller read at, never the current one, so
two cycles minting against two snapshots get a `RegistryConflict` instead
of one silently overwriting the other's ids. That is the case the row path
does not have: a row write is per row and per board, and a lost row write
is a row; a lost registry write is every id on it.

## A document whose content has not changed is not written

Straight from `ticket_docs.write_board`, and for the same measured reason:
rewriting every row on every call spreads the write amplification of the
1.15 MB markdown document across four hundred revisions instead of
removing it. A status change touches one row and must write one document.
The `unchanged` count in the summary is what says whether that held.
"""

import json
import urllib.parse

from . import board_document, entity_id, ticket_docs

#: The end of an `_all_docs` key range. Every id under the prefix sorts
#: below it, and nothing real contains it.
_ID_MAX = "￿"


class StoreError(RuntimeError):
    """CouchDB refused a read or a write of a board record."""


def _check_board(board):
    if board not in board_document.BOARDS:
        raise board_document.DocumentError(
            f"board must be one of {board_document.BOARDS}, not {board!r}")
    return board


def sort_key(doc):
    """The one total order over board records.

    Ranked rows first in rank order, then unranked ones by number. See the
    module docstring for why the absent rank is not simply an empty string.
    """
    rank = doc.get("rank")
    unranked = rank is None or rank == ""
    return (1 if unranked else 0, "" if unranked else rank, doc.get("number") or 0)


def in_order(docs):
    """`docs` sorted by `sort_key`. A new list; the argument is untouched."""
    return sorted(docs, key=sort_key)


def ensure_database():
    """Create the shared database if it is not there. Idempotent."""
    return ticket_docs.ensure_database()


def _prefix_query(prefix, include_docs=True):
    query = {
        "startkey": json.dumps(prefix),
        "endkey": json.dumps(prefix + _ID_MAX),
    }
    if include_docs:
        query["include_docs"] = "true"
    return urllib.parse.urlencode(query)


def _range_query(board, include_docs=True):
    return _prefix_query(f"board:{board}:", include_docs=include_docs)


def _capture_range_query(board, include_docs=True):
    """The **other** key range of one board -- its captures, not its rows.

    Captures deliberately do not live under `board:<board>:`.
    `board_document.capture_document_id` puts them under `capture:<board>:`
    and says why: an id inside the row range would be handed back by
    `read_rows` as a row with no number, and `write_rows`' default
    `prune=True` would tombstone every capture the owner has written the
    first time a migration wrote the rows alone.

    The consequence is that one board is **two** queries, and that is the
    thing to keep hold of. A reader that fetches the row range and then
    sorts captures out of it by `type` gets an empty capture list against a
    real store, forever, while a fake store that answers `read_rows` with a
    hand-built list of both kinds agrees with it -- which is exactly how
    `board_records.contents` shipped reading zero captures with a green
    test.
    """
    return _prefix_query(f"capture:{board}:", include_docs=include_docs)


def stored_documents(board):
    """`{doc_id: the stored document}` for one board, unsorted.

    The write path needs the stored *content* to compare against, not just
    a revision, which is why this returns whole documents.
    """
    _check_board(board)
    status, body = ticket_docs._req(
        "GET", f"{ticket_docs.TICKET_DB}/_all_docs?{_range_query(board)}")
    if status != 200:
        raise StoreError(f"listing {board} records: {status} {json.dumps(body)[:200]}")
    return {row["id"]: row["doc"] for row in body.get("rows", []) if row.get("doc")}


def read_rows(board):
    """Every record document for one board, in `sort_key` order."""
    return in_order(stored_documents(board).values())


def stored_capture_documents(board):
    """`{doc_id: the stored document}` for one board's captures, unsorted.

    Its own `_all_docs` range because captures have their own id prefix --
    see `_capture_range_query`.
    """
    _check_board(board)
    status, body = ticket_docs._req(
        "GET", f"{ticket_docs.TICKET_DB}/_all_docs?{_capture_range_query(board)}")
    if status != 200:
        raise StoreError(
            f"listing {board} captures: {status} {json.dumps(body)[:200]}")
    return {row["id"]: row["doc"] for row in body.get("rows", []) if row.get("doc")}


def read_captures(board):
    """Every capture document for one board, in wire order.

    Deliberately **not** run through `in_order`. A capture carries a rank
    and no number, so `sort_key`'s number tie-break has nothing to work
    with, and `board_document.captures_map` already owns the capture order
    -- ranked first in rank order, then unranked in the order they were
    handed over. Sorting here as well would decide that second half twice,
    in two places, on two rules.
    """
    return list(stored_capture_documents(board).values())


def read_row(board, number):
    """One record document, or `None` if it is not stored."""
    doc_id = board_document.document_id(_check_board(board), number)
    status, body = ticket_docs._req(
        "GET", f"{ticket_docs.TICKET_DB}/{urllib.parse.quote(doc_id, safe='')}")
    if status == 200:
        return body
    if status == 404:
        return None
    raise StoreError(f"reading {doc_id}: {status} {json.dumps(body)[:200]}")


def write_rows(board, docs, prune=True):
    """Write one board's records. Returns a summary dict.

    `docs` are documents from `board_document.to_document`. Each is sent
    with the `_rev` already stored under its id, so a second run updates
    rather than conflicting; an id that is stored but not in `docs` is
    tombstoned, because a row deleted from the board must not survive here
    as something the read-back would render.

    `prune=False` turns the tombstoning off, for a caller writing a subset
    on purpose. It is not the default: a migration that silently left
    deleted rows behind is the failure this store exists to make
    impossible, so dropping rows is what you get unless you say otherwise.

    **For one row, use `write_row` rather than this with a one-item list.**
    Both write the same document, but this one lists the whole board to
    find that row's revision, and it takes the stored revision rather than
    the one the caller read -- right for a migration, where the batch is
    the truth, and a clobber for a cycle changing one cell.
    """
    _check_board(board)
    docs = list(docs)
    for doc in docs:
        board_document._check_identity(doc)
        if doc["board"] != board:
            raise board_document.DocumentError(
                f"document {doc['_id']!r} is not on board {board!r}")
    stored = stored_documents(board)
    written = []
    unchanged = 0
    for doc in docs:
        held = stored.get(doc["_id"])
        if held is not None and ticket_docs._payload(held) == doc:
            unchanged += 1
            continue
        written.append(dict(doc, **({"_rev": held["_rev"]} if held else {})))
    produced = {doc["_id"] for doc in docs}
    tombstones = []
    if prune:
        tombstones = [{"_id": doc_id, "_rev": held["_rev"], "_deleted": True}
                      for doc_id, held in stored.items() if doc_id not in produced]
    if not written and not tombstones:
        return {"written": 0, "deleted": 0, "unchanged": unchanged, "failures": []}
    status, body = ticket_docs._req(
        "POST", f"{ticket_docs.TICKET_DB}/_bulk_docs",
        {"docs": written + tombstones})
    if status not in (200, 201):
        raise StoreError(f"writing {board} records: {status} {json.dumps(body)[:200]}")
    failures = [row for row in body if row.get("error")]
    return {
        "written": len(written),
        "deleted": len(tombstones),
        "unchanged": unchanged,
        "failures": failures,
    }


class RowConflict(StoreError):
    """The stored row moved between the read and the write."""


def write_row(doc):
    """Write one row's record, conditional on the revision it was read at.

    `write_rows(board, [doc], prune=False)` already writes a subset, and it
    is the wrong shape for the ten `tools/board_*.py` writers that change
    one cell: it lists the whole board with `include_docs=true` to find one
    `_rev`, so setting a status pulls back every stored row and every
    write-up body with it -- on the live issues board that is the 123KB of
    `# Details` `roll_health` measures, fetched to write one document. This
    reads the one document instead, which is what the spec's definition of
    done means by *"moving one row writes one document"*.

    Returns the document as it now stands, `_rev` included, so a caller
    writing twice does not have to read again.

    Three rules, and the middle one is why this is not a thin wrapper:

    - A row that is not stored is created.
    - **An update must carry the `_rev` it was read at.** A document fresh
      out of `board_document.to_document` has none, so a caller that read a
      row, changed a cell and re-minted it would otherwise be written on top
      of whatever is stored *now* -- last-writer-wins between two cycles
      editing two different cells of the same row, which is exactly the
      clobber `write_registry` refuses. Falling back to the stored revision
      would make every such write succeed, so it is refused instead.
    - A document whose content already matches what is stored is not
      written and needs no revision, so a re-run costs nothing.

    **A 409 raises `RowConflict` and is not retried**, for `write_registry`'s
    reason: the change was computed against text that lost, so the answer is
    to read the row that won and apply the change to that -- never to resend
    this body with the winner's revision.
    """
    board_document._check_identity(doc)
    # No `_check_board` beside it: `_check_identity` derives the expected id
    # through `document_id`, which refuses an unknown board itself, so a
    # second guard here would be a line no input can reach.
    board = doc["board"]
    doc_id = board_document.document_id(board, doc["number"])
    body = dict(doc, _id=doc_id)
    rev = body.pop("_rev", None)
    held = read_row(board, doc["number"])
    if held is not None and ticket_docs._payload(held) == body:
        return held
    if held is not None and rev is None:
        raise board_document.DocumentError(
            f"{doc_id} is already stored: an update must carry the `_rev` it "
            "was read at, or it overwrites whatever is there now")
    if rev is not None:
        body["_rev"] = rev
    status, answer = ticket_docs._req(
        "PUT", f"{ticket_docs.TICKET_DB}/{urllib.parse.quote(doc_id, safe='')}",
        body)
    if status == 409:
        raise RowConflict(
            f"{doc_id} moved since it was read: re-read the row, apply the "
            "change to the record that won, and write that -- do not resend "
            "this one")
    if status not in (200, 201):
        raise StoreError(f"writing {doc_id}: {status} {json.dumps(answer)[:200]}")
    return dict(body, _rev=answer["rev"])


#: The project/milestone registry, one document beside the row records.
#:
#: The id deliberately carries no third segment, so it sits *outside* every
#: `board:<board>:` key range and `stored_documents` cannot hand it back as
#: a row. That is asserted in the tests rather than left to the reader: a
#: registry that showed up in `read_rows` would reach `from_document` and
#: fail there, one layer away from the naming decision that caused it.
REGISTRY_ID = "board:registry"

#: `board_document.DOCUMENT_TYPE` is the row type and the CouchDB views key
#: on `doc.type`, so the registry needs its own or it joins a view built for
#: a different shape.
REGISTRY_TYPE = "board-registry"


class RegistryConflict(StoreError):
    """The stored registry moved between the read and the write."""


def _check_registry(registry):
    """Refuse anything that is not a registry, before it overwrites one.

    `write_registry` replaces the whole document, so a caller that passed
    `{}` -- a `new_registry()` that lost its assignment, a JSON load of the
    wrong file -- would erase every minted id in one request, and every
    `projectId` already stored on a row would become an orphan pointing at
    a project that no longer exists. There is no undo for that short of a
    database backup, so the shape is checked rather than trusted.
    """
    if not isinstance(registry, dict):
        raise board_document.DocumentError(
            f"registry must be a dict, not {type(registry).__name__}")
    for field in ("projects", "milestones"):
        if not isinstance(registry.get(field), dict):
            raise board_document.DocumentError(
                f"registry {field!r} must be a dict, not {registry.get(field)!r}")
    # `captures` is checked only when it is there, and that asymmetry is
    # deliberate. Every registry written before `entity_id.mint_capture`
    # existed has the other two maps and not this one; requiring it would
    # make the stored registry unwritable, and the recovery from that is a
    # hand-edit of the one document every `projectId` on every row points
    # at. `entity_id.capture_high_water` reads an absent map as zero, so
    # absent and empty already mean the same thing to every caller.
    if "captures" in registry and not isinstance(registry["captures"], dict):
        raise board_document.DocumentError(
            f"registry 'captures' must be a dict, not {registry['captures']!r}")
    return registry


def read_registry():
    """The stored registry document, or a fresh empty one.

    The document *is* a registry in `entity_id`'s sense -- `ensure_project`
    and `ensure_milestone` take it directly and mutate it in place -- with
    `_id`, `type` and (once stored) `_rev` alongside the two maps. Keeping
    them in one object is what lets a caller read, mint and write without
    unpacking anything, and `entity_id` only ever reaches into `projects`
    and `milestones`, so the extra keys ride along untouched.

    An absent document reads as an empty registry rather than as `None`,
    because "nothing has been minted yet" and "the registry is empty" are
    the same state and every caller would otherwise write the same
    `or new_registry()` after the call.
    """
    status, body = ticket_docs._req(
        "GET", f"{ticket_docs.TICKET_DB}/{urllib.parse.quote(REGISTRY_ID, safe='')}")
    if status == 200:
        return body
    if status == 404:
        return dict(entity_id.new_registry(), _id=REGISTRY_ID, type=REGISTRY_TYPE)
    raise StoreError(f"reading {REGISTRY_ID}: {status} {json.dumps(body)[:200]}")


def write_registry(registry):
    """Write the registry back, conditional on the revision it was read at.

    Returns the document as it now stands, with the new `_rev`, so a caller
    minting in two passes can write twice without re-reading.

    A document whose content has not changed is not written, for the same
    reason `write_rows` skips one: minting is idempotent, so a migration
    re-run mints nothing and must cost no revision.

    **A 409 raises `RegistryConflict` and is not retried.** Sending the same
    body again with the winner's `_rev` is the one thing a caller must not
    do: `entity_id.ensure_project` is idempotent against *one* snapshot, and
    two cycles that mint the same new name against two snapshots produce two
    different ids for one project -- which is the orphaning this whole piece
    exists to prevent. Re-read, run the `ensure_*` calls again against the
    text that won, and write that.
    """
    _check_registry(registry)
    doc = {key: value for key, value in registry.items() if key != "_rev"}
    doc["_id"] = REGISTRY_ID
    doc["type"] = REGISTRY_TYPE
    held = read_registry()
    if held.get("_rev") is not None and ticket_docs._payload(held) == doc:
        return held
    # The caller's revision, never the one just read. Sending the *current*
    # `_rev` would make every write succeed, which is the clobber this pair
    # exists to refuse: a caller that read, minted, and was overtaken would
    # overwrite the winner's ids instead of being told about them.
    if registry.get("_rev") is not None:
        doc["_rev"] = registry["_rev"]
    status, body = ticket_docs._req(
        "PUT", f"{ticket_docs.TICKET_DB}/{urllib.parse.quote(REGISTRY_ID, safe='')}",
        doc)
    if status == 409:
        raise RegistryConflict(
            f"{REGISTRY_ID} moved since it was read: re-read it, mint against "
            f"the registry that won, and write that -- do not resend this one")
    if status not in (200, 201):
        raise StoreError(f"writing {REGISTRY_ID}: {status} {json.dumps(body)[:200]}")
    return dict(doc, _rev=body["rev"])
