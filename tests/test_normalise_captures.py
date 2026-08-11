"""The one-time repair that makes the capture files rollable.

The shapes here are the ones the live files actually have, measured on
2026-08-11 -- two streams reaching the same day, most entries carrying no
`(Cycle N)` at all, and an undated tail at the bottom of the top stream
that is the oldest material in the file. A fixture that is merely "not
sorted" would pass under a plain reverse, which is the fix this module
exists to replace.
"""

import pytest

from tools.normalise_captures import keys, merge, normalise, split_streams
from tools.roll_captures import MARKER, check_newest_first
from tools.rolling import RollError, split_bullets


def _file(*entries):
    return "---\ntype: log\n---\n\n# Nova — Issues\n" + MARKER + "\n" + "\n\n".join(entries) + "\n"


def _entries(text):
    return split_bullets(text.split(MARKER, 1)[1])


TOP = ["- (Cycle 112) — newest", "- (Cycle 63) — mid", "- an ancient one with no marker"]
BOTTOM = ["- (Cycle 24) — oldest", "- (Cycle 70) — middling", "- (Cycle 111) — nearly newest"]


def test_the_two_streams_are_found_where_the_order_turns_around():
    """The cut is the *smallest* index that leaves a descending top and an
    ascending bottom, so the unmarked entry in the ambiguous window goes
    to the bottom stream's head -- which reverses to its tail, which is
    where an entry too old to carry a marker belongs."""
    top, bottom = split_streams(TOP + BOTTOM)
    assert top == TOP[:2]
    assert bottom == TOP[2:] + BOTTOM


def test_a_reverse_is_not_the_fix_and_a_merge_is():
    """The whole reason this module exists.

    Both streams reach the present -- Cycle 112 at the top of one and
    Cycle 111 at the top of the other -- so reversing either one leaves
    the file interleaved. Only a merge puts C112 above C111 above C70.
    """
    out = _entries(normalise(_file(*TOP, *BOTTOM)))
    assert out == [
        "- (Cycle 112) — newest",
        "- (Cycle 111) — nearly newest",
        "- (Cycle 70) — middling",
        "- (Cycle 63) — mid",
        "- (Cycle 24) — oldest",
        "- an ancient one with no marker",
    ]
    check_newest_first(out)
    # And the naive fix this replaces would not have got there.
    assert out != TOP[::-1] + BOTTOM
    assert out != TOP + BOTTOM[::-1]


def test_an_unmarked_entry_takes_the_oldest_cycle_it_could_be():
    """Filling from the marker *above* would date the top stream's undated
    tail as Cycle 63 and lift 85 of the oldest captures in the live file
    over half of it. Filling from below leaves them last."""
    assert keys(TOP) == [112, 63, -1]
    out = _entries(normalise(_file(*TOP, *BOTTOM)))
    assert out[-1] == "- an ancient one with no marker"


def test_captures_written_by_one_cycle_keep_the_order_it_wrote_them_in():
    """A cycle files two to five captures at once and they all carry the
    same marker, so ties are the norm. A merge that reordered them would
    scramble the file while claiming to sort it."""
    top = ["- (Cycle 90) — first thing I noticed", "- (Cycle 90) — second thing I noticed"]
    bottom = ["- (Cycle 90) — a third, filed the other way"]
    assert merge(top, bottom) == top + bottom


def test_an_already_ordered_file_comes_back_untouched():
    ordered = _file("- (Cycle 99) — a", "- (Cycle 98) — b", "- (Cycle 97) — c")
    assert _entries(normalise(ordered)) == _entries(ordered)
    # ...and normalising twice is the same as normalising once.
    once = normalise(_file(*TOP, *BOTTOM))
    assert normalise(once) == once


def test_nothing_is_lost_even_when_two_entries_read_the_same():
    """This runs against the only copy of 525 real captures, so the guard
    is a multiset comparison rather than a length check -- a swap that
    dropped one entry and duplicated another would pass a count."""
    text = _file(*TOP, *BOTTOM)
    before, after = _entries(text), _entries(normalise(text))
    assert sorted(before) == sorted(after)


def test_a_file_that_is_not_two_streams_is_refused_rather_than_guessed():
    """`split_streams` finding no valid cut means the file has some third
    shape this tool has never seen, and reordering it would be invention."""
    with pytest.raises(RollError, match="not two streams"):
        split_streams(["- (Cycle 10) — a", "- (Cycle 99) — b", "- (Cycle 20) — c"])


def test_a_file_with_no_entries_section_is_refused():
    with pytest.raises(RollError, match="no '## Entries' section"):
        normalise("---\ntype: log\n---\n\n# Nova — Issues\n\n- (Cycle 1) — a\n")
