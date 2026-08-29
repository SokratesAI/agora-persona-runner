"""`tools.goal_measures` — the goal numbers, taken rather than typed.

Every test below was checked by breaking the code under it: a test that
passes with the fix ripped out is not evidence of anything, which this
loop has now shipped twice.
"""

import json

import pytest

from tools import goal_measures as gm


def _entry(date, board="", title="", blocks=None, kind="cycle"):
    return {
        "date": date, "board": board, "title": title, "kind": kind,
        "blocks": blocks if blocks is not None else [],
    }


def _row(number, status, updated):
    return {"number": number, "statusKey": status, "updated": updated}


class TestDates:
    def test_bare_month_day_reads_against_the_windows_year(self):
        assert gm._iso_in_year("08-27", "2026") == "2026-08-27"

    def test_a_full_date_is_left_alone(self):
        assert gm._iso_in_year("2025-08-27", "2026") == "2025-08-27"

    def test_anything_else_is_none_rather_than_a_guess(self):
        assert gm._iso_in_year("", "2026") is None
        assert gm._iso_in_year("last tuesday", "2026") is None


class TestWindow:
    def test_only_entries_inside_the_window_survive(self):
        entries = [_entry("2026-08-28"), _entry("2026-08-21"), _entry("2026-08-25")]
        got = gm.in_window(entries, "2026-08-22", "2026-08-28")
        assert [e["date"] for e in got] == ["2026-08-28", "2026-08-25"]

    def test_both_ends_are_inclusive(self):
        entries = [_entry("2026-08-22"), _entry("2026-08-28")]
        assert len(gm.in_window(entries, "2026-08-22", "2026-08-28")) == 2


class TestG1:
    def test_rate_is_merges_over_rows_closed_in_the_window(self):
        boards = [[_row("1", "done", "08-25"), _row("2", "done", "08-26")],
                  [_row("3", "done", "2026-08-27")]]
        prs = [{"number": n} for n in range(9)]
        value, detail = gm.measure_g1([], boards, "2026-08-22", "2026-08-28", prs)
        assert value == 3.0
        assert "3 row(s) closed" in detail

    def test_a_row_closed_outside_the_window_is_not_counted(self):
        boards = [[_row("1", "done", "08-25"), _row("2", "done", "08-01")]]
        value, _ = gm.measure_g1([], boards, "2026-08-22", "2026-08-28",
                                 [{"number": 1}, {"number": 2}])
        assert value == 2.0

    def test_an_open_row_is_not_a_closure_however_recent(self):
        boards = [[_row("1", "backlog", "08-27"), _row("2", "done", "08-27")]]
        value, _ = gm.measure_g1([], boards, "2026-08-22", "2026-08-28",
                                 [{"number": 1}, {"number": 2}, {"number": 3}])
        assert value == 3.0

    def test_no_closure_refuses_rather_than_dividing_by_zero(self):
        value, detail = gm.measure_g1([], [[]], "2026-08-22", "2026-08-28", [{"number": 1}])
        assert value is None
        assert "no denominator" in detail

    def test_a_repo_that_could_not_be_counted_refuses_the_ratio(self):
        """A numerator missing a repo is wrong, not merely low.

        The caller used to `continue` past a repo `fetch_merged` refused and
        divide anyway. Measured 2026-08-29: that printed G1 as 2.0 against a
        real 7.1 — a four-fold overnight collapse that read as a fact about
        the week, on the scoreboard at the top of `/plan`.
        """
        boards = [[_row(1, "done", "08-27")]]
        value, detail = gm.measure_g1([], boards, "2026-08-22", "2026-08-28", None)
        assert value is None
        assert "could not be read" in detail


class TestG3:
    def test_a_correction_phrase_is_counted_once_per_entry(self):
        entries = [
            _entry("2026-08-27", title="I was wrong about the LimitRange"),
            _entry("2026-08-26", blocks=[{"text": "and I got it wrong twice"}]),
            _entry("2026-08-25", title="A clean cycle"),
        ]
        value, detail = gm.measure_g3(entries, [], "", "", [])
        assert value == 2
        assert "2 of 3 entries" in detail

    def test_the_match_is_case_insensitive(self):
        entries = [_entry("2026-08-27", title="I WAS WRONG")]
        assert gm.measure_g3(entries, [], "", "", [])[0] == 1

    def test_an_entry_that_owns_nothing_is_not_counted(self):
        entries = [_entry("2026-08-27", title="The build was wrong")]
        assert gm.measure_g3(entries, [], "", "", [])[0] == 0


class TestG4:
    def _hb(self, persona, enabled=True, last="2026-08-27T10:00:00Z", name="x"):
        return {"personaId": persona, "enabled": enabled,
                "lastRunAt": last, "name": name}

    def test_distinct_personas_on_a_heartbeat_that_has_fired(self):
        value, _ = gm.measure_g4([], [], "", "", [], heartbeats=[
            self._hb("a", name="Nova"), self._hb("a", name="Nova retro"),
            self._hb("b", name="Sentinel"),
        ])
        assert value == 2

    def test_a_disabled_heartbeat_is_not_a_tenant(self):
        value, _ = gm.measure_g4([], [], "", "", [], heartbeats=[
            self._hb("a"), self._hb("b", enabled=False),
        ])
        assert value == 1

    def test_a_heartbeat_that_has_never_run_is_not_real_work(self):
        value, _ = gm.measure_g4([], [], "", "", [], heartbeats=[
            self._hb("a"), self._hb("b", last=""),
        ])
        assert value == 1

    def test_none_at_all_is_zero_and_says_so(self):
        value, detail = gm.measure_g4([], [], "", "", [], heartbeats=[])
        assert value == 0
        assert "has ever run" in detail


class TestG5:
    def test_share_of_entries_naming_a_board_row(self):
        entries = [_entry("2026-08-27", board="idea #38"),
                   _entry("2026-08-27", board=""),
                   _entry("2026-08-27", board="issue #7"),
                   _entry("2026-08-27", board="  ")]
        value, detail = gm.measure_g5(entries, [], "", "", [])
        assert value == 50
        assert "2 of 4 entries" in detail

    def test_an_empty_window_refuses_rather_than_reporting_zero(self):
        value, detail = gm.measure_g5([], [], "", "", [])
        assert value is None
        assert "no journal entry" in detail


class TestFetchMerged:
    def _gh(self, rows, monkeypatch, returncode=0):
        class Done:
            pass
        done = Done()
        done.returncode = returncode
        done.stdout = json.dumps(rows)
        done.stderr = ""
        monkeypatch.setattr(gm.subprocess, "run", lambda *a, **k: done)

    def test_only_merges_inside_the_window_are_returned(self, monkeypatch):
        self._gh([{"number": 1, "mergedAt": "2026-08-27T10:00:00Z"},
                  {"number": 2, "mergedAt": "2026-08-01T10:00:00Z"}], monkeypatch)
        got, error = gm.fetch_merged("r", "2026-08-22", "2026-08-28")
        assert error is None
        assert [r["number"] for r in got] == [1]

    def test_a_page_entirely_inside_the_window_is_refused_as_a_floor(self, monkeypatch):
        self._gh([{"number": 1, "mergedAt": "2026-08-27T10:00:00Z"},
                  {"number": 2, "mergedAt": "2026-08-26T10:00:00Z"}], monkeypatch)
        got, error = gm.fetch_merged("r", "2026-08-22", "2026-08-28", limit=2)
        assert got is None
        assert "floor and not a count" in error

    def test_a_failed_gh_is_an_error_not_an_empty_count(self, monkeypatch):
        self._gh([], monkeypatch, returncode=1)
        got, error = gm.fetch_merged("r", "2026-08-22", "2026-08-28")
        assert got is None
        assert "failed" in error

    def test_github_is_asked_for_the_window_rather_than_the_newest_page(self, monkeypatch):
        """The window has to be in the query, or a busy repo cannot be counted.

        Measured 2026-08-29: `agora-persona-runner` merged 213 PRs in seven
        days against a 200-row page, so every row came back in-window and the
        count was unreachable. Scoping the search server-side is what makes a
        full page mean "there may be more" instead of "the window is bigger
        than the page".
        """
        seen = {}

        class Done:
            returncode = 0
            stdout = "[]"
            stderr = ""

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return Done()

        monkeypatch.setattr(gm.subprocess, "run", fake_run)
        gm.fetch_merged("r", "2026-08-22", "2026-08-28")
        argv = seen["argv"]
        assert "--search" in argv
        assert argv[argv.index("--search") + 1] == "merged:2026-08-22..2026-08-28"


class TestCollectMerges:
    def _fetch(self, table, monkeypatch):
        monkeypatch.setattr(gm, "fetch_merged",
                            lambda repo, since, until: table[repo])

    def test_every_repos_merges_are_pooled(self, monkeypatch):
        self._fetch({"a": ([{"number": 1}], None), "b": ([{"number": 2}], None)},
                    monkeypatch)
        prs, problems = gm.collect_merges(("a", "b"), "2026-08-22", "2026-08-28")
        assert [r["number"] for r in prs] == [1, 2]
        assert problems == []

    def test_one_unreadable_repo_voids_the_pool_rather_than_shrinking_it(self, monkeypatch):
        """The bug measured 2026-08-29: a dropped repo made G1 read 2.0 for 7.1."""
        self._fetch({"a": ([{"number": 1}], None), "b": (None, "b: cannot count")},
                    monkeypatch)
        prs, problems = gm.collect_merges(("a", "b"), "2026-08-22", "2026-08-28")
        assert prs is None
        assert problems == ["b: cannot count"]

    def test_a_later_failure_is_still_reported_after_an_earlier_one(self, monkeypatch):
        self._fetch({"a": (None, "a: cannot count"), "b": (None, "b: cannot count")},
                    monkeypatch)
        prs, problems = gm.collect_merges(("a", "b"), "2026-08-22", "2026-08-28")
        assert prs is None
        assert problems == ["a: cannot count", "b: cannot count"]


class TestRender:
    def _goal(self, name, now, unit=""):
        return {"name": name, "now": now, "unit": unit}

    def test_a_measurement_that_matches_the_file_prints_no_drift(self):
        rows = [{"key": "G5", "goal": self._goal("G5 — x", "41", "%"),
                 "value": 41, "detail": "d"}]
        out = gm.render(rows, "2026-08-22", "2026-08-28", [])
        assert "drifted" not in out

    def test_a_measurement_that_differs_names_the_written_number(self):
        rows = [{"key": "G5", "goal": self._goal("G5 — x", "41", "%"),
                 "value": 47, "detail": "d"}]
        out = gm.render(rows, "2026-08-22", "2026-08-28", [])
        assert "goals.md says 41, drifted" in out

    def test_a_percent_written_with_its_sign_still_compares_equal(self):
        rows = [{"key": "G5", "goal": self._goal("G5 — x", "41%", "%"),
                 "value": 41, "detail": "d"}]
        assert "drifted" not in gm.render(rows, "2026-08-22", "2026-08-28", [])

    def test_an_uninstrumented_goal_prints_why_rather_than_a_number(self):
        rows = [{"key": "G2", "goal": self._goal("G2 — x", "3"),
                 "value": None, "detail": "no instrument — judgement"}]
        out = gm.render(rows, "2026-08-22", "2026-08-28", [])
        assert "no instrument" in out
        assert "measured" not in out

    def test_a_problem_is_printed_rather_than_swallowed(self):
        out = gm.render([], "2026-08-22", "2026-08-28", ["gh fell over"])
        assert "! gh fell over" in out


GOALS_FOR_WRITE = """# Goals

```goal
name: G1 — one
measure: things per week
now: 3
target: 1
direction: down
```

Prose he wrote that nothing parses.

```goal
name: G2 — two
measure: a judgement
now: 3
target: 0
direction: down
```
"""


def _mrow(key, name, value, now):
    return {"key": key, "goal": {"name": name, "now": now, "unit": ""},
            "value": value, "detail": ""}


class TestWriteBack:
    def test_a_drifted_measured_value_lands_in_the_fence(self, tmp_path):
        path = tmp_path / "goals.md"
        path.write_text(GOALS_FOR_WRITE, encoding="utf-8")
        report = gm.write_back(str(path), GOALS_FOR_WRITE,
                               [_mrow("G1", "G1 — one", 8.2, "3")])
        written = path.read_text(encoding="utf-8")
        assert "now: 8.2" in written
        assert "Prose he wrote that nothing parses." in written
        assert "WROTE 1 value(s)" in report
        assert "now: 3 -> 8.2" in report

    def test_a_goal_with_no_instrument_is_left_alone(self, tmp_path):
        path = tmp_path / "goals.md"
        path.write_text(GOALS_FOR_WRITE, encoding="utf-8")
        report = gm.write_back(str(path), GOALS_FOR_WRITE,
                               [_mrow("G2", "G2 — two", None, "3")])
        assert path.read_text(encoding="utf-8") == GOALS_FOR_WRITE
        assert "WROTE NOTHING" in report

    def test_an_already_correct_number_writes_nothing_at_all(self, tmp_path):
        """The caller wraps this in a compare-and-swap against a file he edits
        from his phone; a no-op write is a real chance to lose his edit."""
        path = tmp_path / "goals.md"
        path.write_text("untouched", encoding="utf-8")
        report = gm.write_back(str(path), GOALS_FOR_WRITE,
                               [_mrow("G1", "G1 — one", 3.0, "3")])
        assert path.read_text(encoding="utf-8") == "untouched"
        assert "WROTE NOTHING" in report

    def test_a_goal_whose_fence_moved_is_named_not_skipped_quietly(self, tmp_path):
        path = tmp_path / "goals.md"
        path.write_text("untouched", encoding="utf-8")
        report = gm.write_back(str(path), GOALS_FOR_WRITE,
                               [_mrow("G9", "G9 — renamed", 1.0, "3")])
        assert path.read_text(encoding="utf-8") == "untouched"
        assert "G9: could not edit that goal's fence" in report
        # And it must not also claim every goal already agrees — that
        # sentence would report a clean run over the one real failure.
        assert "already carries its measured number" not in report

    def test_two_goals_both_land_in_one_file(self, tmp_path):
        path = tmp_path / "goals.md"
        path.write_text(GOALS_FOR_WRITE, encoding="utf-8")
        gm.write_back(str(path), GOALS_FOR_WRITE,
                      [_mrow("G1", "G1 — one", 8.2, "3"),
                       _mrow("G2", "G2 — two", 9.0, "3")])
        written = path.read_text(encoding="utf-8")
        assert "now: 8.2" in written and "now: 9.0" in written
