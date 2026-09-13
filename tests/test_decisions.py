"""The decision record behind `pm-kr-reversals` (issue #227)."""
import pytest

from agora_runner.decisions import (
    HELD_DAYS,
    days_held,
    measure_reversals,
    parse_decisions,
    problems,
)


def fence(**fields):
    body = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return f"```decision\n{body}\n```\n"


STANDING = dict(id="dec-a", decision="We do it this way.", taken="2026-09-01",
                source="nova")


def test_parses_only_its_own_fences():
    text = (
        "prose above\n"
        + fence(**STANDING)
        + "```python\nid: not-a-decision\ndecision: no\ntaken: 2026-09-01\n```\n"
        + fence(id="dec-b", decision="And this.", taken="2026-09-02")
    )
    rows = parse_decisions(text)
    assert [r["id"] for r in rows] == ["dec-a", "dec-b"]


def test_unknown_keys_are_dropped():
    rows = parse_decisions(fence(**STANDING, reviewer="somebody"))
    assert "reviewer" not in rows[0]


def test_a_clean_document_has_no_problems():
    text = fence(**STANDING) + fence(
        id="dec-b", about="issue #227", decision="A thing.", taken="2026-08-01",
        source="edvard", reversed="2026-08-10", because="He changed his mind.")
    assert problems(text) == []


@pytest.mark.parametrize("row, fragment", [
    (dict(decision="x", taken="2026-09-01"), "has no `id`"),
    (dict(id="dec-a", taken="2026-09-01"), "has no `decision`"),
    (dict(id="dec-a", decision="x", taken="soon"), "not a YYYY-MM-DD date"),
    (dict(id="dec-a", decision="x", taken="2026-09-01", about="#227"),
     "names no board row"),
    (dict(id="dec-a", decision="x", taken="2026-09-01", source="sokrates"),
     "is not one of"),
    (dict(id="dec-a", decision="x", taken="2026-09-01", reversed="2026-09-05"),
     "has no `because`"),
    (dict(id="dec-a", decision="x", taken="2026-09-01", because="reasons"),
     "no `reversed` date"),
])
def test_problems_names_each_mechanical_fault(row, fragment):
    found = problems(fence(**row))
    assert any(fragment in p for p in found), found


def test_a_duplicate_id_is_a_problem():
    text = fence(**STANDING) + fence(**STANDING)
    assert any("two decisions" in p for p in problems(text))


def test_a_reversal_before_the_decision_is_a_problem():
    text = fence(id="dec-a", decision="x", taken="2026-09-10",
                 reversed="2026-09-01", because="time travel")
    assert any("before it was taken" in p for p in problems(text))
    assert days_held(parse_decisions(text)[0]) == -9


def test_days_held_is_none_without_two_dates():
    assert days_held({"taken": "2026-09-01"}) is None
    assert days_held({"taken": "2026-09-01", "reversed": "2026-09-11"}) == 10


def rows(*specs):
    return [dict(spec) for spec in specs]


def test_counts_only_reversals_inside_the_window():
    value, detail = measure_reversals(rows(
        dict(id="in", taken="2026-09-01", reversed="2026-09-10"),
        dict(id="before", taken="2026-07-01", reversed="2026-07-10"),
        dict(id="standing", taken="2026-09-01"),
    ), "2026-08-15", "2026-09-14")
    assert value == 1
    assert "(in)" in detail
    assert "out of 3 decision(s) on record" in detail


def test_a_reversal_dated_after_the_window_is_not_counted():
    """A date past `until` is tomorrow's reversal, or a typo. Either way it is
    not part of this month's count."""
    value, _ = measure_reversals(rows(
        dict(id="future", taken="2026-09-20", reversed="2026-09-25"),
    ), "2026-08-15", "2026-09-14")
    assert value == 0


def test_a_decision_that_held_longer_is_not_counted_but_is_named():
    value, detail = measure_reversals(rows(
        dict(id="outgrew", taken="2026-07-01", reversed="2026-09-10"),
    ), "2026-08-15", "2026-09-14")
    assert value == 0
    assert "1 more were reversed in the window but had held longer" in detail


def test_the_boundary_day_counts_and_the_next_one_does_not():
    at_bound = dict(id="edge", taken="2026-08-15", reversed="2026-09-14")
    assert days_held(at_bound) == HELD_DAYS
    assert measure_reversals([at_bound], "2026-08-15", "2026-09-14")[0] == 1
    past = dict(at_bound, taken="2026-08-14")
    assert days_held(past) == HELD_DAYS + 1
    assert measure_reversals([past], "2026-08-15", "2026-09-14")[0] == 0


def test_an_undated_reversal_is_named_rather_than_counted():
    value, detail = measure_reversals(rows(
        dict(id="no-taken", reversed="2026-09-10"),
    ), "2026-08-15", "2026-09-14")
    assert value == 0
    assert "no readable `taken` date" in detail


def test_an_empty_record_measures_zero_and_says_the_record_is_empty():
    value, detail = measure_reversals([], "2026-08-15", "2026-09-14")
    assert value == 0
    assert "out of 0 decision(s) on record" in detail
    assert "floor" in detail


def section():
    return {"product management": {
        "project": "Product management", "objective": {},
        "keyResults": [{"id": "pm-kr-reversals", "measure": "m"}],
        "kpis": []}}


def test_the_key_result_is_wired_to_this_measure():
    from tools.goal_measures import (
        KEY_RESULT_DECISION_MEASURERS, key_result_rows,
    )
    assert "pm-kr-reversals" in KEY_RESULT_DECISION_MEASURERS
    out = key_result_rows(section(), [], since="2026-09-08", until="2026-09-14",
                          decisions=rows(
                              dict(id="in", taken="2026-09-01",
                                   reversed="2026-09-10")))
    assert [r["value"] for r in out] == [1]


def test_the_measure_looks_back_a_month_not_the_goals_window():
    """The unit is *per month*, so a reversal 20 days ago still counts."""
    from tools.goal_measures import key_result_rows
    out = key_result_rows(section(), [], since="2026-09-08", until="2026-09-14",
                          decisions=rows(
                              dict(id="old", taken="2026-08-20",
                                   reversed="2026-08-25")))
    assert [r["value"] for r in out] == [1]


def test_without_the_document_it_reports_not_measured_not_no_instrument():
    from tools.goal_measures import key_result_rows
    out = key_result_rows(section(), [], since="2026-09-08", until="2026-09-14",
                          decisions=None)
    assert out[0]["value"] is None
    assert out[0]["detail"].startswith("not measured")
