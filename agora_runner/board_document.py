"""A board row record <-> the CouchDB document the spec asks for.

The fourth primitive of issue #203, the owner's decision of 2026-09-08 that
the boards get a real schema (`projects/sokrates/projects/nova/board-records.md`,
`status: approved`), after `rank_key`, `entity_id` and `board_view`. Those
three mint the *parts* of a record -- a stable project id, a sparse rank
key, the markdown view a record renders back to. None of them says what a
record actually **is**, and the spec does, once, in a code block:

    { "_id": "board:issue:41", "type": "row", "board": "issue", "number": 41,
      "title": "...", "projectId": "prj_nova", "milestoneId": "ms_7",
      "rank": "0|hzzzzz:", "priority": "high", "size": "m",
      "status": "open", "updated": "2026-09-08T17:00:00Z" }

This module is that shape, in both directions, and nothing else. The store
that reads and writes these documents, the migration that creates them and
the 23 markdown readers all move in one change -- the spec forbids a facade
phase, because a facade is the window in which two stores are both live --
so like the three primitives before it, **nothing calls this yet.**

## Derived fields are not stored

`parse_board` hands back `statusKey`, `priorityKey` and `sizeKey` beside
`status`, `priority` and `size`. Those are functions of the cell, computed
by `nova_boards` on every parse. A document that carried them would hold
the same fact twice, and the copy in CouchDB would be the one nobody
recomputes -- so a change to `canonical_priority` would fix the board pages
and leave every stored row spelled the old way, which is the split brain
this whole issue is about, one field wide instead of one store wide.

So `to_document` drops them and `from_document` recomputes them through
`nova_boards`, which keeps one spelling of each rule.

`project` is the same argument at a different size. The record's `projectId`
is the truth and the *name* lives in the project's own document, which is
the entire point of minting an id: the spec asks that "renaming a project
does not orphan a row". Writing the name into the row alongside the id
would restore exactly the coupling `entity_id` exists to remove. So
`from_document` takes the names as arguments and resolves nothing itself;
the registry lookup belongs where the registry is.

## `board` is part of the identity, not a field beside it

Both boards number their rows from 1, so `41` alone names two rows. The id
is `board:<board>:<number>` and the document repeats `board` and `number`
as fields, because a CouchDB view that groups by board cannot split an id.
That repetition is a real one and is safe in a way the derived fields are
not: `document_id` is the only thing that writes it, and `from_document`
refuses a document whose `_id` disagrees with its own fields rather than
choosing a winner.

## `order` is not `rank`

`order` is today's dense 1..N position from the `Order` column, and `rank`
is the sparse key that replaces it. They coexist deliberately for one
migration: `order` is what the live boards carry and what the app still
sorts by, `rank` is what one moved row will write. Collapsing them here
would make the migration unable to state what it changed. An unplaced row
has `order: None` -- and `None` is a different answer from `0`, which is
`board_view`'s finding and `parse_project_order_cell`'s rule.
"""

from . import nova_boards

#: Every board document carries this. CouchDB is one database per vault,
#: not one per kind, so a view that wants rows has to be able to say so.
DOCUMENT_TYPE = "row"

#: The two boards the owner keeps. A third would be a decision, not a typo,
#: so this refuses anything else rather than minting an id for it.
BOARDS = ("issue", "idea")

#: The record fields that come straight off a parsed row, unchanged. The
#: keys `parse_board` derives (`statusKey`, `priorityKey`, `sizeKey`) and
#: the ones this module composes (`_id`, `type`, `board`, `projectId`,
#: `milestoneId`, `rank`) are deliberately not in here.
PLAIN_FIELDS = (
    "title", "status", "updated", "where", "priority", "size", "order", "done",
)


class DocumentError(ValueError):
    """A row that cannot become a document, or a document that is malformed."""


def document_id(board, number):
    """`("issue", 41)` -> `"board:issue:41"`. Raises on anything else.

    The number is required to be a positive integer rather than coerced,
    because a row numbered `"41"` and a row numbered `41` would otherwise
    mint the same id from two different parses and the second write would
    silently take the first one's `_rev` fight.
    """
    if board not in BOARDS:
        raise DocumentError(f"board must be one of {BOARDS}, not {board!r}")
    if isinstance(number, bool) or not isinstance(number, int):
        raise DocumentError(f"number must be an int, not {number!r}")
    if number <= 0:
        raise DocumentError(f"number must be positive, not {number}")
    return f"board:{board}:{number}"


def to_document(item, board, project_id=None, milestone_id=None, rank=None):
    """A parsed row -> its record document.

    `project_id` and `milestone_id` come from `entity_id`, `rank` from
    `rank_key`; all three are optional because a `## Done` row carries no
    project, milestone or position in the markdown it was parsed from, and
    inventing one during the migration would be writing a fact nobody
    stated.
    """
    number = item.get("number")
    doc = {
        "_id": document_id(board, number),
        "type": DOCUMENT_TYPE,
        "board": board,
        "number": number,
    }
    for field in PLAIN_FIELDS:
        if field in item:
            doc[field] = item[field]
    doc["done"] = bool(item.get("done"))
    if project_id is not None:
        doc["projectId"] = project_id
    if milestone_id is not None:
        doc["milestoneId"] = milestone_id
    if rank is not None:
        doc["rank"] = rank
    return doc


def from_document(doc, project_name=None, milestone_name=None):
    """A record document -> the row dict `parse_board` would have made.

    The two names are the caller's to resolve: this module holds no
    registry and will not guess one. An absent `project_name` becomes
    `DEFAULT_PROJECT` for exactly the reason `parse_board` does it -- a
    row that predates the column is a row nobody has re-filed, not a row
    that belongs nowhere.
    """
    _check_identity(doc)
    status = doc.get("status", "")
    priority = doc.get("priority", "")
    size = doc.get("size", "")
    done = bool(doc.get("done"))
    return {
        "number": doc["number"],
        "title": doc.get("title", ""),
        "status": status,
        # `done` says which table, `status` says what the cell reads, and
        # a `## Board` row may read `✅ Done` without being in `## Done`.
        # `parse_board` hard-codes the key for a done row rather than
        # deriving it, so this does too.
        "statusKey": "done" if done else nova_boards.status_key(status),
        "updated": doc.get("updated", ""),
        "where": doc.get("where", ""),
        "priority": priority,
        "priorityKey": nova_boards.priority_key(priority),
        "project": project_name or nova_boards.DEFAULT_PROJECT,
        "size": size,
        "sizeKey": nova_boards.size_key(size),
        "milestone": milestone_name or "",
        "order": doc.get("order"),
        "done": done,
    }


def _check_identity(doc):
    """Refuse a document whose `_id` disagrees with its own fields."""
    for key in ("board", "number"):
        if key not in doc:
            raise DocumentError(f"document is missing {key!r}: {doc.get('_id')!r}")
    expected = document_id(doc["board"], doc["number"])
    actual = doc.get("_id")
    if actual is not None and actual != expected:
        raise DocumentError(
            f"document _id {actual!r} disagrees with board/number ({expected!r})"
        )
