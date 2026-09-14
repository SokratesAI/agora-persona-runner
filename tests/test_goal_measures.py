"""`tools.goal_measures` — the goal numbers, taken rather than typed.

Every test below was checked by breaking the code under it: a test that
passes with the fix ripped out is not evidence of anything, which this
loop has now shipped twice.
"""

import json
from datetime import datetime, timezone

import pytest

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
