"""The self-check Edvard asked for after spotting Cycle 134's missing hour."""

from datetime import datetime, timedelta

import pytest

from agora_runner.config import OSLO
from agora_runner.cycle_health import (
    describe,
    findings,
    missing_cycles,
    newest_entry_at,
    stalled_for,
)


def paths(*cycles):
    return [f"projects/x/journal/{100 + n:03d}-cycle-{n}.md" for n in cycles]


def ms(when):
    return int(when.timestamp() * 1000)


NOW = datetime(2026, 8, 12, 13, 0, tzinfo=OSLO)


def test_a_hole_in_the_run_of_cycle_numbers_is_a_cycle_that_wrote_nothing():
    """The real one: 127 and 128 were OOM-killed and the record jumped
    126 -> 129, which a human found by eye."""
    assert missing_cycles(paths(125, 126, 129, 130)) == [127, 128]


def test_a_complete_run_reports_nothing():
    assert missing_cycles(paths(131, 132, 133)) == []


def test_the_newest_number_is_not_reported_missing_by_the_number_check():
    """Cycle 134 died and nothing later exists to bracket it, so the
    sequence alone cannot see it -- that is `stalled_for`'s job, and the
    two must not be conflated or the last failure looks like the only
    healthy one."""
    assert missing_cycles(paths(132, 133)) == []


def test_gaps_come_back_ascending_so_the_last_one_is_the_newest_failure():
    assert missing_cycles(paths(120, 122, 127, 130))[-1] == 129


def test_a_single_entry_cannot_bracket_anything():
    assert missing_cycles(paths(133)) == []
    assert missing_cycles([]) == []


def test_a_file_that_is_not_an_entry_is_not_counted_as_a_cycle():
    """`file_cycle` returns None for it, and counting it would invent a
    bracket that would report every real cycle below it as missing."""
    assert missing_cycles(paths(131, 133) + ["projects/x/journal/README.md"]) == [132]


def test_the_newest_entry_is_the_highest_cycle_not_the_latest_write():
    """Two overlapping cycles write out of chronological order. The
    question is whether the *newest cycle* has a successor, so keying on
    the write time would judge the wrong entry."""
    older_cycle_written_later = {
        "j/148-cycle-133.md": ms(NOW - timedelta(hours=3)),
        "j/147-cycle-132.md": ms(NOW - timedelta(minutes=10)),
    }
    assert newest_entry_at(older_cycle_written_later) == NOW - timedelta(hours=3)


def test_silence_past_two_heartbeats_is_a_stall():
    mtimes = {"j/148-cycle-133.md": ms(NOW - timedelta(minutes=145))}
    assert stalled_for(mtimes, NOW) == 2
    assert findings(list(mtimes), mtimes, NOW)["stalled"] is True


def test_a_cycle_that_is_merely_mid_flight_is_not_reported_dead():
    """An entry is written at the end of a 20-30 minute cycle, so ~50
    minutes old is normal and one interval of grace is not enough."""
    mtimes = {"j/148-cycle-133.md": ms(NOW - timedelta(minutes=95))}
    assert stalled_for(mtimes, NOW) == 1
    assert findings(list(mtimes), mtimes, NOW)["stalled"] is False


def test_no_usable_time_is_none_and_not_zero():
    """`None` means nothing to judge; `0` means judged and healthy. A
    caller that flattened them would report a fresh loop as stalled."""
    assert stalled_for({}, NOW) is None
    assert stalled_for({"j/148-cycle-133.md": 0}, NOW) is None
    assert findings([], {}, NOW)["stalled"] is False


def test_an_entry_stamped_in_the_future_is_not_a_negative_stall():
    mtimes = {"j/148-cycle-133.md": ms(NOW + timedelta(hours=2))}
    assert stalled_for(mtimes, NOW) == 0


def test_the_interval_is_a_parameter_because_edvard_has_changed_it_twice():
    mtimes = {"j/148-cycle-133.md": ms(NOW - timedelta(minutes=145))}
    assert stalled_for(mtimes, NOW, minutes=72) == 2
    assert stalled_for(mtimes, NOW, minutes=360) == 0


def test_a_healthy_loop_describes_itself_as_nothing_at_all():
    """A check that always prints trains the reader to skip it."""
    mtimes = {p: ms(NOW - timedelta(minutes=20)) for p in paths(132, 133)}
    assert describe(findings(paths(132, 133), mtimes, NOW)) == ""


def test_describe_names_the_missing_cycles_and_the_stall_separately():
    mtimes = {p: ms(NOW - timedelta(minutes=200)) for p in paths(126, 129)}
    line = describe(findings(paths(126, 129), mtimes, NOW))
    assert "127, 128" in line
    assert "3 heartbeat intervals" in line


@pytest.mark.parametrize("cycles,expected", [((126, 129), [127, 128]), ((133,), [])])
def test_findings_reports_history_and_now_side_by_side(cycles, expected):
    report = findings(paths(*cycles), {}, NOW)
    assert report["missing"] == expected
    assert report["silent_intervals"] is None
