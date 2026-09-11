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

This module is that shape, in both directions, and nothing else -- plus,
since 2026-09-09, the one thing the spec's code block does not mention and
six modules read anyway: a *capture*, the owner's own bullet above the first
heading. See the `Captures` section at the foot of this file for why that is
its own document with its own id space rather than a field on a row or a
list on the board. The store
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

import json

from . import nova_boards, rank_key

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

#: Record fields a row may carry and `parse_board` never produces, because
#: his markdown has no cell for them. Absent unless written, so a row that
#: never had one reads exactly as `parse_board` would have made it.
#:
#: `placedBy` is issue #202's *"A position or rating he set is recorded as
#: his, and a cycle may not overwrite it"* -- the position half. `order` is
#: a dense seat and says nothing about who chose it, so without this a task
#: he dragged and a task a cycle seated are the same value.
OPTIONAL_FIELDS = ("placedBy",)


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


def to_document(item, board, project_id=None, milestone_id=None, rank=None,
                detail=None):
    """A parsed row -> its record document.

    `project_id` and `milestone_id` come from `entity_id`, `rank` from
    `rank_key`; all three are optional because a `## Done` row carries no
    project, milestone or position in the markdown it was parsed from, and
    inventing one during the migration would be writing a fact nobody
    stated.

    `detail` is the row's prose body -- `parse_board`'s `details[number]`,
    which is a *separate* dict rather than a key on the item, so it has to
    be passed in beside the row it belongs to. It is optional for the same
    reason the three above are: most rows have no body, and a row with no
    body must not become a row whose body is the empty string. See
    `details_map` for the inverse.
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
    for field in OPTIONAL_FIELDS:
        if item.get(field):
            doc[field] = item[field]
    doc["done"] = bool(item.get("done"))
    if project_id is not None:
        doc["projectId"] = project_id
    if milestone_id is not None:
        doc["milestoneId"] = milestone_id
    if rank is not None:
        doc["rank"] = rank
    if detail is not None:
        if not isinstance(detail, str):
            raise DocumentError(f"detail must be a str, not {detail!r}")
        # An empty body and an absent one are the same thing to
        # `parse_board`, which simply has no entry for that number. Storing
        # `""` would make the round trip mint a detail section nobody wrote.
        if detail:
            doc["detail"] = detail
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
    item = {
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
    for field in OPTIONAL_FIELDS:
        if doc.get(field):
            item[field] = doc[field]
    return item


def detail_of(doc):
    """One document's prose body, or `""` if it carries none."""
    return doc.get("detail") or ""


def details_map(docs):
    """Documents -> `parse_board`'s `details` dict, `{number: markdown}`.

    Only rows that actually carry a body appear, which is what
    `parse_board` does: a caller asking `details.get(n)` must be able to
    tell "no detail section" from "a detail section that is empty".
    """
    out = {}
    for doc in docs:
        _check_identity(doc)
        body = detail_of(doc)
        if body:
            out[doc["number"]] = body
    return out


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


# --- Captures ---------------------------------------------------------
#
# A capture is one of the owner's own bullets above the first heading of a
# board file, plus the replies cycles have written under it. Five modules
# read `parse_board(...)["captures"]` and `nova_site` reads the parallel
# `captureReplies`, and none of the eight merged primitives had anywhere
# to put either -- a capture is not a row, so it needed a decision rather
# than a field, and this is that decision.
#
# **A capture is its own document, not a field on the board's.** The
# alternative was one `board:issue` document holding the whole list, which
# is one write for every edit of any bullet, and `nova_site`'s Edit route
# writes one capture at a time from his phone. A list-valued document
# turns two people editing two different bullets into a `_rev` conflict
# between them.
#
# **Its id is minted once and is not derived from its text.** Today a
# capture is addressed *by its own words*: `nova_capture`'s Edit route
# sends the text back as the address. That is already a live bug and the
# docstring of `capture_entries` records what it cost -- when the address
# and the stored text drifted apart by one welded-on reply, the route
# answered "no longer in the list" and he lost an edit he had just typed.
# Editing a capture changes its text, so a text-derived id changes under
# the edit that uses it. Same argument as `entity_id`'s for projects, one
# level down.
#
# **Order is a `rank_key`, not an index.** The list is newest-first and he
# adds to the top, so an index-ordered store renumbers every sibling on
# every capture he writes. `rank` is optional here for the same reason it
# is on a row: the migration reads a file that states an order and nothing
# else, and a capture nobody has placed by hand has `None`, which is a
# different answer from first.
#
# **Replies are a list field, not documents.** They are written and read
# only with the capture they sit under, nothing addresses one on its own,
# and `parse_board` returns them as a list parallel to `captures`. A
# reply document would buy an id for something that has never needed one.

#: What a capture document's `type` reads. Not `DOCUMENT_TYPE`: a view
#: that asks for rows must not be handed captures, and both live in the
#: one database.
CAPTURE_DOCUMENT_TYPE = "capture"


def capture_document_id(board, capture_id):
    """`("issue", "cap_7")` -> `"capture:issue:cap_7"`.

    The board is part of the identity for the same reason it is on a row:
    both files number from the top and one id has to name one bullet.
    `capture_id` is the caller's to mint -- this module holds no counter
    and will not derive one from the text, which is the whole point of
    giving a capture an id at all.

    **The prefix is `capture:` and not `board:`, and that is load-bearing
    rather than cosmetic.** `board_store` selects a board's documents with
    an `_all_docs` range over the literal prefix `board:<board>:` -- so an
    id of `board:issue:capture:cap_7` would sit *inside* the row range.
    `read_rows` would hand every capture back as a row with no number,
    `sort_key` would file them all as unranked, and `write_rows` with the
    default `prune=True` would tombstone every one of his captures the
    first time a caller wrote the rows without them. Two id spaces that do
    not overlap is a guarantee; a `type` filter at each call site is a
    thing somebody forgets once.
    """
    if board not in BOARDS:
        raise DocumentError(f"board must be one of {BOARDS}, not {board!r}")
    if not isinstance(capture_id, str) or not capture_id.strip():
        raise DocumentError(f"capture_id must be a non-empty str, not {capture_id!r}")
    if ":" in capture_id:
        # `board:issue:capture:a:b` would parse two ways and neither of
        # them is wrong, so refuse it at the one place that builds the id.
        raise DocumentError(f"capture_id must not contain ':': {capture_id!r}")
    return f"capture:{board}:{capture_id}"


def to_capture_document(text, board, capture_id, rank=None, replies=()):
    """One of his bullets (and the replies under it) -> its record document.

    `text` is his words alone, exactly as `capture_entries` splits them --
    not his line with a cycle's answer welded onto the end, which is the
    shape that broke the Edit route.
    """
    if not isinstance(text, str):
        raise DocumentError(f"capture text must be a str, not {text!r}")
    if not text.strip():
        # `capture_entries` never yields one: a bare `- ` is not a capture.
        # Storing it would put an empty bullet on his board on the way back.
        raise DocumentError("capture text must not be empty")
    replies = list(replies or ())
    for reply in replies:
        if not isinstance(reply, str):
            raise DocumentError(f"a capture reply must be a str, not {reply!r}")
    doc = {
        "_id": capture_document_id(board, capture_id),
        "type": CAPTURE_DOCUMENT_TYPE,
        "board": board,
        "captureId": capture_id,
        "text": text,
    }
    if rank is not None:
        # One shape per board, or `captures_in_order` compares an int with a
        # str and every read of his board raises (Cycle 1391).
        if not rank_key.is_valid(rank):
            raise DocumentError(f"capture rank must be a rank_key, not {rank!r}")
        doc["rank"] = rank
    if replies:
        # Absent and empty are the same thing to `parse_board`, which hands
        # back `[]` for a bullet nobody has answered. Storing `[]` would
        # make the two spellings differ in CouchDB and agree everywhere
        # else, which is the split brain one field wide.
        doc["replies"] = replies
    return doc


def capture_text_of(doc):
    """One capture document's text, checked against its own `_id`."""
    _check_capture_identity(doc)
    return doc.get("text", "")


def capture_replies_of(doc):
    """The replies written under one capture, oldest first, `[]` if none."""
    _check_capture_identity(doc)
    return list(doc.get("replies") or ())


def captures_in_order(docs):
    """Capture documents in the order his board shows them.

    Ranked captures first in rank order, then unranked ones in the order
    they were handed over. That pair is deliberate and it is
    `board_store.sort_key`'s rule, which this deliberately restates rather
    than imports -- `board_store` imports this module, so the arrow only
    goes one way. `_all_docs` answers in lexical id order, so
    `capture:issue:cap_10` sorts before `cap_2` and every read has to
    re-sort in Python. Sorting on `rank or ""` instead would put every
    unranked capture *first*, at the top of his board, which is where a
    capture he never placed is most visible and least earned.

    It is its own function because `captures_map` is not the only caller
    that needs the order any more. `tools.board_capture` takes an
    `--index` into the list `captures_map` produced and has to delete
    *that* capture's document -- so the position and the document behind
    it are decided by one rule in one place, or the tool boards the bullet
    he pointed at and removes a different one.
    """
    # Materialised before the check, because a caller handing over a
    # generator (a `_all_docs` page, `reversed(...)`) would otherwise have
    # it consumed by the check and sort an empty list.
    docs = list(docs)
    for doc in docs:
        _check_capture_identity(doc)
    return sorted(
        docs, key=lambda doc: (doc.get("rank") is None, doc.get("rank") or ""))


def captures_map(docs):
    """Capture documents -> `parse_board`'s two parallel lists.

    Returns `{"captures": [text], "captureReplies": [[reply]]}`, the two
    the same length, because six modules read them as a pair and index
    one by the other's position.

    The order is `captures_in_order`'s and the reasoning is there.
    """
    ordered = captures_in_order(docs)
    return {
        "captures": [doc.get("text", "") for doc in ordered],
        "captureReplies": [list(doc.get("replies") or ()) for doc in ordered],
    }


def _check_capture_identity(doc):
    """Refuse a capture document whose `_id` disagrees with its own fields."""
    for key in ("board", "captureId"):
        if key not in doc:
            raise DocumentError(f"capture is missing {key!r}: {doc.get('_id')!r}")
    expected = capture_document_id(doc["board"], doc["captureId"])
    actual = doc.get("_id")
    if actual is not None and actual != expected:
        raise DocumentError(
            f"capture _id {actual!r} disagrees with board/captureId ({expected!r})"
        )


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------
#
# `board_view.document_layout` reads a board file and hands back the order of
# its blocks -- the table, the done table, each write-up by number, and every
# other paragraph verbatim. That is the record home for the residue
# `parse_board` does not model: the owner's `## Processed captures` archive,
# the `# Done — detail` heading, and `ideas.md`'s `## Discarded` table, which
# together are 19,653 words of `issues.md` and 6,469 of `ideas.md`.
#
# Cycle 1324 computed that layout and stored it nowhere, so the only thing
# that could render his board without deleting those words was a caller
# holding the *source markdown* -- which is the markdown the records exist to
# replace. This is where it lives instead.

#: One layout document per board, and its own `type` for the same reason the
#: registry has one: the CouchDB views key on `doc.type` and a layout is not
#: a row.
LAYOUT_DOCUMENT_TYPE = "board-layout"


def layout_document_id(board):
    """`"issue"` -> `"board:layout:issue"`.

    **The board is the last segment, not the second, and that is the whole
    of the id decision.** `board_store` selects a board's rows with an
    `_all_docs` range over the literal prefix `board:<board>:`, so
    `board:issue:layout` would sit *inside* the row range: `read_rows` would
    hand the layout back as a row with no number and `write_rows`' default
    `prune=True` would tombstone it the first time anything wrote the rows.
    `board:layout:issue` is outside both that range and the
    `capture:<board>:` one, the same way `board:registry` is -- three id
    spaces that cannot overlap, rather than a `type` filter at each call
    site that somebody forgets once.
    """
    if board not in BOARDS:
        raise DocumentError(f"board must be one of {BOARDS}, not {board!r}")
    return f"board:layout:{board}"


def _check_layout_blocks(blocks):
    """Refuse anything that is not a layout, before it is stored.

    This is stricter than it looks worth being, and the reason is what a
    bad layout does downstream. `board_view.render_document` draws the
    document *from* the layout: a block it does not recognise is silently
    dropped, so a layout that lost its `verbatim` blocks -- a JSON load of
    the wrong file, a caller passing `[]` because a parse came back empty --
    renders a board with his archive deleted and no error anywhere. The
    words are gone in the same shape as the bug this whole piece fixes.
    """
    if not isinstance(blocks, list):
        raise DocumentError(
            f"layout must be a list of blocks, not {type(blocks).__name__}")
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise DocumentError(
                f"layout block {index} must be a dict, not {block!r}")
        kind = block.get("kind")
        if kind not in board_view_kinds():
            raise DocumentError(
                f"layout block {index} has kind {kind!r}, "
                f"not one of {board_view_kinds()}")
        if kind == "detail" and not isinstance(block.get("number"), int):
            raise DocumentError(
                f"layout block {index} is a detail with number "
                f"{block.get('number')!r}, which is not an int")
        if kind == "verbatim" and not isinstance(block.get("markdown"), str):
            raise DocumentError(
                f"layout block {index} is verbatim with markdown "
                f"{block.get('markdown')!r}, which is not a str")
    return blocks


def board_view_kinds():
    """`board_view.LAYOUT_KINDS`, imported late to keep the import one way.

    `board_view` already imports `nova_boards` and this module imports
    `nova_boards` too; a module-level `from . import board_view` here would
    make the pair circular the first time `board_view` wanted a document.
    The kinds live in `board_view` because that is what mints and renders
    them -- restating the tuple here would be the second copy of a fact,
    and the copy nobody updates is the one that refuses a new kind.
    """
    from . import board_view
    return board_view.LAYOUT_KINDS


SOURCE_DOCUMENT_TYPE = "board-source"


def source_document_id(board):
    """`"issue"` -> `"board:source:issue"`.

    Outside the row range and the capture range for the reason
    `layout_document_id` spells out: `board:<board>:` is an `_all_docs`
    prefix that `write_rows` prunes, so anything filed under it that is not
    a row is tombstoned by the first migration that writes the rows alone.
    """
    if board not in BOARDS:
        raise DocumentError(f"board must be one of {BOARDS}, not {board!r}")
    return f"board:source:{board}"


def to_source_document(source_rev, board):
    """`("5-abc", "issue")` -> the document that records where the records came from.

    `source_rev` is the vault `_rev` the board markdown carried when these
    records were written. It is stored as a string and never as anything
    else, because the only thing anybody does with it is compare it for
    equality against a live revision, and a value that arrived as an int
    would compare unequal to the same revision read back as text.
    """
    if not isinstance(source_rev, str) or not source_rev.strip():
        raise DocumentError(
            f"source revision must be a non-empty string, not {source_rev!r}")
    if board not in BOARDS:
        raise DocumentError(f"board must be one of {BOARDS}, not {board!r}")
    return {
        "_id": source_document_id(board),
        "type": SOURCE_DOCUMENT_TYPE,
        "board": board,
        "sourceRev": source_rev,
    }


def source_rev_of(doc):
    """The revision back out of a stored source document.

    Refuses a document whose `_id` disagrees with its own `board` field, the
    same check `layout_blocks_of` makes and for the same reason: a source
    stamp served under the wrong board would certify one board's records as
    current from the other board's write.
    """
    if not isinstance(doc, dict):
        raise DocumentError(f"source document must be a dict, not {doc!r}")
    board = doc.get("board")
    if board not in BOARDS:
        raise DocumentError(
            f"source document board must be one of {BOARDS}, not {board!r}")
    if doc.get("_id") != source_document_id(board):
        raise DocumentError(
            f"source document _id {doc.get('_id')!r} disagrees with its "
            f"board {board!r}")
    rev = doc.get("sourceRev")
    if not isinstance(rev, str) or not rev.strip():
        raise DocumentError(
            f"source document revision must be a non-empty string, not {rev!r}")
    return rev


def to_layout_document(blocks, board):
    """`(blocks, "issue")` -> the document that stores them.

    The blocks go through JSON on the way in, and that is not tidiness.
    `board_view.document_layout` puts the owner's own table header in a
    block as a **tuple** -- `_header_cells` returns one -- and JSON has no
    tuple, so the same layout read back out of CouchDB carries a list. Every
    conditional write in `board_store` decides whether to write by comparing
    a fresh document against the stored one, so without this the two never
    compare equal and a migration re-run that computed an identical layout
    would burn a revision every time. Normalising here rather than at the
    comparison keeps one spelling of a stored layout.
    """
    return {
        "_id": layout_document_id(board),
        "type": LAYOUT_DOCUMENT_TYPE,
        "board": board,
        "blocks": json.loads(json.dumps(_check_layout_blocks(blocks))),
    }


def layout_blocks_of(doc):
    """The blocks back out of a stored layout document.

    Refuses a document whose `_id` disagrees with its own `board` field,
    the same way `_check_identity` does for a row: the id and the field are
    written by one function and read by two, and a layout served under the
    wrong board renders one board's archive into the other.
    """
    if not isinstance(doc, dict):
        raise DocumentError(f"layout document must be a dict, not {doc!r}")
    board = doc.get("board")
    if board not in BOARDS:
        raise DocumentError(
            f"layout document board must be one of {BOARDS}, not {board!r}")
    if doc.get("_id") != layout_document_id(board):
        raise DocumentError(
            f"layout document _id {doc.get('_id')!r} disagrees with its "
            f"board {board!r}")
    return _check_layout_blocks(doc.get("blocks"))
