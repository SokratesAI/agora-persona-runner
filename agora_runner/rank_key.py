"""Sparse ordering keys, so moving one board row writes one document.

The first piece of issue #203 -- the owner's decision of 2026-09-08 that
the boards get a real schema (`projects/sokrates/projects/nova/board-records.md`,
`status: approved`). That spec names this primitive directly: *"A sparse
rank key -- LexoRank (Jira) or fractional indexing (Figma), both
established prior art -- rather than dense 1..N, so moving one row writes
one document."*

The thing it replaces is `nova_boards.set_project_order`, which renumbers
every row in the table on every move. **That costs nothing today** and the
first draft of this docstring wrongly said it did: a board is one markdown
document with one `_rev`, so moving one row and renumbering forty are the
same single write to the same document either way. The narrower conflict
window is a property of the per-row store this feeds, where a move that
touches one document instead of four hundred is the difference between two
overlapping cycles reordering different projects cleanly and one of them
losing its write.

**Nothing calls this yet, and that is deliberate rather than an oversight.**
The spec is explicit that the store, the migration and all 29 readers move
in one change with no facade phase, because a facade is what creates the
window in which two stores are both live. This is the pure function that
change needs and can be checked on its own; the store lands on top of it.

## Which of the two prior arts this is

Fractional indexing rather than LexoRank -- but **only the fractional half
of it**, and the half that is missing has a measurable cost, so it is stated
here rather than in a follow-up nobody reads. The `fractional-indexing`
package carries a variable-length *integer part* in front of the fraction and
appends by incrementing that, in constant time and constant length; this
module has no integer part, so `between(last, None)` walks into the
fractional fallback that library treats as a last resort. The keys stay
correct and stay ordered, and they **grow about one character every five
appends to the tail**. Adding the integer part is the optimisation, and it is
boarded; a board that gains a few rows a day is thousands of appends away
from the length mattering.

What is kept from that library is the invariant that makes the scheme work at
all, and it is easy to lose: **a key never ends in the smallest digit.**
Without it there are pairs of adjacent keys with nothing strictly between
them -- "V" and "V0" are adjacent in this alphabet, and a midpoint of them
would have to be "V0" again -- so a row could become unmovable with no error
anywhere. Every function here refuses a key that breaks it rather than
returning a key that silently collides.

## Two things the store on top of this has to do, which this cannot

**Sort with a byte comparator, not CouchDB's default.** Ordering here is
plain lexicographic byte order. CouchDB's view collation is ICU, which
interleaves case (`a < A < aa < b`) rather than running digits, then
uppercase, then lowercase -- so a view keyed on `rank` under the default
collation does *not* reproduce the order these functions define. An earlier
draft of this docstring claimed the opposite; it was wrong, and it would have
sent whoever built the view looking for the bug somewhere else.

**Detect a duplicate key and retry.** `between` is a pure function, so two
writers inserting into the same gap at the same moment get the *same* string
back. That is inherent to every scheme of this family and it is the store's
problem, not this module's -- but nothing here will warn you about it.
"""

from __future__ import annotations

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

SMALLEST = DIGITS[0]
LARGEST = DIGITS[-1]


class RankError(ValueError):
    """A key that breaks the invariant, or a pair that is not in order."""


def is_valid(key: str) -> bool:
    """True for a string this module would ever hand out.

    Non-empty, every character in the alphabet, and no trailing smallest
    digit -- that last clause is the whole scheme, see the module docstring.
    """
    if not isinstance(key, str) or not key:
        return False
    if any(character not in DIGITS for character in key):
        return False
    return key[-1] != SMALLEST


def _check(key: str | None, label: str) -> None:
    if key is None:
        return
    if not is_valid(key):
        raise RankError(f"{label} is not a valid rank key: {key!r}")


def between(before: str | None, after: str | None) -> str:
    """A key that sorts strictly after `before` and strictly before `after`.

    `None` on either side means "no neighbour there" -- `between(None, None)`
    is the first key on an empty board, `between(last, None)` appends, and
    `between(None, first)` prepends. Every call writes one row and touches
    no other.
    """
    _check(before, "before")
    _check(after, "after")
    if before is not None and after is not None and before >= after:
        raise RankError(f"before must sort before after: {before!r} >= {after!r}")
    return _midpoint(before or "", after)


def _midpoint(before: str, after: str | None) -> str:
    """The published `midpoint`, with `""` for -inf and `None` for +inf.

    **Iterative, and that is not a style choice.** Written with the obvious
    recursion this takes one stack frame per leading character it agrees on,
    so `between(key, None)` -- appending a row to the bottom of a board, the
    most ordinary call there is -- raised `RecursionError` once the key
    reached 998 characters, which is the 5,987th consecutive append. The
    tests could not see it: they appended two hundred times, two orders of
    magnitude short. Reviewer finding, cycle 1239, reproduced before fixing.

    Kept as its own function because `between` validates the whole keys and
    this walks suffixes, and a suffix legitimately may end in the smallest
    digit even though a whole key may not.
    """
    out = []
    while True:
        if after is not None:
            shared = 0
            while shared < len(after):
                mine = before[shared] if shared < len(before) else SMALLEST
                if mine != after[shared]:
                    break
                shared += 1
            if shared > 0:
                out.append(after[:shared])
                before, after = before[shared:], after[shared:]
                continue

        low = DIGITS.index(before[0]) if before else 0
        high = DIGITS.index(after[0]) if after else len(DIGITS)

        if high - low > 1:
            out.append(DIGITS[(low + high) // 2])
            return "".join(out)
        if after is not None and len(after) > 1:
            # The gap at this position is closed, but `after` continues, so
            # the whole of `after`'s first digit is available one level down.
            out.append(after[:1])
            return "".join(out)
        # Nothing fits here. Keep this digit and look one place further in,
        # with no upper neighbour left to respect.
        out.append(DIGITS[low])
        before, after = before[1:], None


def sequence(count: int) -> list[str]:
    """`count` keys in ascending order, evenly spread, for seeding a board.

    The migration has to give four hundred existing rows a rank in the order
    they are already in. Doing that with repeated `between(previous, None)`
    is correct and produces keys that grow a character every few rows,
    because each call lands halfway to the top; spreading them across the
    alphabet up front keeps every key short and leaves room on both sides
    and between every pair.
    """
    if count < 0:
        raise RankError(f"count must not be negative: {count}")
    if count == 0:
        return []

    base = len(DIGITS)
    # Enough width that consecutive keys are at least two apart, so nudging
    # one off the smallest digit below can never reach its neighbour.
    width = 1
    while base**width < 2 * (count + 1):
        width += 1
    step = base**width // (count + 1)

    keys = []
    for index in range(1, count + 1):
        keys.append(_encode(index * step, width))
    return keys


def _encode(value: int, width: int) -> str:
    digits = []
    for _ in range(width):
        value, remainder = divmod(value, len(DIGITS))
        digits.append(DIGITS[remainder])
    key = "".join(reversed(digits))
    if key[-1] == SMALLEST:
        # Preserve the invariant. `step >= 2` above is what makes this safe:
        # the bump is one position and the next key is at least two away.
        key = key[:-1] + DIGITS[1]
    return key
