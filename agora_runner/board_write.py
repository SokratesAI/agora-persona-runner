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
"""

import copy

from agora_runner import board_records, board_store


class WriteRefused(ValueError):
    """The change was not attempted -- a row that is not there, a key that is
    not on the item. Nothing was written."""


class BoardDamaged(RuntimeError):
    """The write landed and the board came back wrong.

    Separate from `WriteRefused` because the two need opposite responses: a
    refusal means fix the call, this means a row of his is in an unexpected
    state *now* and the message says which.
    """


def _differences(before, after, number):
    """Every way the board moved other than row `number`'s declared keys."""
    problems = []
    numbers_before = [item.get("number") for item in before["items"]]
    numbers_after = [item.get("number") for item in after["items"]]
    if numbers_before != numbers_after:
        problems.append(
            "the row order or the set of rows changed: "
            f"{numbers_before} -> {numbers_after}")
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


def change_row(board, number, changes, detail=None, store=board_store):
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
    unknown = sorted(key for key in changes if key not in held)
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
