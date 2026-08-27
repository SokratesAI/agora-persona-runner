"""`tools.doc_owners` -- which site-rendered documents have a job that
refreshes them.

Everything here drives the pure half (`owners`, `window_days`, `verdict`,
`report`, `render`, `parse_recent`) with hand-built inputs. The vault half
is four `vault_tool.py` subprocesses and is exercised by running the tool,
not by mocking a subprocess into asserting its own arguments back.
"""

from datetime import datetime, timedelta

import pytest

from tools.doc_owners import (
    OSLO,
    age_days,
    check_registry,
    claim_age,
    declared_date,
    longest_gap_days,
    owners,
    parse_recent,
    render,
    report,
    stamp_explains,
    verdict,
    window_days,
)

NOW = datetime(2026, 8, 25, 4, 0, tzinfo=OSLO)

HOURLY = ("prompt.md", "the hourly cycle", (0, 1, 2, 3, 4, 5, 6))
MONDAY = ("weekly-reprioritise.md", "the goals & reprioritise run", (0,))

DOCS = (
    ("Roadmap", "projects/sokrates/projects/nova/roadmap.md", "/plan", "what next"),
    ("Digest", "projects/sokrates/projects/agora/journal-digest.md", "/", "what happened"),
)


def _texts(**kwargs):
    return dict(kwargs)


class TestCadence:
    def test_monday_only_is_a_seven_day_gap(self):
        assert longest_gap_days((0,)) == 7

    def test_mon_wed_fri_is_three_days_friday_to_monday(self):
        assert longest_gap_days((0, 2, 4)) == 3

    def test_every_day_is_one_day(self):
        assert longest_gap_days((0, 1, 2, 3, 4, 5, 6)) == 1

    def test_the_gap_wraps_the_week_rather_than_stopping_at_sunday(self):
        # Tue/Thu/Sat: the biggest gap is Saturday round to Tuesday, which
        # only exists if the week is treated as a cycle. Stopping at the
        # last listed day would give 2 and hide a third of the cadence.
        assert longest_gap_days((1, 3, 5)) == 3

    def test_a_prompt_with_no_firing_days_is_refused(self):
        with pytest.raises(ValueError):
            longest_gap_days(())

    def test_the_window_adds_one_day_of_grace(self):
        assert window_days((0,)) == 8
        assert window_days((0, 2, 4)) == 4
        assert window_days((0, 1, 2, 3, 4, 5, 6)) == 2


class TestOwners:
    def test_a_prompt_that_names_the_document_owns_it(self):
        found = owners(
            "projects/sokrates/projects/nova/roadmap.md",
            _texts(**{"weekly-reprioritise.md": "rewrite roadmap.md every Monday"}),
        )
        assert [f for f, _, _ in found] == ["weekly-reprioritise.md"]

    def test_a_document_no_prompt_names_has_no_owner(self):
        # The roadmap's own nine stale days: the word appeared in none of
        # the prompts, and nothing anywhere reported it.
        assert owners(
            "projects/sokrates/projects/nova/roadmap.md",
            _texts(**{"weekly-reprioritise.md": "re-rate every open board row"}),
        ) == []

    def test_a_prompt_that_could_not_be_read_owns_nothing(self):
        # Absent from `prompt_texts` entirely, rather than present-and-empty.
        assert owners("x/roadmap.md", {}) == []

    def test_the_full_path_is_not_required(self):
        assert owners("a/b/goals.md", _texts(**{"prompt.md": "see goals.md"})) == [
            ("prompt.md", "the hourly cycle", (0, 1, 2, 3, 4, 5, 6))
        ]


class TestVerdict:
    def test_fresh_inside_the_window(self):
        assert verdict(True, 3.0, [MONDAY]) == "fresh"

    def test_stale_past_the_window(self):
        assert verdict(True, 9.0, [MONDAY]) == "stale"

    def test_no_owner_beats_being_young(self):
        # The roadmap case. A document nobody refreshes is the finding
        # even on the day it was written, because nothing will stop it.
        assert verdict(True, 0.1, []) == "no owner"

    def test_an_unread_prompt_is_unknown_owner_not_no_owner(self):
        # The reviewer's finding, proved against the live registry: drop
        # `weekly-reprioritise.md` alone and both /plan documents printed
        # "nothing refreshes it and waiting will not help", which is false.
        # An owner that failed to load and an owner that does not exist are
        # opposite findings.
        assert verdict(True, 0.1, [], blind=True) == "unknown owner"
        assert verdict(True, 0.1, [], blind=False) == "no owner"

    def test_a_document_that_does_have_an_owner_is_unaffected_by_a_blind_read(self):
        assert verdict(True, 0.1, [MONDAY], blind=True) == "fresh"

    def test_missing_beats_no_owner(self):
        assert verdict(False, None, []) == "missing"

    def test_no_write_time_at_all_reads_as_stale_not_unknown(self):
        assert verdict(True, None, [MONDAY]) == "stale"

    def test_the_tightest_owner_decides(self):
        # 6 days old, owned by both the hourly cycle (2d) and the Monday
        # run (8d). Under the longest window this is fresh; the digest is
        # the real document in this position and rewriting it every cycle
        # is the promise that matters.
        assert verdict(True, 6.0, [HOURLY, MONDAY]) == "stale"
        assert verdict(True, 6.0, [MONDAY]) == "fresh"


class TestAge:
    def test_age_is_measured_from_the_write_time(self):
        assert age_days(NOW - timedelta(days=2), NOW) == pytest.approx(2.0)

    def test_no_write_time_is_none_rather_than_zero(self):
        assert age_days(None, NOW) is None


class TestParseRecent:
    def test_rows_are_parsed_as_oslo_time(self):
        found = parse_recent(
            "[2 file(s) modified in the last 24h]\n"
            "2026-08-25 04:34  projects/a/journal-digest.md\n"
        )
        assert found["projects/a/journal-digest.md"] == datetime(
            2026, 8, 25, 4, 34, tzinfo=OSLO
        )

    def test_the_header_line_is_not_a_row(self):
        assert parse_recent("[949 file(s) modified in the last 2160h]") == {}

    def test_a_duplicated_path_keeps_the_newest_write(self):
        # Measured live: the vault holds two file docs for
        # `journal-digest.md`, and the listing is newest-first, so keeping
        # the last row read a digest written minutes ago as 6.6 days old.
        found = parse_recent(
            "2026-08-25 04:34  projects/a/journal-digest.md\n"
            "2026-08-18 15:32  projects/a/journal-digest.md\n"
        )
        assert found["projects/a/journal-digest.md"] == datetime(
            2026, 8, 25, 4, 34, tzinfo=OSLO
        )

    def test_order_within_the_listing_does_not_decide_it(self):
        found = parse_recent(
            "2026-08-18 15:32  projects/a/journal-digest.md\n"
            "2026-08-25 04:34  projects/a/journal-digest.md\n"
        )
        assert found["projects/a/journal-digest.md"].day == 25

    def test_an_unparseable_row_is_dropped_rather_than_guessed(self):
        assert parse_recent("not-a-date  projects/a/roadmap.md") == {}


class TestRegistry:
    def test_the_shipped_registry_has_distinct_basenames(self):
        check_registry()

    def test_two_documents_sharing_a_basename_are_refused(self):
        # `issues.md` exists twice in the vault -- the owner's and mine -- and
        # basename matching would credit one with the other's owner.
        with pytest.raises(ValueError, match="basename"):
            check_registry(
                (
                    ("Mine", "a/nova/resources/issues.md", "/issues", ""),
                    ("His", "a/nova/issues.md", "/issues", ""),
                )
            )


class TestReport:
    def _rows(self, prompt_texts, written, present=None):
        if present is None:
            present = {path for _, path, _, _ in DOCS}
        return report(DOCS, prompt_texts, written, present, NOW)

    def test_an_owned_recent_document_is_fresh(self):
        rows = self._rows(
            _texts(**{"weekly-reprioritise.md": "roadmap.md and journal-digest.md"}),
            {path: NOW - timedelta(days=1) for _, path, _, _ in DOCS},
        )
        assert [r["verdict"] for r in rows] == ["fresh", "fresh"]

    def test_the_roadmap_gap_is_reported_as_no_owner(self):
        rows = self._rows(
            _texts(**{"prompt.md": "journal-digest.md"}),
            {path: NOW - timedelta(days=9) for _, path, _, _ in DOCS},
        )
        by_name = {r["name"]: r for r in rows}
        assert by_name["Roadmap"]["verdict"] == "no owner"
        assert by_name["Roadmap"]["limit"] is None

    def test_an_owned_document_past_its_window_is_stale_not_unowned(self):
        rows = self._rows(
            _texts(**{"weekly-reprioritise.md": "roadmap.md"}),
            {"projects/sokrates/projects/nova/roadmap.md": NOW - timedelta(days=30)},
        )
        by_name = {r["name"]: r for r in rows}
        assert by_name["Roadmap"]["verdict"] == "stale"
        assert by_name["Roadmap"]["limit"] == 8

    def test_one_unread_prompt_does_not_invent_a_missing_owner(self):
        # Same shape end to end: the Monday prompt failed to fetch, so the
        # roadmap has no readable owner, and the report must say it could
        # not tell rather than that nobody refreshes it.
        rows = report(
            DOCS,
            _texts(**{"prompt.md": "journal-digest.md"}),
            {path: NOW - timedelta(days=9) for _, path, _, _ in DOCS},
            {path for _, path, _, _ in DOCS},
            NOW,
            blind=True,
        )
        by_name = {r["name"]: r for r in rows}
        assert by_name["Roadmap"]["verdict"] == "unknown owner"
        assert by_name["Digest"]["verdict"] == "stale"

    def test_a_document_absent_from_the_vault_is_missing(self):
        rows = self._rows(
            _texts(**{"weekly-reprioritise.md": "roadmap.md"}),
            {},
            present=set(),
        )
        # Both, and `missing` even for the one no prompt names: a page with
        # nothing to draw and a page drawing something nobody refreshes send
        # a reader to different places, and absent is the first fact.
        assert {r["verdict"] for r in rows} == {"missing"}


class TestRender:
    def _row(self, **kwargs):
        row = {
            "name": "Roadmap",
            "path": "projects/sokrates/projects/nova/roadmap.md",
            "page": "/plan",
            "claim": "what I would work next, in order",
            "verdict": "fresh",
            "age": 1.0,
            "owners": [MONDAY],
            "limit": 8,
        }
        row.update(kwargs)
        return row

    def test_a_clean_sweep_names_what_it_checked(self):
        # "checked and fine" must never be printable by a run that looked
        # at nothing -- the rule `security_alerts` prints under.
        text = render([self._row()], [])
        assert "Roadmap" in text
        assert "Nothing to act on" in text

    def test_a_no_owner_row_says_waiting_will_not_help(self):
        text = render([self._row(verdict="no owner", owners=[], limit=None)], [])
        assert "NO OWNER" in text
        assert "Nothing to act on" not in text

    def test_an_unknown_owner_row_does_not_claim_nothing_refreshes_it(self):
        text = render(
            [self._row(verdict="unknown owner", owners=[], limit=None)],
            ["weekly-reprioritise.md"],
        )
        assert "UNKNOWN" in text
        assert "waiting will not help" not in text
        assert "NO OWNER" not in text

    def test_a_stale_row_says_the_job_exists(self):
        text = render([self._row(verdict="stale", age=30.0)], [])
        assert "STALE" in text
        assert "has not run" in text

    def test_a_missing_document_is_not_reported_as_stale(self):
        text = render([self._row(verdict="missing", age=None)], [])
        assert "MISSING" in text
        assert "STALE" not in text

    def test_an_unreadable_source_is_said_out_loud_on_a_clean_sweep(self):
        # No instrument must not print as no findings.
        text = render([self._row()], ["weekly-retro.md"])
        assert "could not read weekly-retro.md" in text

    def test_a_missing_write_time_is_not_rendered_as_zero_days(self):
        text = render([self._row(verdict="stale", age=None)], [])
        assert "0.0d" not in text
        assert "not written in 90d" in text


ROADMAP = DOCS[0][1]


class TestDeclaredDate:
    def test_the_frontmatter_stamp_is_read_as_an_oslo_date(self):
        text = "---\nupdated: 2026-08-16\n---\n\n# Roadmap\n"
        assert declared_date(text) == datetime(2026, 8, 16, tzinfo=OSLO)

    def test_a_document_with_no_frontmatter_has_no_stamp(self):
        assert declared_date("# Roadmap\n\nupdated: 2026-08-16\n") is None

    def test_a_stamp_that_is_not_a_plain_date_is_not_guessed_at(self):
        assert declared_date("---\nupdated: last Tuesday\n---\n") is None

    def test_no_text_at_all_is_no_stamp_rather_than_a_crash(self):
        assert declared_date(None) is None


class TestClaimAge:
    def test_the_older_of_the_two_decides(self):
        assert claim_age(6.0, 11.1) == 11.1

    def test_a_stamp_younger_than_the_bytes_cannot_make_it_look_fresh(self):
        assert claim_age(11.1, 6.0) == 11.1

    def test_no_stamp_falls_back_to_the_write_time(self):
        assert claim_age(6.0, None) == 6.0

    def test_no_write_time_falls_back_to_the_stamp(self):
        assert claim_age(None, 11.1) == 11.1

    def test_neither_is_unknown_rather_than_zero(self):
        assert claim_age(None, None) is None


class TestStampDecidesTheVerdict:
    def _rows(self, written, declared):
        return report(
            DOCS,
            _texts(**{"weekly-reprioritise.md": "roadmap.md and journal-digest.md"}),
            written,
            {path for _, path, _, _ in DOCS},
            NOW,
            declared=declared,
        )

    def test_a_document_written_recently_but_stamped_long_ago_is_stale(self):
        # The live case, Cycle 510: roadmap.md's bytes changed six days ago
        # and it stamps itself eleven, inside and outside an eight-day window.
        rows = self._rows(
            {path: NOW - timedelta(days=6) for _, path, _, _ in DOCS},
            {ROADMAP: NOW - timedelta(days=11)},
        )
        by_name = {r["name"]: r for r in rows}
        assert by_name["Roadmap"]["verdict"] == "stale"
        assert by_name["Digest"]["verdict"] == "fresh"

    def test_without_the_stamp_the_same_document_reads_as_fresh(self):
        rows = self._rows(
            {path: NOW - timedelta(days=6) for _, path, _, _ in DOCS},
            {},
        )
        assert {r["name"]: r["verdict"] for r in rows}["Roadmap"] == "fresh"

    def test_both_ages_are_carried_so_the_report_can_explain_itself(self):
        rows = self._rows(
            {path: NOW - timedelta(days=6) for _, path, _, _ in DOCS},
            {ROADMAP: NOW - timedelta(days=11)},
        )
        row = {r["name"]: r for r in rows}["Roadmap"]
        assert round(row["writeAge"]) == 6
        assert round(row["stampAge"]) == 11
        assert round(row["age"]) == 11


class TestStampExplains:
    def _row(self, verdict_word, write_age, limit=8):
        return {"verdict": verdict_word, "writeAge": write_age, "limit": limit}

    def test_a_stale_row_whose_bytes_are_young_is_explained(self):
        assert stamp_explains(self._row("stale", 6.0)) is True

    def test_a_stale_row_whose_bytes_are_also_old_needs_no_explanation(self):
        assert stamp_explains(self._row("stale", 11.0)) is False

    def test_a_fresh_row_is_never_explained(self):
        assert stamp_explains(self._row("fresh", 1.0)) is False

    def test_a_row_with_no_write_time_is_not_explained(self):
        assert stamp_explains(self._row("stale", None)) is False


class TestRenderExplainsTheStamp:
    def _render(self, write_days, stamp_days):
        rows = report(
            DOCS,
            _texts(**{"weekly-reprioritise.md": "roadmap.md and journal-digest.md"}),
            {path: NOW - timedelta(days=write_days) for _, path, _, _ in DOCS},
            {path for _, path, _, _ in DOCS},
            NOW,
            declared={ROADMAP: NOW - timedelta(days=stamp_days)},
        )
        return render(rows, [])

    def test_the_stale_line_says_the_bytes_are_younger_than_the_claim(self):
        text = self._render(6, 11)
        assert "STALE" in text
        assert "the stamp is why" in text
        assert "6.0d old" in text

    def test_a_document_stale_by_its_bytes_too_gets_no_extra_line(self):
        assert "the stamp is why" not in self._render(11, 11)
