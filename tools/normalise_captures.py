"""Put Nova's capture files back into one order, so they can be rolled.

`roll_captures.py` refuses `nova/resources/issues.md` and `ideas.md`
because they are not newest-first. The remedy filed alongside that
refusal -- in Cycle 112's journal entry and in the capture it left in
`nova/resources/issues.md`, not in `roll_captures.py` itself, which
names no remedy at all -- was "reversing the prepend-era region in place
is the shape that works". **It is not, and the measurement that says so
is below.** The two regions do not sit one after the other in time; they
overlap almost completely, so no reversal of either one makes the file
newest-first.

**What is actually in the file.** Measured against the live
`nova/resources/issues.md` on 2026-08-11, 324 entries, 89 of them
carrying a `(Cycle N)` marker:

- entries 0-113 run **newest-first** -- C112, C112, C103, C63 ... C54,
  then 85 entries too old to carry a marker at all;
- entries 114-323 run **oldest-first** -- C24, C26, C28 ... C110, C111.

Both runs reach today. Cycle 112 is at the top *and* Cycle 111 is at the
bottom. So this is not one convention that changed on a date; it is two
conventions running side by side, and the mechanism is
`vault_tool.py append`: given the `## Entries` marker it inserts
**under the marker**, at the top of the section, and without it appends
at the end of the file. Every cycle picked one, and the file has been
accumulating in both directions ever since.

**So the normalisation is a merge, not a reverse.** Each stream is
already correctly ordered within itself -- the top one descending, the
bottom one ascending -- which is the one thing a two-stream file gives
you for free. Reverse the bottom stream and merge the two, newest-first,
keeping each stream's internal order exactly. Nothing is sorted: an
entry's position comes from the stream it was written into and from the
markers around it, never from a comparison between two entries that both
lack one.

**Where an entry has no marker of its own it inherits the next marker
*below* it in its own stream**, i.e. the oldest cycle it can be. That
direction is the safe one: filling from above would date the 85 undated
entries at the bottom of the top stream as Cycle 54 and lift them over
half the file, when in fact they are the oldest captures here and have
no marker precisely because they predate the convention. Filling from
below leaves them where they belong, at the end.

    python3 -m tools.normalise_captures --live issues.md --dry-run
    python3 -m tools.normalise_captures --live issues.md

**There is a second shape and a second mode, and the file grew into it.**
Measured 2026-09-01, inside the `## Entries` section: `issues.md` is 922
entries with 874 markers and **three ascents**, `ideas.md` 310 with 274
and **none at all**. That is not two streams -- `split_streams` refuses
it, correctly -- it is a file that is almost entirely newest-first with a
few markers in the wrong place, which is what the merge era left behind
once most cycles started passing the marker.
`--mode strays` moves only those blocks and never touches an unmarked
entry. Both modes go through the same `verify`, so neither can lose a
capture or return something `check_newest_first` would still refuse.

    python3 -m tools.normalise_captures --live issues.md --mode strays

It is a one-time repair, not a scheduled job -- once both streams are
one, `prompt.md` step 6's append keeps it that way as long as every
cycle passes the marker. It is idempotent, but by refusing rather than
by symmetry: `normalise` returns a file that already passes
`check_newest_first` untouched, because running the merge on its own
output would *not* leave it alone. See the guard there.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.md_sections import split_at_heading
from tools.rolling import RollError, join_bullets, split_bullets
from agora_runner.rolling import _body
from tools.roll_captures import MARKER, _CYCLE_RE, check_newest_first, spec_for


def _cycle(entry):
    match = _CYCLE_RE.match(entry)
    return int(match.group(1)) if match else None


def split_streams(entries):
    """`(newest_first, oldest_first)` -- the two ways the file was written.

    The boundary is any index where everything above is non-ascending by
    cycle marker and everything below is non-descending. That is a window
    rather than a point, because only about a third of entries carry a
    marker at all and the ones either side of the break carry none.
    **The largest valid cut wins**, and this is the one decision in the
    module that a reviewer caught me getting backwards, on the live file,
    after I had already merged it.

    The argument for the smallest cut was that it pushes the ambiguous
    unmarked entries into the head of the bottom stream, which reverses
    to its tail, which is where something too old to carry a marker
    belongs. That is true for *one* such entry and false for eighty-five.
    The live `issues.md` has 85 consecutive unmarked entries sitting
    directly under the top stream's last marker, and the smallest cut
    sweeps every one of them into `bottom` -- where `normalise`'s
    `bottom[::-1]` then reverses their internal order. Measured: they
    came out at positions 323, 322, 321 ... exactly backwards. Nothing
    caught it, because `check_newest_first` and `verify` both only see
    entries that carry a marker, so an unmarked run can be scrambled
    silently while every guard passes.

    The largest cut keeps that run in `top`, the stream it was actually
    written into, and `top` is never reversed. What it costs is that the
    bottom stream's oldest marked entry can be absorbed into `top` --
    harmless, since `keys` then dates the run above it as that cycle and
    the merge places the whole block by its own key, in order.

    Post-fix, measured on both live files: `issues.md` cuts at 118 and
    `ideas.md` at 92, the top stream's order is preserved exactly and the
    bottom stream is exactly reversed. That pair of assertions is the
    invariant this function owes `merge`, and it is now a test.

    On an already-newest-first file the cut lands at the end -- the whole
    file is one descending stream and the second is empty -- so nothing
    is reversed at all. `normalise` returns such a file untouched anyway,
    before this is ever called.
    """

    marks = [(i, c) for i, e in enumerate(entries) if (c := _cycle(e)) is not None]
    for cut in range(len(entries), -1, -1):
        if _non_ascending([c for i, c in marks if i < cut]) and _non_ascending(
            [c for i, c in marks if i >= cut][::-1]
        ):
            return entries[:cut], entries[cut:]
    raise RollError(
        "refusing to normalise: no split point makes the top of this file "
        "descend and the bottom ascend, so it is not two streams and this "
        "tool cannot say what order it is in -- if it is mostly newest-first "
        "with a few blocks out of place, that is --mode strays"
    )


def _non_ascending(cycles):
    return all(b <= a for a, b in zip(cycles, cycles[1:]))


def keys(newest_first):
    """A cycle number per entry, filled from the next marker *below* it.

    Ties are the norm, not the exception -- a cycle writes two to five
    captures in one go and they all carry the same marker -- so the merge
    below has to be stable, and this deliberately does not invent a
    tiebreak. An entry with no marker anywhere below it gets `-1`, which
    sorts under every real cycle: that is the top stream's undated tail,
    and it is genuinely the oldest thing in the file.
    """
    filled = []
    current = -1
    for entry in reversed(newest_first):
        cycle = _cycle(entry)
        if cycle is not None:
            current = cycle
        filled.append(current)
    return filled[::-1]


def merge(top, bottom):
    """Two newest-first streams -> one, stable, neither reordered inside.

    A plain merge of two sorted lists. On a tie the top stream goes
    first, which only decides which of two same-cycle captures written
    through different mechanisms comes first -- and there is no fact
    anywhere in the file that would decide it differently.
    """
    top_keys, bottom_keys = keys(top), keys(bottom)
    out = []
    i = j = 0
    while i < len(top) and j < len(bottom):
        if top_keys[i] >= bottom_keys[j]:
            out.append(top[i])
            i += 1
        else:
            out.append(bottom[j])
            j += 1
    return out + top[i:] + bottom[j:]


def strays(entries):
    """Indices of the markered entries that break the descending run.

    Greedy from the top: the first marker sets the run, and any later
    marker larger than the running minimum is a stray. `split_streams`
    above cannot help here because these files are no longer two streams
    -- measured 2026-09-01, `issues.md`'s Entries section has 922 entries
    with 874 markers and three ascents, and no cut point makes the top
    descend and the bottom ascend, so `split_streams` refuses. That
    refusal is correct and the shape it describes is real: the file is
    almost entirely newest-first with a handful of markers in the wrong
    place, which is a different repair from a merge.

    **Count the ascents inside the section or the number is a different
    number.** The unbounded read this module used to do saw eleven, nine
    of them retired items and board rows; that is the bug `normalise`
    fixes below and the reason the count here says which read it came
    from.

    Greedy is deliberate over a longest-non-increasing-subsequence. LNIS
    would minimise the number of entries that move, and on this file it
    would decide the undated tail at the bottom of the file is the run to
    keep and lift the newest captures around it. The run that matters is the one that
    starts at the top of the file, because that is the end
    `roll_captures.plan` keeps.
    """
    out = []
    floor = None
    for index, entry in enumerate(entries):
        cycle = _cycle(entry)
        if cycle is None:
            continue
        if floor is not None and cycle > floor:
            out.append(index)
        else:
            floor = cycle
    return out


def repair_strays(entries):
    """Move only the stray markers, each to where its own cycle belongs.

    Unmarked entries never move. They have no key of their own -- `keys`
    dates them from the next marker *below* -- so relocating one is a
    guess, and the ones in these files sit in the undated tail that gets
    archived whichever marker ends up under them.

    A stray is inserted at the first position whose filled key is lower
    than its own, which is the same placement rule `merge` uses, so a
    block of same-cycle strays keeps its internal order: after the first
    lands, that position's key is the block's own cycle and the next one
    goes below it.
    """
    stray = set(strays(entries))
    if not stray:
        return list(entries)
    out = [e for i, e in enumerate(entries) if i not in stray]
    for index in sorted(stray):
        entry = entries[index]
        cycle = _cycle(entry)
        filled = keys(out)
        at = next((i for i, k in enumerate(filled) if k < cycle), len(out))
        out.insert(at, entry)
    return out


def normalise(live, mode="merge"):
    """The whole file, entries reordered, everything else untouched."""
    if split_at_heading(live, MARKER) is None:
        raise RollError(
            f"refusing to normalise: no {MARKER.strip()!r} section in this file"
        )
    # `_body` and not `split_at_heading`, and this is the whole reason this
    # tool disagreed with the roller it exists to unblock. The two-piece
    # split returns *everything* below the marker, and both capture files
    # carry `## Retired`, `## Board` and `# Details` under it -- 107 of the
    # 1,029 things `split_bullets` then called entries in `issues.md`, and
    # 19 of 329 in `ideas.md`. So `check_newest_first` here saw eleven
    # ascents where the engine, which has bounded the section at the next
    # heading since `_body` was written, sees exactly one; a repair built
    # on the wide read moves retired items back into the live list and
    # rewrites the board table. `roll_captures` was never affected.
    head, body, tail = _body(live, spec_for(live))
    entries = split_bullets(body)
    try:
        check_newest_first(entries)
    except RollError:
        pass
    else:
        # Already the thing this tool exists to produce, so stop -- and
        # this is a correctness guard, not a shortcut. Without it a second
        # run is *not* a no-op: the normalised file's tail is the entries
        # too old to carry a marker, `split_streams` cuts above them
        # because a single trailing marker is trivially non-descending,
        # and reversing that stream shuffles them. Refusing to touch a
        # file that already passes is what makes this idempotent.
        return live
    if mode == "strays":
        merged = repair_strays(entries)
    else:
        top, bottom = split_streams(entries)
        merged = merge(top, bottom[::-1])
    verify(entries, merged)
    return head + "\n" + join_bullets(merged) + "\n" + tail


def verify(before, after):
    """Every entry survives, exactly once, and the result is newest-first.

    The first half is the one that matters: this reorders a file that is
    the only copy of 525 captures, so "same multiset" is checked against
    the entries themselves rather than against a count. The second half
    is the point of the exercise -- if the output would still make
    `roll_captures.check_newest_first` refuse, the reorder achieved
    nothing and writing it would be a reformat for free.
    """
    if sorted(before) != sorted(after):
        lost = sorted(set(before) - set(after))
        gained = sorted(set(after) - set(before))
        raise RollError(
            "refusing to normalise: the reorder is not entry-preserving -- "
            f"{len(before)} in, {len(after)} out, {len(lost)} lost, "
            f"{len(gained)} invented; first lost: {lost[:1]}"
        )
    check_newest_first(after)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--mode",
        choices=("merge", "strays"),
        default="merge",
        help="merge: two whole streams back into one (the 2026-08-11 shape). "
        "strays: move only the markers that break the descending run.",
    )
    args = parser.parse_args(argv)

    with open(args.live, encoding="utf-8") as handle:
        live = handle.read()
    try:
        out = normalise(live, mode=args.mode)
    except RollError as error:
        print(error, file=sys.stderr)
        return 1
    if out == live:
        print("already newest-first, nothing to normalise")
        return 0
    before = split_bullets(_body(live, spec_for(live))[1])
    after = split_bullets(_body(out, spec_for(out))[1])
    if args.mode == "strays":
        # A positional diff counts every entry below the first insertion
        # as moved -- 886 of 1,029 for a repair that relocates 25 -- so
        # count what the repair actually picked up instead.
        moved = len(strays(before))
    else:
        moved = sum(1 for a, b in zip(before, after) if a != b)
    print(f"{args.live}: {moved} entries move, {len(live)} -> {len(out)} bytes")
    if args.dry_run:
        return 0
    with open(args.live, "w", encoding="utf-8") as handle:
        handle.write(out)
    print(f"wrote {args.live}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
