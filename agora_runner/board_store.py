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
holds `ticket:<path>:<n>` and this holds `board:<board>:<n>`, two id
namespaces in one database, because CouchDB is one database per vault and
a second one would need its own credentials, its own backup and its own
reason.

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

## A document whose content has not changed is not written

Straight from `ticket_docs.write_board`, and for the same measured reason:
rewriting every row on every call spreads the write amplification of the
1.15 MB markdown document across four hundred revisions instead of
removing it. A status change touches one row and must write one document.
The `unchanged` count in the summary is what says whether that held.
"""

import json
import urllib.parse

from . import board_document, ticket_docs

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


def _range_query(board, include_docs=True):
    prefix = f"board:{board}:"
    query = {
        "startkey": json.dumps(prefix),
        "endkey": json.dumps(prefix + _ID_MAX),
    }
    if include_docs:
        query["include_docs"] = "true"
    return urllib.parse.urlencode(query)


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
    on purpose -- one moved row rather than a whole board. It is not the
    default: a migration that silently left deleted rows behind is the
    failure this store exists to make impossible, so dropping rows is what
    you get unless you say otherwise.
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
