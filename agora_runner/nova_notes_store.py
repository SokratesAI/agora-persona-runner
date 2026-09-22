"""Notes as CouchDB records: the store half of `notes-records.md`.

The owner approved rebuilding `/notes` on 2026-09-21 (spec
`projects/sokrates/projects/nova/notes-records.md`): a note is a JSON
document, not a bullet parsed out of `notes.md`. This module is the only
place that opens a CouchDB connection for one. It shares `ticket_docs`'s
database and credentials for the reason `board_store` gives -- one CouchDB,
one set of credentials -- and it sits in its own two key ranges,
`note:` and `comment:`, which no board range overlaps.

    {"_id": "note:8f2a1c", "type": "note", "author": "edvard",
     "text": "...", "archived": false, "created": ..., "updated": ...}
    {"_id": "comment:8f2a1c:000001", "type": "comment",
     "noteId": "note:8f2a1c", "author": "nova", "text": "...", "created": ...}

**`text` is stored exactly as given.** A paragraph break is `\\n\\n` inside a
JSON string, and nothing here joins, strips or re-wraps a line -- the
continuation-line logic in `nova_notes._bullets()` is the bug this replaces.

**`author` is an argument, never a field lifted from a request body.** The
store only checks it is one of the three senders; *who* the caller is has to
be decided by the route that calls this, from something the caller cannot
type. The spec's first answer to that (trust `Tailscale-User-Login`) does not
hold inside the cluster, and the journal entry for Cycle 1964 says why.

Comment ids carry a zero-padded sequence so CouchDB's lexical `_all_docs`
order is the order they were written in; a second writer that raced for the
same number gets a 409 on the create and takes the next one.
"""

import datetime
import json
import secrets
import urllib.parse

from . import ticket_docs

SENDERS = ("edvard", "sokrates", "nova")

NOTE_TYPE = "note"
COMMENT_TYPE = "comment"

_ID_MAX = "￰"
_CREATE_ATTEMPTS = 20


class StoreError(RuntimeError):
    """CouchDB refused a read or a write of a note."""


class NoteConflict(StoreError):
    """The stored note moved between the read and the write."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_author(author):
    if author not in SENDERS:
        raise ValueError(f"author must be one of {', '.join(SENDERS)}, not {author!r}")


def _check_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a note or comment needs some text")


def _path(doc_id):
    return f"{ticket_docs.TICKET_DB}/{urllib.parse.quote(doc_id, safe='')}"


def _list(prefix):
    query = urllib.parse.urlencode({
        "startkey": json.dumps(prefix),
        "endkey": json.dumps(prefix + _ID_MAX),
        "include_docs": "true",
    })
    status, body = ticket_docs._req("GET", f"{ticket_docs.TICKET_DB}/_all_docs?{query}")
    if status != 200:
        raise StoreError(f"listing {prefix}: {status} {json.dumps(body)[:200]}")
    return [row["doc"] for row in body.get("rows", []) if row.get("doc")]


def _create(doc):
    """PUT a new document; None on a 409, meaning the id is taken."""
    status, answer = ticket_docs._req("PUT", _path(doc["_id"]), doc)
    if status == 409:
        return None
    if status not in (200, 201):
        raise StoreError(f"writing {doc['_id']}: {status} {json.dumps(answer)[:200]}")
    return dict(doc, _rev=answer["rev"])


def _update(doc):
    if not doc.get("_rev"):
        raise ValueError(f"{doc.get('_id')}: an update must carry the `_rev` it was read at")
    status, answer = ticket_docs._req("PUT", _path(doc["_id"]), doc)
    if status == 409:
        raise NoteConflict(f"{doc['_id']} moved since it was read: re-read it and apply the change again")
    if status not in (200, 201):
        raise StoreError(f"writing {doc['_id']}: {status} {json.dumps(answer)[:200]}")
    return dict(doc, _rev=answer["rev"])


def _key(note_id):
    """`note:8f2a1c` -> `8f2a1c`, refusing anything that is not a note id."""
    if not isinstance(note_id, str) or not note_id.startswith("note:") or ":" in note_id[5:] or not note_id[5:]:
        raise ValueError(f"not a note id: {note_id!r}")
    return note_id[5:]


def create_note(author, text):
    _check_author(author)
    _check_text(text)
    now = _now()
    for _ in range(_CREATE_ATTEMPTS):
        doc = {"_id": f"note:{secrets.token_hex(3)}", "type": NOTE_TYPE, "author": author,
               "text": text, "archived": False, "created": now, "updated": now}
        stored = _create(doc)
        if stored:
            return stored
    raise StoreError(f"no free note id after {_CREATE_ATTEMPTS} attempts")


def read_note(note_id):
    _key(note_id)
    status, body = ticket_docs._req("GET", _path(note_id))
    if status == 404:
        return None
    if status != 200:
        raise StoreError(f"reading {note_id}: {status} {json.dumps(body)[:200]}")
    return body


def list_notes(archived=False):
    """Live notes (or archived ones), newest first."""
    notes = [doc for doc in _list("note:")
             if doc.get("type") == NOTE_TYPE and bool(doc.get("archived")) == archived]
    return sorted(notes, key=lambda doc: (doc.get("created", ""), doc["_id"]), reverse=True)


def edit_note(doc, text):
    _check_text(text)
    return _update(dict(doc, text=text, updated=_now()))


def set_archived(doc, archived=True):
    return _update(dict(doc, archived=bool(archived), updated=_now()))


def mark_read(doc, reader):
    """Stamp `readBy[reader]` without touching `updated`.

    How a cycle says it has acted on a note without writing anything he sees:
    the /notes page never draws `readBy`, and `updated` stays his so an edit
    he makes afterwards reads as unread again (`is_unread`).
    """
    _check_author(reader)
    return _update(dict(doc, readBy=dict(doc.get("readBy") or {}, **{reader: _now()})))


def is_unread(doc, reader="nova"):
    """A live note someone else wrote that `reader` has not marked since it last changed."""
    if doc.get("archived") or doc.get("author") == reader:
        return False
    seen = (doc.get("readBy") or {}).get(reader)
    return not seen or seen < (doc.get("updated") or doc.get("created") or "")


def read_comments(note_id):
    """A note's comments, oldest first."""
    return [doc for doc in _list(f"comment:{_key(note_id)}:") if doc.get("type") == COMMENT_TYPE]


def add_comment(note_id, author, text):
    _check_author(author)
    _check_text(text)
    key = _key(note_id)
    if read_note(note_id) is None:
        raise StoreError(f"{note_id} does not exist")
    taken = read_comments(note_id)
    number = max((int(doc["_id"].rsplit(":", 1)[1]) for doc in taken), default=0)
    for _ in range(_CREATE_ATTEMPTS):
        number += 1
        doc = {"_id": f"comment:{key}:{number:06d}", "type": COMMENT_TYPE, "noteId": note_id,
               "author": author, "text": text, "created": _now()}
        stored = _create(doc)
        if stored:
            return stored
    raise StoreError(f"no free comment number on {note_id} after {_CREATE_ATTEMPTS} attempts")


def delete_note(doc):
    """Delete a note and every comment on it. The note goes last.

    Comments first, so a delete that dies halfway leaves a note with fewer
    comments rather than comments whose note is gone, which nothing lists.
    """
    if not doc.get("_rev"):
        raise ValueError(f"{doc.get('_id')}: deleting must carry the `_rev` it was read at")
    for comment in read_comments(doc["_id"]):
        _delete(comment)
    return _delete(doc)


def _delete(doc):
    status, body = ticket_docs._req(
        "DELETE", f"{_path(doc['_id'])}?rev={urllib.parse.quote(doc['_rev'], safe='')}")
    if status in (200, 202):
        return True
    if status == 404:
        return False
    if status == 409:
        raise NoteConflict(f"{doc['_id']} moved since it was read: re-read it before deleting")
    raise StoreError(f"deleting {doc['_id']}: {status} {json.dumps(body)[:200]}")
