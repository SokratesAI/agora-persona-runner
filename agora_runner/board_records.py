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

**Rows and captures are two key ranges, not one.** This module shipped
believing the opposite -- that a capture's id was `board:<board>:capture:<id>`
and so one `read_rows` fetched both kinds -- and it is wrong.
`board_document.capture_document_id` mints `capture:<board>:<id>`, outside
`board_store`'s row range, deliberately: an id under `board:` would come
back from `read_rows` as a row with no number, and `write_rows`' default
`prune=True` would tombstone every capture the owner has ever written the
first time a migration wrote the rows alone. So `contents` asks the store
twice, and the second query is the only thing that makes `captures` and
`captureReplies` non-empty against a real CouchDB.

**The green test that hid it is the lesson, not the typo.** The fake store
answered `read_rows` with a hand-built list of both kinds, so it agreed
with a join no real store could perform. A fake that cannot say *"that id
is not in the range you asked for"* cannot fail this, and every reader
converted onto `contents` would have reported the owner's captures as
none. `type` still separates the documents, because a document that
disagrees with the range it was stored in is a bug worth raising rather
than silently filing under the other kind.

**And the same join in the other direction, for the writers.** `store_item`
is `contents`' mirror: it takes one row in `parse_board`'s shape and writes
one document. It is here rather than in the ten `tools/board_*.py` writers
for the reason `contents` is -- they hold *names* and the store wants ids,
and ten copies of that lookup is ten chances to mint a second project called
`Nova`, drop a write-up, or send a row to the bottom of the board by
re-minting it without its rank.

**A row carries `projectId`/`milestoneId`; `parse_board` carried names.**
The registry is the only place the two are joined, and a row pointing at
an id the registry does not hold raises rather than falling back to the
default project. That fallback is right for a row with *no* project --
`from_document` does it, for a row nobody has re-filed -- and wrong for a
dangling id, which is precisely the orphan stable ids exist to prevent.
Rendering it as `Nova` would put the orphan on his board looking re-filed.
"""

import copy

from agora_runner import board_document, board_store, entity_id


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
    it needs `read_rows`, `read_captures` and `read_registry`. The fake
    must honour the two key ranges separately -- one that answers
    `read_rows` with captures in it is agreeing with a join CouchDB cannot
    perform, which is how the missing `read_captures` call stayed green.

    **A capture found in the row range raises rather than being read.**
    It is not merely misfiled: the next `write_rows` prunes it, so a reader
    that quietly took it would be the last thing to see it.

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
    rows, misfiled = split_documents(store.read_rows(board))
    if misfiled:
        raise RecordError(
            f"{len(misfiled)} capture document(s) are stored inside the row "
            f"key range of board {board!r} "
            f"({', '.join(sorted(str(doc.get('_id')) for doc in misfiled))}); "
            "a `write_rows` with the default `prune=True` would tombstone "
            "them. They belong under `capture:<board>:` -- see "
            "`board_document.capture_document_id`")
    _, captures = split_documents(store.read_captures(board))
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


def capture_documents(board, store=board_store):
    """Every capture document of one board, in the order his board shows them.

    The documents as they were read, `_rev` included, for `capture_at`'s
    reason: `board_store.delete_capture` and `board_write.change_capture_text`
    both refuse a document without one, and a caller that re-minted the
    document to get a shape it liked would hand over a write conditional on
    nothing.

    Its own function because `capture_at` is no longer the only caller that
    needs the ordered list. `tools.close_done_captures` walks every bullet
    looking for the ones the claims ledger has closed, and resolving that by
    calling `capture_at(board, 0..n)` would be one `_all_docs` query per
    bullet -- and worse, a *different* query each time, so a concurrent write
    landing halfway through would have the walk read two versions of his
    board and never say so.

    A capture stored in the row key range is `contents`' error and not this
    function's, so this reaches for the capture range only: raising here as
    well would be a second copy of a rule that is already enforced on every
    read of the board.

    **An unmigrated store raises here too, and that is not a second copy of
    `contents`' check -- it is the same rule on a path that went around it.**
    `read_captures` answers `[]` for a board that has never been written and
    for a board whose box he has emptied, and those are the same value.
    Measured 2026-09-10 against the live store, which is not migrated yet:
    `contents('issue')` raised and `capture_documents('issue')` returned `[]`,
    so `tools.close_done_captures` printed "nothing to mark" and exited 0
    having looked at nothing. The registry tells the two apart because
    `board_migrate` writes it on every run.
    """
    if store.read_registry().get("_rev") is None:
        raise UnmigratedStore(
            "the record store has never been written, so it holds no "
            f"captures for board {board!r}: the registry document has no "
            "revision. Run `python3 -m tools.board_migrate --board <board> "
            "--file <md> --apply` first")
    _, captures = split_documents(store.read_captures(board))
    return board_document.captures_in_order(captures)


def capture_at(board, index, store=board_store):
    """The capture document at one position in `contents(board)["captures"]`.

    `None` when the position does not exist. The document comes back as it
    was read, `_rev` included, which is the whole reason this returns a
    document rather than the text: `board_store.delete_capture` refuses one
    without it, and a caller that re-minted the document to get a shape it
    liked would hand over a delete conditional on nothing.

    **The index is his, not the store's.** `tools.board_capture --index` is
    the position `tools.top_board_rows` prints beside each bullet, which is
    a position in the list `captures_map` builds -- and `read_captures`
    answers in lexical id order, where `cap_10` sits between `cap_1` and
    `cap_2`. So the two orders disagree for any board past ten captures,
    and a lookup that skipped the sort would board the bullet he pointed at
    and delete a different one. `captures_in_order` is the single rule both
    go through.

    A capture stored in the row key range is `contents`' error and not this
    function's, so this reaches for the capture range only: raising here as
    well would be a second copy of a rule that is already enforced on every
    read of the board.
    """
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        return None
    ordered = capture_documents(board, store=store)
    if index >= len(ordered):
        return None
    return ordered[index]


def store_item(board, item, detail=None, rank=None, store=board_store):
    """One row in `parse_board`'s shape -> one written record document.

    The write mirror of `contents`, and the seam the ten `tools/board_*.py`
    writers need at the switchover. Each of them changes one cell of one row
    and today does it by rewriting his markdown. `board_store.write_row`
    already writes one document without pulling the whole board back, but it
    takes a *document* -- id, ids, rank, `_rev` -- and a writer holds an
    `item`, whose project and milestone are **names**. The registry is the
    only place names and ids are joined, exactly as in `contents`, so that
    join lives here and not in ten copies of itself.

    Returns the document as it now stands, `_rev` included, which is
    `board_store.write_row`'s contract.

    Four things it does that a hand-rolled `to_document` + `write_row` would
    not, and each of them loses something without an error:

    **The registry is written before the row, and only when minting changed
    it.** A row pointing at a project id the registry does not hold makes
    `contents` raise for the *whole board* -- so the order is not a
    preference, it is the difference between a failed write and a board
    nothing can read. It fires the first time he files a row into a project
    that is new, which is an ordinary Tuesday. An *unchanged* registry is not
    rewritten, because every write burns a revision on the one document every
    row of both boards points at, and the change is detected by comparing the
    whole registry rather than by asking `resolve_*` twice -- `ensure_project`
    may also touch an existing entry, and a comparison sees that where a
    second resolve does not.

    **`rank` is for a row that has none stored yet, and it is ignored for
    one that has.** A row already on his board holds the position he put it
    in, and a writer changing a cell must not move it; a row being created has
    no stored rank to carry forward, and `board_store.sort_key` puts an
    unranked document *below* every ranked one -- so a new row minted without
    one lands at the bottom of his board rather than at the top, which is
    where `nova_boards.add_row` puts it. The caller mints the key, because
    only the caller knows which neighbours it goes between.

    **The stored row's `rank` is carried forward.** `to_document` takes the
    rank as an argument and leaves it out when it is absent, and
    `board_store.in_order` puts an unranked document after every ranked one --
    so re-minting a row to change its status would silently move it to the
    bottom of his board. That is `capture_plan`'s bug in the row range.

    **The stored row's `detail` is carried forward unless the caller passes
    one.** A body is not a key on the item -- `parse_board` keeps `details` in
    a separate dict -- so a writer that holds only the row has nothing to pass
    and would otherwise delete his write-up. `detail=""` means *remove it* and
    is deliberately distinct from `detail=None`, which means *leave whatever
    is stored alone*.

    **An unmigrated store raises rather than being seeded with one row**, for
    `contents`' reason: `read_registry` answers a revisionless document for a
    store nobody has migrated, so a writer that created its one row there
    would leave behind a board whose every other row is missing, and
    `contents` would then read that one row as the board.
    """
    registry = store.read_registry()
    if registry.get("_rev") is None:
        raise UnmigratedStore(
            "the record store has never been written, so a row cannot be "
            f"written into board {board!r}: the registry document has no "
            "revision. Run `python3 -m tools.board_migrate --board <board> "
            "--file <md> --apply` first")

    held = store.read_row(board, item.get("number"))

    # Minted on the same conditions as the migration (`records` in
    # `tools/board_migration_preflight.py`): a milestone needs a project,
    # because a milestone with no project is the orphan `entity_id` exists to
    # prevent, and two projects may each have a `Backup`.
    before = copy.deepcopy(registry)
    project_id = milestone_id = None
    project_name = item.get("project") or ""
    milestone_name = item.get("milestone") or ""
    if project_name:
        project_id = entity_id.ensure_project(registry, project_name)
        if milestone_name:
            milestone_id = entity_id.ensure_milestone(
                registry, project_id, milestone_name)
    if registry != before:
        store.write_registry(registry)

    body = detail
    if body is None and held is not None:
        body = board_document.detail_of(held) or None
    doc = board_document.to_document(
        item, board, project_id=project_id, milestone_id=milestone_id,
        rank=(held or {}).get("rank") or rank, detail=body)
    if held is not None:
        doc["_rev"] = held["_rev"]
    return store.write_row(doc)


def project_names(store=board_store):
    """Every project name the registry holds, in the order it was minted.

    The records spelling of `tools.board_capture --projects-from`, which took
    a *path to the sibling board's markdown* because the tool could see one
    file at a time. The flag existed because the picker in the app builds its
    list from both boards while the tool resolved his `#slug` tag against the
    one board it was writing, so picking `Maintenance` on an issue left the
    tag sitting in the title (PR #888's defect, measured 2026-09-08).

    Against records that flag has no spelling and needs none: both boards
    mint into **one** registry, so the union the picker offers is a document
    rather than a second file to remember to pass. That is the point of the
    registry -- `store_item`'s comment says the names/ids join lives here and
    nowhere else -- and it means a caller can no longer narrow the list by
    forgetting an argument.

    An unmigrated store raises, `contents`' rule: `read_registry` answers a
    revisionless document for a store nobody has migrated, and an empty name
    list read off one is indistinguishable from a board with no projects --
    which would resolve every tag to nothing, silently.
    """
    registry = store.read_registry()
    if registry.get("_rev") is None:
        raise UnmigratedStore(
            "the record store has never been written, so it holds no project "
            "names: the registry document has no revision. Run `python3 -m "
            "tools.board_migrate --board <board> --file <md> --apply` first")
    names = []
    for entry in (registry.get("projects") or {}).values():
        name = (entry.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


#: `currency`'s three verdicts. Deliberately the same three words
#: `ticket_docs` answers with, because `nova_site` reads one today and will
#: read the other after #203's switchover, and a renamed verdict at that
#: seam is a silent behaviour change in a comparison nobody re-reads.
CURRENT = "current"
STALE = "stale"
UNKNOWN = "unknown"


def stamp_source_rev(board, source_rev, store=board_store):
    """Record that `board`'s records were built from vault revision `source_rev`.

    Called after the records have been written, never before: the stamp is a
    claim about what is already stored, and one written first would certify
    a write that then failed.
    """
    return store.write_source(board, source_rev)


def stored_source_rev(board, store=board_store):
    """The revision `board`'s records were built from, or `None`."""
    return store.read_source(board)


def currency(board, live_rev, store=board_store):
    """Are `board`'s records still built from the markdown as it stands now?

    Returns `(verdict, why)`. This is the question `nova_site` has to answer
    on every board request after the switchover, and the reason it cannot
    just fetch the markdown and compare: his `issues.md` is 700KB, and
    re-reading it per request is the cost issue #203 exists to remove. So
    the store carries the revision it was built from and the caller brings
    the one the vault holds now, and the comparison is two short strings.

    **`UNKNOWN` is a verdict and not a failure, and it must never be able to
    read as `CURRENT`.** A board that has never been stamped and a board
    whose stamp is behind are different findings -- the first says nothing
    about whether the records are right, the second says they are not -- and
    a caller that treated a missing stamp as "no drift detected" would serve
    stale rows with more confidence than a caller that knew nothing. That is
    the same failure `board_put` refuses when it declines to stamp a
    revision it could not prove belongs to the text the store now holds.
    """
    stored = store.read_source(board)
    if not stored:
        return UNKNOWN, "the records carry no source revision"
    if not live_rev:
        return UNKNOWN, "no live revision to compare against"
    if stored == live_rev:
        return CURRENT, f"built from {stored}"
    return STALE, f"built from {stored}, the file is at {live_rev}"
