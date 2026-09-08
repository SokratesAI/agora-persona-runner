"""Sparse ordering keys, so moving one board row writes one document.

The first piece of issue #203 -- the owner's decision of 2026-09-08 that
the boards get a real schema (`projects/sokrates/projects/nova/board-records.md`,
`status: approved`). That spec names this primitive directly: *"A sparse
rank key -- LexoRank (Jira) or fractional indexing (Figma), both
established prior art -- rather than dense 1..N, so moving one row writes
one document."*

The thing it replaces is `nova_boards.set_project_order`, which renumbers
every row in the table on every move. On a store that keeps a `_rev` per
document that is the widest possible conflict window for the smallest
possible change: three cycles overlap now, and two of them reordering
different projects in the same second is a lost write today.

**Nothing calls this yet, and that is deliberate rather than an oversight.**
The spec is explicit that the store, the migration and all 29 readers move
in one change with no facade phase, because a facade is what creates the
window in which two stores are both live. This is the pure function that
change needs and can be checked on its own; the store lands on top of it.

## Which of the two prior arts this is

Fractional indexing, in the shape Figma published and the `fractional-indexing`
npm package implements, not LexoRank. Both give sparse keys; the difference
is that LexoRank carries a bucket prefix and a rebalancing daemon, and a
board of four hundred rows edited by hand a few times a day will never need
one. What is kept from that library is the invariant that makes the scheme
work at all, and it is easy to lose: **a key never ends in the smallest
digit.** Without it there are pairs of adjacent keys with nothing strictly
between them -- "V" and "V0" are adjacent in this alphabet, and a midpoint
of them would have to be "V0" again -- so a row could become unmovable with
no error anywhere. Every function here refuses a key that breaks it rather
than returning a key that silently collides.

Ordering is plain lexicographic byte order on the strings, which is what
CouchDB's own view collation gives for strings of this alphabet, so a
sorted-by-rank query needs no comparator.
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

    Kept as its own function because `between` validates and this recurses:
    the recursive calls pass suffixes, and a suffix legitimately may end in
    the smallest digit even though a whole key may not.
    """
    if after is not None:
        shared = 0
        while shared < len(after):
            mine = before[shared] if shared < len(before) else SMALLEST
            if mine != after[shared]:
                break
            shared += 1
        if shared > 0:
            return after[:shared] + _midpoint(before[shared:], after[shared:])

    low = DIGITS.index(before[0]) if before else 0
    high = DIGITS.index(after[0]) if after else len(DIGITS)

    if high - low > 1:
        return DIGITS[(low + high) // 2]
    if after is not None and len(after) > 1:
        # The gap at this position is closed, but `after` continues, so the
        # whole of `after`'s first digit is available one level down.
        return after[:1]
    return DIGITS[low] + _midpoint(before[1:], None)


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
