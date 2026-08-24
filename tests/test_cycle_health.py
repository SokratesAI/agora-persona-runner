"""The self-check the owner asked for after spotting Cycle 134's missing hour."""

from datetime import datetime, timedelta

import pytest

from agora_runner.config import OSLO
from agora_runner.cycle_health import (
    describe,
    findings,
    gaps_since,
    heartbeat_findings,
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


def test_reading_nothing_is_not_a_healthy_loop():
    """The one that actually happened, 2026-08-12. Run from the bridge pod --
    where `prompt.md` sends cycles and where the handoff told the next cycle
    to run this -- the vault credentials are unset, the listing 401s, and
    `vault_bulk_fetch` returns an empty dict. Every field below then reads
    clean, so the check printed nothing and exited 0 while the live journal
    folder visibly skipped cycle 134."""
    report = findings([], {}, NOW)
    assert report["entries"] == 0
    assert report["missing"] == []
    assert report["stalled"] is False
    assert describe(report) != ""
    assert "cannot tell" in describe(report)


def test_a_blind_read_does_not_also_claim_zero_gaps():
    """Suppressed rather than appended: "0 gaps, and also I could not look"
    invites reading the first half. Handed the dict directly because
    `findings` cannot produce gaps and no entries at the same time -- the
    contract being pinned is `describe`'s, on a report that says it read
    nothing."""
    line = describe({"entries": 0, "missing": [131], "silent_intervals": 9, "stalled": True})
    assert "131" not in line
    assert "heartbeat intervals" not in line


def test_main_exits_nonzero_when_the_journal_listing_comes_back_empty(monkeypatch, capsys):
    """End to end, because the silent exit 0 is the whole failure: a cycle
    runs this, sees no output, and writes down that the loop is healthy."""
    import agora_runner.vault as vault
    from agora_runner.cycle_health import main

    monkeypatch.setattr(vault, "vault_bulk_fetch", lambda prefix, with_mtimes=False: ({}, {}))
    assert main() == 1
    assert "cannot tell" in capsys.readouterr().out


# --- Which gaps a heartbeat is told about ------------------------------
#
# `missing_cycles` is history and never shrinks. Put in front of every
# cycle it would recite the same six holes every hour forever, which is
# the failure `describe`'s empty-on-healthy contract exists to avoid.


def written(mapping):
    """`{cycle: when}` -> the `{path: mtime_ms}` the check actually takes."""
    return {f"projects/x/journal/{100 + n:03d}-cycle-{n}.md": ms(when)
            for n, when in mapping.items()}


def test_a_dead_cycle_is_announced_to_the_run_that_could_first_have_seen_it():
    """134 died. Nothing about it changed when it died -- it left no
    document -- so the hole only became observable when 135 wrote the entry
    that brackets it, and that write is the event to key on."""
    mtimes = written({133: NOW - timedelta(hours=3), 135: NOW - timedelta(minutes=35)})
    since = NOW - timedelta(hours=1)
    assert gaps_since(list(mtimes), mtimes, since) == [134]


def test_the_same_dead_cycle_is_not_announced_again_an_hour_later():
    """The property the whole filter exists for, and the one a plain
    `missing_cycles` fails: 135's entry is now older than this run's
    boundary, so the gap has already been shown to somebody."""
    mtimes = written({133: NOW - timedelta(hours=4), 135: NOW - timedelta(hours=2)})
    since = NOW - timedelta(hours=1)
    assert missing_cycles(list(mtimes)) == [134]
    assert gaps_since(list(mtimes), mtimes, since) == []


def test_no_previous_run_reports_every_gap_once():
    """First run after a deploy: there is no boundary, so nothing has been
    shown to anyone yet and silence would be a convenient lie."""
    mtimes = written({120: NOW - timedelta(days=9), 125: NOW - timedelta(days=8)})
    assert gaps_since(list(mtimes), mtimes, None) == [121, 122, 123, 124]


def test_an_older_gap_is_silent_while_a_fresh_one_speaks():
    """Both are real holes; only one is news. Reporting both is how the
    line becomes permanent furniture."""
    mtimes = written({
        126: NOW - timedelta(days=1), 129: NOW - timedelta(days=1),
        133: NOW - timedelta(hours=3), 135: NOW - timedelta(minutes=35),
    })
    since = NOW - timedelta(hours=1)
    assert missing_cycles(list(mtimes)) == [127, 128, 130, 131, 132, 134]
    assert gaps_since(list(mtimes), mtimes, since) == [134]


def test_a_gap_with_no_timed_entry_above_it_stays_quiet():
    """Nothing dates it, so any answer is a guess -- and guessing "new"
    re-reports old history on every single run."""
    mtimes = written({130: NOW - timedelta(hours=9)})
    paths_with_untimed_top = list(mtimes) + ["projects/x/journal/103-cycle-133.md"]
    assert missing_cycles(paths_with_untimed_top) == [131, 132]
    assert gaps_since(paths_with_untimed_top, mtimes, NOW - timedelta(hours=1)) == []


def test_an_addendum_does_not_postpone_the_cycle_that_wrote_it():
    """Cycle 135 wrote twice. The gap below it became visible at the first
    write, so bracketing on the later one would announce 134 an hour late
    -- to a run that has already swept the workspace."""
    mtimes = {
        "projects/x/journal/136-cycle-133.md": ms(NOW - timedelta(hours=4)),
        "projects/x/journal/137-cycle-135.md": ms(NOW - timedelta(hours=2)),
        "projects/x/journal/138-cycle-135.md": ms(NOW - timedelta(minutes=20)),
    }
    assert gaps_since(list(mtimes), mtimes, NOW - timedelta(hours=1)) == []


def test_the_heartbeat_report_keeps_the_stall_and_the_blindness_whole():
    """Only the gap list is filtered. Both other findings are already
    statements about right now, so there is nothing to age out of them --
    and a stall is exactly the multi-cycle outage the gap filter cannot
    see, because a hole needs a bracket and a stall has none."""
    mtimes = written({133: NOW - timedelta(minutes=200)})
    report = heartbeat_findings(list(mtimes), mtimes, NOW, NOW - timedelta(hours=1))
    assert report["stalled"] is True
    assert "heartbeat intervals" in describe(report)
    blind = heartbeat_findings([], {}, NOW, NOW - timedelta(hours=1))
    assert "cannot tell" in describe(blind)


def test_a_healthy_loop_still_says_nothing_to_a_heartbeat():
    mtimes = written({132: NOW - timedelta(minutes=80), 133: NOW - timedelta(minutes=20)})
    assert describe(heartbeat_findings(list(mtimes), mtimes, NOW,
                                       NOW - timedelta(hours=1))) == ""


def test_the_heartbeat_report_does_not_forget_to_filter():
    """`heartbeat_findings` differs from `findings` in exactly one field,
    and a version that forgot the substitution passes every other test
    here -- the stall, the blindness and the healthy case are all
    identical between the two."""
    mtimes = written({126: NOW - timedelta(days=1), 129: NOW - timedelta(hours=2),
                      130: NOW - timedelta(minutes=20)})
    report = heartbeat_findings(list(mtimes), mtimes, NOW, NOW - timedelta(hours=1))
    assert findings(list(mtimes), mtimes, NOW)["missing"] == [127, 128]
    assert report["missing"] == []
    assert describe(report) == ""
