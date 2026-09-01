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
from agora_runner.rolling import _body
from tools.roll_captures import spec_for
from tools.rolling import RollError, split_bullets


def _file(*entries):
    return "---\ntype: log\n---\n\n# Nova — Issues\n" + MARKER + "\n" + "\n\n".join(entries) + "\n"


def _entries(text):
    return split_bullets(text.split(MARKER, 1)[1])


TOP = ["- (Cycle 112) — newest", "- (Cycle 63) — mid", "- an ancient one with no marker"]
BOTTOM = ["- (Cycle 24) — oldest", "- (Cycle 70) — middling", "- (Cycle 111) — nearly newest"]


def test_the_two_streams_are_found_where_the_order_turns_around():
    """The cut is the *largest* index that leaves a descending top and an
    ascending bottom, so an unmarked entry in the ambiguous window stays
    with the top stream -- the stream it was written into, and the one
    `normalise` never reverses."""
    top, bottom = split_streams(TOP + BOTTOM)
    assert top == TOP + BOTTOM[:1]
    assert bottom == BOTTOM[1:]


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
        "- an ancient one with no marker",
        "- (Cycle 24) — oldest",
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
    # It sat above `(Cycle 24)` in the stream it was written into, so it
    # inherits 24 and the stable merge keeps it above it -- last but one,
    # not lifted over half the file the way filling from above would.
    assert out[-2:] == ["- an ancient one with no marker", "- (Cycle 24) — oldest"]


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


def test_a_long_unmarked_run_is_not_reversed_by_the_reverse():
    """The bug a reviewer found on the live file after I had merged it.

    `issues.md` has 85 consecutive unmarked entries directly under the
    top stream's last marker. Taking the smallest valid cut swept all of
    them into the bottom stream, where `bottom[::-1]` reversed their
    internal order -- and every guard passed, because `check_newest_first`
    and `verify` only ever look at entries that carry a marker. A fixture
    with one trailing unmarked entry cannot see this: reversing a
    one-element list is a no-op, which is exactly why the first round of
    tests missed it.
    """
    run = [f"- unmarked, written {n}th" for n in range(6)]
    top = ["- (Cycle 90) — newest"] + run
    bottom = ["- (Cycle 10) — oldest", "- (Cycle 50) — middling"]
    out = _entries(normalise(_file(*top, *bottom)))
    assert out == [
        "- (Cycle 90) — newest",
        "- (Cycle 50) — middling",
    ] + run + ["- (Cycle 10) — oldest"]
    assert [e for e in out if e in run] == run


def test_the_top_stream_is_never_reordered_and_the_bottom_is_exactly_reversed():
    """The invariant `split_streams` owes `merge`, asserted on both halves
    separately rather than on the merged result -- a merge that scrambled
    one stream can still come out newest-first by marker."""
    top = ["- (Cycle 99) — a", "- unmarked b", "- unmarked c", "- (Cycle 40) — d"]
    bottom = ["- (Cycle 41) — e", "- unmarked f", "- (Cycle 98) — g"]
    entries = top + bottom
    out = _entries(normalise(_file(*entries)))
    cut = len(split_streams(entries)[0])
    positions = [out.index(e) for e in entries]
    assert positions[:cut] == sorted(positions[:cut])
    assert positions[cut:] == sorted(positions[cut:], reverse=True)


# --- `--mode strays`, and the section boundary it depends on -----------
#
# The live files stopped being two streams. Measured 2026-09-01: inside
# `## Entries`, `ideas.md` is already newest-first and `issues.md` has a
# single ascent. What made it look like eleven was reading past the end of
# the section, which is what the fixtures below pin.


def _section(text):
    """Just the `## Entries` section -- `_entries` above reads past it."""
    return split_bullets(_body(text, spec_for(text))[1])


def _file_with_tail(*entries):
    return (
        "---\ntype: log\n---\n\n# Nova — Issues\n"
        + MARKER
        + "\n"
        + "\n\n".join(entries)
        + "\n\n## Retired\n\n- DONE (Cycle 9): (Cycle 400) — retired, and out of scope\n"
        + "\n## Board\n\n| # | Item |\n| - | - |\n| 1 | a row |\n"
    )


def test_a_marker_below_the_entries_section_is_not_an_ascent():
    """`## Retired` holds cycle numbers and is not a capture list.

    The two-piece `split_at_heading` returns everything under the marker,
    so a retired item dated Cycle 400 read as an entry sitting under a
    Cycle 10 capture -- an ascent, in a section the roller never touches.
    """
    live = _file_with_tail("- (Cycle 12) — newest", "- (Cycle 10) — older")
    assert normalise(live, mode="strays") == live


def test_the_sections_below_entries_come_back_byte_for_byte():
    live = _file_with_tail(
        "- (Cycle 10) — older", "- (Cycle 12) — newest", "- (Cycle 8) — oldest"
    )
    out = normalise(live, mode="strays")
    assert out != live
    assert out.split("\n## Retired\n", 1)[1] == live.split("\n## Retired\n", 1)[1]


def test_a_stray_moves_to_where_its_own_cycle_belongs():
    live = _file_with_tail(
        "- (Cycle 20) — a", "- (Cycle 10) — b", "- (Cycle 15) — c", "- (Cycle 5) — d"
    )
    assert [e[:12] for e in _section(normalise(live, mode="strays"))] == [
        "- (Cycle 20)",
        "- (Cycle 15)",
        "- (Cycle 10)",
        "- (Cycle 5) ",
    ]


def test_a_block_of_same_cycle_strays_keeps_its_own_order():
    live = _file_with_tail(
        "- (Cycle 20) — a",
        "- (Cycle 5) — b",
        "- (Cycle 12) — first of the block",
        "- (Cycle 12) — second of the block",
    )
    assert [e for e in _section(normalise(live, mode="strays"))] == [
        "- (Cycle 20) — a",
        "- (Cycle 12) — first of the block",
        "- (Cycle 12) — second of the block",
        "- (Cycle 5) — b",
    ]


def test_an_unmarked_entry_is_never_moved_by_the_stray_repair():
    """It has no key of its own, so relocating it would be a guess."""
    live = _file_with_tail(
        "- (Cycle 20) — a", "- undated, and it stays put", "- (Cycle 30) — the stray"
    )
    out = _section(normalise(live, mode="strays"))
    assert out.index("- undated, and it stays put") == 2
    assert out[0] == "- (Cycle 30) — the stray"


def test_the_stray_repair_is_idempotent():
    live = _file_with_tail("- (Cycle 10) — a", "- (Cycle 30) — b", "- (Cycle 5) — c")
    once = normalise(live, mode="strays")
    assert normalise(once, mode="strays") == once


def test_the_stray_repair_refuses_to_lose_a_capture():
    live = _file_with_tail("- (Cycle 10) — a", "- (Cycle 30) — b")
    before = _section(live)
    after = _section(normalise(live, mode="strays"))
    assert sorted(before) == sorted(after)
    check_newest_first(after)


def test_merge_mode_also_stops_at_the_section_boundary():
    """The wide read was `normalise`'s, not one mode's."""
    live = _file_with_tail("- (Cycle 10) — a", "- (Cycle 30) — b")
    out = normalise(live, mode="merge")
    assert out.split("\n## Retired\n", 1)[1] == live.split("\n## Retired\n", 1)[1]


def test_an_unmarked_entry_at_the_top_does_not_become_the_floor():
    """The first *marker* sets the descending run, not the first entry.

    An entry with no marker has no cycle, and treating it as one -- as
    `-1`, say -- makes every real capture below it larger than the floor
    and therefore a stray, so a file that is already in order gets its
    whole list lifted above its own oldest entry.
    """
    live = _file_with_tail(
        "- undated, and it stays at the top",
        "- (Cycle 10) — a",
        "- (Cycle 30) — the one real stray",
        "- (Cycle 5) — b",
    )
    # The undated entry inherits Cycle 10 from the marker below it, so a
    # Cycle 30 stray lands above it. What it must not do is fall to the
    # bottom, which is where it goes the moment "no marker" is read as a
    # cycle of its own.
    assert _section(normalise(live, mode="strays")) == [
        "- (Cycle 30) — the one real stray",
        "- undated, and it stays at the top",
        "- (Cycle 10) — a",
        "- (Cycle 5) — b",
    ]
