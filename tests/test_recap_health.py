"""The preflight check that raises when the Journal page's recap card is stale.

Two things are worth stating about how these are written. The exit code is
the whole product of this module, so every test asserts on it rather than on
the prose beside it. And the freshness threshold is never re-spelled here:
`STALE_AFTER_HOURS` is imported from `nova_recap`, so a test that hard-coded
`4` would keep passing if the card's own definition of stale moved, which is
exactly the drift this check exists to be free of.
"""

import datetime

import pytest

from agora_runner.nova_recap import STALE_AFTER_HOURS
from tools import recap_health


OSLO = datetime.timezone(datetime.timedelta(hours=2))


def _doc(stamp="2026-09-04T11:00+02:00", cycles="871-901", bullets=2):
    lines = [
        "---",
        "type: log",
        "---",
        "",
        "# Last 12 hours",
        "",
        f"<!-- generated: {stamp} | cycles {cycles} -->" if stamp
        else "<!-- generated: | cycles -->",
        "",
    ]
    lines += [f"- **Bullet {n}.** Something happened." for n in range(bullets)]
    return "\n".join(lines) + "\n"


def _parse(doc, hours_later):
    now = datetime.datetime(2026, 9, 4, 11, 0, tzinfo=OSLO) + datetime.timedelta(hours=hours_later)
    from agora_runner.nova_recap import parse_recap

    return parse_recap(doc, now=now)


def _run(doc, hours_later, since=None):
    printed = []
    code = recap_health.report(_parse(doc, hours_later), since, out=printed.append)
    return code, "\n".join(printed)


def test_a_fresh_card_passes():
    code, out = _run(_doc(), hours_later=STALE_AFTER_HOURS - 0.5)
    assert code == 0
    assert "CURRENT" in out


def test_a_card_past_the_threshold_raises():
    code, out = _run(_doc(), hours_later=STALE_AFTER_HOURS + 0.5)
    assert code == 2
    assert "STALE" in out
    assert "tools.recap --put" in out


def test_the_threshold_is_the_cards_own_not_a_second_one():
    """The precondition the test above depends on: the two ages either side
    of the threshold really are either side of the *card's* threshold, so
    moving `STALE_AFTER_HOURS` moves both of these together."""
    assert _parse(_doc(), STALE_AFTER_HOURS - 0.5)["stale"] is False
    assert _parse(_doc(), STALE_AFTER_HOURS + 0.5)["stale"] is True


def test_a_card_with_no_readable_stamp_raises():
    code, out = _run(_doc(stamp="not-a-date"), hours_later=0)
    assert code == 2
    assert "no readable" in out


def test_a_fresh_card_still_says_how_many_cycles_have_filed_since():
    code, out = _run(_doc(), hours_later=0.1, since=3)
    assert code == 0
    assert "3 cycle(s)" in out


def test_a_stale_card_names_the_cycles_since():
    code, out = _run(_doc(), hours_later=STALE_AFTER_HOURS + 1, since=7)
    assert code == 2
    assert "7 cycle(s)" in out


def test_an_unreadable_recap_is_exit_1_not_a_clean_card(monkeypatch):
    monkeypatch.setattr(recap_health, "read_recap", lambda: None)
    assert recap_health.main([]) == 1


def test_cycles_since_counts_only_entries_past_the_covered_range():
    filed = [899, 900, 901, 902, 903]
    assert recap_health.cycles_since("871-901", filed) == 2
    assert recap_health.cycles_since("903", filed) == 0


def test_an_unstated_range_is_unknown_not_zero():
    """0 would read as 'nothing has happened since', which is a claim this
    cannot make from a recap that never said what it covers."""
    assert recap_health.cycles_since("", [900, 901]) is None
    assert recap_health.cycles_since(None, [900, 901]) is None


def test_entry_cycles_reads_the_numbers_out_of_a_listing():
    listing = "\n".join([
        "projects/sokrates/projects/agora/nova/journal/0900-cycle-899.md",
        "0901-cycle-900.md",
        "0902-cycle-901.md",
        "not-an-entry.txt",
    ])
    assert recap_health.entry_cycles(listing) == [899, 900, 901]


def test_a_weekly_entry_carries_no_cycle_number_and_is_not_counted():
    """Its heartbeat has its own counter starting at 1, so counting it would
    put a number in `cycles since` that no recap range can ever cover."""
    listing = "0902-monday-research.md\n0903-cycle-902.md\n"
    assert recap_health.entry_cycles(listing) == [902]


def test_the_check_is_in_preflight():
    from tools import preflight

    assert "recap_health" in preflight.CHECKS
    assert "recap_health" in preflight.SUBJECT


# --- the journal marker: staleness as a fact rather than a 3-hour guess ---
#
# His issue #219. When the card names the journal entry it was built from,
# that comparison decides the verdict outright and `STALE_AFTER_HOURS` is not
# consulted. These tests are written so the age and the marker DISAGREE --
# a test where both point the same way would pass on either implementation
# and prove nothing about which one ran.

def _stamped(journal, hours_old, cycles="900-901"):
    written = datetime.datetime.now(OSLO) - datetime.timedelta(hours=hours_old)
    return (
        "# Last 12 hours\n\n"
        f"<!-- generated: {written.isoformat(timespec='minutes')} | "
        f"cycles {cycles} | journal {journal} -->\n\n"
        "- **A thing** happened\n"
    )


def test_a_matching_marker_is_current_even_well_past_the_age_threshold(capsys):
    body = _stamped("42-cycle-901.md", hours_old=STALE_AFTER_HOURS + 5)
    code = recap_health.report(recap_health.parse_recap(body), since=0,
                               newest="42-cycle-901.md")
    assert code == 0
    assert "CURRENT" in capsys.readouterr().out


def test_a_stale_marker_is_behind_even_minutes_after_it_was_written(capsys):
    body = _stamped("42-cycle-901.md", hours_old=0.1)
    code = recap_health.report(recap_health.parse_recap(body), since=2,
                               newest="43-cycle-902.md")
    out = capsys.readouterr().out
    assert code == 2
    assert "BEHIND" in out
    assert "43-cycle-902.md" in out


def test_a_card_with_no_marker_falls_back_to_the_age_guess(capsys):
    # Every card written before 2026-09-13 is this one. It must not read as
    # behind just because it names no entry.
    body = (
        "# Last 12 hours\n\n"
        f"<!-- generated: {datetime.datetime.now(OSLO).isoformat(timespec='minutes')}"
        " | cycles 900-901 -->\n\n- **A thing** happened\n"
    )
    assert recap_health.report(recap_health.parse_recap(body), since=0,
                               newest="43-cycle-902.md") == 0
    assert "CURRENT" in capsys.readouterr().out


def test_an_unreadable_journal_listing_never_declares_a_card_behind(capsys):
    # `newest` is "" when the `ls` failed. Declaring the card behind an entry
    # nobody read is the negative-result-guaranteed-in-advance failure.
    body = _stamped("42-cycle-901.md", hours_old=0.1)
    assert recap_health.report(recap_health.parse_recap(body), since=None,
                               newest="") == 0
