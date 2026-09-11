"""One cell of one row, changed in the records, with the whole board checked.

`board_records.store_item` is the write mirror of `contents` and it writes
*one document*. That is the right size for a seam and it is not the whole of
what the nine `tools/board_*.py` writers do today. Each of them:

1. parses his markdown,
2. edits one cell,
3. **parses the result again and refuses unless exactly that cell moved**,
4. and only then writes the file.

Step 3 is the part that matters and it is the part `store_item` does not
cover. Its nine copies are field-specific on the surface -- `check` in
`board_status.py` knows about a status, `board_priority.py`'s knows about a
rating and its derived key -- but the half that actually protects him is the
same sentence in all nine: *nothing else on this board moved.* No other row,
no row order, no capture, no reply, no write-up.

So it lives here once, and the switchover converts nine call sites onto it
rather than writing the after-check nine more times against a store instead of
a parser. That is step 2's rule taken at its word: three fixes of one shape
means the shape is the bug, and this shape is at nine.

**Two reads of the store, not one.** The check needs the board as it was and
the board as it is, and `parse_board`-era code got the "as it was" copy for
free because it was holding the source string. Against records the before copy
is a real query, and it has to be the *whole* board rather than the one row --
the failure being guarded is a write that damages a row the writer never named.

**What it deliberately does not do is judge the change.** A caller says which
keys move and to what; this refuses a key that is not on the item (a typo in a
writer would otherwise write a field nothing reads back) and is otherwise
indifferent to whether `🟡 In progress` is a sensible status. Deciding that is
each tool's own job and it already does it, with its own error text and its own
`--help`. Moving that judgement in here would make one module the authority on
nine unrelated vocabularies.

**The row is compare-and-swapped between the read and the write, and without
that this guard could not see its own worst failure.** `wanted` is built from
the board as it was read, and `store_item` re-reads the row only for its `rank`,
its `_rev` and its stored write-up -- every *field value* comes from that older
snapshot. So a second cycle changing a different cell of the **same** row in
between would be silently overwritten with the stale value, and the after-check
could not catch it, because `landed` is compared against `wanted` and `wanted`
*is* the clobbering value. The check would agree with the clobber. So the row
document's revision is read at the same moment as the board and asserted again
immediately before the write, and a change refuses rather than landing: this is
the one race where being late has to mean stopping rather than reporting.

**A second cycle writing a *different* row of the same board still reads as
damage, and that is the safe direction rather than the right one.** The pair of
whole-board reads carries no revision across it and cannot -- a board is not one
document. That write shows up in the after-read as "row #N was not the row being
changed and its status moved", which is a true sentence about a board that is
fine. `BoardDamaged` names the possibility in its own message: a caller that
retries from the top gets the right answer, and the alternative -- narrowing the
check to the one row -- would delete the guard this module exists to be. That
one is a real limit rather than a solved problem, and it belongs to whoever
converts the nine call sites.

**A no-op change set is allowed, on purpose.** Re-rating a row to the rating it
already has is an ordinary thing for him to ask for twice, `board_store.write_row`
already short-circuits a document it would not alter, and refusing here would
turn "you asked for something already true" into an error the nine tools would
each have to translate. The after-check is unaffected: nothing moved is exactly
what it wants to see.

**`append_note` is the second door here and it exists because `change_row`
alone cannot serve four of the six writers left.** `--note` is on
`board_status`, `board_size`, `board_milestone` and `board_priority`, and it
appends one dated line to a row's write-up -- which `change_row` can only do by
*replacing* the write-up, so a caller that gets the read-modify-write wrong
deletes his prose and reports success. It is the records half of
`nova_boards.append_detail_note`, it carries the caller's change set through in
the same single write, and it sets the row's `updated` cell itself, which is
what `_touch_row_updated` was doing as a second pass over the markdown.
"""

import copy

from agora_runner import (
    board_document, board_records, board_store, nova_boards, rank_key)
from agora_runner.nova_boards import NOTE_AUTHORS


class WriteRefused(ValueError):
    """The change was not attempted -- a row that is not there, a key that is
    not on the item. Nothing was written."""


class BoardDamaged(RuntimeError):
    """The write landed and the board came back wrong.

    Separate from `WriteRefused` because the two need opposite responses: a
    refusal means fix the call, this means a row of his is in an unexpected
    state *now* and the message says which.
    """


def refuse_cell(value, flag, allow_blank=False):
    """Why `value` may not go in a cell of the generated board view, or `None`.

    A `|` splits the cell and a `\\r` or `\\n` splits the row, in the markdown
    the daily GitHub backup renders off these records. The store itself would
    hold any of the three quite happily, which is why the rule has to live
    somewhere a writer cannot skip -- and CommonMark makes a bare `\\r` a line
    ending, so Obsidian breaks the row on his phone even though a `"\\n" in
    value` check sees nothing.

    It lives here rather than in each CLI because the rule is the same
    sentence in every one of them: `board_status` had the only copy and
    `board_milestone` would have been the second, with `board_size` and
    `board_priority` still to convert. Four copies of a cell rule is how two
    of them come to disagree. All four are here now (`board_priority` was the
    last, Cycle 1377), and `board_size` deliberately does **not**
    route its own `--size` through this -- that value is bounded to four
    constants, so the vocabulary check is already the cell check.

    `allow_blank` is for `--milestone ''`, the one flag whose empty value is a
    real instruction: clearing a row back to ungrouped has to stay reachable,
    and unlike a size or a status an empty milestone cannot be confused with
    a typo. Everywhere else blank means the caller passed nothing useful.
    """
    if value is None:
        return None
    if not value.strip() and not allow_blank:
        return f"{flag} may not be blank"
    for character in "|\r\n":
        shown = {"\r": "a carriage return", "\n": "a newline"}.get(
            character, f"a {character!r}")
        if character in value:
            return (f"{flag} carries {shown}, which escapes its own cell in "
                    "the generated board view")
    return None


def _differences(before, after, number, added=False):
    """Every way the board moved other than row `number`'s declared keys.

    `added=True` is `add_row`'s case: row `number` is expected to appear, at
    the *top*, and every other row is expected to keep its place under it. The
    flag rather than a looser comparison, because "the set of rows changed" is
    the whole check for a writer that changes a cell -- a version that tolerated
    an extra row for everybody would stop seeing the row `change_row` duplicated.
    """
    problems = []
    numbers_before = [item.get("number") for item in before["items"]]
    numbers_after = [item.get("number") for item in after["items"]]
    expected = ([number] + numbers_before) if added else numbers_before
    if expected != numbers_after:
        problems.append(
            "the row order or the set of rows changed: "
            f"{numbers_before} -> {numbers_after}"
            + (f", expected #{number} at the top of them" if added else ""))
        return problems
    if added:
        after = {**after, "items": after["items"][1:]}
    for was, now in zip(before["items"], after["items"]):
        if was.get("number") != now.get("number"):
            continue
        if was.get("number") == number:
            continue
        if was != now:
            moved = sorted(
                key for key in set(was) | set(now)
                if was.get(key) != now.get(key))
            problems.append(
                f"row #{was.get('number')} was not the row being changed and "
                f"its {', '.join(moved)} moved")
    if before["captures"] != after["captures"]:
        # The count when it moved, the word when it did not: "2 -> 2" is true and
        # tells whoever reads this nothing, and a bullet whose *text* was
        # rewritten is the case where the count cannot move.
        if len(before["captures"]) != len(after["captures"]):
            problems.append(
                f"the capture bullets changed: {len(before['captures'])} -> "
                f"{len(after['captures'])}")
        else:
            problems.append(
                "the capture bullets changed: still "
                f"{len(after['captures'])} of them, and the text of at least "
                "one is different")
    if before["captureReplies"] != after["captureReplies"]:
        problems.append("the replies under his capture bullets changed")
    return problems


def change_row(board, number, changes, detail=None, store=board_store,
               expect=None):
    """Change `changes` on row `number` of `board`, and check the whole board.

    `changes` is `{item key: new value}` in `parse_board`'s row vocabulary --
    a writer that derives a second key from one cell passes both, exactly as
    it does today (`{"priority": "🟠 High", "priorityKey": "high"}`).

    `detail` is `store_item`'s: `None` leaves his write-up alone, a string
    replaces it, `""` removes it. The after-check expects precisely that, so a
    writer that meant to leave the prose alone finds out here rather than from
    him.

    Returns `(before_item, after_item)`. Raises `WriteRefused` before writing
    anything, or `BoardDamaged` after a write that did not land cleanly.
    """
    before = board_records.contents(board, store=store)
    # Read straight after the board and kept for the compare-and-swap below.
    # `contents` hands back `parse_board`'s four keys and no revisions, on
    # purpose, so the revision has to be asked for separately.
    held_rev = (store.read_row(board, number) or {}).get("_rev")
    held = None
    for item in before["items"]:
        if item.get("number") == number:
            held = item
            break
    if held is None:
        raise WriteRefused(
            f"#{number} is not a row on board {board!r} -- it holds "
            f"{len(before['items'])} row(s)")
    # `expect` is `{key: value}` the row must still hold, judged on this read
    # -- which the compare-and-swap below ties to the write -- rather than on
    # whatever older read the caller decided from.
    for key, value in (expect or {}).items():
        if held.get(key) != value:
            raise WriteRefused(
                f"row #{number} of board {board!r} now has {key} "
                f"{held.get(key)!r}, not {value!r}; nothing was written")
    # An optional field is absent from a row until it is first written, so
    # "not a key on this row" is not the test for one.
    unknown = sorted(key for key in changes if key not in held
                     and key not in board_document.OPTIONAL_FIELDS)
    if unknown:
        raise WriteRefused(
            f"{', '.join(unknown)} is not a key on a row of board {board!r}; "
            f"a row carries {', '.join(sorted(held))}")

    wanted = dict(copy.deepcopy(held), **changes)
    # The compare-and-swap. `wanted` was built from the board read at the top of
    # this function, so if the row has moved since then the write would put the
    # older values back -- and the after-check could not see it, because it
    # compares what landed against `wanted`, which *is* the stale copy.
    if (store.read_row(board, number) or {}).get("_rev") != held_rev:
        raise WriteRefused(
            f"row #{number} of board {board!r} changed between reading the "
            "board and writing it, so this write would put the older values "
            "back; re-read the board and try again")
    board_records.store_item(board, wanted, detail=detail, store=store)

    after = board_records.contents(board, store=store)
    problems = _differences(before, after, number)
    landed = next(
        (item for item in after["items"] if item.get("number") == number), None)
    if landed is None:
        problems.append(f"row #{number} is not on the board any more")
    elif landed != wanted:
        missed = sorted(
            key for key in set(wanted) | set(landed)
            if wanted.get(key) != landed.get(key))
        problems.append(
            f"row #{number} came back with {', '.join(missed)} not as written")
    expected_details = dict(before["details"])
    if detail is not None:
        if detail == "":
            expected_details.pop(number, None)
        else:
            expected_details[number] = detail
    if after["details"] != expected_details:
        changed = sorted(
            key for key in set(expected_details) | set(after["details"])
            if expected_details.get(key) != after["details"].get(key))
        problems.append(
            "the write-up under "
            f"{', '.join('#' + str(key) for key in changed)} changed")
    if problems:
        raise BoardDamaged(
            f"the write to row #{number} of board {board!r} landed and the "
            "board came back wrong: " + "; ".join(problems)
            + " -- if another cycle wrote a different row of this board in "
            "between, re-read the board and try again; this check cannot tell "
            "that apart from damage")
    return held, landed


class RowGone(WriteRefused):
    """The row was there when the board was read and gone when the delete
    reached it -- another delete of the same row won, most likely a second
    tap. Its own class because the caller's answer is "that row is not there"
    rather than "the write failed"."""


def remove_row(board, number, store=board_store):
    """Delete row `number` of `board` and its write-up, and check the board.

    The records half of the app's Delete button. The row's document is read
    straight after the board, as `change_row` reads its revision, and deleted
    on that revision -- so a row another writer changed after this read comes
    back as a refusal rather than a delete of text nobody here has seen. The
    after-check is `change_row`'s with the expected board being the one read
    minus this row: every other row, the order, the captures, the replies and
    every other write-up come back exactly as they were.

    Returns `(item, write_up)` as they were read, `write_up` being `None` for
    a row that had none -- the caller archives the deleted text off these.
    Raises `WriteRefused` before deleting anything, or `BoardDamaged` after a
    delete that did not leave the rest of the board intact.
    """
    before = board_records.contents(board, store=store)
    doc = store.read_row(board, number)
    held = next(
        (item for item in before["items"] if item.get("number") == number), None)
    if held is None or doc is None:
        raise WriteRefused(
            f"#{number} is not a row on board {board!r} -- it holds "
            f"{len(before['items'])} row(s)")
    try:
        removed = store.delete_row(doc)
    except board_store.RowConflict as problem:
        raise WriteRefused(
            f"row #{number} of board {board!r} changed between reading the "
            f"board and deleting it; re-read the board and try again ({problem})")
    if not removed:
        raise RowGone(
            f"row #{number} of board {board!r} was already gone when the "
            "delete reached it")

    after = board_records.contents(board, store=store)
    expected = {**before, "items": [
        item for item in before["items"] if item.get("number") != number]}
    problems = _differences(expected, after, number)
    expected_details = {key: body for key, body in before["details"].items()
                        if key != number}
    if after["details"] != expected_details:
        changed = sorted(
            key for key in set(expected_details) | set(after["details"])
            if expected_details.get(key) != after["details"].get(key))
        problems.append(
            "the write-up under "
            f"{', '.join('#' + str(key) for key in changed)} changed")
    if problems:
        raise BoardDamaged(
            f"the delete of row #{number} of board {board!r} landed and the "
            "board came back wrong: " + "; ".join(problems)
            + " -- if another cycle wrote a different row of this board in "
            "between, re-read the board; this check cannot tell that apart "
            "from damage")
    return held, before["details"].get(number)


class NoteRefused(WriteRefused):
    """The note itself was not writable -- an empty line, a line break, an
    author who is not one of the two. Nothing was written.

    A subclass of `WriteRefused` because it is the same promise (nothing has
    happened, fix the call) and the four converting tools each want to print
    their own sentence about a bad `--note` without also having to catch a
    second exception for a bad `--status`.
    """


def _note_line(note, dated, cycle, author):
    """The one dated line, or `None` if this is not a note that can be written.

    Split out from `append_note` so the refusals and the rendering are read
    together: every one of them exists because of what the line has to survive
    once it is back in his markdown, and reading them next to the string they
    guard is the only way that stays true.

    **A line break is refused even though the write-up is a whole field now.**
    In `nova_boards.append_detail_note` this was structural -- `_detail_spans`
    ends a write-up at the next heading, so a note carrying a newline truncated
    the block and every later line of his own text stopped rendering. Against
    records the body is one value and a newline cannot reach another row. It is
    still refused, because the *reader* is unchanged: `_COMMENT_NOTE_RE` is
    `re.MULTILINE` and anchors a note at the start of a line, so a two-line note
    reads back as one note plus an orphaned sentence with nobody's name on it.
    One line in, one line out, on both sides of the store.

    **`\r` counts and is the one that gets past a `"\n" in note` check.**
    `re.MULTILINE` anchors on `\n` alone, so a bare `\r` splits nothing here
    and every test agrees the note is harmless. CommonMark defines it as a line
    ending, so Obsidian on his phone renders the break anyway.

    **A `|` in `dated` is refused because `dated` is also the row's `updated`
    cell.** `board_view.render_row` raises on a pipe rather than escaping it,
    so this refusal now fires before a write that would otherwise fail halfway
    through rendering his board back. The note body is unaffected and keeps
    taking any `|` it likes -- prose is not a cell.

    **The author is checked against `NOTE_AUTHORS` rather than written
    through**, and an unknown one is a refusal rather than a fallback to mine:
    the name lands inside `**...**` in his own file, and attributing his
    sentence to me is exactly the corruption worth stopping.
    """
    note = (note or "").strip()
    dated = (dated or "").strip()
    if not note or not dated:
        return None
    if any(c in note or c in dated for c in "\r\n"):
        return None
    if "|" in dated:
        return None
    name = NOTE_AUTHORS.get(("Nova" if author is None else author).strip().lower())
    if name is None:
        return None
    who = f"{name}, {dated}" if cycle is None else f"{name}, {dated} (Cycle {cycle})"
    return f"**{who}:** {note}"


def append_note(board, number, note, dated, cycle=None, author=None,
                changes=None, store=board_store):
    """Add one dated line to the end of row `number`'s write-up, in one write.

    The records half of `nova_boards.append_detail_note`, and the thing four of
    the six writers left when it was written were waiting on: `--note` is on
    `board_status`, `board_size`, `board_milestone` and `board_priority`, and
    `change_row` can only *replace* a write-up. Replacing it from a caller that
    wanted to append is how his prose gets deleted by a tool that reported
    success.

    **It is one call and one write on purpose.** A status move and its reason
    are one thing he asked for -- `--status done --note 'what closed it'` --
    and doing it as two `change_row` calls would be two writes, two after-checks
    and a window in between where his board says a row closed and nothing says
    why. So `changes` is passed straight through and the note rides with it.

    **`updated` is set here and a caller may not also name it.** In markdown
    this was `_touch_row_updated`, a second pass over the document that could
    not fail and silently did nothing when the row was missing -- because a
    caller whose note landed did not want an exception about a table cell.
    Against records it is an ordinary key in the same change set, under the same
    compare-and-swap as everything else, so the hedge is gone: the cell moves or
    the write refuses. A caller passing its own `updated` is refused rather than
    quietly overruled, because the two values disagreeing is a bug in the caller
    and picking one of them hides it.

    **A row with no write-up is refused, and that is parity rather than a
    judgement.** `append_detail_note` returned `None` there because there was no
    span to append to -- structural, not a decision. Against records a note is a
    perfectly good first line of a write-up and `store_item` would create one.
    Lifting the refusal is a real improvement and it is not this commit's: the
    switchover converts, it does not redesign, and four tools currently print
    `REFUSED: could not append the note` in that case. Whoever lifts it should
    do it in one place for all four at once.

    Returns `(before_item, after_item)` from `change_row`. Raises `NoteRefused`
    or `WriteRefused` before writing anything, `BoardDamaged` after a write that
    did not land cleanly.
    """
    line = _note_line(note, dated, cycle, author)
    if line is None:
        raise NoteRefused(
            f"the note for row #{number} of board {board!r} is not one writable "
            "line: it must be non-empty, carry no line break, name an author "
            f"among {', '.join(sorted(NOTE_AUTHORS.values()))}, and come with a "
            "date carrying no '|'")
    changes = dict(changes or {})
    if "updated" in changes:
        raise NoteRefused(
            f"append_note sets row #{number}'s updated cell from the note's own "
            f"date ({dated!r}), so the change set may not name it too")
    changes["updated"] = dated.strip()

    body = board_records.contents(board, store=store)["details"].get(number)
    if body is None:
        raise NoteRefused(
            f"row #{number} of board {board!r} has no write-up to append to")
    # Trailing blank lines are the separator before whatever came next in the
    # document, not part of the body -- walk back over them so note two lands
    # directly under note one instead of drifting a line further each time.
    lines = body.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    # An empty write-up has nothing to separate the note from, and a leading
    # blank line there renders as one.
    detail = "\n".join(lines + ["", line]) if lines else line
    return change_row(board, number, changes, detail=detail, store=store)


class RowRefused(WriteRefused):
    """The new row itself was not writable -- no title, a rating that is not
    one, a cell that would escape itself. Nothing was written."""


def _next_number(rows):
    """The lowest number no stored row is using -- `next_row_number`'s rule.

    Highest plus one, never the first gap. `nova_boards.next_row_number` says
    why and the reason survives the move to records unchanged: a closed row's
    number is still spoken for by every journal entry, claim slug and comment
    that ever pointed at it, so handing it out again re-labels history. It
    reads all three of the markdown's places a number can live; against records
    there is only one, because a `## Done` row is an ordinary document with
    `done` set and a write-up is a field on the row rather than a heading of
    its own.
    """
    highest = 0
    for doc in rows:
        number = doc.get("number")
        if isinstance(number, int) and not isinstance(number, bool):
            highest = max(highest, number)
    return highest + 1


def add_row(board, title, dated, priority, status="backlog", write_up="",
            notes=(), project="", cycle=None, author=None, store=board_store):
    """Board a new row. Returns the row it wrote, in `parse_board`'s shape.

    The records half of `nova_boards.add_row`, and the door `board_capture`
    was blocked on: it promotes one of his bare captures into a row. Every
    writer converted before this one *changes* a row that already exists --
    `change_row` and `append_note` between them cover all of that -- and none
    of them can create one. This docstring named two more callers when it was
    written and neither was right: `board_row` boards a row on **Nova's own**
    two files and came off the migration list entirely, and
    `close_done_captures` marks a bullet in place, so what it needed was
    `change_capture_text` below.

    **The new row goes to the top, because that is where the markdown put
    it.** `nova_boards._board_insert_line` inserts directly under the header
    rule and says why in its own comment: newest first is the order every one
    of these tables already reads in. Against records the position is a rank,
    so this is `rank_key.between(None, first)` -- one key, written on this row
    alone, touching no other. A row minted with no rank at all would land
    *below* every ranked row instead, because `board_store.sort_key` puts the
    unranked last, so leaving it out is not neutral: it is the opposite of
    what he sees today.

    **`notes` ride across as dated lines under the write-up.** They are the
    replies a cycle already wrote under his capture, and they go through
    `_note_line` -- the same renderer and the same refusals as `append_note`,
    because they are the same kind of line and end up matched by the same
    `_COMMENT_NOTE_RE`. A note this function cannot render is a refusal rather
    than a line dropped in silence: the thread surviving the promotion is the
    point of carrying them at all.

    **The number is minted from the store, not passed in.** A caller holding
    the number would have had to read the board to get it, and two callers
    reading the same board mint the same number -- which against markdown was
    a merge conflict and against records is one document silently taking the
    other's place. `board_store.write_row` refuses a document whose id is
    already stored, so the collision surfaces as a refusal here rather than as
    a lost row.
    """
    # Stripped, never collapsed. `" ".join(title.split())` would turn a
    # newline into a space and quietly hand `refuse_cell` a title it approves
    # of -- one cell rule, in one place, is the point of that function.
    title = (title or "").strip()
    if not title:
        raise RowRefused("a row needs a title")
    for value, flag in ((title, "title"), (dated, "dated"), (project, "project")):
        refusal = refuse_cell(value or "", flag, allow_blank=(flag == "project"))
        if refusal:
            raise RowRefused(refusal)
    label = nova_boards.canonical_priority(priority)
    if label is None:
        raise RowRefused(
            f"{priority!r} is not a rating. One of: "
            + ", ".join(key for key in nova_boards.PRIORITY_LABELS if key))
    if status not in nova_boards.STATUS_LABELS:
        raise RowRefused(
            f"{status!r} is not a status. One of: "
            + ", ".join(nova_boards.STATUS_LABELS))

    body_lines = []
    written = (write_up or "").strip()
    if written:
        body_lines.append(written)
    for note in notes or ():
        line = _note_line(str(note), dated, cycle, author)
        if line is None:
            raise RowRefused(
                f"the reply {str(note)[:60]!r} cannot be written as a dated "
                "note under the new row -- it is empty, carries a line break, "
                "or names an author that is not one of "
                + ", ".join(sorted(set(NOTE_AUTHORS.values()))))
        body_lines.append(line)
    detail = "\n\n".join(body_lines)

    before = board_records.contents(board, store=store)
    rows, _ = board_records.split_documents(store.read_rows(board))
    number = _next_number(rows)
    first = board_store.in_order(rows)[0].get("rank") if rows else None
    label_status = nova_boards.STATUS_LABELS[status]
    wanted = {
        "number": number,
        "title": title,
        "status": label_status,
        "statusKey": status,
        "updated": dated,
        "where": "",
        "priority": label,
        "priorityKey": nova_boards.priority_key(label),
        "project": project or nova_boards.DEFAULT_PROJECT,
        "size": "",
        "sizeKey": nova_boards.size_key(""),
        "milestone": "",
        "order": None,
        "done": status == "done",
    }
    # The registry join is `store_item`'s and lives nowhere else, so the row
    # goes through it rather than straight to `write_row` -- a project named
    # here and minted nowhere is the dangling id `board_records._names` raises
    # on, and it breaks the read of the whole board rather than of this row.
    board_records.store_item(
        board, wanted, detail=detail,
        rank=rank_key.between(None, first), store=store)

    after = board_records.contents(board, store=store)
    problems = _differences(before, after, number, added=True)
    landed = next(
        (item for item in after["items"] if item.get("number") == number), None)
    if landed is None:
        problems.append(f"row #{number} is not on the board afterwards")
    elif landed != wanted:
        missed = sorted(
            key for key in set(wanted) | set(landed)
            if wanted.get(key) != landed.get(key))
        problems.append(
            f"row #{number} came back with {', '.join(missed)} not as written")
    if len(after["items"]) != len(before["items"]) + 1:
        problems.append(
            f"row count went {len(before['items'])} -> {len(after['items'])}, "
            "expected +1")
    expected_details = dict(before["details"])
    if detail:
        expected_details[number] = detail
    if after["details"] != expected_details:
        changed = sorted(
            key for key in set(expected_details) | set(after["details"])
            if expected_details.get(key) != after["details"].get(key))
        problems.append(
            "the write-up under "
            f"{', '.join('#' + str(key) for key in changed)} changed")
    if problems:
        raise BoardDamaged(
            f"the new row #{number} on board {board!r} landed and the board "
            "came back wrong: " + "; ".join(problems)
            + " -- if another cycle wrote to this board in between, re-read it "
            "and try again; this check cannot tell that apart from damage")
    return landed


class CaptureRefused(WriteRefused):
    """A capture rewrite that was not attempted. Nothing was written."""


def change_capture_text(board, doc, text, store=board_store):
    """Rewrite one capture's own words, and check the whole board afterwards.

    The door `tools.close_done_captures` is built on, and the last of the
    three capture primitives #203 needs: `board_records.capture_at` finds a
    bullet, `board_store.delete_capture` removes one, and this changes the
    words in one. `board_store.write_capture`'s docstring already named this
    caller -- *"the two tools that move captures between his `## Captures`
    list and his `## Processed captures` archive change a **subset** of
    them"* -- and until now that door had no after-check above it.

    **It takes the document as read, not an index or an id**, the same call
    `delete_capture` makes and for the same reason: a capture carries the
    owner's own words and every reply a cycle has written under them, and the
    `_rev` is the only thing standing between a rewrite and clobbering a reply
    that landed while I was deciding to mark the bullet. A document with no
    revision has not been read from the store this write is aimed at.

    **The replies are copied across untouched and are not the caller's to
    pass.** A signature that took them would let a caller marking his bullet
    `DONE (Cycle N):` drop an answer by omission, which is exactly the shape
    `delete_capture` refuses `write_captures(prune=True)` for.

    Returns `(old_text, text)`. Raises `CaptureRefused` before writing
    anything, or `BoardDamaged` after a write that did not land cleanly --
    and lets `board_store.CaptureConflict` through untouched, which is the
    third outcome and the one a caller has to handle rather than report.
    `write_capture` sends the revision this document was read at, so a
    concurrent edit of *this* bullet is refused by CouchDB itself; that is
    a lost race and not damage, and the answer to it is to re-read and try
    again.
    """
    if not isinstance(doc, dict):
        raise CaptureRefused(
            "change_capture_text takes the capture document as read, not "
            f"{type(doc).__name__}; see board_records.capture_at")
    if not doc.get("_rev"):
        raise CaptureRefused(
            f"the capture {doc.get('_id')!r} carries no revision, so this "
            "write would be conditional on nothing; read it through "
            "board_records.capture_at or board_records.capture_documents")
    if doc.get("board") != board:
        raise CaptureRefused(
            f"the capture {doc.get('_id')!r} is on board "
            f"{doc.get('board')!r}, not {board!r}")
    if not doc.get("captureId"):
        raise CaptureRefused(
            f"the capture {doc.get('_id')!r} carries no captureId, so there "
            "is no document for this write to be aimed at")
    if not isinstance(text, str) or not text.strip():
        raise CaptureRefused(
            "a capture with no words is a capture deleted; use "
            "board_store.delete_capture if that is what you meant")
    # `refuse_cell` and `_note_line` both refuse these and say why: a
    # capture is rendered as `- {text}`, so a newline in it puts a bare
    # paragraph line between two list items in the file he opens in
    # Obsidian, and CommonMark makes a lone `\r` a line ending too. The
    # after-check below compares text and cannot see format, so "nothing
    # else moved" is satisfied while the one thing that moved is broken.
    if "\n" in text or "\r" in text:
        raise CaptureRefused(
            "a capture is one bullet, so its words may not contain a line "
            "break -- his board would render the remainder as a bare "
            "paragraph between two list items")
    old_text = board_document.capture_text_of(doc)
    if text == old_text:
        raise CaptureRefused(
            f"the capture {doc.get('_id')!r} already reads exactly that, so "
            "this write would burn a revision and the after-check below "
            "could not tell it from a write that landed nowhere")

    before = board_records.contents(board, store=store)
    store.write_capture(dict(doc, text=text))
    after = board_records.contents(board, store=store)

    problems = _capture_differences(before, after, old_text, text)
    if problems:
        raise BoardDamaged(
            f"the rewrite of capture {doc.get('_id')!r} on board {board!r} "
            "landed and the board came back wrong: " + "; ".join(problems)
            + " -- if another cycle wrote this board in between, re-read it "
            "and try again; this check cannot tell that apart from damage")
    return old_text, text


def _capture_differences(before, after, old_text, text):
    """Every way the board moved other than this one bullet's words.

    Deliberately does **not** take the position the bullet was at. The
    caller holds a document, not an index, and asking it for one would mean
    deciding the order a second time -- so instead this asserts that exactly
    one position changed and that the position which changed held `old_text`
    and now holds `text`. That is the stronger check of the two: a write that
    landed on the *wrong* capture moves a position whose old words are not
    `old_text`, and it is caught here rather than passing because the count
    was right.
    """
    problems = []
    if before["items"] != after["items"]:
        problems.append("the board rows changed")
    if before["details"] != after["details"]:
        problems.append("the write-ups under his rows changed")
    was, now = before["captures"], after["captures"]
    # Before the replies check below, and that order is load-bearing rather
    # than tidy: the two lists are always the same length, so a lost bullet
    # moves the replies too and a replies-first version reports the wrong
    # cause -- and made the count check unreachable, which is how a mutation
    # deleting it survived this file's first round.
    if len(was) != len(now):
        problems.append(
            f"the capture bullets changed: {len(was)} -> {len(now)}")
        return problems
    if before["captureReplies"] != after["captureReplies"]:
        problems.append("the replies under his capture bullets changed")
    moved = [index for index, pair in enumerate(zip(was, now))
             if pair[0] != pair[1]]
    if len(moved) != 1:
        problems.append(
            f"{len(moved)} capture bullet(s) changed, expected exactly 1")
        return problems
    index = moved[0]
    if was[index] != old_text:
        problems.append(
            f"the bullet that changed was at position {index} and held "
            f"{was[index][:60]!r}, not the one that was written")
    elif now[index] != text:
        problems.append(
            f"the bullet at position {index} came back as "
            f"{now[index][:60]!r}, not as written")
    return problems
