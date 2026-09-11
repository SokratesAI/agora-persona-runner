"""The CouchDB connection the #203 record store is built on.

This module used to be the `nova_tickets` mirror: slice 2 of the
2026-09-02 store migration wrote each of the owner's board rows into
CouchDB as one document, kept them in step with every board write, and
served `nova_site` three views over them. Issue #203's record store
(`agora_runner.board_store`) replaced it, and once nothing on the site
read the mirror (Cycle 1379) the mirror was deleted rather than kept in
step for nobody (Cycle 1380) -- the write-through, the views, the
currency stamp, `ticket_store`, `tools.ticket_drift` and
`tools.ticket_migrate` all went together.

What is left is what `board_store` imports: the database name, the
credentials, the request helper, `ensure_database` and `_payload`. The
records live in the same `nova_tickets` database in their own key
ranges, so the name stays even though the tickets are gone. The mirror's
old documents (`ticket:`, `layout:`, `source-rev:` and three `_design/`
documents) are still in that database until something deletes them;
nothing reads them.

**Its own database, not the vault's.** These documents are not files, and
a LiveSync database is a set of files -- `vault_bulk_list("")` walks
`_all_docs` and treats every row it finds as a vault document. Writing
non-file documents into that database would put them in front of every
tool that lists it.
"""

import base64
import json
import os

from .config import COUCHDB_URL, COUCHDB_USER, COUCHDB_PASSWORD
from .http_util import http_json


# Its own database. See the module docstring: these are not vault files and
# they must not appear in a listing of one.
TICKET_DB = os.environ.get("COUCHDB_TICKET_DB", "nova_tickets")


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


def _payload(doc):
    """A stored document without its revision, so it compares to a fresh one.

    `_rev` is the only field CouchDB adds, and it changes on every write
    by definition -- comparing it would make every document differ from
    itself and the skip below would never fire once.
    """
    return {key: value for key, value in doc.items() if key != "_rev"}


# The four board files: his two and my two. `tools.board_put` refuses any
# other path, so a cycle cannot come to believe every vault write goes
# through it. The record store holds only his two (`board_put.RECORD_BOARDS`).
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
    caller writing mixed case reaches the same document.
    """
    return (path or "").lower() in _BOARD_KEYS
