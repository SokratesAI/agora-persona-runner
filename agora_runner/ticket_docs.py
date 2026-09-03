"""One CouchDB document per ticket -- slice 2 of the store migration.

Slice 1 (`agora_runner.ticket_store`, runner#661) proved the only claim
that had to hold before any of this was safe: a ticket can be lifted out
of one of the owner's board files and put back without losing a byte.
This is the write. It turns those records into CouchDB documents, one per
ticket plus one layout document per board, and reads them back into the
same records so `ticket_store.to_markdown` still renders the file.

**Nothing reads a board from here yet.** The site, the board tools and
the Nova app all still read the markdown, and the markdown is still the
source of truth. What this buys is that the tickets now also exist as
documents that can be written one at a time -- which is the whole point
of the migration, because today a status change rewrites a 1.15 MB
document and most of the 38.2 MB of dead revisions the nightly
compaction reclaims is that rewrite happening over and over.

Three decisions worth knowing before reading the code.

**Its own database, not the vault's.** These documents are not files, and
a LiveSync database is a set of files -- `vault_bulk_list("")` walks
`_all_docs` and treats every row it finds as a vault document. Writing 430
non-file documents into that database would put them in front of every
tool that lists it. `nova_tickets` is a separate database, so the blast
radius of this whole slice is one `DELETE /nova_tickets`.

**The document id is the board path, not a slug for it.** A slug would be
a second copy of the truth, kept in a table here, going stale exactly the
way every other hand-maintained mapping in this loop has. `ticket:<the
board's vault path, lowercased>:<number>` needs no table and is
unambiguous, and the `ticket:` prefix cannot collide with a file path.

**A ticket that left the markdown is deleted, not left behind.** A write
that only ever adds and updates leaves a deleted ticket readable forever,
so the read-back would render a row his file no longer has. `write_board`
diffs against what is already there and tombstones the difference.
"""

import base64
import json
import os
import urllib.parse

from . import ticket_store
from .config import COUCHDB_URL, COUCHDB_USER, COUCHDB_PASSWORD
from .http_util import http_json


# Its own database. See the module docstring: these are not vault files and
# they must not appear in a listing of one.
TICKET_DB = os.environ.get("COUCHDB_TICKET_DB", "nova_tickets")

# The highest code point, so a prefix scan of `_all_docs` is a key range
# rather than a full-database read. Same trick and same reason as
# `vault._id_range`; not imported from there because that module is the
# vault client and this database is deliberately not the vault.
_ID_MAX = "￿"


def ticket_doc_id(path, number):
    return f"ticket:{path.lower()}:{int(number)}"


def layout_doc_id(path):
    return f"board:{path.lower()}"


def to_documents(path, records, source_rev=None):
    """`{tickets, layout}` -> the CouchDB documents for one board.

    One `type: "ticket"` document per ticket and one `type: "board"`
    document carrying the layout. The layout is what makes the render
    total, so it belongs to the board rather than to any ticket.

    `source_rev` is the `_rev` the markdown had when these documents were
    built, stamped on the board document so a reader can ask whether the
    store is still current without fetching the markdown to compare
    against -- see `currency`. **A caller that does not know the rev
    stamps nothing, and that clears any stamp already there**, which is
    the safe direction: an unknown answer can never be mistaken for a
    current one, and keeping the old stamp beside newer content would
    claim currency the store cannot prove.
    """
    docs = [
        {
            "_id": ticket_doc_id(path, ticket["number"]),
            "type": "ticket",
            "board": path,
            "number": ticket["number"],
            "ticket": ticket,
        }
        for ticket in records["tickets"]
    ]
    docs.append({
        "_id": layout_doc_id(path),
        "type": "board",
        "board": path,
        # JSON has no tuples, so a layout block round-trips as a list.
        # `ticket_store.to_markdown` unpacks it either way.
        "layout": [list(block) for block in records["layout"]],
        "tickets": len(records["tickets"]),
        "sourceRev": source_rev,
    })
    return docs


def from_documents(docs):
    """The documents for one board -> `{tickets, layout}`.

    The inverse of `to_documents`, and the thing the round-trip test
    actually exercises: feed the result to `ticket_store.to_markdown` and
    the board file comes back.
    """
    layout = None
    tickets = []
    for doc in docs:
        if doc.get("type") == "board":
            layout = [tuple(block) for block in doc.get("layout") or []]
        elif doc.get("type") == "ticket":
            tickets.append(doc["ticket"])
    if layout is None:
        raise KeyError("no board document -- the layout is what renders the file")
    # `to_records` hands them out highest number first; keep that, so a
    # caller comparing the two sides is comparing like with like.
    tickets.sort(key=lambda ticket: ticket["number"], reverse=True)
    return {"tickets": tickets, "layout": layout}


def credentials():
    """`(url, user, password)` for the ticket database, from either pod.

    The two pods spell the same CouchDB three different ways: the runner
    exports `COUCHDB_*`, which is what `config` reads, and the bridge pod
    -- the one a cycle's `Bash` runs in -- exports `CDB_BASE`, `CDB_USER`
    and `CDB_PASS` instead. `prompt.md` writes that split down as a rule
    to remember (`cycle_health` must run in the runner pod or it reads an
    empty journal and certifies a healthy loop from a blind instrument).

    This falls back rather than restating the rule, so a check on this
    database answers from wherever it is run. It is deliberately scoped to
    the ticket store and not fixed in `config`: `CDB_NOVA_DB` is also set
    on the bridge pod, and mapping that one across would switch the vault
    client's routing there as a side effect of a credential change.
    """
    return (
        os.environ.get("COUCHDB_URL") or os.environ.get("CDB_BASE") or COUCHDB_URL,
        os.environ.get("COUCHDB_USER") or os.environ.get("CDB_USER") or COUCHDB_USER,
        os.environ.get("COUCHDB_PASSWORD") or os.environ.get("CDB_PASS")
        or COUCHDB_PASSWORD,
    )


def _req(method, path, body=None, timeout=60):
    url, user, password = credentials()
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    return http_json(method, f"{url}/{path}", body,
                     {"Authorization": f"Basic {auth}"}, timeout=timeout)


def ensure_database():
    """Create the ticket database if it is not there. Idempotent.

    CouchDB answers 412 for a database that already exists, which is a
    success for this caller and the only reason this is not a bare PUT.
    """
    status, body = _req("PUT", TICKET_DB)
    if status in (201, 202, 412):
        return True, status
    return False, f"{status} {json.dumps(body)[:200]}"


def _existing(path):
    """`{doc_id: the stored document}` for everything already held for one board.

    This used to return `{doc_id: _rev}`, which was all a full rewrite
    needed. `write_board` now sends only the documents whose content
    actually changed, so it needs the stored content to compare against,
    and `include_docs` costs one flag on a request that was already being
    made.
    """
    prefix = f"ticket:{path.lower()}:"
    query = urllib.parse.urlencode({
        "startkey": json.dumps(prefix),
        "endkey": json.dumps(prefix + _ID_MAX),
        "include_docs": "true",
    })
    status, body = _req("GET", f"{TICKET_DB}/_all_docs?{query}")
    if status != 200:
        raise RuntimeError(f"listing {path}: {status} {json.dumps(body)[:200]}")
    stored = {row["id"]: row["doc"] for row in body.get("rows", []) if row.get("doc")}
    status, body = _req("GET", f"{TICKET_DB}/{urllib.parse.quote(layout_doc_id(path), safe='')}")
    if status == 200:
        stored[body["_id"]] = body
    elif status != 404:
        raise RuntimeError(f"reading the layout of {path}: {status}")
    return stored


def _payload(doc):
    """A stored document without its revision, so it compares to a fresh one.

    `_rev` is the only field CouchDB adds, and it changes on every write
    by definition -- comparing it would make every document differ from
    itself and the skip below would never fire once.
    """
    return {key: value for key, value in doc.items() if key != "_rev"}


def write_board(path, records, source_rev=None):
    """Write one board's tickets as documents. Returns a summary dict.

    Every document is sent with the `_rev` already stored under its id, so
    a second run updates rather than conflicting, and an id that is stored
    but no longer produced is tombstoned -- a ticket he deleted from the
    markdown must not survive here as a row the read-back would render.

    **A document whose content has not changed is not sent**, which is the
    whole reason the store exists. Slice 2 rewrote all 445 documents on
    every call, so keeping the store current would have cost the same
    write amplification as the 1.15 MB markdown document it is meant to
    replace -- just spread across 445 revisions instead of one. A status
    change touches one ticket, so it should write one document, and the
    `unchanged` count in the summary is what says whether that held.
    """
    docs = to_documents(path, records, source_rev=source_rev)
    stored = _existing(path)
    written = []
    unchanged = 0
    for doc in docs:
        held = stored.get(doc["_id"])
        if held is not None and _payload(held) == doc:
            unchanged += 1
            continue
        written.append(dict(doc, **({"_rev": held["_rev"]} if held else {})))
    produced = {doc["_id"] for doc in docs}
    tombstones = [{"_id": doc_id, "_rev": held["_rev"], "_deleted": True}
                  for doc_id, held in stored.items() if doc_id not in produced]
    if not written and not tombstones:
        return {"written": 0, "deleted": 0, "unchanged": unchanged, "failures": []}
    status, body = _req("POST", f"{TICKET_DB}/_bulk_docs",
                        {"docs": written + tombstones})
    if status not in (200, 201):
        raise RuntimeError(f"writing {path}: {status} {json.dumps(body)[:200]}")
    failures = [row for row in body if row.get("error")]
    return {
        "written": len(written),
        "deleted": len(tombstones),
        "unchanged": unchanged,
        "failures": failures,
    }


def read_board(path):
    """Read one board back out of CouchDB as `{tickets, layout}`."""
    prefix = f"ticket:{path.lower()}:"
    query = urllib.parse.urlencode({
        "startkey": json.dumps(prefix),
        "endkey": json.dumps(prefix + _ID_MAX),
        "include_docs": "true",
    })
    status, body = _req("GET", f"{TICKET_DB}/_all_docs?{query}")
    if status != 200:
        raise RuntimeError(f"reading {path}: {status} {json.dumps(body)[:200]}")
    docs = [row["doc"] for row in body.get("rows", []) if row.get("doc")]
    status, layout = _req(
        "GET", f"{TICKET_DB}/{urllib.parse.quote(layout_doc_id(path), safe='')}")
    if status != 200:
        raise RuntimeError(f"reading the layout of {path}: {status}")
    docs.append(layout)
    return from_documents(docs)


def render_from_couch(path):
    """The board file, rendered from what is stored in CouchDB."""
    return ticket_store.to_markdown(read_board(path))


# The four board files. These are the only vault paths this store holds, and
# a write to anything else must never reach `nova_tickets`.
#
# It lives here rather than in `tools.ticket_migrate`, where slice 2 first
# wrote it, because `agora_runner` may not import from `tools` -- and the
# write-through below is in `agora_runner`. The migration imports it from
# here now, so there is still exactly one copy.
BOARDS = (
    "projects/sokrates/projects/nova/issues.md",
    "projects/sokrates/projects/nova/ideas.md",
    "projects/sokrates/projects/agora/nova/resources/issues.md",
    "projects/sokrates/projects/agora/nova/resources/ideas.md",
)

_BOARD_KEYS = frozenset(board.lower() for board in BOARDS)


def is_board(path):
    """Is `path` one of the four board files?

    Lowercased on both sides because vault paths are: `_vault_put_raw`
    normalises the `_id` and the stored `path` field to lowercase, so a
    caller writing mixed case reaches the same document and has to reach
    the same tickets.
    """
    return (path or "").lower() in _BOARD_KEYS


def push_markdown(path, source, source_rev=None):
    """Update the stored documents for one board from its new markdown.

    Returns the `write_board` summary, or `None` when `path` is not a
    board -- which is every other write in the vault, so the common case
    is one set membership test and no request at all.

    This is the write side of the migration. Slice 2 loaded the tickets
    and slice 3 watched them go stale; the markdown is still the source of
    truth and every writer still writes markdown, so the store can only
    stay current by following the markdown write that just happened.
    """
    if not is_board(path):
        return None
    ensure_database()
    # The row index lives beside the tickets it projects, so a store that
    # has documents in it always has the view over them -- a reader that
    # had to remember to build its own index would be a second thing to
    # keep current. It is a no-op unless the map function changed.
    ensure_views()
    return write_board(path, ticket_store.to_records(source),
                       source_rev=source_rev)


# The fields a *list* of rows needs: his board table, the ranking in
# `nova_next.rank`, and the chips on the site's board page. Everything
# else a ticket carries -- `cells`, `detailHeading`, `details` -- is the
# write-up, and the write-up is the bulk. Measured on `ideas.md`,
# 2026-09-03: the 241 ticket documents are 760KB read whole and 101KB read
# as rows, and the vault document they came from is 656KB.
#
# It is one tuple rather than a list here and a list in the JavaScript
# below, because the map function is generated from this. A hand-written
# map would be a second copy of the field names, going stale the first
# time `ticket_store` renames one -- and it would fail silently, because a
# CouchDB view emitting `undefined` is a row with a missing key, not an
# error.
ROW_FIELDS = (
    "number", "title", "status", "statusKey", "updated",
    "priority", "priorityKey", "project", "where", "done",
)

ROWS_DDOC_ID = "_design/rows"
ROWS_VIEW = "by_board"


def _rows_map_js():
    """The view's map function, generated from `ROW_FIELDS`.

    Keyed `[board, number]` so one board is a key range rather than a scan
    of every board, the same reason `_existing` uses `startkey`/`endkey`.
    """
    projection = ", ".join(f"{field}: t.{field}" for field in ROW_FIELDS)
    return (
        "function (doc) {\n"
        "  if (doc.type === 'ticket' && doc.ticket) {\n"
        "    var t = doc.ticket;\n"
        f"    emit([doc.board, doc.number], {{{projection}}});\n"
        "  }\n"
        "}"
    )


def rows_design_document():
    return {
        "_id": ROWS_DDOC_ID,
        "language": "javascript",
        "views": {ROWS_VIEW: {"map": _rows_map_js()}},
    }


def ensure_views():
    """Put the row-projection design document. Returns what it did.

    **An unchanged design document is not rewritten**, and that is not a
    tidiness: writing a design document invalidates its index, so a PUT on
    every board write would rebuild 410 rows every time a status changed.
    That is precisely the write amplification `write_board` exists to end,
    moved one layer down where nothing would have measured it. The first
    query after a real change costs the rebuild -- 2.8s against 241
    documents, measured 2026-09-03 -- and every one after it is 0.1s.
    """
    wanted = rows_design_document()
    status, held = _req("GET", f"{TICKET_DB}/{urllib.parse.quote(ROWS_DDOC_ID, safe='')}")
    if status == 200:
        if held.get("views") == wanted["views"]:
            return "unchanged"
        wanted["_rev"] = held["_rev"]
    elif status != 404:
        raise RuntimeError(f"reading {ROWS_DDOC_ID}: {status} {json.dumps(held)[:200]}")
    status, body = _req(
        "PUT", f"{TICKET_DB}/{urllib.parse.quote(ROWS_DDOC_ID, safe='')}", wanted)
    if status not in (200, 201):
        raise RuntimeError(f"writing {ROWS_DDOC_ID}: {status} {json.dumps(body)[:200]}")
    return "written"


def read_rows(path):
    """One board's tickets as rows only -- no write-ups, no layout.

    The read the board *list* actually needs. `read_board` above is the
    read that renders the file and it has to carry every byte of his
    prose; a list of 241 rows does not, and the difference is 760KB
    against 101KB on `ideas.md`.

    **Ordered the way the board file orders its rows**, which is not the
    same as ordering by number and this used to sort by number. The page
    breaks a tie on `a.index - b.index`, the row's position in the list it
    was handed (`app.js`, `sortItems`), so the order is part of what a
    reader has to reproduce and not a presentation detail. Measured
    2026-09-03 against the live boards: 63 of 170 rows on `issues.md` and
    51 of 241 on `ideas.md` sit at a different position under a
    number-descending sort, and every one of the 411 agrees with its
    ticket document on all ten fields -- so a reader switched onto the
    number-sorted version would have reshuffled his board while every
    field-by-field check stayed green.

    The order comes off the board's layout document, which is where
    `write_board` already records it, rather than out of a new field on
    each ticket. A position stored per ticket would have to be rewritten
    on every row above an insertion, which is the write amplification this
    whole store exists to end.

    A ticket the layout does not name is real drift rather than a shape to
    absorb quietly, so those go last, highest number first, and
    `tools.ticket_drift` is what reports them.
    """
    query = urllib.parse.urlencode({
        "startkey": json.dumps([path]),
        "endkey": json.dumps([path, {}]),
    })
    status, body = _req(
        "GET",
        f"{TICKET_DB}/{urllib.parse.quote(ROWS_DDOC_ID, safe='')}"
        f"/_view/{ROWS_VIEW}?{query}")
    if status != 200:
        raise RuntimeError(f"reading rows of {path}: {status} {json.dumps(body)[:200]}")
    rows = [row["value"] for row in body.get("rows", [])]
    order = {number: position for position, number in enumerate(row_order(path))}
    rows.sort(key=lambda row: (
        order.get(row["number"], len(order)),
        -row["number"] if row["number"] not in order else 0,
    ))
    return rows


def row_order(path):
    """The row numbers of one board, in the order its file lists them.

    Read off the layout document `write_board` writes, so there is one
    record of the order rather than a second copy kept beside the rows.
    A board with no layout document has no order to give and returns `[]`
    -- `read_rows` then falls back to number-descending, which is what it
    did before the layout was consulted at all.
    """
    status, body = _req(
        "GET", f"{TICKET_DB}/{urllib.parse.quote(layout_doc_id(path), safe='')}")
    if status == 404:
        return []
    if status != 200:
        raise RuntimeError(f"reading the layout of {path}: {status}")
    return [block[1] for block in body.get("layout") or [] if block and block[0] == "row"]


# `currency` verdicts. Three, not two, and the third is the point: a store
# that cannot say whether it is current must never be read as one that
# said yes.
CURRENT = "current"
STALE = "stale"
UNKNOWN = "unknown"


def stored_source_rev(path):
    """The markdown `_rev` this board's documents were built from, or None.

    None covers three different situations on purpose -- no board
    document, a board document written before this field existed, and a
    write whose caller did not know the rev (`tools.board_*`, which writes
    the file from the bridge pod in a separate process). All three mean
    the same thing to `currency`: the store cannot prove it is current.
    """
    status, body = _req(
        "GET", f"{TICKET_DB}/{urllib.parse.quote(layout_doc_id(path), safe='')}")
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"reading the layout of {path}: {status}")
    return body.get("sourceRev")


def currency(path, live_rev):
    """Is this board's store current with the markdown at `live_rev`?

    Returns `(verdict, detail)`. This is the check that has to exist
    before any reader may stop fetching the markdown, and it is why the
    stamp exists at all.

    Today `nova_site._rows_from_store` proves the store agrees with the
    file by fetching the file and comparing 411 rows field by field, which
    is the strongest check available and also the reason the migration
    saves nothing yet: the fetch it would remove is the fetch the check
    depends on. A revision is the one thing that answers "has his file
    moved since these documents were built" without reading the file --
    every writer goes through CouchDB and every write moves the `_rev`, so
    a matching rev covers the writers this module has never heard of as
    well as the ones it hooks.

    `UNKNOWN` is a verdict and not a failure. A board written by
    `tools.board_put` from the bridge pod carries no stamp, because that
    path writes the file in a different process against a client with no
    route to this one -- so a reader gets "cannot say", falls back to the
    markdown, and is exactly as correct as it is today.
    """
    stored = stored_source_rev(path)
    if not stored:
        return UNKNOWN, "the board document carries no source revision"
    if not live_rev:
        return UNKNOWN, "no live revision to compare against"
    if stored == live_rev:
        return CURRENT, f"built from {stored}"
    return STALE, f"built from {stored}, the file is at {live_rev}"
