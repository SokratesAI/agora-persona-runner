"""The cost-ledger -> cycle-number join (issues.md #59).

The join is on time, which is the kind of key that returns a plausible
wrong answer instead of an error, so most of what is tested here is the
refusal rather than the match.
"""

import json

import pytest

from agora_runner.nova_runtimes import attach_runtimes, cycle_runtimes


def ledger(*sessions):
    """`("2026-08-15T00:00:55Z", 940.2)` pairs -> raw ledger text.

    UTC with a `Z`, exactly as `publish_costs` in the bridge writes it,
    because converting that to the Oslo stamps the headings carry is the
    thing most likely to be wrong and a fixture in local time would hide
    it.
    """
    return json.dumps(
        {"cycles": [{"startedAt": at, "durationSeconds": secs} for at, secs in sessions]}
    )


def entry(cycle, date, time):
    return {"cycle": cycle, "date": date, "time": time}


def test_an_entry_is_joined_to_the_session_it_was_written_during():
    # 00:00:55Z is 02:00:55 Oslo; the entry is stamped 14 minutes in.
    document = ledger(("2026-08-15T00:00:55Z", 940.2))
    assert cycle_runtimes(document, [entry(204, "2026-08-15", "02:14")]) == {204: 940.2}


def test_the_stamp_is_read_as_oslo_not_as_utc():
    """The whole join rests on this and nothing else would catch it.

    If the heading stamp were read as UTC, 02:14 would land two hours
    *after* a session that started at 02:00 Oslo -- past `MAX_LAG_SECONDS`
    on this fixture -- and the answer would be no runtime rather than a
    wrong one. So the assertion is that the correct session is found, and
    the second session exists to make the wrong answer a *different*
    number rather than an absence.
    """
    document = ledger(
        ("2026-08-15T00:00:00Z", 600.0),   # 02:00 Oslo
        ("2026-08-15T02:00:00Z", 111.0),   # 04:00 Oslo -- what UTC-reading picks
    )
    assert cycle_runtimes(document, [entry(204, "2026-08-15", "02:14")]) == {204: 600.0}


def test_a_stamp_past_the_end_of_its_session_still_joins():
    """`endedAt` is published before the cycle's wrap-up finishes.

    Live: 136 of 235 entries fall inside `[startedAt, endedAt]` and 208
    of 209 stamped ones fall within an hour of a start. Matching on
    containment would drop cycle 200, whose session ran 22:00-22:25 and
    whose entry is stamped 22:29.
    """
    document = ledger(("2026-08-14T20:00:00Z", 1500.0))  # 22:00-22:25 Oslo
    assert cycle_runtimes(document, [entry(200, "2026-08-14", "22:29")]) == {200: 1500.0}


def test_two_cycles_writing_during_one_session_both_get_nothing():
    """The 11% error this module exists to refuse.

    Live, 18 of 160 matched sessions are claimed by two cycle numbers --
    23/24, 26/27, 28/29, 30/31, 64/65 and thirteen more. Printing a
    runtime for either puts one cycle's wall-clock on another's card and
    looks entirely correct on screen.
    """
    document = ledger(("2026-08-05T06:00:00Z", 900.0))  # 08:00 Oslo
    entries = [entry(24, "2026-08-05", "08:47"), entry(23, "2026-08-05", "08:52")]
    assert cycle_runtimes(document, entries) == {}


def test_a_neighbouring_unambiguous_session_survives_an_ambiguous_one():
    """The refusal is per session, not per ledger."""
    document = ledger(
        ("2026-08-05T06:00:00Z", 900.0),   # 08:00 Oslo -- two cycles claim it
        ("2026-08-05T07:00:00Z", 700.0),   # 09:00 Oslo -- one does
    )
    entries = [
        entry(24, "2026-08-05", "08:47"),
        entry(23, "2026-08-05", "08:52"),
        entry(25, "2026-08-05", "09:10"),
    ]
    assert cycle_runtimes(document, entries) == {25: 700.0}


def test_an_entry_stamped_more_than_an_hour_after_any_session_is_dropped():
    document = ledger(("2026-08-07T12:00:00Z", 600.0))  # 14:00 Oslo
    assert cycle_runtimes(document, [entry(38, "2026-08-07", "16:05")]) == {}


def test_an_entry_before_every_known_session_is_dropped():
    document = ledger(("2026-08-15T00:00:00Z", 600.0))
    assert cycle_runtimes(document, [entry(1, "2026-08-01", "09:00")]) == {}


@pytest.mark.parametrize(
    "bad",
    [
        {"cycle": None, "date": "2026-08-15", "time": "02:14"},   # a report card
        {"cycle": 204, "date": "", "time": "02:14"},              # 26 live entries
        {"cycle": 204, "date": "2026-08-15", "time": ""},
        {"cycle": 204, "date": "not-a-date", "time": "02:14"},
    ],
)
def test_an_entry_with_no_usable_stamp_is_skipped_not_raised(bad):
    document = ledger(("2026-08-15T00:00:55Z", 940.2))
    assert cycle_runtimes(document, [bad]) == {}


def test_a_cycle_with_two_entries_keeps_its_earliest_session():
    """The addendum case: one cycle, two documents, hours apart.

    The runtime a card shows is the run the cycle *started* in, so a
    second entry written later must not overwrite it with a later
    session's duration.
    """
    document = ledger(
        ("2026-08-14T15:00:00Z", 1080.0),  # 17:00 Oslo -- cycle 196's own run
        ("2026-08-14T16:00:00Z", 240.0),   # 18:00 Oslo
    )
    entries = [entry(196, "2026-08-14", "17:12"), entry(196, "2026-08-14", "18:03")]
    assert cycle_runtimes(document, entries) == {196: 1080.0}


def test_an_absent_ledger_is_no_runtimes_and_not_an_error():
    """Distinct from unparseable, exactly as `nova_costs` treats it."""
    assert cycle_runtimes("", [entry(204, "2026-08-15", "02:14")]) == {}
    assert cycle_runtimes("   ", [entry(204, "2026-08-15", "02:14")]) == {}


def test_an_unparseable_ledger_raises_rather_than_reading_as_empty():
    with pytest.raises(json.JSONDecodeError):
        cycle_runtimes("{not json", [entry(204, "2026-08-15", "02:14")])


def test_a_session_missing_its_duration_or_start_is_dropped_not_crashed():
    document = json.dumps(
        {
            "cycles": [
                {"startedAt": "2026-08-15T00:00:00Z"},                    # no duration
                {"durationSeconds": 500.0},                               # no start
                {"startedAt": "nonsense", "durationSeconds": 500.0},       # unparseable
            ]
        }
    )
    assert cycle_runtimes(document, [entry(204, "2026-08-15", "02:14")]) == {}


def test_a_session_with_no_duration_does_not_donate_its_hour_to_the_one_before():
    """The wrong-number path, which is the only kind that matters here.

    Dropping an unusable session removes it from the *boundary* list, not
    from the timeline -- so an entry written during it falls through to the
    previous session and gets that run's wall-clock on its card, which is
    indistinguishable from a correct answer. Keeping the row with `None`
    turns that into a blank.

    **The sessions here are 40 minutes apart, and that is load-bearing.**
    Written first with hourly ones, this test passed under its own
    mutation: falling back an hour puts the entry more than
    `MAX_LAG_SECONDS` after the earlier start, so that guard refused it
    and both rules answered `{}` for different reasons. The heartbeat has
    actually run at 40 minutes (the owner's `notes.md`, 2026-08-12), which is
    exactly the spacing where the fallback lands *inside* the window and
    the wrong answer becomes reachable.

    Cycle 205 is stamped 02:50, inside the 02:40 session. Dropping that row
    hands it to the 02:00 one, 50 minutes back and within the guard, and
    prints its 940s.
    """
    document = json.dumps(
        {
            "cycles": [
                {"startedAt": "2026-08-15T00:00:00Z", "durationSeconds": 940.0},  # 02:00 Oslo
                {"startedAt": "2026-08-15T00:40:00Z"},                            # 02:40 Oslo
            ]
        }
    )
    assert cycle_runtimes(document, [entry(205, "2026-08-15", "02:50")]) == {}


def test_the_boundary_still_works_when_the_gap_session_is_usable():
    """The positive control for the test above.

    Without it, "no runtime" passes because the join broke, not because
    the duration was missing. Same two sessions, the second one whole --
    and the answer must be the *second* session's 611, never the first's
    940, which is the wrong number the test above is guarding against.
    """
    document = json.dumps(
        {
            "cycles": [
                {"startedAt": "2026-08-15T00:00:00Z", "durationSeconds": 940.0},
                {"startedAt": "2026-08-15T00:40:00Z", "durationSeconds": 611.0},
            ]
        }
    )
    assert cycle_runtimes(document, [entry(205, "2026-08-15", "02:50")]) == {205: 611.0}


def test_attach_writes_the_field_on_every_part_of_a_resolved_cycle():
    """The client reads `runtimeSeconds` off the *earliest* part.

    `renderCard` picks `ordered[0]` for the meta row, so a runtime written
    only onto the newest entry of a two-part cycle would be invisible on
    exactly the cycles that wrote twice.
    """
    document = ledger(("2026-08-14T15:00:00Z", 1080.0))
    entries = [entry(196, "2026-08-14", "17:12"), entry(196, "2026-08-14", "17:40")]
    attach_runtimes(entries, document)
    assert [e["runtimeSeconds"] for e in entries] == [1080, 1080]


def test_attach_leaves_the_key_off_entirely_when_there_is_no_answer():
    """Absent rather than `None`, so the client has one falsy case."""
    entries = [entry(23, "2026-08-05", "08:52"), entry(24, "2026-08-05", "08:47")]
    attach_runtimes(entries, ledger(("2026-08-05T06:00:00Z", 900.0)))
    assert all("runtimeSeconds" not in e for e in entries)
