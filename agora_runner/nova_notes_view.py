"""Shape a note record and its comments into what `/api/notes/records` sends.

The view half of `notes-records.md`: `nova_notes_store` reads and writes the
documents, this turns them into the payload `notes.js` renders. It replaces
`nova_notes.notes_payload`, which re-derives notes by parsing `notes.md`.

A sender is shown by name, the same way for all three. The spec drops the
old chat view's owner/cycle split, so nothing here marks a side.
"""

NAMES = {"edvard": "Edvard", "sokrates": "Sokrates", "nova": "Nova"}


def _name(author):
    return NAMES.get(author, author or "")


def shape_comment(doc):
    return {
        "id": doc["_id"],
        "author": _name(doc.get("author")),
        "text": doc.get("text", ""),
        "created": doc.get("created", ""),
    }


def shape_note(doc, comments=()):
    """A note plus its comments, oldest comment first.

    `rev` goes out because an edit, archive or delete has to send back the
    revision it was read at; the store refuses a write without it, which is
    what stops a stale tab from overwriting a newer edit.
    """
    return {
        "id": doc["_id"],
        "rev": doc.get("_rev", ""),
        "author": _name(doc.get("author")),
        "text": doc.get("text", ""),
        "archived": bool(doc.get("archived")),
        "created": doc.get("created", ""),
        "updated": doc.get("updated", ""),
        "comments": [shape_comment(c) for c in comments],
    }


def notes_page(store, archived=False):
    """Every live note (or every archived one), newest first, with comments."""
    return {
        "archived": bool(archived),
        "notes": [shape_note(doc, store.read_comments(doc["_id"]))
                  for doc in store.list_notes(archived=archived)],
    }
