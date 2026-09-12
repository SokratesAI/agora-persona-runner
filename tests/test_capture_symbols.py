"""The capture-staleness rule: a line naming a symbol newer than itself.

Built Cycle 1452 after three cycles in a row wrote the same warning into
`resources/issues.md` by hand. What these pin is the discrimination, not
the plumbing: the rule has to separate a symbol that postdates its note
from one that predates it, and it has to come back quiet on a note whose
symbols were all already there -- otherwise it is red on every line and
says nothing.
"""

import pytest

from agora_runner.capture_symbols import (
    ABSENT, BUILT_AFTER, PREDATES, line_verdict, render, symbols, verdict,
)


class TestSymbols:
    def test_keeps_camel_case_and_snake_case(self):
        text = "`draftVolume` and `parse_notes` and `PHASE_VOLUME`"
        assert symbols(text) == ["draftVolume", "parse_notes", "PHASE_VOLUME"]

    def test_drops_ordinary_backticked_words(self):
        # The journal backticks English constantly. A rule that kept these
        # would grep every repo for `main` and report the whole backlog.
        assert symbols("`main` and `note` and `plan` and `get`") == []

    def test_drops_a_path_and_a_command(self):
        assert symbols("`public/app.js` and `git log -S` and `tools.recap`") == []

    def test_keeps_order_and_deduplicates(self):
        assert symbols("`weekTarget` then `homeGoal` then `weekTarget`") == [
            "weekTarget", "homeGoal"]

    def test_no_backticks_at_all(self):
        assert symbols("the plan holds sets and reps and never a weight") == []
        assert symbols(None) == []


class TestVerdict:
    def test_a_symbol_committed_after_the_note_is_a_finding(self):
        assert verdict("2026-09-11", "2026-09-12") == BUILT_AFTER

    def test_a_symbol_that_predates_the_note_is_not(self):
        assert verdict("2026-09-11", "2026-09-04") == PREDATES

    def test_same_day_reads_as_predating(self):
        # A commit on the day the note was written could have landed either
        # side of it. Calling that a finding sends a cycle to read code over
        # a coin flip, so the tie goes the quiet way.
        assert verdict("2026-09-11", "2026-09-11") == PREDATES

    def test_nothing_carries_the_symbol(self):
        assert verdict("2026-09-11", "") == ABSENT
        assert verdict("2026-09-11", None) == ABSENT

    def test_an_undated_note_cannot_be_judged(self):
        assert verdict("", "2026-09-12") == PREDATES

    def test_absent_wins_over_an_undated_note(self):
        # ABSENT is a fact about the repos and does not need the note's date,
        # so it must survive the undated short-circuit rather than being
        # swallowed by it.
        assert verdict("", "") == ABSENT


class TestLineVerdict:
    def test_one_new_symbol_among_old_ones_still_raises(self):
        assert line_verdict([PREDATES, PREDATES, BUILT_AFTER]) == BUILT_AFTER

    def test_all_absent(self):
        assert line_verdict([ABSENT, ABSENT]) == ABSENT

    def test_a_mix_of_absent_and_old_is_quiet(self):
        assert line_verdict([ABSENT, PREDATES]) == PREDATES

    def test_no_symbols_has_no_verdict(self):
        assert line_verdict([]) is None


def _row(verdict_, name="draftVolume", first_seen="2026-09-12"):
    return {
        "date": "2026-09-11", "cycle": 1419, "text": "the draft carries no weight",
        "verdict": verdict_,
        "symbols": [{"name": name, "verdict": verdict_,
                     "first_seen": first_seen, "where": "marcus"}],
    }


class TestRender:
    def test_a_finding_names_the_symbol_the_date_and_the_repo(self):
        out = render([_row(BUILT_AFTER)], scanned=8, skipped=3)
        assert "1 capture line(s)" in out
        assert "`draftVolume` first committed 2026-09-12 in marcus" in out
        assert "Cycle 1419" in out

    def test_a_quiet_run_says_so_and_still_prints_what_it_measured(self):
        # The counts are the measured negative. Without them a run with
        # nothing to say is indistinguishable from a run that judged nothing.
        out = render([_row(PREDATES), _row(ABSENT)], scanned=8, skipped=3)
        assert "No capture line names a symbol newer than itself." in out
        assert "Judged 2 line(s)" in out
        assert "1 predating" in out
        assert "1 still absent everywhere" in out

    def test_it_names_the_lines_it_could_not_judge(self):
        out = render([], scanned=8, skipped=24)
        assert "24 line(s) backtick nothing" in out

    def test_a_predating_symbol_is_never_printed_as_a_finding(self):
        out = render([_row(PREDATES)], scanned=8, skipped=0)
        assert "first committed" not in out
