"""`tools.goal_measures` — the goal numbers, taken rather than typed.

Every test below was checked by breaking the code under it: a test that
passes with the fix ripped out is not evidence of anything, which this
loop has now shipped twice.
"""

import types
import json
import sys
from collections import Counter
from datetime import datetime, timezone

import pytest

import tools
from tools import goal_measures as gm
from tools import goal_measures


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


class TestTodayInOslo:
    """The window's end date. It used to be UTC plus a flat two hours, which
    is Oslo for seven months of the year and a day wrong in the other five.
    """

    def test_winter_late_evening_does_not_roll_the_date_forward(self):
        # 22:30 UTC on a January night is 23:30 in Oslo, still the 15th.
        # The old `+2h` made it 00:30 on the 16th, so the window ended
        # tomorrow and `merged:<since>..<until>` reached a day too far.
        utc = datetime(2026, 1, 15, 22, 30, tzinfo=timezone.utc)
        assert gm.today_oslo(utc) == "2026-01-15"

    def test_summer_late_evening_still_rolls_forward(self):
        # The other side of the same boundary: in August Oslo really is
        # UTC+2, so 22:30 UTC is 00:30 on the 30th and the date does move.
        # Without this the fix could be "always use UTC", which is wrong
        # in the opposite direction for five months.
        utc = datetime(2026, 8, 29, 22, 30, tzinfo=timezone.utc)
        assert gm.today_oslo(utc) == "2026-08-30"

    def test_winter_midday_is_unremarkable(self):
        utc = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        assert gm.today_oslo(utc) == "2026-01-15"

    def test_main_takes_its_default_window_end_from_today_oslo(self, tmp_path, monkeypatch, capsys):
        """The helper above being right is worth nothing if `main` does not
        call it. Nothing tested `main` at all, so a revert of that one line
        would have failed no test — the same shape as a guard that guards
        nothing.
        """
        path = tmp_path / "goals.md"
        path.write_text(GOALS_FOR_WRITE, encoding="utf-8")
        monkeypatch.setattr(gm, "today_oslo", lambda now=None: "2026-01-15")
        monkeypatch.setattr(gm, "fetch_entries", lambda limit, site=None: ([], None))
        monkeypatch.setattr(gm, "fetch_board", lambda name, site=None: ([], None))
        monkeypatch.setattr(gm, "collect_merges", lambda repos, since, until: ({}, []))
        assert gm.main(["--goals", str(path)]) == 0
        assert "2026-01-09 to 2026-01-15" in capsys.readouterr().out


PG_DOC = """# Project goals

## Nova

```key-result
id: nova-kr-your-rows
name: The work closes your rows
measure: Merged pull requests per board row closed
now: 6.8
target: 2.0
direction: down
```

```key-result
id: nova-kr-in-the-app
name: Everything reaches your phone
measure: Things you still have to leave the Nova app to do
now: 3
target: 0
direction: down
```
"""


def _pg_sections():
    from agora_runner.project_goals import parse_project_goals
    return parse_project_goals(PG_DOC)


def test_key_result_rows_reuses_the_goal_measurement_rather_than_recomputing():
    rows = [{"key": "G1", "goal": {}, "value": 3.9, "detail": "254 PRs / 65 rows"}]
    out = goal_measures.key_result_rows(_pg_sections(), rows)
    by_id = {row["id"]: row for row in out}
    assert by_id["nova-kr-your-rows"]["value"] == 3.9
    assert "G1" in by_id["nova-kr-your-rows"]["detail"]
    assert by_id["nova-kr-your-rows"]["project"] == "nova"


def test_key_result_with_no_instrument_is_never_given_a_number():
    rows = [{"key": "G1", "goal": {}, "value": 3.9, "detail": "x"}]
    out = goal_measures.key_result_rows(_pg_sections(), rows)
    by_id = {row["id"]: row for row in out}
    assert by_id["nova-kr-in-the-app"]["value"] is None
    assert "no instrument" in by_id["nova-kr-in-the-app"]["detail"]


def test_key_result_whose_goal_could_not_be_measured_says_so_and_stays_none():
    # A failed measurement must not read as "this has no instrument" -- those
    # have opposite fixes, and only one of them is a missing map entry.
    rows = [{"key": "G1", "goal": {}, "value": None, "detail": "a repo could not be read"}]
    out = goal_measures.key_result_rows(_pg_sections(), rows)
    row = {r["id"]: r for r in out}["nova-kr-your-rows"]
    assert row["value"] is None
    assert "G1 could not be measured" in row["detail"]
    assert "a repo could not be read" in row["detail"]


def test_write_back_key_results_writes_only_the_drifted_instrumented_one(tmp_path):
    path = tmp_path / "project-goals.md"
    path.write_text(PG_DOC, encoding="utf-8")
    rows = [{"key": "G1", "goal": {}, "value": 3.9, "detail": "x"}]
    kr_rows = goal_measures.key_result_rows(_pg_sections(), rows)
    report = goal_measures.write_back_key_results(str(path), PG_DOC, kr_rows)
    assert "WROTE 1 value(s)" in report
    written = path.read_text(encoding="utf-8")
    assert "now: 3.9" in written
    # The uninstrumented key result keeps the number the document carried.
    assert "now: 3\n" in written


def test_write_back_key_results_writes_nothing_when_the_number_already_agrees(tmp_path):
    path = tmp_path / "project-goals.md"
    path.write_text("untouched", encoding="utf-8")
    rows = [{"key": "G1", "goal": {}, "value": 6.8, "detail": "x"}]
    kr_rows = goal_measures.key_result_rows(_pg_sections(), rows)
    report = goal_measures.write_back_key_results(str(path), PG_DOC, kr_rows)
    assert "WROTE NOTHING" in report
    assert path.read_text(encoding="utf-8") == "untouched"


def test_write_back_key_results_names_a_fence_it_could_not_edit(tmp_path):
    path = tmp_path / "project-goals.md"
    path.write_text("untouched", encoding="utf-8")
    rows = [{"key": "G1", "goal": {}, "value": 3.9, "detail": "x"}]
    kr_rows = goal_measures.key_result_rows(_pg_sections(), rows)
    # The id moved between the read and the write.
    report = goal_measures.write_back_key_results(
        str(path), PG_DOC.replace("nova-kr-your-rows", "nova-kr-renamed"), kr_rows)
    assert "could not edit that key-result fence" in report
    assert "nova-kr-your-rows" in report
    # And it must not read as a clean run.
    assert "every instrumented key result already carries" not in report
    assert path.read_text(encoding="utf-8") == "untouched"


def test_render_key_results_marks_drift_and_prints_the_written_number():
    rows = [{"key": "G1", "goal": {}, "value": 3.9, "detail": "254 PRs / 65 rows"}]
    out = goal_measures.render_key_results(
        goal_measures.key_result_rows(_pg_sections(), rows), "project-goals.md")
    assert "measured 3.9" in out
    assert "the document says 6.8, drifted" in out
    assert "254 PRs / 65 rows" in out


MARCUS_PG_DOC = """# Project goals

## Marcus

```key-result
id: marcus-kr-sessions-logged
name: You log the training you did
measure: Training sessions logged per week
now: 0
target: 3
direction: up
```

```key-result
id: marcus-kr-a-plan-of-his-own
name: The plan on the screen is one you made
measure: The active plan is one you drafted with the coach
now: 0
target: 1
direction: up
```

```key-result
id: marcus-kr-coach-first-try
name: The coach answers on the first tap
measure: Share of coach taps that return a usable answer without a retry
now: 83
target: 99
direction: up
```

## Nova

```key-result
id: nova-kr-in-the-app
name: Everything happens in the Nova app
measure: Things he still has to leave the app to do
now: 3
target: 0
direction: down
```
"""


def _marcus_sections():
    from agora_runner.project_goals import parse_project_goals
    return parse_project_goals(MARCUS_PG_DOC)


def test_marcus_sessions_are_counted_by_the_day_they_are_logged_for():
    # Two inside the window, one before it, one after it. The out-of-window
    # pair is what separates a real window filter from `len(sessions)`.
    state = {"sessions": [
        {"id": "a", "date": "2026-09-08"},
        {"id": "b", "date": "2026-09-11"},
        {"id": "c", "date": "2026-09-06"},
        {"id": "d", "date": "2026-09-14"},
    ]}
    value, detail = goal_measures.measure_marcus_sessions_logged(
        state, "2026-09-07", "2026-09-13")
    assert value == 2.0
    assert "2 session(s)" in detail and "out of 4" in detail


def test_marcus_sessions_report_a_weekly_rate_not_a_raw_count():
    # A 14-day window holding 4 sessions is 2 per week, not 4.
    state = {"sessions": [{"id": str(n), "date": "2026-09-08"} for n in range(4)]}
    value, _ = goal_measures.measure_marcus_sessions_logged(
        state, "2026-08-31", "2026-09-13")
    assert value == 2.0


def test_marcus_sessions_with_no_usable_date_are_named_as_a_floor():
    state = {"sessions": [{"id": "a", "date": "2026-09-08"}, {"id": "b"}]}
    value, detail = goal_measures.measure_marcus_sessions_logged(
        state, "2026-09-07", "2026-09-13")
    assert value == 1.0
    assert "1 carry no YYYY-MM-DD date" in detail and "floor" in detail


def test_marcus_sessions_missing_entirely_is_not_measured_as_zero():
    # A store that answered without the list is a broken read, and reporting
    # it as "0 sessions per week" would write a lie into the document.
    value, detail = goal_measures.measure_marcus_sessions_logged(
        {}, "2026-09-07", "2026-09-13")
    assert value is None
    assert "no `sessions` list" in detail


def test_marcus_empty_plan_reads_zero_and_names_the_block():
    state = {"plan": {"blockName": "No plan yet",
                      "days": [{"day": "Monday", "focus": "Open", "exercises": []}]}}
    value, detail = goal_measures.measure_marcus_own_plan(state, None, None)
    assert value == 0
    assert "No plan yet" in detail


def test_marcus_filled_plan_reads_one_and_says_it_is_a_ceiling():
    state = {"plan": {"blockName": "Hypertrophy Block", "days": [
        {"day": "Monday", "exercises": [{"name": "Squat"}]},
        {"day": "Tuesday", "exercises": []},
    ]}}
    value, detail = goal_measures.measure_marcus_own_plan(state, None, None)
    assert value == 1
    assert "ceiling" in detail and "demo" in detail


def test_marcus_key_results_are_measured_from_the_state_not_from_a_goal(monkeypatch):
    # `marcus-kr-coach-first-try` is read live off Marcus, so without this the
    # test tries to open a socket to the pod -- which is the shape of every
    # "a new fetch makes old tests hit the network" failure.
    _no_outcomes(monkeypatch)
    state = {"sessions": [{"id": "a", "date": "2026-09-11"}],
             "plan": {"blockName": "No plan yet", "days": []}}
    out = goal_measures.key_result_rows(
        _marcus_sections(), [], marcus=state,
        since="2026-09-07", until="2026-09-13")
    by_id = {row["id"]: row for row in out}
    assert by_id["marcus-kr-sessions-logged"]["value"] == 1.0
    assert by_id["marcus-kr-a-plan-of-his-own"]["value"] == 0
    # The third has no instrument at all, and that is a different sentence
    # from "the state could not be read".
    #
    # It used to be `marcus-kr-coach-first-try`, and this test went red the
    # day that one got an instrument -- which is the point: a fixture standing
    # in for "the uninstrumented one" has to name an id that can never acquire
    # an instrument, or closing the gap is what breaks it. `nova-kr-in-the-app`
    # is a judgement about how the app feels to him and there is nothing on
    # this box that could ever read it.
    third = by_id["nova-kr-in-the-app"]
    assert third["value"] is None
    assert "no instrument" in third["detail"]
    assert "a judgement about his experience" in third["detail"]


def test_an_unread_marcus_state_never_reads_as_having_no_instrument(monkeypatch):
    _no_outcomes(monkeypatch)
    # These have opposite fixes: one is a map entry, the other is a pod.
    out = goal_measures.key_result_rows(
        _marcus_sections(), [], marcus=None,
        marcus_error="could not read http://marcus/api/state: timed out",
        since="2026-09-07", until="2026-09-13")
    row = {r["id"]: r for r in out}["marcus-kr-sessions-logged"]
    assert row["value"] is None
    assert "not measured" in row["detail"] and "timed out" in row["detail"]
    assert "no instrument" not in row["detail"]


def test_a_document_with_no_marcus_key_result_never_calls_marcus():
    assert goal_measures._needs_marcus(_pg_sections()) is False
    assert goal_measures._needs_marcus(_marcus_sections()) is True


def test_main_writes_the_marcus_numbers_and_names_a_dead_state_in_the_report(
        tmp_path, monkeypatch, capsys):
    goals = tmp_path / "goals.md"
    goals.write_text(GOALS_FOR_WRITE, encoding="utf-8")
    pg = tmp_path / "project-goals.md"
    pg.write_text(MARCUS_PG_DOC, encoding="utf-8")
    _no_outcomes(monkeypatch)
    monkeypatch.setattr(gm, "today_oslo", lambda now=None: "2026-09-13")
    monkeypatch.setattr(gm, "fetch_entries", lambda limit, site=None: ([], None))
    monkeypatch.setattr(gm, "fetch_board", lambda name, site=None: ([], None))
    monkeypatch.setattr(gm, "collect_merges", lambda repos, since, until: ({}, []))
    monkeypatch.setattr(gm, "fetch_marcus_state", lambda site=None: (
        {"sessions": [{"id": "a", "date": "2026-09-11"},
                      {"id": "b", "date": "2026-09-12"}],
         "plan": {"blockName": "No plan yet", "days": []}}, None))
    assert gm.main(["--goals", str(goals), "--project-goals", str(pg),
                    "--write"]) == 0
    assert "now: 2.0" in pg.read_text(encoding="utf-8")

    # And a state that did not answer lands in the goals report's own
    # cannot-see list rather than vanishing.
    pg.write_text(MARCUS_PG_DOC, encoding="utf-8")
    monkeypatch.setattr(gm, "fetch_marcus_state",
                        lambda site=None: (None, "could not read /api/state: refused"))
    capsys.readouterr()
    assert gm.main(["--goals", str(goals), "--project-goals", str(pg)]) == 0
    out = capsys.readouterr().out
    assert "! could not read /api/state: refused" in out
    assert pg.read_text(encoding="utf-8") == MARCUS_PG_DOC


PG_PM_DOC = """---
type: board
---

# Project goals

## Product management

```key-result
id: pm-kr-written-why
name: Shipped work traces back to a written why
measure: Share of merged pull requests that name a board row or a capture
target: 80
unit: %
direction: up
```
"""


def _pm_sections():
    from agora_runner.project_goals import parse_project_goals
    return parse_project_goals(PG_PM_DOC)


def test_written_why_counts_a_labelled_board_reference_in_the_title():
    value, detail = goal_measures.measure_pm_written_why(
        [{"number": 1, "title": "Fix the picker (issue #227) (#1062)", "body": ""},
         {"number": 2, "title": "Tidy the worktree sweeper (#1063)", "body": ""}],
        "2026-09-07", "2026-09-13")
    assert value == 50
    assert "1 of 2 merged PR(s)" in detail


def test_written_why_ignores_the_pull_requests_own_number():
    # Every squash title GitHub writes carries a bare `(#N)`. If that counted,
    # the share would read 100% forever and measure nothing at all.
    value, _ = goal_measures.measure_pm_written_why(
        [{"number": 9, "title": "Something entirely untraceable (#1062)", "body": "Closes #77"}],
        "2026-09-07", "2026-09-13")
    assert value == 0


def test_written_why_reads_the_body_as_well_as_the_title():
    value, _ = goal_measures.measure_pm_written_why(
        [{"number": 3, "title": "no row here (#4)", "body": "Serves idea #38 on his board."}],
        "2026-09-07", "2026-09-13")
    assert value == 100


def test_written_why_accepts_the_plural_and_the_spacing_he_actually_writes():
    for title in ("closes issues #12", "see Idea # 38", "ISSUE #7 again"):
        value, _ = goal_measures.measure_pm_written_why(
            [{"number": 1, "title": title, "body": ""}], "2026-09-07", "2026-09-13")
        assert value == 100, title


def test_written_why_is_not_measured_when_a_repo_could_not_be_read():
    # Same contract as G1: a share missing part of its denominator is wrong,
    # not low, so the honest answer is no answer rather than a bigger number.
    value, detail = goal_measures.measure_pm_written_why(None, "2026-09-07", "2026-09-13")
    assert value is None
    assert "could not be read" in detail


def test_written_why_has_no_denominator_when_nothing_merged():
    value, detail = goal_measures.measure_pm_written_why([], "2026-09-07", "2026-09-13")
    assert value is None
    assert "no denominator" in detail


def test_written_why_is_measured_from_the_merge_list_not_from_a_goal():
    out = goal_measures.key_result_rows(
        _pm_sections(), [], since="2026-09-07", until="2026-09-13",
        prs=[{"number": 1, "title": "a (issue #227)", "body": ""},
             {"number": 2, "title": "b", "body": ""},
             {"number": 3, "title": "c", "body": ""},
             {"number": 4, "title": "d", "body": ""}])
    row = {r["id"]: r for r in out}["pm-kr-written-why"]
    assert row["value"] == 25
    assert "no instrument" not in row["detail"]


def test_an_unreadable_merge_list_never_reads_as_having_no_instrument():
    out = goal_measures.key_result_rows(
        _pm_sections(), [], since="2026-09-07", until="2026-09-13", prs=None)
    row = {r["id"]: r for r in out}["pm-kr-written-why"]
    assert row["value"] is None
    assert row["detail"].startswith("not measured")
    assert "no instrument" not in row["detail"]


def test_written_why_is_written_into_a_key_result_that_carries_no_now_yet(tmp_path):
    # Its fence has never had a `now:` line -- the number was blank rather than
    # zero because nobody had measured it -- so the setter has to insert one.
    kr_rows = goal_measures.key_result_rows(
        _pm_sections(), [], since="2026-09-07", until="2026-09-13",
        prs=[{"number": 1, "title": "a (issue #227)", "body": ""},
             {"number": 2, "title": "b", "body": ""}])
    target = tmp_path / "project-goals.md"
    target.write_text(PG_PM_DOC, encoding="utf-8")
    report = goal_measures.write_back_key_results(str(target), PG_PM_DOC, kr_rows)
    assert "WROTE 1 value(s)" in report
    assert "now: (blank) -> 50" in report
    assert "now: 50" in target.read_text(encoding="utf-8")


def test_gh_pr_list_asks_for_the_title_and_body_the_share_is_read_from():
    # The measure is a substring search over text this call is the only source
    # of; dropping either field from --json leaves every PR looking untraceable.
    import inspect
    source = inspect.getsource(goal_measures.fetch_merged)
    assert "number,mergedAt,title,body" in source


def test_main_hands_the_merge_list_to_the_key_results(tmp_path, monkeypatch, capsys):
    # The wiring, not the measurer. `key_result_rows` defaults `prs` to None,
    # so dropping the argument from main's call leaves every run reporting
    # "not measured" while every unit test above still passes -- the whole
    # instrument would be dead and nothing would say so.
    goals = tmp_path / "goals.md"
    goals.write_text(GOALS_FOR_WRITE, encoding="utf-8")
    pg = tmp_path / "project-goals.md"
    pg.write_text(PG_PM_DOC, encoding="utf-8")
    monkeypatch.setattr(gm, "today_oslo", lambda now=None: "2026-09-13")
    monkeypatch.setattr(gm, "fetch_entries", lambda limit, site=None: ([], None))
    monkeypatch.setattr(gm, "fetch_board", lambda name, site=None: ([], None))
    monkeypatch.setattr(gm, "collect_merges", lambda repos, since, until: (
        [{"number": 1, "title": "a (issue #227)", "body": ""},
         {"number": 2, "title": "b (#2)", "body": ""},
         {"number": 3, "title": "c (#3)", "body": ""},
         {"number": 4, "title": "d (#4)", "body": ""}], []))
    assert gm.main(["--goals", str(goals), "--project-goals", str(pg),
                    "--write"]) == 0
    assert "not measured" not in capsys.readouterr().out
    assert "now: 25" in pg.read_text(encoding="utf-8")


# --- KPIs ------------------------------------------------------------------
#
# Issue #227's rule 4: a KPI is a guardrail with a range, never a target. These
# cover the half of `project-goals.md` that had no instrument at all -- every
# `now:` under a ```kpi fence was typed by a cycle, and `nova-kpi-cost-per-cycle`
# still carries a number copied out of a paragraph in `prompt.md` describing a
# window that closed on 2026-08-28.

KPI_DOC = """# Project goals

## Nova

```kpi
id: nova-kpi-dropped-ticks
name: Heartbeat slots that produce no run
measure: Share of scheduled firings in 24h with no run
now: 8
low: 0
high: 10
unit: %
```

```kpi
id: nova-kpi-invented-for-this-fixture
name: Something nothing here can read
measure: A number no measurer computes
now: 1.74
low: 0
high: 2.0
```
"""
# The second block is deliberately an id that does NOT exist in production.
# It used to be `marcus-kpi-coach-latency`, which was the real uninstrumented
# KPI at the time -- and the day that KPI got an instrument these six tests
# started making live HTTP calls to Marcus, because the fixture's meaning
# ("the one with no measurer") was pinned to a fact about the world rather
# than to the fixture. A stand-in that can never be instrumented cannot rot
# that way.


def _kpi_sections():
    from agora_runner.project_goals import parse_project_goals
    return parse_project_goals(KPI_DOC)


def test_kpi_rows_measures_the_one_with_an_instrument(monkeypatch):
    monkeypatch.setitem(goal_measures.KPI_MEASURERS, "nova-kpi-dropped-ticks",
                        lambda since, until: (3, "1 of 33 slot(s)"))
    out = goal_measures.kpi_rows(_kpi_sections(), "2026-09-07", "2026-09-13")
    by_id = {row["id"]: row for row in out}
    assert by_id["nova-kpi-dropped-ticks"]["value"] == 3
    assert by_id["nova-kpi-dropped-ticks"]["detail"] == "1 of 33 slot(s)"


def test_kpi_rows_says_a_kpi_with_no_measurer_is_uninstrumented():
    out = goal_measures.kpi_rows(_kpi_sections(), "2026-09-07", "2026-09-13")
    by_id = {row["id"]: row for row in out}
    row = by_id["nova-kpi-invented-for-this-fixture"]
    assert row["value"] is None
    assert "no instrument" in row["detail"]
    assert "nothing here computes this measure" in row["detail"]


def test_kpi_rows_prints_the_written_reason_when_there_is_one(monkeypatch):
    """`KPI_NO_INSTRUMENT` is empty in production now that every KPI has a
    measurer, and the mechanism still has to work for the next one added
    without one: a blank `now` says nothing about whether anyone tried, which
    is how three cycles come to re-derive the same gap."""
    monkeypatch.setitem(goal_measures.KPI_NO_INSTRUMENT,
                        "nova-kpi-invented-for-this-fixture",
                        "nothing on this box records it")
    out = goal_measures.kpi_rows(_kpi_sections(), "2026-09-07", "2026-09-13")
    row = {r["id"]: r for r in out}["nova-kpi-invented-for-this-fixture"]
    assert row["detail"] == "no instrument — nothing on this box records it"


def test_kpi_rows_separates_a_failed_reading_from_a_missing_instrument(monkeypatch):
    monkeypatch.setitem(goal_measures.KPI_MEASURERS, "nova-kpi-dropped-ticks",
                        lambda since, until: (None, "Agora did not answer"))
    out = goal_measures.kpi_rows(_kpi_sections(), "2026-09-07", "2026-09-13")
    row = {r["id"]: r for r in out}["nova-kpi-dropped-ticks"]
    assert row["value"] is None
    assert row["detail"].startswith("not measured —")
    assert "no instrument" not in row["detail"]


def test_write_back_kpis_moves_now_and_leaves_the_bounds_alone(tmp_path):
    path = tmp_path / "project-goals.md"
    path.write_text(KPI_DOC, encoding="utf-8")
    rows = goal_measures.kpi_rows(_kpi_sections())
    for row in rows:
        if row["id"] == "nova-kpi-dropped-ticks":
            row["value"], row["detail"] = 3, "1 of 33"
    report = goal_measures.write_back_kpis(str(path), KPI_DOC, rows)
    out = path.read_text(encoding="utf-8")
    assert "now: 3" in out
    assert "now: 8" not in out
    # Rule 4: nothing here may move a guardrail's range to fit its reading.
    assert "low: 0" in out and "high: 10" in out
    # The uninstrumented KPI is untouched, not blanked.
    assert "now: 1.74" in out
    assert "nova-kpi-dropped-ticks  now: 8 -> 3" in report


def test_write_back_kpis_writes_nothing_when_the_document_already_agrees(tmp_path):
    path = tmp_path / "project-goals.md"
    path.write_text(KPI_DOC, encoding="utf-8")
    rows = goal_measures.kpi_rows(_kpi_sections())
    for row in rows:
        if row["id"] == "nova-kpi-dropped-ticks":
            row["value"], row["detail"] = 8, "1 of 33"
    report = goal_measures.write_back_kpis(str(path), KPI_DOC, rows)
    assert report.startswith("WROTE NOTHING")
    assert path.read_text(encoding="utf-8") == KPI_DOC


def test_render_kpis_prints_the_range_and_flags_drift():
    """8 -> 3 inside `[0..10]` is the carve-out, 8 -> 14 is still drift.

    Both halves live here because the range is what separates them, and this
    is the only renderer that prints a range. See
    `goal_measures.kpi_drift_crosses_bounds`.
    """
    rows = goal_measures.kpi_rows(_kpi_sections())
    for row in rows:
        if row["id"] == "nova-kpi-dropped-ticks":
            row["value"], row["detail"] = 3, "1 of 33"
    text = goal_measures.render_kpis(rows, "project-goals.md")
    assert "measured 3  [0..10]" in text
    assert "the document says 8, moved inside its own range" in text
    assert "drifted" not in text

    rows = goal_measures.kpi_rows(_kpi_sections())
    for row in rows:
        if row["id"] == "nova-kpi-dropped-ticks":
            row["value"], row["detail"] = 14, "14 of 33"
    text = goal_measures.render_kpis(rows, "project-goals.md")
    assert "the document says 8, drifted" in text


def test_measure_nova_dropped_ticks_is_the_share_over_judged_heartbeats(monkeypatch):
    from tools import heartbeat_gaps
    monkeypatch.setattr(heartbeat_gaps, "_fetch", lambda: ([{"id": "a"}, {"id": "b"}], None))
    monkeypatch.setattr(heartbeat_gaps, "fetch_conversations", lambda: ([], None))
    judged = iter([
        {"verdict": "judged", "expected": 72, "missed": [1, 2, 3, 4, 5, 6]},
        {"verdict": "unjudged", "detail": "disabled"},
    ])
    monkeypatch.setattr(heartbeat_gaps, "judge",
                        lambda h, c, now, hours: next(judged))
    value, detail = goal_measures.measure_nova_dropped_ticks(None, None)
    assert value == 8  # 6 of 72
    # An unjudged heartbeat is in neither half of the share, and the report
    # says so -- a share taken over one of two heartbeats is not a claim
    # about the scheduler.
    assert "6 of 72" in detail
    assert "1 more could not be judged" in detail


def test_measure_nova_dropped_ticks_reads_24h_not_the_goals_window(monkeypatch):
    from tools import heartbeat_gaps
    monkeypatch.setattr(heartbeat_gaps, "_fetch", lambda: ([{"id": "a"}], None))
    monkeypatch.setattr(heartbeat_gaps, "fetch_conversations", lambda: ([], None))
    seen = []

    def _judge(heartbeat, conversations, now, hours):
        seen.append(hours)
        return {"verdict": "judged", "expected": 10, "missed": []}

    monkeypatch.setattr(heartbeat_gaps, "judge", _judge)
    # `since`/`until` are the goals' seven-day window and must not reach it: a
    # guardrail averaged over a week hides the bad night it exists to catch.
    goal_measures.measure_nova_dropped_ticks("2026-09-07", "2026-09-13")
    assert seen == [24.0]


def test_measure_nova_dropped_ticks_refuses_rather_than_reading_zero(monkeypatch):
    from tools import heartbeat_gaps
    monkeypatch.setattr(heartbeat_gaps, "_fetch", lambda: ([{"id": "a"}], None))
    monkeypatch.setattr(heartbeat_gaps, "fetch_conversations", lambda: ([], None))
    monkeypatch.setattr(heartbeat_gaps, "judge",
                        lambda h, c, now, hours: {"verdict": "unjudged"})
    value, detail = goal_measures.measure_nova_dropped_ticks(None, None)
    # Nothing judged is not a perfect scheduler. A 0 here would be written into
    # the document as a healthy guardrail off an instrument that saw nothing.
    assert value is None
    assert "no denominator" in detail


def test_measure_nova_dropped_ticks_names_an_unread_route(monkeypatch):
    from tools import heartbeat_gaps
    monkeypatch.setattr(heartbeat_gaps, "_fetch", lambda: ([], "Agora returned 503"))
    value, detail = goal_measures.measure_nova_dropped_ticks(None, None)
    assert value is None
    assert "503" in detail


PG_BOTH_DOC = """# Project goals

## Marcus

```key-result
id: marcus-kr-sessions-logged
name: You log the training you did
measure: Training sessions logged per week
now: 0
target: 3
direction: up
```

```kpi
id: nova-kpi-dropped-ticks
name: Heartbeat slots that produce no run
measure: Share of scheduled firings in 24h with no run
now: 8
low: 0
high: 10
```
"""


def test_main_keeps_both_writes_when_a_key_result_and_a_kpi_both_move(
        tmp_path, monkeypatch):
    """Two setters, one file, one run -- and the second must not undo the first.

    `write_back_key_results` writes the file, then `write_back_kpis` edits text
    of its own. If that text is the copy read before the first write, its edit
    lands on a document that no longer exists on disk and the key result's new
    number is silently reverted -- while the report happily says both wrote.
    """
    goals = tmp_path / "goals.md"
    goals.write_text(GOALS_FOR_WRITE, encoding="utf-8")
    pg = tmp_path / "project-goals.md"
    pg.write_text(PG_BOTH_DOC, encoding="utf-8")
    monkeypatch.setattr(gm, "today_oslo", lambda now=None: "2026-09-13")
    monkeypatch.setattr(gm, "fetch_entries", lambda limit, site=None: ([], None))
    monkeypatch.setattr(gm, "fetch_board", lambda name, site=None: ([], None))
    monkeypatch.setattr(gm, "collect_merges", lambda repos, since, until: ({}, []))
    monkeypatch.setattr(gm, "fetch_marcus_state", lambda site=None: (
        {"sessions": [{"id": "a", "date": "2026-09-11"},
                      {"id": "b", "date": "2026-09-12"}],
         "plan": {"blockName": "No plan yet", "days": []}}, None))
    monkeypatch.setitem(gm.KPI_MEASURERS, "nova-kpi-dropped-ticks",
                        lambda since, until: (3, "1 of 33 slot(s)"))
    assert gm.main(["--goals", str(goals), "--project-goals", str(pg),
                    "--write"]) == 0
    out = pg.read_text(encoding="utf-8")
    assert "now: 2.0" in out, "the key-result write was undone by the KPI write"
    assert "now: 3" in out, "the KPI write did not land"


# --- nova-kpi-cost-per-cycle -----------------------------------------------
#
# The one KPI whose written number said out loud where it came from: a
# paragraph in `prompt.md` about the 08-24..08-28 window. The ledger behind it
# is republished after every cycle and served at `/api/costs`.

COST_COLUMNS = ["at", "minutes", "turns", "toolCalls", "weighted",
                "subagentTurns", "subagentWeighted"]


def _ms_ago(hours):
    from datetime import datetime, timezone
    return (datetime.now(timezone.utc).timestamp() - hours * 3600) * 1000


def _ledger(rows, columns=None):
    return {"rows": rows, "columns": list(columns or COST_COLUMNS)}


def test_cost_per_cycle_is_the_median_of_the_window_in_millions():
    # 1.0/2.0/4.0 medians to 2.0 and means to 2.33, so a mean slipped in here
    # is a different number rather than the same one.
    rows = [
        [_ms_ago(1), 11.0, 60, 60, 1_000_000, 0, 0],
        [_ms_ago(5), 11.0, 60, 60, 4_000_000, 0, 0],
        [_ms_ago(20), 11.0, 60, 60, 2_000_000, 0, 0],
    ]
    value, detail = goal_measures.measure_nova_cost_per_cycle(
        None, None, ledger=_ledger(rows))
    assert value == 2.0
    assert "median of 3 cycle(s) in the last 24h" in detail


def test_cost_per_cycle_leaves_out_a_cycle_older_than_the_window():
    """The window is the measure. A cheap week does not make tonight cheap.

    Every row here is inside the ledger and only two are inside 24h, so a
    measurer that medians the whole document reads 0.4 instead of 2.0 -- and
    0.4 is a healthy guardrail reading taken over cycles that ran days ago.
    """
    rows = [
        [_ms_ago(2), 11.0, 60, 60, 2_000_000, 0, 0],
        [_ms_ago(23), 11.0, 60, 60, 2_000_000, 0, 0],
        [_ms_ago(30), 11.0, 60, 60, 400_000, 0, 0],
        [_ms_ago(100), 11.0, 60, 60, 400_000, 0, 0],
        [_ms_ago(200), 11.0, 60, 60, 400_000, 0, 0],
    ]
    value, _ = goal_measures.measure_nova_cost_per_cycle(
        None, None, ledger=_ledger(rows))
    assert value == 2.0


def test_cost_per_cycle_reads_the_columns_by_name_not_by_position():
    """`nova_costs` warns that reordering the column tuple swaps what is read.

    So the order comes off the payload's own `cycleColumns`. With `weighted`
    moved to the front, a measurer indexing position 4 would read `toolCalls`.
    """
    columns = ["weighted", "at", "minutes", "turns", "toolCalls",
               "subagentTurns", "subagentWeighted"]
    rows = [[3_000_000, _ms_ago(1), 11.0, 60, 60, 0, 0]]
    value, _ = goal_measures.measure_nova_cost_per_cycle(
        None, None, ledger=_ledger(rows, columns))
    assert value == 3.0


def test_cost_per_cycle_refuses_rather_than_reading_zero_on_an_empty_window():
    rows = [[_ms_ago(48), 11.0, 60, 60, 1_000_000, 0, 0]]
    value, detail = goal_measures.measure_nova_cost_per_cycle(
        None, None, ledger=_ledger(rows))
    # A 0 here would be written into the document as the cheapest cycles ever
    # run, off an instrument that saw no cycle at all.
    assert value is None
    assert "no median to take" in detail


def test_cost_per_cycle_names_a_ledger_it_could_not_read(monkeypatch):
    monkeypatch.setattr(goal_measures, "fetch_cost_ledger",
                        lambda *a, **k: (None, "could not read /api/costs: 503"))
    value, detail = goal_measures.measure_nova_cost_per_cycle(None, None)
    assert value is None
    assert "503" in detail


def test_cost_per_cycle_reports_delegation_without_adding_it():
    """A parent's `weighted` does not absorb its subagents' -- so say so.

    Folding them in would push the reading toward `high` for a definitional
    reason, against bounds set on the narrower measure, and rule 4 forbids
    moving the bounds to fit. The gap is printed instead.
    """
    rows = [
        [_ms_ago(1), 11.0, 60, 60, 1_000_000, 3, 500_000],
        [_ms_ago(2), 11.0, 60, 60, 1_000_000, 0, 0],
    ]
    value, detail = goal_measures.measure_nova_cost_per_cycle(
        None, None, ledger=_ledger(rows))
    assert value == 1.0
    assert "0.50M on top" in detail
    assert "does NOT include" in detail


def test_cost_per_cycle_calls_a_missing_attribution_unknown_not_zero():
    """Rows written before 2026-08-19 carry no subagent cost key at all.

    `nova_costs._subagent` is explicit that those are the absence of an
    instrument rather than a measurement of nothing, so they are counted
    separately instead of joining the "delegated nothing" pile.
    """
    rows = [[_ms_ago(1), 11.0, 60, 60, 1_000_000, 0, None]]
    value, detail = goal_measures.measure_nova_cost_per_cycle(
        None, None, ledger=_ledger(rows))
    assert value == 1.0
    assert "unknown rather than zero" in detail


def test_cost_per_cycle_is_wired_into_the_kpi_map():
    """A measurer nothing calls is not an instrument.

    `kpi_rows` reads `KPI_MEASURERS`, so deleting this entry leaves every test
    above green while the document goes back to a hand-typed number.
    """
    assert goal_measures.KPI_MEASURERS["nova-kpi-cost-per-cycle"] is \
        goal_measures.measure_nova_cost_per_cycle
    # And it must no longer claim to have no instrument.
    assert "nova-kpi-cost-per-cycle" not in goal_measures.KPI_NO_INSTRUMENT


def _pm_stub(monkeypatch, results, conversations, error=None):
    from tools import cycle_postmortem
    monkeypatch.setattr(
        cycle_postmortem, "collect",
        lambda *a, **k: (results, max(conversations or [0]), error,
                         conversations, []))
    return cycle_postmortem


def _conv(minutes_ago):
    from datetime import datetime, timedelta, timezone
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {"createdAt": when.isoformat().replace("+00:00", "Z")}


def test_measure_nova_silent_cycles_counts_only_cycles_inside_the_window(monkeypatch):
    """A silent cycle from last week is history, not today's guardrail."""
    # 10 is five minutes the wrong side of the 24h cutoff and 11 is an hour
    # the right side of it, so the boundary itself is what separates them --
    # a cutoff nudged by an hour in either direction changes the answer.
    conversations = {10: _conv(60 * 24 + 5), 11: _conv(60 * 23), 12: _conv(20),
                     13: _conv(10)}
    results = [
        {"number": 10, "verdict": "silent"},   # outside the 24h window
        {"number": 11, "verdict": "silent"},
        {"number": 12, "verdict": "lost"},
    ]
    _pm_stub(monkeypatch, results, conversations)
    value, detail = goal_measures.measure_nova_silent_cycles(None, None)
    assert value == 2
    assert "2 of 3 cycle(s)" in detail
    assert "11 silent" in detail and "12 lost" in detail
    assert "10 silent" not in detail


def test_measure_nova_silent_cycles_does_not_count_an_entry_that_exists(monkeypatch):
    """`misfiled` and `unnumbered` mean the work IS in the journal.

    They are `lost` downgraded after the search found the entry under another
    number or another name, so counting them counts a cycle that wrote. Same
    for `still running`, which is the newest few overlapping cycles.
    """
    conversations = {20: _conv(40), 21: _conv(30), 22: _conv(20), 23: _conv(5)}
    results = [
        {"number": 20, "verdict": "misfiled"},
        {"number": 21, "verdict": "unnumbered"},
        {"number": 22, "verdict": "still running"},
        {"number": 23, "verdict": "silent"},
    ]
    _pm_stub(monkeypatch, results, conversations)
    value, detail = goal_measures.measure_nova_silent_cycles(None, None)
    assert value == 1
    assert "3 more entryless number(s) are not counted" in detail


def test_measure_nova_silent_cycles_places_an_absent_cycle_by_its_number(monkeypatch):
    """An `absent` cycle has no conversation, so it has no stamp to filter on.

    Cycle numbers are handed out in order, so a number above the lowest one
    that started inside the window started inside it too. Dropping the ones
    with no stamp would silently exclude the single verdict that means no run
    happened at all.
    """
    conversations = {30: _conv(60 * 40), 32: _conv(20)}
    results = [{"number": 31, "verdict": "absent"}, {"number": 33, "verdict": "absent"}]
    _pm_stub(monkeypatch, results, conversations)
    value, _detail = goal_measures.measure_nova_silent_cycles(None, None)
    # 31 sits below the first in-window number (32) and is history; 33 is above.
    assert value == 1


def test_measure_nova_silent_cycles_refuses_rather_than_reading_zero(monkeypatch):
    """No cycle ran at all is a dead loop, not a perfect one."""
    _pm_stub(monkeypatch, [], {40: _conv(60 * 40)})
    value, detail = goal_measures.measure_nova_silent_cycles(None, None)
    assert value is None
    assert "not a clean zero" in detail


def test_measure_nova_silent_cycles_names_an_unread_source(monkeypatch):
    _pm_stub(monkeypatch, [], {}, error="Agora returned 503")
    value, detail = goal_measures.measure_nova_silent_cycles(None, None)
    assert value is None
    assert "503" in detail


def test_silent_cycles_is_wired_into_the_kpi_map():
    """A measurer nothing calls is not an instrument."""
    assert goal_measures.KPI_MEASURERS["nova-kpi-silent-cycles"] is \
        goal_measures.measure_nova_silent_cycles
    assert "nova-kpi-silent-cycles" not in goal_measures.KPI_NO_INSTRUMENT


def _board_stub(monkeypatch, boards, error=None):
    def fake(name, site=None):
        if error:
            return [], error
        return boards.get(name, []), None
    monkeypatch.setattr(goal_measures, "fetch_board", fake)


def test_measure_pm_deprecations_counts_both_boards_inside_the_window(monkeypatch):
    """Both boards retire rows, and the KPI is one number over the pair."""
    _board_stub(monkeypatch, {
        "issues": [{"number": 7, "statusKey": "outdated", "updated": "09-12"}],
        "ideas": [{"number": 295, "statusKey": "outdated", "updated": "2026-08-20"}],
    })
    value, detail = goal_measures.measure_pm_deprecations(None, "2026-09-14")
    assert value == 2
    assert "issues #7" in detail and "ideas #295" in detail


def test_measure_pm_deprecations_ignores_a_row_that_is_not_outdated(monkeypatch):
    """`done` closes a row too and is not a retirement -- issue #225 counts
    what was taken away, not what was delivered."""
    _board_stub(monkeypatch, {
        "issues": [
            {"number": 1, "statusKey": "done", "updated": "09-12"},
            {"number": 2, "statusKey": "backlog", "updated": "09-12"},
            {"number": 3, "statusKey": "outdated", "updated": "09-12"},
        ],
    })
    value, _detail = goal_measures.measure_pm_deprecations(None, "2026-09-14")
    assert value == 1


def test_measure_pm_deprecations_drops_a_row_outside_the_window(monkeypatch):
    """The window IS the unit: `per month` is 30 days, not every row ever
    retired. A row dated on the boundary itself is outside it."""
    _board_stub(monkeypatch, {
        "issues": [
            {"number": 9, "statusKey": "outdated", "updated": "08-15"},
            {"number": 10, "statusKey": "outdated", "updated": "08-16"},
            {"number": 11, "statusKey": "outdated", "updated": "09-14"},
        ],
    })
    value, _detail = goal_measures.measure_pm_deprecations(None, "2026-09-14")
    assert value == 2


def test_measure_pm_deprecations_skips_an_undated_row(monkeypatch):
    """An undated row is not evidence of a retirement inside the window, and
    an unparseable cell must not read as today."""
    _board_stub(monkeypatch, {
        "issues": [
            {"number": 4, "statusKey": "outdated", "updated": ""},
            {"number": 5, "statusKey": "outdated", "updated": "soon"},
            {"number": 6, "statusKey": "outdated", "updated": "09-99"},
        ],
    })
    value, _detail = goal_measures.measure_pm_deprecations(None, "2026-09-14")
    assert value == 0


def test_measure_pm_deprecations_names_an_unread_board(monkeypatch):
    """A board that would not answer is not a month that retired nothing."""
    _board_stub(monkeypatch, {}, error="could not read the board: HTTP 503")
    value, detail = goal_measures.measure_pm_deprecations(None, "2026-09-14")
    assert value is None
    assert "503" in detail


def test_measure_pm_deprecations_says_the_date_is_a_last_touch(monkeypatch):
    """The one thing a reader has to know about this number: nothing records
    when a status moved, so the date is the row's last edit."""
    _board_stub(monkeypatch, {
        "issues": [{"number": 7, "statusKey": "outdated", "updated": "09-12"}],
    })
    _value, detail = goal_measures.measure_pm_deprecations(None, "2026-09-14")
    assert "last touch" in detail


def test_deprecations_is_wired_into_the_kpi_map():
    """A measurer nothing calls is not an instrument."""
    assert goal_measures.KPI_MEASURERS["pm-kpi-deprecations"] is \
        goal_measures.measure_pm_deprecations
    assert "pm-kpi-deprecations" not in goal_measures.KPI_NO_INSTRUMENT


# --- marcus-kpi-push-subscribers -------------------------------------------
#
# The guardrail under Marcus's Notifications and nudges milestone: how many
# devices the 20:00 reminder can actually reach. It had no instrument until
# `GET /api/push/subscribers` was added to Marcus (SokratesAI/marcus#160),
# because the only routes that disclosed the count were the two halves of
# `/api/push/subscribe` and reading it there means mutating the list first.


def _subscribers_stub(monkeypatch, payload, error=None):
    """Stand in for the live pod at `/api/push/subscribers`.

    Patches `_get_json` rather than the fetch helper, so the helper's own
    validation of the payload is the thing under test.
    """
    def fake(url, timeout=60):
        assert url.endswith("/api/push/subscribers"), url
        return (None, error) if error else (payload, None)
    monkeypatch.setattr(goal_measures, "_get_json", fake)


def test_measure_push_subscribers_counts_the_devices(monkeypatch):
    _subscribers_stub(monkeypatch, {"count": 3})
    value, detail = goal_measures.measure_marcus_push_subscribers(None, None)
    assert value == 3
    assert "3 device(s)" in detail


def test_measure_push_subscribers_reads_an_empty_list_as_a_real_zero(monkeypatch):
    """Zero is the reading this most expects to take, and it is a measurement:
    the nightly job runs and delivers to nobody."""
    _subscribers_stub(monkeypatch, {"count": 0})
    value, detail = goal_measures.measure_marcus_push_subscribers(None, None)
    assert value == 0
    assert "nobody" in detail


def test_measure_push_subscribers_never_turns_an_unreachable_pod_into_zero(monkeypatch):
    """The failure that would matter: a pod that will not answer written into
    the document as `now: 0`, which is exactly what a real empty list says."""
    _subscribers_stub(monkeypatch, None, error="could not read ...: HTTP 503")
    value, detail = goal_measures.measure_marcus_push_subscribers(None, None)
    assert value is None
    assert "503" in detail


def test_measure_push_subscribers_refuses_an_answer_that_is_not_a_count(monkeypatch):
    """A route that answers `{"count": "many"}` -- or a boolean, which Python
    would otherwise accept as an int -- is not a reading."""
    for bad in ({}, {"count": "many"}, {"count": None}, {"count": True},
                {"count": -1}, {"count": 2.5}):
        _subscribers_stub(monkeypatch, bad)
        value, detail = goal_measures.measure_marcus_push_subscribers(None, None)
        assert value is None, bad
        assert "non-negative integer" in detail, bad


def test_push_subscribers_is_wired_into_the_kpi_map():
    """A measurer nothing calls is not an instrument."""
    assert goal_measures.KPI_MEASURERS["marcus-kpi-push-subscribers"] is \
        goal_measures.measure_marcus_push_subscribers
    assert "marcus-kpi-push-subscribers" not in goal_measures.KPI_NO_INSTRUMENT


# --- marcus-kpi-coach-latency ----------------------------------------------
#
# How long the coach makes him wait. It carried no instrument for a week with
# the reason "timing it means driving the live coach", which was true about a
# *synthetic* sample and false about a recorded one: Marcus is the process that
# does the waiting, so `GET /api/coach/latency` reports the median of the taps
# it timed itself (SokratesAI/marcus#161).


def _latency_stub(monkeypatch, payload, error=None):
    """Stand in for the live pod at `/api/coach/latency`.

    Patches `_get_json` for the same reason `_subscribers_stub` does: the
    fetch helper's own validation of the payload is part of what is tested.
    """
    def fake(url, timeout=60):
        assert url.endswith("/api/coach/latency"), url
        return (None, error) if error else (payload, None)
    monkeypatch.setattr(goal_measures, "_get_json", fake)


def test_measure_coach_latency_reports_seconds_not_milliseconds(monkeypatch):
    """The KPI's unit is `s` and the route answers `medianMs`."""
    _latency_stub(monkeypatch, {"count": 5, "medianMs": 14_900,
                                "newestAt": "2026-09-14T00:12:00.000Z"})
    value, detail = goal_measures.measure_marcus_coach_latency(None, None)
    assert value == 14.9
    assert "5 answered plan draft(s)" in detail


def test_measure_coach_latency_prints_the_sample_count_beside_the_number(monkeypatch):
    """A median over 1 tap and over 60 are the same number and different
    readings, so the count is never left out of the detail line."""
    _latency_stub(monkeypatch, {"count": 1, "medianMs": 3_000,
                                "newestAt": "2026-09-14T00:12:00.000Z"})
    value, detail = goal_measures.measure_marcus_coach_latency(None, None)
    assert value == 3.0
    assert "1 answered plan draft(s)" in detail
    assert "2026-09-14T00:12:00.000Z" in detail


def test_measure_coach_latency_refuses_to_call_an_empty_history_zero(monkeypatch):
    """The trap this measure has, and it runs the OPPOSITE way from the
    subscriber count's: there 0 is the real reading, here 0 cannot be one. A
    zero-second median would say the coach answers instantly."""
    _latency_stub(monkeypatch, {"count": 0, "medianMs": None, "newestAt": None})
    value, detail = goal_measures.measure_marcus_coach_latency(None, None)
    assert value is None
    assert "no answered plan draft" in detail


def test_measure_coach_latency_never_turns_an_unreachable_pod_into_a_number(monkeypatch):
    _latency_stub(monkeypatch, None, error="could not read ...: HTTP 503")
    value, detail = goal_measures.measure_marcus_coach_latency(None, None)
    assert value is None
    assert "503" in detail


def test_measure_coach_latency_refuses_an_answer_that_is_not_a_summary(monkeypatch):
    """A count that is not a non-negative integer is not a reading -- including
    a boolean, which Python would otherwise accept as an int."""
    for bad in ({}, {"count": "many"}, {"count": None}, {"count": True},
                {"count": -1}, {"count": 2.5}):
        _latency_stub(monkeypatch, bad)
        value, detail = goal_measures.measure_marcus_coach_latency(None, None)
        assert value is None, bad
        assert "non-negative integer" in detail, bad


def test_measure_coach_latency_refuses_samples_with_no_median(monkeypatch):
    """`count` above zero and `medianMs` missing is a broken route, not a
    reading -- and `round(None / 1000)` would be a crash rather than a `None`."""
    for bad in ({"count": 4, "medianMs": None}, {"count": 4, "medianMs": "slow"},
                {"count": 4}):
        _latency_stub(monkeypatch, bad)
        value, detail = goal_measures.measure_marcus_coach_latency(None, None)
        assert value is None, bad
        assert "no numeric `medianMs`" in detail, bad


def test_coach_latency_is_wired_into_the_kpi_map():
    """A measurer nothing calls is not an instrument."""
    assert goal_measures.KPI_MEASURERS["marcus-kpi-coach-latency"] is \
        goal_measures.measure_marcus_coach_latency
    assert "marcus-kpi-coach-latency" not in goal_measures.KPI_NO_INSTRUMENT


def test_every_kpi_in_the_document_now_has_a_measurer():
    """The state this cycle left behind: `KPI_NO_INSTRUMENT` is empty because
    every KPI has an instrument, not because the mechanism was deleted. If a
    later cycle adds a KPI with no measurer, it belongs in that map with its
    reason and this test says so."""
    assert goal_measures.KPI_NO_INSTRUMENT == {}


def _no_outcomes(monkeypatch):
    """Marcus unreachable for the outcomes route, for a test about something else."""
    monkeypatch.setattr(goal_measures, "fetch_marcus_coach_outcomes",
                        lambda site=None: (None, "could not read /api/coach/outcomes"))


def _outcomes(payload, monkeypatch):
    """Stub Marcus's `/api/coach/outcomes` with `payload`."""
    def fake(url, **kwargs):
        assert url.endswith("/api/coach/outcomes"), url
        return payload, None
    monkeypatch.setattr(goal_measures, "_get_json", fake)


def test_coach_first_try_is_the_share_marcus_recorded(monkeypatch):
    _outcomes({"count": 20, "answered": 19, "firstTryPct": 95.0,
               "newestAt": "2026-09-14T00:11:00.000Z",
               "byRoute": {"plan-draft": {"count": 12, "answered": 11},
                           "chat": {"count": 8, "answered": 8}}}, monkeypatch)
    value, detail = goal_measures.measure_marcus_coach_first_try(None, None)
    assert value == 95.0
    # The count travels with the number for `measure_pm_reversals`' reason:
    # 95% over 20 taps and 95% over 200 are the same number and different
    # readings.
    assert "19 of 20 coach call(s)" in detail
    assert "plan-draft 11/12" in detail and "chat 8/8" in detail
    assert "unconfigured or metered refusal" in detail


def test_coach_first_try_with_no_calls_is_never_written_as_zero(monkeypatch):
    # 0% says every tap failed. An empty history says nobody has tapped.
    _outcomes({"count": 0, "answered": 0, "firstTryPct": None,
               "newestAt": None, "byRoute": {}}, monkeypatch)
    value, detail = goal_measures.measure_marcus_coach_first_try(None, None)
    assert value is None
    assert "no coach call yet" in detail
    assert "the route is live" in detail


def test_coach_first_try_refuses_more_answers_than_calls(monkeypatch):
    # A share above 100% is not a reading, and taking `firstTryPct` on trust
    # would write one into the document.
    _outcomes({"count": 3, "answered": 4, "firstTryPct": 133.3,
               "newestAt": None, "byRoute": {}}, monkeypatch)
    value, detail = goal_measures.measure_marcus_coach_first_try(None, None)
    assert value is None
    assert "which cannot be" in detail


def test_coach_first_try_refuses_a_payload_with_no_count(monkeypatch):
    _outcomes({"answered": 2, "firstTryPct": 100.0}, monkeypatch)
    value, detail = goal_measures.measure_marcus_coach_first_try(None, None)
    assert value is None
    assert "non-negative integer `count`" in detail


def test_coach_first_try_refuses_calls_with_no_share(monkeypatch):
    _outcomes({"count": 5, "answered": 5, "firstTryPct": "all of them"},
              monkeypatch)
    value, detail = goal_measures.measure_marcus_coach_first_try(None, None)
    assert value is None
    assert "no numeric `firstTryPct`" in detail


def test_coach_first_try_reaches_the_key_result_row(monkeypatch):
    # The dispatch, not the measurer: a fourth measurer map that `key_result_rows`
    # does not consult reads as "no instrument" and the row stays blank.
    _outcomes({"count": 4, "answered": 4, "firstTryPct": 100.0,
               "newestAt": "2026-09-14T00:11:00.000Z", "byRoute": {}}, monkeypatch)
    out = goal_measures.key_result_rows(
        _marcus_sections(), [], marcus={"sessions": [], "plan": {}},
        since="2026-09-07", until="2026-09-13")
    row = {r["id"]: r for r in out}["marcus-kr-coach-first-try"]
    assert row["value"] == 100.0
    assert "no instrument" not in row["detail"]


# --- nova-kpi-unfixed-advisories -------------------------------------------
#
# The guardrail under `Secrets and safety`, which was a lights-on milestone
# under no KPI at all -- rule 4 of issue #227 says lights-on work sits under a
# guardrail, so an orphan with nothing to name was a gap in the model rather
# than a pruning signal. The check itself has existed since Cycle 397; what did
# not exist was a *number* the goals document carries, so a week with an
# unpatched advisory in it left no trace anywhere he reads.


class _FakeAlerts:
    OK = "ok"

    def __init__(self, repos, results, incomplete=False, orgs=("SokratesAI",)):
        self._repos = repos
        self._results = results
        self._incomplete = incomplete
        self._orgs = orgs
        self.verified = False

    def _repos_to_sweep(self):
        return self._repos, [], [], self._incomplete

    def _orgs_from_workspace(self):
        return self._orgs

    def alerts_for(self, repo):
        return self._results[repo]

    def org_alerts(self, org):
        return {}

    def fold_in_org_only(self, results, org_views):
        return None

    def verify_landed(self, results):
        self.verified = True

    @staticmethod
    def _still_counts(alert):
        return alert["state"] == "open"


def _install_fake_alerts(monkeypatch, fake):
    """Swap the submodule on the `tools` package, not only in `sys.modules`.

    `from tools import security_alerts` inside the measurer reads the attribute
    off the already-imported package, so a `sys.modules` entry alone is a fake
    the code under test never sees -- and it fails by silently measuring
    production, which is a green test that proves nothing.
    """
    import sys
    import tools
    import tools.security_alerts  # noqa: F401 -- makes the attribute exist
    monkeypatch.setattr(tools, "security_alerts", fake)
    monkeypatch.setitem(sys.modules, "tools.security_alerts", fake)


def _alert(package, severity="high", state="open", landed=False):
    return {"package": package, "severity": severity, "state": state,
            "landed": landed}


def test_unfixed_advisories_counts_only_what_is_still_open_and_unpatched(
        monkeypatch):
    """A dismissed alert and one already fixed on main are both not work."""
    fake = _FakeAlerts(
        ["SokratesAI/a", "SokratesAI/b"],
        {"SokratesAI/a": ("ok", [_alert("brace-expansion"),
                                 _alert("js-yaml", landed=True)]),
         "SokratesAI/b": ("ok", [_alert("tar", state="dismissed")])},
    )
    _install_fake_alerts(monkeypatch, fake)
    value, detail = gm.measure_nova_unfixed_advisories(None, None)
    assert value == 1
    assert "brace-expansion" in detail
    assert "js-yaml" not in detail
    assert fake.verified, "the landed check has to run before anything is counted"


def test_unfixed_advisories_calls_a_repo_it_cannot_ask_a_floor_not_a_zero(
        monkeypatch):
    """Dependabot switched off is no instrument, and must not read as clean."""
    fake = _FakeAlerts(
        ["SokratesAI/a", "SokratesAI/vault"],
        {"SokratesAI/a": ("ok", []),
         "SokratesAI/vault": ("disabled", [])},
    )
    _install_fake_alerts(monkeypatch, fake)
    value, detail = gm.measure_nova_unfixed_advisories(None, None)
    assert value == 0
    assert "1 of 2 repo(s)" in detail
    assert "a floor, not a total" in detail
    assert "SokratesAI/vault" in detail


def test_unfixed_advisories_refuses_a_sweep_that_lost_its_own_repo_list(
        monkeypatch):
    """An unknown denominator is not a floor over anything."""
    fake = _FakeAlerts(["SokratesAI/a"], {"SokratesAI/a": ("ok", [])},
                       incomplete=True)
    _install_fake_alerts(monkeypatch, fake)
    value, detail = gm.measure_nova_unfixed_advisories(None, None)
    assert value is None
    assert "enumerate" in detail


def test_unfixed_advisories_is_wired_into_the_kpi_map():
    """A measurer nothing calls is not an instrument."""
    assert goal_measures.KPI_MEASURERS["nova-kpi-unfixed-advisories"] is \
        goal_measures.measure_nova_unfixed_advisories
    assert "nova-kpi-unfixed-advisories" not in goal_measures.KPI_NO_INSTRUMENT


# --- nova-kpi-markdown-board-readers ---------------------------------------
#
# The guardrail under `Board store and growth`, the second Nova lights-on
# milestone that named nothing. It is a ratchet rather than a target: the
# migration itself is a milestone with a definition of done, and what no
# milestone catches is a twelfth markdown reader written while the eleven are
# still there.


class _FakeInventory:
    def __init__(self, found, unreadable=(), untokenized=(), mine=(),
                 vetoed=()):
        self._found = found
        self._unreadable = list(unreadable)
        self._untokenized = list(untokenized)
        self._mine = list(mine)
        self._vetoed = list(vetoed)
        self.include_tests = None

    def scan(self, root=None, include_tests=None):
        self.include_tests = include_tests
        return (self._found, [], self._unreadable, self._untokenized,
                self._mine, self._vetoed)


def _install_fake_inventory(monkeypatch, fake):
    """Same swap and the same reason as `_install_fake_alerts` above."""
    import sys
    import tools
    import tools.board_reader_inventory  # noqa: F401 -- attribute must exist
    monkeypatch.setattr(tools, "board_reader_inventory", fake)
    monkeypatch.setitem(sys.modules, "tools.board_reader_inventory", fake)


def test_markdown_board_readers_counts_the_modules_the_inventory_found(
        monkeypatch):
    fake = _FakeInventory({"agora_runner/nova_boards.py": 1,
                           "tools/board_publish.py": 2})
    _install_fake_inventory(monkeypatch, fake)
    value, detail = gm.measure_nova_markdown_board_readers(None, None)
    assert value == 2
    assert "agora_runner/nova_boards.py" in detail
    assert "must never rise" in detail


def test_markdown_board_readers_never_counts_tests(monkeypatch):
    """`parse_board`'s own tests must not make the gate unclearable."""
    fake = _FakeInventory({"agora_runner/nova_boards.py": 1})
    _install_fake_inventory(monkeypatch, fake)
    gm.measure_nova_markdown_board_readers(None, None)
    assert fake.include_tests is False


def test_markdown_board_readers_refuses_a_scan_it_could_not_complete(
        monkeypatch):
    """A count over an unknown set of files is not a reading."""
    fake = _FakeInventory({"agora_runner/nova_boards.py": 1},
                          untokenized=["tools/odd.py"])
    _install_fake_inventory(monkeypatch, fake)
    value, detail = gm.measure_nova_markdown_board_readers(None, None)
    assert value is None
    assert "unknown set" in detail


def test_markdown_board_readers_is_wired_into_the_kpi_map():
    """A measurer nothing calls is not an instrument."""
    assert goal_measures.KPI_MEASURERS["nova-kpi-markdown-board-readers"] is \
        goal_measures.measure_nova_markdown_board_readers
    assert "nova-kpi-markdown-board-readers" not in \
        goal_measures.KPI_NO_INSTRUMENT
# --- marcus-kpi-browser-monolith -------------------------------------------
#
# The guardrail under `Codebase health`, the last lights-on milestone on
# either board that named no KPI at all. A ratchet on the biggest hand-written
# browser file, because Marcus has no bundler by choice (idea #213) and what
# no milestone catches is that file growing back.


class _FakeRun:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _fake_gh(monkeypatch, result, record=None):
    def run(argv, **kwargs):
        if record is not None:
            record.append(argv)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(gm.subprocess, "run", run)


def test_browser_monolith_reads_the_largest_js_file_in_kilobytes(monkeypatch):
    _fake_gh(monkeypatch, _FakeRun(json.dumps([
        {"name": "app.js", "size": 292126},
        {"name": "app-core.js", "size": 169355},
        {"name": "sw.js", "size": 5705},
    ])))
    value, detail = gm.measure_marcus_browser_monolith(None, None)
    assert value == 292
    assert "app.js is 292KB" in detail
    assert "must never rise" in detail
    assert "app-core.js 169KB" in detail


def test_browser_monolith_ignores_files_that_are_not_javascript(monkeypatch):
    """A 900KB sprite sheet is not a subsystem hiding in one file."""
    _fake_gh(monkeypatch, _FakeRun(json.dumps([
        {"name": "sprites.png", "size": 900000},
        {"name": "styles.css", "size": 400000},
        {"name": "app.js", "size": 120000},
    ])))
    value, detail = gm.measure_marcus_browser_monolith(None, None)
    assert value == 120
    assert "app.js" in detail
    assert "sprites.png" not in detail


def test_browser_monolith_lists_public_without_recursing(monkeypatch):
    """A vendored bundle under public/vendor/ must not decide this number."""
    calls = []
    _fake_gh(monkeypatch, _FakeRun(json.dumps([{"name": "app.js",
                                                "size": 1000}])), calls)
    gm.measure_marcus_browser_monolith(None, None)
    argv = calls[0]
    assert argv[:2] == ["gh", "api"]
    assert argv[2] == "repos/SokratesAI/marcus/contents/public"
    assert not any("recursive" in str(a) or "git/trees" in str(a)
                   for a in argv)


def test_browser_monolith_returns_nothing_when_gh_fails(monkeypatch):
    """A failed read is not zero kilobytes of front end."""
    _fake_gh(monkeypatch, _FakeRun("", returncode=1, stderr="Not Found"))
    value, detail = gm.measure_marcus_browser_monolith(None, None)
    assert value is None
    assert "Not Found" in detail


def test_browser_monolith_returns_nothing_when_the_directory_holds_no_js(
        monkeypatch):
    """public/ emptied of JS is a moved directory, not a 0KB reading."""
    _fake_gh(monkeypatch, _FakeRun(json.dumps([{"name": "index.html",
                                                "size": 4000}])))
    value, detail = gm.measure_marcus_browser_monolith(None, None)
    assert value is None
    assert "moved directory" in detail


def test_browser_monolith_is_wired_into_the_kpi_map():
    """A measurer nothing calls is not an instrument."""
    assert goal_measures.KPI_MEASURERS["marcus-kpi-browser-monolith"] is \
        goal_measures.measure_marcus_browser_monolith
    assert "marcus-kpi-browser-monolith" not in goal_measures.KPI_NO_INSTRUMENT


# --- agora-kpi-metered-spend ------------------------------------------------

_CATALOG = [
    {"id": "anthropic:claude-opus-5", "provider": "anthropic", "metered": True},
    {"id": "claude-cli:claude-opus-5", "provider": "claude-cli", "metered": False},
    {"id": "gemini:gemini-3.6-flash", "provider": "gemini", "metered": False},
]


def _agora_stub(monkeypatch, *, catalog=None, personas=(), heartbeats=(),
                conversations=(), error_on=None):
    """Stand in for Agora's four reads, one payload each."""
    bodies = {
        "/models": catalog if catalog is not None else _CATALOG,
        "/personas": list(personas),
        "/heartbeats": list(heartbeats),
        "/conversations?active=true": list(conversations),
    }

    def fake(url, timeout=60):
        for suffix, body in bodies.items():
            if url.endswith(suffix):
                if error_on and url.endswith(error_on):
                    return None, f"could not read {url}: HTTP 503"
                return body, None
        raise AssertionError(url)
    monkeypatch.setattr(goal_measures, "_get_json", fake)


def test_metered_spend_reads_a_clean_estate_as_a_real_zero(monkeypatch):
    """Zero is the state rule 9 says this must be in, so it is a measurement
    and not a blank -- the same shape as an empty push list."""
    _agora_stub(
        monkeypatch,
        personas=[{"name": "Nova", "model": "claude-cli:claude-opus-5"}],
        heartbeats=[{"name": "Nova", "enabled": True}],
        conversations=[{"name": "Nova — Cycle 1", "model": "claude-cli:claude-opus-5",
                        "personas": [{"name": "Nova", "model": "claude-cli:claude-opus-5"}]}],
    )
    value, detail = goal_measures.measure_agora_metered_spend(None, None)
    assert value == 0
    assert "nothing in Agora" in detail


def test_metered_spend_finds_a_metered_persona(monkeypatch):
    """The positive result this has to be able to produce: without it, the 0
    above is guaranteed in advance and measures nothing."""
    _agora_stub(monkeypatch,
                personas=[{"name": "Spendy", "model": "anthropic:claude-opus-5"}])
    value, detail = goal_measures.measure_agora_metered_spend(None, None)
    assert value == 1
    assert "persona Spendy -> anthropic:claude-opus-5" in detail


def test_metered_spend_finds_a_conversations_own_model_and_its_persona_link(monkeypatch):
    """A conversation carries its own model AND one per persona link, and the
    link is what actually runs -- a sweep of personas alone would see neither."""
    _agora_stub(monkeypatch, conversations=[{
        "name": "Trial", "model": "anthropic:claude-opus-5",
        "personas": [{"name": "Helper", "model": "anthropic:claude-sonnet-5"}],
    }])
    value, detail = goal_measures.measure_agora_metered_spend(None, None)
    assert value == 2
    assert "conversation Trial -> anthropic:claude-opus-5" in detail
    assert "conversation Trial / Helper -> anthropic:claude-sonnet-5" in detail


def test_metered_spend_ignores_what_cannot_spend(monkeypatch):
    """A disabled heartbeat and an archived conversation never run."""
    _agora_stub(
        monkeypatch,
        heartbeats=[{"name": "Old trial", "enabled": False,
                     "model": "anthropic:claude-opus-5"}],
        conversations=[{"name": "Archived", "archived": True,
                        "model": "anthropic:claude-opus-5"}],
    )
    value, detail = goal_measures.measure_agora_metered_spend(None, None)
    assert value == 0, detail


def test_metered_spend_matches_a_model_id_the_catalog_has_retired(monkeypatch):
    """Matching on the provider rather than the id: a config naming a model
    Agora no longer lists still spends the same balance."""
    _agora_stub(monkeypatch,
                personas=[{"name": "Stale", "model": "anthropic:claude-opus-3"}])
    value, _ = goal_measures.measure_agora_metered_spend(None, None)
    assert value == 1


def test_metered_spend_is_never_blinder_than_the_refusal_it_watches(monkeypatch):
    """If Agora's catalog stopped flagging `anthropic` as metered, the guard in
    `reply.py` would still refuse it -- so this must still count it."""
    from agora_runner.reply import METERED_PROVIDERS
    assert "anthropic" in METERED_PROVIDERS
    _agora_stub(
        monkeypatch,
        catalog=[{"id": "anthropic:claude-opus-5", "provider": "anthropic",
                  "metered": False}],
        personas=[{"name": "Spendy", "model": "anthropic:claude-opus-5"}],
    )
    value, _ = goal_measures.measure_agora_metered_spend(None, None)
    assert value == 1


def test_metered_spend_refuses_a_catalog_with_no_metered_provider_at_all(monkeypatch):
    """An empty catalog would make every estate look clean. `reply` still names
    one, so this only fires when that tuple is emptied too."""
    monkeypatch.setattr("agora_runner.reply.METERED_PROVIDERS", ())
    _agora_stub(monkeypatch, catalog=[])
    value, detail = goal_measures.measure_agora_metered_spend(None, None)
    assert value is None
    assert "no metered provider at all" in detail


def test_metered_spend_never_turns_an_unreadable_agora_into_zero(monkeypatch):
    """`None` here means only that Agora could not be read. A breach has to
    come back as a number above the ceiling, because `has_drifted` treats
    `None` as no drift and the sweep would go silent on it."""
    for dead in ("/models", "/personas", "/heartbeats", "?active=true"):
        _agora_stub(monkeypatch,
                    personas=[{"name": "Nova", "model": "claude-cli:claude-opus-5"}],
                    error_on=dead)
        value, detail = goal_measures.measure_agora_metered_spend(None, None)
        assert value is None, dead
        assert "503" in detail, dead


def test_metered_spend_is_wired_into_the_kpi_map():
    """A measurer nothing calls is not an instrument."""
    assert goal_measures.KPI_MEASURERS["agora-kpi-metered-spend"] is \
        goal_measures.measure_agora_metered_spend
    assert "agora-kpi-metered-spend" not in goal_measures.KPI_NO_INSTRUMENT


# --- docs-kpi-staleness -----------------------------------------------------

def _docs_stub(monkeypatch, stamp=None, error=None):
    monkeypatch.setattr(goal_measures, "fetch_docs_last_commit",
                        lambda *a, **k: (stamp, error))
    monkeypatch.setattr(goal_measures, "today_oslo", lambda *a, **k: "2026-09-14")


def test_docs_staleness_counts_days_from_the_newest_commit(monkeypatch):
    _docs_stub(monkeypatch, stamp="2026-09-12T16:20:48Z")
    value, detail = goal_measures.measure_docs_staleness(None, None)
    assert value == 2
    assert "2026-09-12" in detail


def test_docs_staleness_dates_the_commit_in_oslo_not_utc(monkeypatch):
    """22:30 UTC on the 11th is 00:30 Oslo on the 12th. Reading the `Z`
    timestamp's own date prefix would call this 3 days stale, not 2."""
    _docs_stub(monkeypatch, stamp="2026-09-11T22:30:00Z")
    value, _ = goal_measures.measure_docs_staleness(None, None)
    assert value == 2


def test_docs_staleness_reads_a_commit_today_as_a_real_zero(monkeypatch):
    """The low bound is 0, so an unreadable repo must not be able to look
    fresher than a repo that changed this morning."""
    _docs_stub(monkeypatch, stamp="2026-09-14T06:00:00Z")
    value, detail = goal_measures.measure_docs_staleness(None, None)
    assert value == 0
    assert detail


def test_docs_staleness_never_turns_an_unreadable_repo_into_zero(monkeypatch):
    _docs_stub(monkeypatch, error="gh api failed on SokratesAI/sokrates-docs: 404")
    value, detail = goal_measures.measure_docs_staleness(None, None)
    assert value is None
    assert "404" in detail


def test_docs_staleness_refuses_a_commit_dated_in_the_future(monkeypatch):
    """A negative number of days is a clock problem wearing a reading."""
    _docs_stub(monkeypatch, stamp="2026-09-20T06:00:00Z")
    value, detail = goal_measures.measure_docs_staleness(None, None)
    assert value is None
    assert "future" in detail


def test_docs_staleness_refuses_an_unparseable_commit_date(monkeypatch):
    _docs_stub(monkeypatch, stamp="last tuesday")
    value, detail = goal_measures.measure_docs_staleness(None, None)
    assert value is None
    assert "unreadable date" in detail


def test_docs_staleness_is_wired_into_the_kpi_map():
    assert goal_measures.KPI_MEASURERS["docs-kpi-staleness"] is \
        goal_measures.measure_docs_staleness


# --- post-kpi-volume --------------------------------------------------------

def _articles(days):
    out = []
    for day, count in days.items():
        for _ in range(count):
            out.append({"published_at": f"{day}T09:00:00Z"})
    return out


def _post_stub(monkeypatch, articles=None, error=None, today="2026-09-14"):
    payload = None if error else {"articles": articles or []}
    monkeypatch.setattr(goal_measures, "_get_json",
                        lambda url, timeout=60: (payload, error))
    monkeypatch.setattr(goal_measures, "today_oslo", lambda *a, **k: today)


SEVEN_DAYS = {"2026-09-07": 109, "2026-09-08": 118, "2026-09-09": 132,
              "2026-09-10": 132, "2026-09-11": 121, "2026-09-12": 95,
              "2026-09-13": 82}


def test_post_volume_averages_the_seven_complete_days_to_yesterday(monkeypatch):
    _post_stub(monkeypatch, _articles(SEVEN_DAYS))
    value, detail = goal_measures.measure_post_volume(None, None)
    assert value == 113
    assert "2026-09-07..2026-09-13" in detail


def test_post_volume_leaves_todays_partial_day_out(monkeypatch):
    """At 17:00 Oslo the Post had printed 2 articles against a seven-day floor
    of 82. Counting today would write `2` into a document bounded 10..60 and
    call the paper dead."""
    with_today = dict(SEVEN_DAYS)
    with_today["2026-09-14"] = 2
    _post_stub(monkeypatch, _articles(with_today))
    value, _ = goal_measures.measure_post_volume(None, None)
    assert value == 113


def test_post_volume_leaves_out_a_day_older_than_the_window(monkeypatch):
    older = dict(SEVEN_DAYS)
    older["2026-09-06"] = 5000
    _post_stub(monkeypatch, _articles(older))
    value, _ = goal_measures.measure_post_volume(None, None)
    assert value == 113


def test_post_volume_counts_a_silent_day_as_a_zero_not_as_a_missing_day(monkeypatch):
    """The divisor is always seven. Dividing by the days that happened to
    carry an article would hide a stopped timer."""
    quiet = {"2026-09-13": 70}
    _post_stub(monkeypatch, _articles(quiet))
    value, detail = goal_measures.measure_post_volume(None, None)
    assert value == 10
    assert "0, 0, 0, 0, 0, 0, 70" in detail


def test_post_volume_buckets_an_article_by_its_oslo_day(monkeypatch):
    """23:30 UTC on the 13th is 01:30 Oslo on the 14th -- today, and therefore
    outside the window.

    Seventy of them rather than one, because one article rounds to 0 a day
    under either reading and a test whose answer does not move is not a test.
    Bucketing on the `Z` date prefix puts all seventy inside the window and
    reads 10 a day."""
    _post_stub(monkeypatch, [{"published_at": "2026-09-13T23:30:00Z"}] * 70)
    value, _ = goal_measures.measure_post_volume(None, None)
    assert value == 0


def test_post_volume_says_so_when_an_article_carries_no_date(monkeypatch):
    articles = _articles(SEVEN_DAYS) + [{"title_en": "undated"}]
    _post_stub(monkeypatch, articles)
    value, detail = goal_measures.measure_post_volume(None, None)
    assert value == 113
    assert "1 article(s) carry no publication date" in detail
    assert "floor" in detail


def test_post_volume_never_turns_an_unreachable_app_into_zero(monkeypatch):
    _post_stub(monkeypatch, error="could not read /api/articles: 503")
    value, detail = goal_measures.measure_post_volume(None, None)
    assert value is None
    assert "503" in detail


def test_post_volume_refuses_a_payload_that_is_not_a_list_of_articles(monkeypatch):
    monkeypatch.setattr(goal_measures, "_get_json",
                        lambda url, timeout=60: ({"articles": {"oops": 1}}, None))
    value, detail = goal_measures.measure_post_volume(None, None)
    assert value is None
    assert "nothing here to count" in detail


def test_post_volume_is_wired_into_the_kpi_map():
    assert goal_measures.KPI_MEASURERS["post-kpi-volume"] is \
        goal_measures.measure_post_volume


# --- nas-kpi-services-down --------------------------------------------------

def _nas_stub(monkeypatch, down=0, judged=4, error=None):
    from tools import nas_health
    monkeypatch.setattr(nas_health, "services_down",
                        lambda *a, **k: (down, judged, error))


def test_nas_services_down_reads_a_healthy_box_as_a_real_zero(monkeypatch):
    """The ceiling is 0, so `None` here would read exactly like a clean box."""
    _nas_stub(monkeypatch, down=0, judged=4)
    value, detail = goal_measures.measure_nas_services_down(None, None)
    assert value == 0
    assert "all 4" in detail


def test_nas_services_down_counts_the_services_that_did_not_answer(monkeypatch):
    _nas_stub(monkeypatch, down=2, judged=4)
    value, detail = goal_measures.measure_nas_services_down(None, None)
    assert value == 2
    assert "2 of 4" in detail


def test_nas_services_down_never_turns_a_pod_that_could_not_look_into_zero(monkeypatch):
    _nas_stub(monkeypatch, down=None, judged=0,
              error="this pod cannot make the SSH hop")
    value, detail = goal_measures.measure_nas_services_down(None, None)
    assert value is None
    assert "SSH hop" in detail


def test_nas_services_down_refuses_a_sweep_that_judged_nothing(monkeypatch):
    _nas_stub(monkeypatch, down=0, judged=0)
    value, detail = goal_measures.measure_nas_services_down(None, None)
    assert value is None
    assert "nothing to count" in detail


def test_nas_services_down_is_wired_into_the_kpi_map():
    assert goal_measures.KPI_MEASURERS["nas-kpi-services-down"] is \
        goal_measures.measure_nas_services_down


# --- infra-kpi-ci-minutes ---------------------------------------------------

class _CIStub:
    ORG = "SokratesAI"

    def __init__(self, private, public, unknown=None, raises=None):
        self._split = (Counter(private), Counter(public), Counter(unknown or {}), 0.0)
        self._raises = raises

    def fetch_usage(self, org, year, month):
        if self._raises:
            raise RuntimeError(self._raises)
        return []

    def fetch_visibility(self, org):
        return {}

    def split_minutes(self, items, visibility):
        return self._split

    def month_progress(self, now):
        return 13.6, 30.0


def _stub_tool(monkeypatch, name, stub):
    """Swap `tools.<name>` for `stub`, both ways a `from` import can find it.

    `from tools import ci_minutes` reads the attribute off the already-imported
    `tools` package before it ever looks in `sys.modules`, so patching only the
    module table leaves the real module in place and the test passes against
    production code that went and swept the org.
    """
    monkeypatch.setitem(sys.modules, "tools." + name, stub)
    monkeypatch.setattr(tools, name, stub, raising=False)


def _ci_stub(monkeypatch, stub):
    _stub_tool(monkeypatch, "ci_minutes", stub)


def test_ci_minutes_counts_only_the_private_repos(monkeypatch):
    """The high bound is the *private* allowance, so folding in free public
    minutes would make the guardrail fire on spend that costs nothing."""
    _ci_stub(monkeypatch, _CIStub({"a": 300.0, "b": 158.0}, {"docs": 9000.0}))
    value, detail = goal_measures.measure_infra_ci_minutes(None, None)
    assert value == 458
    assert "9000 public minute(s) are free" in detail


def test_ci_minutes_reads_a_quiet_month_as_a_real_zero(monkeypatch):
    """The low bound is 0 and a month with no billable run is a real month."""
    _ci_stub(monkeypatch, _CIStub({}, {}))
    value, detail = goal_measures.measure_infra_ci_minutes(None, None)
    assert value == 0
    assert detail


def test_ci_minutes_says_so_when_a_repo_has_no_visibility(monkeypatch):
    _ci_stub(monkeypatch, _CIStub({"a": 10.0}, {}, unknown={"ghost": 40.0}))
    value, detail = goal_measures.measure_infra_ci_minutes(None, None)
    assert value == 10
    assert "floor" in detail and "40 minute(s)" in detail


def test_ci_minutes_never_turns_an_unreadable_api_into_zero(monkeypatch):
    _ci_stub(monkeypatch, _CIStub({}, {}, raises="gh api failed: 403"))
    value, detail = goal_measures.measure_infra_ci_minutes(None, None)
    assert value is None
    assert "403" in detail


def test_ci_minutes_is_wired_into_the_kpi_map():
    assert goal_measures.KPI_MEASURERS["infra-kpi-ci-minutes"] is \
        goal_measures.measure_infra_ci_minutes


# --- infra-kpi-node-headroom ------------------------------------------------

class _NodeStub:
    MIB = 1024.0 ** 2

    def __init__(self, readings, names=None, fails=None):
        self._readings = readings
        self._names = names if names is not None else list(readings)
        self._fails = fails or {}

    def read_node_names(self, **kwargs):
        if self._names is None:
            raise OSError("kubectl get nodes: connection refused")
        return list(self._names)

    def read_summary(self, node, **kwargs):
        if node in self._fails:
            raise OSError(self._fails[node])
        return {"node": {"memory": self._readings[node]}}

    @staticmethod
    def node_memory(summary):
        memory = (summary.get("node") or {}).get("memory") or {}
        if memory.get("availableBytes") is None:
            return None
        return memory["availableBytes"], 0


def _node_stub(monkeypatch, stub):
    _stub_tool(monkeypatch, "node_memory", stub)


def _mib(n):
    return {"availableBytes": int(n * 1024 * 1024)}


def test_node_headroom_reports_the_tightest_node_not_the_total(monkeypatch):
    """Two nodes at 8000Mi and 100Mi have the headroom of the 100Mi one."""
    _node_stub(monkeypatch, _NodeStub(
        {"server1": _mib(4222), "server2": _mib(4670)}))
    value, detail = goal_measures.measure_infra_node_headroom(None, None)
    assert value == 4222
    assert "server1" in detail and "server2 4670Mi" in detail


def test_node_headroom_refuses_when_a_node_could_not_be_read(monkeypatch):
    """A minimum over the half that answered can only read HIGHER than the
    truth, which is the flattering direction on a low-bounded guardrail."""
    _node_stub(monkeypatch, _NodeStub(
        {"server1": _mib(4222), "server2": _mib(80)},
        fails={"server2": "nodes/proxy: 503"}))
    value, detail = goal_measures.measure_infra_node_headroom(None, None)
    assert value is None
    assert "503" in detail


def test_node_headroom_refuses_a_kubelet_that_reported_no_available(monkeypatch):
    _node_stub(monkeypatch, _NodeStub({"server1": {"workingSetBytes": 1}}))
    value, detail = goal_measures.measure_infra_node_headroom(None, None)
    assert value is None
    assert "availableBytes" in detail


def test_node_headroom_refuses_an_empty_node_list(monkeypatch):
    _node_stub(monkeypatch, _NodeStub({}, names=[]))
    value, detail = goal_measures.measure_infra_node_headroom(None, None)
    assert value is None
    assert "no nodes" in detail


def test_node_headroom_is_wired_into_the_kpi_map():
    assert goal_measures.KPI_MEASURERS["infra-kpi-node-headroom"] is \
        goal_measures.measure_infra_node_headroom


# --- maint-kr-supported and maint-kpi-eol-unjudged --------------------------

def _eol_image(image, tag, verdict=None, kind="image"):
    return {"kind": kind, "image": image, "tag": tag, "verdict": verdict,
            "days": 0, "product": image, "eol": "2025-05-05"}


class _EolStub:
    DEFAULT_WITHIN_DAYS = 180

    def __init__(self, judged, not_judged, repos=("SokratesAI/agora",),
                 incomplete=False, products=({},), catalogue_why=None):
        self._judged = judged
        self._not_judged = not_judged
        self._repos = list(repos)
        self._incomplete = incomplete
        self._products = products[0]
        self._why = catalogue_why
        self.sweeps = 0

    def _repos_to_sweep(self):
        return self._repos, [], [], self._incomplete

    def catalogue(self):
        if self._why:
            return None, self._why
        return self._products, None

    def sweep(self, repos, products, today, within_days):
        self.sweeps += 1
        return list(self._judged), list(self._not_judged), []

    def image_map(self, products):
        return {}, {}

    def cluster_images(self):
        return [], []

    def judge(self, *a, **k):
        raise AssertionError("no cluster image was handed in")

    @staticmethod
    def group(images):
        groups = {}
        for image in images:
            groups.setdefault(
                (image.get("kind", "image"), image["image"], image["tag"]), []
            ).append(image)
        return groups

    @staticmethod
    def _pin(image):
        return "%s:%s" % (image["image"], image["tag"] or "")


def _eol_stub(monkeypatch, stub):
    monkeypatch.setattr(goal_measures, "_EOL_SWEEP", {})
    _stub_tool(monkeypatch, "eol_watch", stub)
    return stub


def test_maint_supported_counts_distinct_lines_not_occurrences(monkeypatch):
    """One dead base image named in six Dockerfile stages is one line of work,
    not six -- a count of occurrences measures our file layout."""
    dead = [_eol_image("couchdb", "3.3", "eol") for _ in range(6)]
    _eol_stub(monkeypatch, _EolStub(
        dead + [_eol_image("node", "22", "supported")], []))
    value, detail = goal_measures.measure_maint_supported(None, None)
    assert value == 1
    assert "couchdb:3.3" in detail


def test_maint_supported_counts_a_line_going_dead_soon(monkeypatch):
    _eol_stub(monkeypatch, _EolStub(
        [_eol_image("couchdb", "3.3", "eol"),
         _eol_image("prom/prometheus", "v3.14.0", "soon"),
         _eol_image("node", "22", "supported")], []))
    value, _ = goal_measures.measure_maint_supported(None, None)
    assert value == 2


def test_maint_supported_does_not_count_a_line_nothing_could_judge(monkeypatch):
    """Unjudged is `maint-kpi-eol-unjudged`'s number. Folding it in here would
    make an estate nothing can read look supported."""
    _eol_stub(monkeypatch, _EolStub(
        [_eol_image("node", "22", "supported")],
        [_eol_image("go", "1.27"), _eol_image("nginx", "1.31")]))
    value, _ = goal_measures.measure_maint_supported(None, None)
    assert value == 0


def test_maint_eol_unjudged_counts_distinct_unreadable_lines(monkeypatch):
    _eol_stub(monkeypatch, _EolStub(
        [_eol_image("node", "22", "supported")],
        [_eol_image("go", "1.27"), _eol_image("go", "1.27"),
         _eol_image("nginx", "1.31")]))
    value, detail = goal_measures.measure_maint_eol_unjudged(None, None)
    assert value == 2
    assert "2 distinct line(s)" in detail


def test_maint_eol_unjudged_reads_a_fully_judged_estate_as_zero(monkeypatch):
    _eol_stub(monkeypatch, _EolStub([_eol_image("node", "22", "supported")], []))
    value, _ = goal_measures.measure_maint_eol_unjudged(None, None)
    assert value == 0


def test_maint_eol_numbers_come_from_one_sweep(monkeypatch):
    """The sweep lists every repo in the org and asks endoflife.date about
    every line in it. Two numbers, one sweep, or the report doubles its own
    slowest call to answer a question that cannot have two answers."""
    stub = _eol_stub(monkeypatch, _EolStub(
        [_eol_image("couchdb", "3.3", "eol")], [_eol_image("go", "1.27")]))
    assert goal_measures.measure_maint_supported(None, None)[0] == 1
    assert goal_measures.measure_maint_eol_unjudged(None, None)[0] == 1
    assert stub.sweeps == 1


def test_maint_eol_refuses_when_the_catalogue_is_unreadable(monkeypatch):
    _eol_stub(monkeypatch, _EolStub([], [], catalogue_why="endoflife.date: 502"))
    for measurer in (goal_measures.measure_maint_supported,
                     goal_measures.measure_maint_eol_unjudged):
        value, detail = measurer(None, None)
        assert value is None
        assert "502" in detail


def test_maint_eol_refuses_when_the_repo_list_is_incomplete(monkeypatch):
    _eol_stub(monkeypatch, _EolStub([], [], incomplete=True))
    value, detail = goal_measures.measure_maint_supported(None, None)
    assert value is None
    assert "enumerate" in detail


def test_maint_supported_is_wired_into_the_fetch_map():
    assert goal_measures.KEY_RESULT_FETCH_MEASURERS["maint-kr-supported"] is \
        goal_measures.measure_maint_supported
    assert goal_measures.KPI_MEASURERS["maint-kpi-eol-unjudged"] is \
        goal_measures.measure_maint_eol_unjudged


# --- maint-kr-pins-current --------------------------------------------------

def _pin_row(what, pinned, latest, gap):
    return {"what": what, "pinned": pinned, "latest": latest, "gap": gap}


class _PinStub:
    def __init__(self, judged, problems=(), repos=("SokratesAI/agora",),
                 incomplete=False):
        self._judged = judged
        self._problems = list(problems)
        self._repos = list(repos)
        self._incomplete = incomplete

    def _repos_to_sweep(self):
        return self._repos, [], [], self._incomplete

    def sweep(self, repos):
        return list(self._judged), [], list(self._problems)

    @staticmethod
    def _group(pins):
        groups = {}
        for pin in pins:
            groups.setdefault((pin["what"], pin["pinned"], pin["latest"]), []).append(pin)
        return groups


def _pin_stub(monkeypatch, stub):
    _stub_tool(monkeypatch, "pin_drift", stub)


def test_pins_current_counts_a_patch_gap_too(monkeypatch):
    """`pin_drift` raises only on a minor or a major, because that is where it
    wants a cycle to act. The key result's measure is *behind upstream*, and a
    patch behind is behind."""
    _pin_stub(monkeypatch, _PinStub([
        _pin_row("GH_CLI_VERSION", "2.98.0", "2.100.0", "minor"),
        _pin_row("docker/setup-buildx-action", "v3", "v4.3.0", "major"),
        _pin_row("NODE_VERSION", "22.1.0", "22.1.4", "patch"),
        _pin_row("KUSTOMIZE_VERSION", "5.7.1", "5.7.1", "current"),
    ]))
    value, detail = goal_measures.measure_maint_pins_current(None, None)
    assert value == 3
    assert "2 of them by a minor or a major" in detail


def test_pins_current_does_not_count_a_pin_ahead_of_upstream(monkeypatch):
    """A pin past its ceiling is a real defect and the opposite one. Counting
    it here would let a bump in the wrong direction cancel a missing one."""
    _pin_stub(monkeypatch, _PinStub([
        _pin_row("KUBECTL_VERSION", "v1.36.2", "v1.35.9", "ahead"),
        _pin_row("GH_CLI_VERSION", "2.98.0", "2.100.0", "minor"),
    ]))
    value, _ = goal_measures.measure_maint_pins_current(None, None)
    assert value == 1


def test_pins_current_counts_distinct_triples_not_files(monkeypatch):
    _pin_stub(monkeypatch, _PinStub([
        _pin_row("actions/checkout", "v4", "v7.0.1", "major"),
        _pin_row("actions/checkout", "v4", "v7.0.1", "major"),
        _pin_row("actions/checkout", "v4", "v7.0.1", "major"),
    ]))
    value, _ = goal_measures.measure_maint_pins_current(None, None)
    assert value == 1


def test_pins_current_reads_a_fully_current_org_as_zero(monkeypatch):
    _pin_stub(monkeypatch, _PinStub([
        _pin_row("KUSTOMIZE_VERSION", "5.7.1", "5.7.1", "current")]))
    value, _ = goal_measures.measure_maint_pins_current(None, None)
    assert value == 0


def test_pins_current_says_it_is_a_floor_when_a_pin_was_unreadable(monkeypatch):
    _pin_stub(monkeypatch, _PinStub(
        [_pin_row("GH_CLI_VERSION", "2.98.0", "2.100.0", "minor")],
        problems=["SokratesAI/agora: Dockerfile: FOO pinned 1, upstream unreadable"]))
    value, detail = goal_measures.measure_maint_pins_current(None, None)
    assert value == 1
    assert "floor" in detail


def test_pins_current_refuses_when_the_repo_list_is_incomplete(monkeypatch):
    _pin_stub(monkeypatch, _PinStub([], incomplete=True))
    value, detail = goal_measures.measure_maint_pins_current(None, None)
    assert value is None
    assert "enumerate" in detail


def test_pins_current_is_wired_into_the_fetch_map():
    assert goal_measures.KEY_RESULT_FETCH_MEASURERS["maint-kr-pins-current"] is \
        goal_measures.measure_maint_pins_current


class TestDemosOpened:
    """`demos-kr-opened` -- the share of demos handed over that he opened.

    Stubbed at `tools.demo._read_registry` rather than through `sys.modules`:
    `measure_demos_opened` does `from tools import demo`, which reads the
    attribute off the already-imported package, so a module-level swap would
    leave the real vault read in place and the test would talk to production.
    """

    def _registry(self, payload, monkeypatch):
        from tools import demo as demo_tool
        monkeypatch.setattr(demo_tool, "_read_registry",
                            lambda: (payload, "/tmp/rev"))

    def test_a_retired_demo_counts_the_same_as_a_running_one(self, monkeypatch):
        # Counting only the live rows answers "of the demos running right
        # now", which is a question about this afternoon.
        self._registry({
            "demos": [{"slug": "a", "opened_at": "2026-09-14T09:00:00"},
                      {"slug": "b"}],
            "retired": [{"slug": "c", "opened_at": "2026-09-01T09:00:00"},
                        {"slug": "d", "opened_at": None}],
        }, monkeypatch)
        value, detail = gm.measure_demos_opened(None, None)
        assert value == 50.0
        assert "2 of 4" in detail
        assert "2 running, 2 retired" in detail

    def test_an_empty_registry_is_no_reading_rather_than_nought_percent(
            self, monkeypatch):
        # 0% is the worst reading this key result has, and a share over
        # nothing is not it.
        self._registry({"demos": [], "retired": []}, monkeypatch)
        assert gm.measure_demos_opened(None, None)[0] is None

    def test_an_unreadable_registry_is_no_reading(self, monkeypatch):
        from tools import demo as demo_tool

        def boom():
            raise demo_tool.DemoError("could not read demos.json")

        monkeypatch.setattr(demo_tool, "_read_registry", boom)
        value, detail = gm.measure_demos_opened(None, None)
        assert value is None
        assert "could not read demos.json" in detail

    def test_the_reading_is_named_as_a_floor(self, monkeypatch):
        # A row predating the durable mark cannot carry one, and is not
        # distinguishable from a demo he ignored.
        self._registry({"demos": [{"slug": "a"}]}, monkeypatch)
        value, detail = gm.measure_demos_opened(None, None)
        assert value == 0.0
        assert "a floor, not a total" in detail


class TestDemosNoLitter:
    """`demos-kr-no-litter` -- directories on disk no running demo claims.

    Both halves are stubbed: `nova_demos.DURABLE_ROOT` is pointed at a real
    temporary directory so the listing is a real `os.listdir`, and the
    registry is swapped on `tools.demo` for the reason `TestDemosOpened`
    gives -- the measurer does `from tools import demo`, so a `sys.modules`
    swap would leave the production vault read in place.
    """

    def _root(self, tmp_path, monkeypatch, names):
        from agora_runner import nova_demos
        for name in names:
            (tmp_path / name).mkdir()
        monkeypatch.setattr(nova_demos, "DURABLE_ROOT", str(tmp_path))
        return str(tmp_path)

    def _registry(self, payload, monkeypatch):
        from tools import demo as demo_tool
        monkeypatch.setattr(demo_tool, "_read_registry",
                            lambda: (payload, "/tmp/rev"))

    def test_it_counts_and_names_what_no_running_row_claims(
            self, tmp_path, monkeypatch):
        root = self._root(tmp_path, monkeypatch, ["station", "board166", "old"])
        self._registry({"demos": [
            {"slug": "station", "dir": root + "/station"},
        ]}, monkeypatch)
        value, detail = gm.measure_demos_no_litter(None, None)
        assert value == 2
        assert "2 of 3" in detail
        assert "1 running" in detail
        assert "board166, old" in detail
        assert "station" not in detail.split(":")[-1]

    def test_a_retired_demos_directory_is_litter(self, tmp_path, monkeypatch):
        # `nova_demos.retire` keeps no `dir`, so a tombstone claims nothing --
        # which is the answer this key result wants: the question is settled
        # and the files are still here.
        root = self._root(tmp_path, monkeypatch, ["gone"])
        self._registry({
            "demos": [],
            "retired": [{"slug": "gone", "started_at": "2026-09-01T09:00:00",
                         "opened_at": "2026-09-01T10:00:00"}],
        }, monkeypatch)
        value, detail = gm.measure_demos_no_litter(None, None)
        assert value == 1
        assert "gone" in detail

    def test_an_unlistable_root_is_no_reading_rather_than_nought(
            self, tmp_path, monkeypatch):
        # The failure this whole measurer is shaped around. `tools.demo`'s own
        # `durable_dirs` answers [] here, which would report a spotless loop
        # off a disk nothing ever read -- and 0 is this measure's target.
        from agora_runner import nova_demos
        monkeypatch.setattr(nova_demos, "DURABLE_ROOT",
                            str(tmp_path / "not-here"))
        self._registry({"demos": []}, monkeypatch)
        value, detail = gm.measure_demos_no_litter(None, None)
        assert value is None
        assert "not-here" in detail
        assert "has seen the disk" in detail

    def test_an_empty_root_is_a_real_nought(self, tmp_path, monkeypatch):
        # The other side of the test above: a root that exists and holds
        # nothing is a clean loop, and must not be refused as no reading.
        self._root(tmp_path, monkeypatch, [])
        self._registry({"demos": []}, monkeypatch)
        value, detail = gm.measure_demos_no_litter(None, None)
        assert value == 0
        assert "0 of 0" in detail

    def test_an_unreadable_registry_is_no_reading(self, tmp_path, monkeypatch):
        from tools import demo as demo_tool

        self._root(tmp_path, monkeypatch, ["board166"])

        def boom():
            raise demo_tool.DemoError("could not read demos.json")

        monkeypatch.setattr(demo_tool, "_read_registry", boom)
        value, detail = gm.measure_demos_no_litter(None, None)
        assert value is None
        assert "could not read demos.json" in detail

    def test_a_row_pointing_outside_the_root_protects_nothing(
            self, tmp_path, monkeypatch):
        # `orphan_dirs` already decides this; the assertion pins that the
        # measurer inherits it rather than re-deciding it.
        self._root(tmp_path, monkeypatch, ["station"])
        self._registry({"demos": [
            {"slug": "station", "dir": "/somewhere/else/station"},
        ]}, monkeypatch)
        assert gm.measure_demos_no_litter(None, None)[0] == 1

    def test_it_is_wired_into_the_fetch_map(self):
        assert gm.KEY_RESULT_FETCH_MEASURERS["demos-kr-no-litter"] is \
            gm.measure_demos_no_litter


class TestResearchReused:
    """`research-kr-reused` -- the share of research write-ups a later entry cites.

    Both halves are stubbed on the module under test: the folder listing and
    the journal read. A test that reached the real vault or the real site
    would measure this afternoon rather than the rule.
    """

    def _sources(self, monkeypatch, slugs, entries):
        monkeypatch.setattr(gm, "research_write_ups", lambda: (slugs, None))
        monkeypatch.setattr(gm, "_every_journal_entry", lambda: (entries, None))

    def _entry(self, text):
        return {"title": text, "blocks": []}

    def test_the_first_entry_naming_a_write_up_is_its_author_not_a_citation(
            self, monkeypatch):
        # Nothing records who wrote a document, so the earliest naming entry
        # is read as the one that wrote it. One mention is therefore 0%.
        self._sources(monkeypatch, ["idp-2026-08"],
                      [self._entry("wrote research/idp-2026-08")])
        value, detail = gm.measure_research_reused(None, None)
        assert value == 0.0
        assert "0 of 1" in detail

    def test_a_second_entry_naming_it_is_the_citation(self, monkeypatch):
        self._sources(monkeypatch, ["idp-2026-08"],
                      [self._entry("wrote research/idp-2026-08"),
                       self._entry("read research/idp-2026-08 again")])
        assert gm.measure_research_reused(None, None)[0] == 100.0

    def test_the_filename_counts_as_naming_it_and_the_bare_slug_does_not(
            self, monkeypatch):
        # `board-records` is a write-up AND a document in his own project
        # folder; matching the bare word read 27 entries about issue #203 as
        # citations of a file none of them opened.
        self._sources(monkeypatch, ["board-records"],
                      [self._entry("board-records"),
                       self._entry("board-records"),
                       self._entry("board-records")])
        assert gm.measure_research_reused(None, None)[0] == 0.0
        self._sources(monkeypatch, ["board-records"],
                      [self._entry("board-records.md"),
                       self._entry("board-records.md")])
        assert gm.measure_research_reused(None, None)[0] == 100.0

    def test_an_empty_folder_is_no_reading_rather_than_nought_percent(
            self, monkeypatch):
        self._sources(monkeypatch, [], [self._entry("anything")])
        assert gm.measure_research_reused(None, None)[0] is None

    def test_an_unreadable_folder_is_no_reading(self, monkeypatch):
        monkeypatch.setattr(gm, "research_write_ups",
                            lambda: ([], "vault_tool.py ls exited 1"))
        value, detail = gm.measure_research_reused(None, None)
        assert value is None
        assert "vault_tool.py ls exited 1" in detail

    def test_an_unreadable_journal_is_no_reading(self, monkeypatch):
        monkeypatch.setattr(gm, "research_write_ups", lambda: (["a"], None))
        monkeypatch.setattr(gm, "_every_journal_entry",
                            lambda: ([], "could not read the journal API"))
        value, detail = gm.measure_research_reused(None, None)
        assert value is None
        assert "could not read the journal API" in detail

    def test_the_listing_keeps_only_markdown_and_drops_the_template(self):
        class Done:
            returncode = 0
            stdout = (gm.RESEARCH_PREFIX + "idp-2026-08.md\n"
                      + gm.RESEARCH_PREFIX + "_template.md\n"
                      + gm.RESEARCH_PREFIX + "notes.txt\n")
            stderr = ""

        slugs, error = gm.research_write_ups(runner=lambda *a, **k: Done())
        assert error is None
        assert slugs == ["idp-2026-08"]

    def test_a_failed_listing_is_an_error_and_not_an_empty_folder(self):
        # An empty folder and an unreadable one read as 0 write-ups either
        # way; only the error tells them apart, and they mean opposite things.
        class Done:
            returncode = 1
            stdout = ""
            stderr = "no such folder"

        slugs, error = gm.research_write_ups(runner=lambda *a, **k: Done())
        assert slugs == []
        assert "no such folder" in error

    def test_research_reused_is_wired_into_the_fetch_map(self):
        assert gm.KEY_RESULT_FETCH_MEASURERS["research-kr-reused"] is \
            gm.measure_research_reused


class TestInfraOutlivesTheBox:
    """The share of preflight's checks that survive the box they run on.

    The measure reads `tools.preflight`'s own `SUBJECT` labels, so the tests
    swap that registry rather than a fixture of their own -- a test that built
    its own label map would pass on a build where `SUBJECT` is never consulted.
    """

    def _roster(self, monkeypatch, subject):
        from tools import preflight
        monkeypatch.setattr(preflight, "CHECKS", tuple(subject))
        monkeypatch.setattr(preflight, "SUBJECT",
                            {n: (where, n) for n, where in subject.items()})

    def test_it_is_the_off_box_share_of_the_whole_roster(self, monkeypatch):
        self._roster(monkeypatch, {
            "nas_health": "off-box", "open_prs": "off-box",
            "alerts": "on-box", "oom_history": "on-box",
        })
        value, detail = gm.measure_infra_outlives_the_box(None, None)
        assert value == 50.0
        assert "2 of 4" in detail
        assert "nas_health, open_prs" in detail

    def test_it_ignores_the_cadence_that_decides_a_sweep(self, monkeypatch):
        # The hand-typed 23 was 8 of 35 because a sweep runs only the checks
        # whose subject can have moved. The roster is 69. A measure that moved
        # with the time of day would be reporting the scheduler, not the
        # monitoring estate, so CADENCE_HOURS must not reach this number.
        from tools import preflight
        self._roster(monkeypatch, {
            "nas_health": "off-box", "alerts": "on-box", "oom_history": "on-box",
        })
        monkeypatch.setattr(preflight, "CADENCE_HOURS", {"nas_health": 168.0})
        first, _ = gm.measure_infra_outlives_the_box(None, None)
        monkeypatch.setattr(preflight, "CADENCE_HOURS", {})
        second, _ = gm.measure_infra_outlives_the_box(None, None)
        assert first == second == round(100 / 3, 1)

    def test_an_unlabelled_check_gets_no_reading_rather_than_a_side(
            self, monkeypatch):
        from tools import preflight
        self._roster(monkeypatch, {
            "nas_health": "off-box", "alerts": "on-box",
        })
        monkeypatch.setattr(preflight, "CHECKS",
                            ("nas_health", "alerts", "brand_new"))
        value, detail = gm.measure_infra_outlives_the_box(None, None)
        assert value is None
        assert "brand_new" in detail
        assert "no SUBJECT label" in detail

    def test_a_label_that_is_neither_side_gets_no_reading(self, monkeypatch):
        self._roster(monkeypatch, {
            "nas_health": "off-box", "alerts": "maybe",
        })
        value, detail = gm.measure_infra_outlives_the_box(None, None)
        assert value is None
        assert "alerts" in detail
        assert "neither on-box nor off-box" in detail

    def test_an_empty_roster_is_no_reading_and_not_zero(self, monkeypatch):
        self._roster(monkeypatch, {})
        value, detail = gm.measure_infra_outlives_the_box(None, None)
        assert value is None
        assert "no check at all" in detail

    def test_the_detail_says_the_number_is_a_floor(self, monkeypatch):
        # nova-deadman is the one instrument that reported the 2026-09-01
        # outage and it is not in this roster, so the share understates the
        # off-box monitoring that exists.
        self._roster(monkeypatch, {"nas_health": "off-box", "alerts": "on-box"})
        _value, detail = gm.measure_infra_outlives_the_box(None, None)
        assert "floor" in detail
        assert "nova-deadman" in detail

    def test_the_live_roster_is_fully_labelled_and_answers(self):
        # Against the real preflight, not a fixture: preflight refuses to run
        # with an unlabelled check, so this measure must never be the thing
        # that discovers one.
        value, detail = gm.measure_infra_outlives_the_box(None, None)
        assert value is not None, detail
        assert 0 <= value <= 100

    def test_outlives_the_box_is_wired_into_the_fetch_map(self):
        assert gm.KEY_RESULT_FETCH_MEASURERS["infra-kr-outlives-the-box"] is \
            gm.measure_infra_outlives_the_box


class TestDocsCoversWhatRuns:
    """Share of the workloads we run that a docs page is named after.

    Two halves, and each one has a way of going wrong that flatters the
    number: a namespace that fails to read shrinks the denominator, and a
    substring match inflates the numerator. Both get a test.
    """

    def _kubectl(self, per_namespace):
        """A `subprocess.run` stand-in answering `kubectl get` per namespace."""
        def runner(args, **_kwargs):
            namespace = args[args.index("-n") + 1]
            answer = per_namespace[namespace]
            if isinstance(answer, int):
                return types.SimpleNamespace(
                    returncode=answer, stdout="", stderr="kubectl said no")
            items = [{"metadata": {"name": name}} for name in answer]
            return types.SimpleNamespace(
                returncode=0, stdout=json.dumps({"items": items}), stderr="")
        return runner

    def _pages(self, monkeypatch, files):
        from tools import running_images
        monkeypatch.setattr(running_images, "fetch_manifests",
                            lambda **_kw: (files, None))

    def test_it_is_the_share_of_workloads_a_page_is_named_after(
            self, monkeypatch):
        monkeypatch.setattr(gm, "DOCUMENTED_NAMESPACES", ("agents",))
        monkeypatch.setattr(gm, "read_documented_workloads",
                            lambda: ([("agents", "agora"), ("agents", "marcus"),
                                      ("agents", "redis"), ("agents", "hub")],
                                     None))
        self._pages(monkeypatch, {
            "docs/explanation/agora.md": "---\ntitle: How Agora runs an agent\n---\n",
            "docs/reference/marcus.md": "# Marcus\n",
        })
        value, detail = gm.measure_docs_covers_what_runs(None, None)
        assert value == 50.0
        assert "2 of 4" in detail
        assert "redis" in detail and "hub" in detail

    def test_a_mention_inside_another_word_does_not_count(self, monkeypatch):
        # `hub` is a Deployment in infra and `GitHub` is in half the headings
        # on that site; `agora` is a Deployment and a prefix of agora-persona.
        # A substring match reads 2 of 2 here and the honest answer is 0.
        monkeypatch.setattr(gm, "read_documented_workloads",
                            lambda: ([("infra", "hub"), ("agents", "agora")],
                                     None))
        self._pages(monkeypatch, {
            "docs/reference/github-service.md": "# GitHubService\n",
            "docs/reference/agora-persona.md": "# agora-persona\n",
        })
        value, detail = gm.measure_docs_covers_what_runs(None, None)
        assert value == 0.0, detail
        assert "0 of 2" in detail

    def test_a_heading_outside_docs_is_not_a_page(self, monkeypatch):
        # `.github/workflows/docs-sync.md` is a gh-aw workflow whose shell
        # comments all start with `#`. A repo-wide heading scan reads those as
        # headings, which is how a comment could credit a workload with a page.
        monkeypatch.setattr(gm, "read_documented_workloads",
                            lambda: ([("agents", "marcus")], None))
        self._pages(monkeypatch, {
            ".github/workflows/docs-sync.md": "# marcus is mentioned here\n",
            "docs/intro.md": "# Sokrates Developer Docs\n",
        })
        value, detail = gm.measure_docs_covers_what_runs(None, None)
        assert value == 0.0, detail
        assert "1 pages under docs/" in detail

    def test_a_namespace_that_cannot_be_read_gets_no_reading(self, monkeypatch):
        monkeypatch.setattr(gm, "DOCUMENTED_NAMESPACES", ("agents", "infra"))
        runner = self._kubectl({"agents": ["agora"], "infra": 1})
        names, why = gm.read_documented_workloads(runner=runner)
        assert names is None
        assert "infra" in why

    def test_an_empty_namespace_is_a_failed_read_not_a_clean_one(
            self, monkeypatch):
        # All three demonstrably run something, so an empty list shrinks the
        # denominator and raises the share -- an error in the flattering
        # direction on a number whose job is to be low.
        monkeypatch.setattr(gm, "DOCUMENTED_NAMESPACES", ("agents", "obsidian"))
        runner = self._kubectl({"agents": ["agora"], "obsidian": []})
        names, why = gm.read_documented_workloads(runner=runner)
        assert names is None
        assert "obsidian" in why

    def test_both_kinds_are_read_in_one_call(self):
        seen = []

        def runner(args, **_kwargs):
            seen.append(args)
            return types.SimpleNamespace(
                returncode=0, stdout=json.dumps({"items": [
                    {"metadata": {"name": "x"}}]}), stderr="")

        gm.read_documented_workloads(runner=runner)
        assert all("deploy,statefulset" in args for args in seen)
        assert len(seen) == len(gm.DOCUMENTED_NAMESPACES)

    def test_a_site_with_no_pages_gets_no_reading(self, monkeypatch):
        monkeypatch.setattr(gm, "read_documented_workloads",
                            lambda: ([("agents", "agora")], None))
        self._pages(monkeypatch, {"README.md": "# sokrates-docs\n"})
        value, detail = gm.measure_docs_covers_what_runs(None, None)
        assert value is None
        assert "no markdown under docs/" in detail

    def test_the_live_pair_answers(self):
        # Against the real cluster and the real repo: the two halves of this
        # measure are a kubectl read and a tarball read, and a fixture cannot
        # tell me either one still works.
        value, detail = gm.measure_docs_covers_what_runs(None, None)
        assert value is not None, detail
        assert 0 <= value <= 100

    def test_covers_what_runs_is_wired_into_the_fetch_map(self):
        assert gm.KEY_RESULT_FETCH_MEASURERS["docs-kr-covers-what-runs"] is \
            gm.measure_docs_covers_what_runs
