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


def to_documents(path, records):
    """`{tickets, layout}` -> the CouchDB documents for one board.

    One `type: "ticket"` document per ticket and one `type: "board"`
    document carrying the layout. The layout is what makes the render
    total, so it belongs to the board rather than to any ticket.
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
    """`{doc_id: _rev}` for everything already stored for one board."""
    prefix = f"ticket:{path.lower()}:"
    query = urllib.parse.urlencode({
        "startkey": json.dumps(prefix),
        "endkey": json.dumps(prefix + _ID_MAX),
    })
    status, body = _req("GET", f"{TICKET_DB}/_all_docs?{query}")
    if status != 200:
        raise RuntimeError(f"listing {path}: {status} {json.dumps(body)[:200]}")
    revs = {row["id"]: row["value"]["rev"] for row in body.get("rows", [])}
    status, body = _req("GET", f"{TICKET_DB}/{urllib.parse.quote(layout_doc_id(path), safe='')}")
    if status == 200:
        revs[body["_id"]] = body["_rev"]
    elif status != 404:
        raise RuntimeError(f"reading the layout of {path}: {status}")
    return revs


def write_board(path, records):
    """Write one board's tickets as documents. Returns a summary dict.

    Every document is sent with the `_rev` already stored under its id, so
    a second run updates rather than conflicting, and an id that is stored
    but no longer produced is tombstoned -- a ticket he deleted from the
    markdown must not survive here as a row the read-back would render.
    """
    docs = to_documents(path, records)
    revs = _existing(path)
    written = [dict(doc, **({"_rev": revs[doc["_id"]]} if doc["_id"] in revs else {}))
               for doc in docs]
    produced = {doc["_id"] for doc in docs}
    tombstones = [{"_id": doc_id, "_rev": rev, "_deleted": True}
                  for doc_id, rev in revs.items() if doc_id not in produced]
    status, body = _req("POST", f"{TICKET_DB}/_bulk_docs",
                        {"docs": written + tombstones})
    if status not in (200, 201):
        raise RuntimeError(f"writing {path}: {status} {json.dumps(body)[:200]}")
    failures = [row for row in body if row.get("error")]
    return {
        "written": len(written),
        "deleted": len(tombstones),
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
