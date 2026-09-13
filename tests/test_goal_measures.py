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


def test_marcus_key_results_are_measured_from_the_state_not_from_a_goal():
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
    third = by_id["marcus-kr-coach-first-try"]
    assert third["value"] is None
    assert "no instrument" in third["detail"]
    assert "production LLM route" in third["detail"]


def test_an_unread_marcus_state_never_reads_as_having_no_instrument():
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
