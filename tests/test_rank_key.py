"""The rank key has one job and it is an ordering, so every test is an order.

The failure this guards against is not an exception -- it is a key that
looks fine and sorts wrong, or a pair of rows that quietly becomes
unsplittable. So the assertions here compare strings with `<` rather than
checking shapes.
"""

import pytest

from agora_runner.rank_key import (
    DIGITS,
    RankError,
    between,
    is_valid,
    sequence,
)


def test_first_key_on_an_empty_board():
    key = between(None, None)
    assert is_valid(key)


def test_appending_and_prepending_stay_in_order():
    middle = between(None, None)
    after = between(middle, None)
    before = between(None, middle)
    assert before < middle < after
    assert is_valid(before) and is_valid(after)


def test_between_two_neighbours_lands_strictly_between():
    low = between(None, None)
    high = between(low, None)
    mid = between(low, high)
    assert low < mid < high


def test_repeated_splits_of_the_same_gap_never_collide():
    """The case that kills a dense 1..N scheme, run a hundred times.

    Each round re-splits the *same* pair, which is what a row dragged one
    position at a time does. A scheme with a bounded number of positions
    between two keys runs out here; this one grows the key instead.
    """
    low = between(None, None)
    high = between(low, None)
    seen = {low, high}
    for _ in range(100):
        mid = between(low, high)
        assert low < mid < high
        assert mid not in seen
        assert is_valid(mid)
        seen.add(mid)
        high = mid


def test_repeated_appends_stay_ordered():
    keys = [between(None, None)]
    for _ in range(200):
        keys.append(between(keys[-1], None))
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    assert all(is_valid(key) for key in keys)


def test_repeated_prepends_stay_ordered():
    keys = [between(None, None)]
    for _ in range(200):
        keys.insert(0, between(None, keys[0]))
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    assert all(is_valid(key) for key in keys)


def test_appending_past_the_largest_digit():
    """`z` has nothing above it in the alphabet, so the key has to grow."""
    key = "z" * 5
    assert is_valid(key)
    nxt = between(key, None)
    assert key < nxt
    assert is_valid(nxt)


def test_prepending_below_a_key_that_starts_at_the_smallest_digit():
    low = "01"
    assert is_valid(low)
    nxt = between(None, low)
    assert nxt < low
    assert is_valid(nxt)


def test_adjacent_alphabet_neighbours_still_split():
    """`V` and `W` are one digit apart -- the gap has to open one level down."""
    mid = between("V", "W")
    assert "V" < mid < "W"


@pytest.mark.parametrize("count", [1, 2, 3, 61, 62, 63, 400, 1000, 2000])
def test_sequence_is_ascending_unique_and_valid(count):
    keys = sequence(count)
    assert len(keys) == count
    assert keys == sorted(keys)
    assert len(set(keys)) == count
    assert all(is_valid(key) for key in keys)


def test_sequence_leaves_room_on_both_ends_and_in_every_gap():
    """A seeded board must still take an insert anywhere, or the seed is a
    dense numbering with extra characters."""
    keys = sequence(50)
    assert between(None, keys[0]) < keys[0]
    assert between(keys[-1], None) > keys[-1]
    for low, high in zip(keys, keys[1:]):
        mid = between(low, high)
        assert low < mid < high


def test_sequence_of_none():
    assert sequence(0) == []


def test_sequence_refuses_a_negative_count():
    with pytest.raises(RankError):
        sequence(-1)


def test_a_key_may_not_end_in_the_smallest_digit():
    """The invariant the whole scheme rests on, asserted as itself.

    `V0` sorts immediately after `V` with nothing between them, so a key
    ending in the smallest digit is a row that can never be moved above its
    neighbour.
    """
    assert not is_valid("V" + DIGITS[0])
    assert is_valid("V" + DIGITS[1])
    with pytest.raises(RankError):
        between("V" + DIGITS[0], None)
    with pytest.raises(RankError):
        between(None, "V" + DIGITS[0])


def test_rejects_characters_outside_the_alphabet():
    assert not is_valid("V-1")
    assert not is_valid("")
    with pytest.raises(RankError):
        between("V-1", None)


def test_refuses_a_pair_that_is_not_in_order():
    with pytest.raises(RankError):
        between("W", "V")
    with pytest.raises(RankError):
        between("V", "V")


def test_a_board_can_be_appended_to_forever():
    """The reviewer's finding, cycle 1239, as a test that can actually see it.

    `between(key, None)` is how a new row goes to the bottom of a board. With
    a recursive midpoint this raised `RecursionError` on append 5,987, when
    the key reached 998 characters -- and the 200-append test above passed
    the whole time, two orders of magnitude short of it.
    """
    key = between(None, None)
    previous = key
    for _ in range(20000):
        key = between(previous, None)
        assert previous < key
        previous = key
    assert is_valid(key)
