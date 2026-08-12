"""The display half of Edvard's #72 -- a dead cycle you can see on screen.

> Mismatch between Nova and agora cycles. Nova is 1 behind agora. Agora
> failed a cycle Journal and you did not catch it. You do not have good
> enough system set up to catch if the previous cycle failed or if any
> cycle in the past failed or is missing.

`cycle_health` answers this for Nova (it now runs at heartbeat dispatch,
Cycle 148). Nothing answered it for Edvard, who found cycles 127 and 128
himself by noticing the feed jump from 126 to 129.

The two halves are tested apart because they fail apart: the holes are
history and pure, the stall is a judgement about right now and is the one
that a cache can freeze.
"""

from datetime import datetime, timedelta

from agora_runner.config import OSLO
from agora_runner.cycle_health import (
    STALL_GRACE_INTERVALS,
    gaps_between,
    missing_cycles,
)
from agora_runner.nova_journal import build_status, parse_journal
from agora_runner.nova_site import _with_silence, journal_page

NOW = datetime(2026, 8, 12, 23, 0, tzinfo=OSLO)


def _journal(*cycles):
    """A journal document holding one entry per cycle number given.

    Every entry carries the same stamp as `NOW`, so the silence under test
    is entirely the offset each test passes and not an artefact of how far
    apart the fixture's own entries are.
    """
    return "\n".join(
        f"### 2026-08-12 23:00 (Oslo) — Cycle {n}\n\nBody {n}.\n\n"
        f"---\nPR: none | Outcome: merged\n"
        for n in cycles
    )


def _entries(*cycles):
    return parse_journal(_journal(*cycles))


# --- the holes, which are history -----------------------------------------


def test_the_status_names_the_cycles_that_wrote_no_entry():
    status = build_status(_entries(129, 126, 125))
    assert status["missingCycles"] == [127, 128]


def test_a_journal_with_no_holes_reports_none():
    assert build_status(_entries(129, 128, 127))["missingCycles"] == []


def test_an_entry_with_no_cycle_number_is_not_a_hole():
    """Edvard's own notes carry no `Cycle N`, so they cannot be missing.

    They are real entries and they sit in the feed between numbered ones.
    Counting them into the range would invent a gap out of a note.
    """
    markdown = (
        "### 2026-08-12 09:00 (Oslo) — Cycle 129\n\nBody.\n\n"
        "---\nPR: none | Outcome: merged\n"
        "### 2026-08-12 08:00 (Oslo) — a note from Edvard\n\nBody.\n\n"
        "### 2026-08-12 07:00 (Oslo) — Cycle 128\n\nBody.\n\n"
        "---\nPR: none | Outcome: merged\n"
    )
    assert build_status(parse_journal(markdown))["missingCycles"] == []


def test_the_page_and_the_self_check_cannot_disagree_about_a_hole():
    """One definition of "missing", read by both callers.

    The point of `gaps_between` existing: `cycle_health` answers this for
    Nova off filenames and the site answers it for Edvard off parsed
    headings, and a second implementation is the hand-synced pair this
    repo keeps finding drifted. If these two ever disagree, one of the two
    readers is lying to somebody.
    """
    paths = [f"{n:03d}-cycle-{n}.md" for n in (125, 126, 129)]
    assert missing_cycles(paths) == build_status(
        _entries(129, 126, 125))["missingCycles"] == [127, 128]


def test_gaps_between_needs_two_entries_to_bracket_anything():
    # Below the lowest and above the highest there is no evidence a cycle
    # ever ran -- that end is the stall's question, answered with a clock.
    assert gaps_between([]) == []
    assert gaps_between([7]) == []


# --- the stall, which is a judgement about right now -----------------------


def test_a_cycle_still_running_is_not_reported_as_dead():
    """The ambiguity #72 is actually about.

    An entry is written at the *end* of a cycle, so for the 20-30 minutes
    one is running, agora has started cycle N and this page can only see
    N-1. That is indistinguishable from cycle N having died, and calling
    it dead would raise a false alarm every single hour.

    Ninety minutes rather than forty-five on purpose: the heartbeat is
    hourly and a cycle writes at the end of its hour, so the healthy gap
    between two entries routinely exceeds one interval. Under an hour the
    silence is zero intervals and *any* threshold passes this -- the test
    would be pinning nothing.
    """
    status = _with_silence(
        build_status(_entries(129, 128)), now=NOW + timedelta(minutes=90))
    assert status["stalled"] is False
    assert status["silentIntervals"] == 1


def test_a_loop_that_has_gone_quiet_says_so():
    status = _with_silence(
        build_status(_entries(129, 128)), now=NOW + timedelta(hours=3))
    assert status["stalled"] is True
    assert status["silentIntervals"] == 3


def test_the_grace_boundary_is_where_the_constant_says_it_is():
    entries = build_status(_entries(129, 128))
    hours = STALL_GRACE_INTERVALS
    assert _with_silence(
        entries, now=NOW + timedelta(hours=hours, minutes=-1))["stalled"] is False
    assert _with_silence(entries, now=NOW + timedelta(hours=hours))["stalled"] is True


def test_no_usable_stamp_is_not_reported_as_a_healthy_loop():
    """`None` and `0` are different answers and only one is reassurance."""
    status = _with_silence(build_status([]))
    assert status["silentIntervals"] is None
    assert status["stalled"] is False


def test_an_entry_stamped_in_the_future_is_not_a_negative_silence():
    status = _with_silence(
        build_status(_entries(129, 128)), now=NOW - timedelta(hours=5))
    assert status["silentIntervals"] == 0
    assert status["stalled"] is False


# --- the two halves meeting: the cache -------------------------------------


def test_the_stall_is_judged_per_request_and_not_frozen_into_the_cache():
    """The bug this design exists to avoid, pinned.

    `journal_payload` is cached and warmed at startup. Judge the stall in
    `build_status` and every request for the life of that process answers
    with the clock reading from the moment the payload was built -- so a
    process that warmed while the loop was healthy would keep saying
    "healthy" for exactly the hours it needed to say otherwise, and the
    feature would look like it worked in every test that built its own
    payload.

    So: one payload, built once, asked twice.
    """
    payload = {"entries": [dict(e) for e in _entries(129, 128)]}
    payload["status"] = build_status(parse_journal(_journal(129, 128)))

    fresh = journal_page(payload, now=NOW + timedelta(minutes=30))
    later = journal_page(payload, now=NOW + timedelta(hours=4))

    assert fresh["status"]["stalled"] is False
    assert later["status"]["stalled"] is True


def test_build_status_never_reads_the_clock():
    """The same guard from the other side, and the one that survives a rewrite.

    The test above pins the behaviour; this pins the *reason*, so a future
    cycle tidying the stall back into `build_status` (the obvious place for
    it) fails here rather than shipping a header frozen at whatever was
    true when the pod started.
    """
    status = build_status(_entries(129, 128))
    assert "stalled" not in status
    assert "silentIntervals" not in status
