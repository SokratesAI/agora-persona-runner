"""The record behind `pm-kr-calibration` (issue #227)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.expectations import (
    about_target, closed_items, measure_calibration, parse_expectations,
    problems,
)

FENCE = "```"


def doc(*blocks):
    return "\n\n".join(blocks)


def block(**fields):
    body = "\n".join(f"{k.replace('_', '-')}: {v}" for k, v in fields.items())
    return f"{FENCE}expectation\n{body}\n{FENCE}"


GOOD = dict(id="exp-a", about="issue #227", expect="he stops retyping numbers",
            written="2026-09-01", source="edvard")


def test_parses_every_field_and_drops_an_unknown_key():
    rows = parse_expectations(doc(block(**GOOD, note="ignored")))
    assert len(rows) == 1
    assert rows[0]["about"] == "issue #227"
    assert "note" not in rows[0]


def test_a_fence_this_module_does_not_own_is_not_read_as_fields():
    sample = f"{FENCE}python\nid: not-an-expectation\n{FENCE}"
    assert parse_expectations(doc(sample, block(**GOOD))) == [
        parse_expectations(block(**GOOD))[0]]


def test_about_needs_the_word_because_a_bare_number_names_two_rows():
    assert about_target({"about": "issue #227"}) == ("issue", 227)
    assert about_target({"about": "idea #38"}) == ("idea", 38)
    assert about_target({"about": "#227"}) is None
    assert about_target({"about": "issue 227"}) is None


def test_problems_names_each_mechanical_rule():
    found = problems(doc(
        block(id="", about="#227", expect="", written="soon"),
        block(**GOOD),
        block(**{**GOOD, "checked": "2026-09-10"}),
        block(**{**GOOD, "id": "exp-b", "outcome": "maybe",
                 "checked": "2026-09-10"}),
        block(**{**GOOD, "id": "exp-c", "source": "sokrates"}),
    ))
    joined = "\n".join(found)
    assert "has no `id`" in joined
    assert "names no board row" in joined
    assert "has no `expect`" in joined
    assert "not a YYYY-MM-DD date" in joined
    assert "used by two expectations" in joined          # exp-a twice
    assert "not one of right, wrong, partly" in joined
    assert "no `outcome`" in joined
    assert "not one of edvard, nova" in joined


def test_a_complete_open_expectation_has_no_problems():
    assert problems(doc(block(**GOOD))) == []


def test_a_settled_expectation_has_no_problems():
    assert problems(doc(block(**GOOD, outcome="partly",
                              checked="2026-09-10"))) == []


def row(number, updated, status="done"):
    return {"number": number, "updated": updated, "statusKey": status}


BOARDS = {"issue": [row(227, "09-10"), row(228, "09-11"),
                    row(229, "09-11", status="backlog"),
                    row(230, "07-02")],
          "idea": [row(38, "09-12")]}


def test_closed_items_takes_the_window_and_the_year_off_since():
    assert closed_items(BOARDS, "2026-09-08", "2026-09-14") == {
        ("issue", 227): "2026-09-10",
        ("issue", 228): "2026-09-11",
        ("idea", 38): "2026-09-12",
    }


def test_share_counts_only_a_prediction_written_before_the_row_closed():
    rows = parse_expectations(doc(
        block(id="a", about="issue #227", expect="x", written="2026-09-01",
              outcome="right", checked="2026-09-11"),
        block(id="b", about="idea #38", expect="y", written="2026-09-05",
              outcome="wrong", checked="2026-09-13"),
    ))
    value, detail = measure_calibration(rows, BOARDS, "2026-09-08", "2026-09-14")
    assert value == 67                      # 2 of 3 closed rows
    assert "2 of 3" in detail
    assert "NOT counted" not in detail


def test_an_expectation_written_after_the_close_is_dropped_and_named():
    rows = parse_expectations(doc(
        block(id="a", about="issue #227", expect="x", written="2026-09-10",
              outcome="right", checked="2026-09-11"),
    ))
    value, detail = measure_calibration(rows, BOARDS, "2026-09-08", "2026-09-14")
    assert value == 0
    assert "1 settled expectation(s) were NOT counted" in detail


def test_an_open_prediction_does_not_count_because_nobody_checked_it():
    rows = parse_expectations(doc(
        block(id="a", about="issue #227", expect="x", written="2026-09-01"),
    ))
    value, detail = measure_calibration(rows, BOARDS, "2026-09-08", "2026-09-14")
    assert value == 0
    assert "NOT counted" not in detail       # open is not hindsight


def test_a_prediction_about_a_row_that_did_not_ship_is_not_in_the_numerator():
    rows = parse_expectations(doc(
        block(id="a", about="issue #229", expect="x", written="2026-09-01",
              outcome="right", checked="2026-09-11"),
    ))
    assert measure_calibration(rows, BOARDS, "2026-09-08", "2026-09-14")[0] == 0


def test_two_predictions_about_one_row_count_once():
    rows = parse_expectations(doc(
        block(id="a", about="issue #227", expect="x", written="2026-09-01",
              outcome="right", checked="2026-09-11"),
        block(id="b", about="issue #227", expect="z", written="2026-09-02",
              outcome="wrong", checked="2026-09-11"),
    ))
    assert measure_calibration(rows, BOARDS, "2026-09-08", "2026-09-14")[0] == 33


def test_no_row_closed_is_missing_rather_than_zero():
    value, why = measure_calibration([], BOARDS, "2026-08-01", "2026-08-07")
    assert value is None
    assert "no denominator" in why


def test_the_key_result_is_wired_to_this_measure():
    from tools.goal_measures import (
        KEY_RESULT_EXPECTATION_MEASURERS, key_result_rows,
    )
    assert "pm-kr-calibration" in KEY_RESULT_EXPECTATION_MEASURERS
    sections = {"product management": {
        "project": "Product management", "objective": {},
        "keyResults": [{"id": "pm-kr-calibration", "measure": "m"}],
        "kpis": []}}
    rows = parse_expectations(doc(
        block(id="a", about="issue #227", expect="x", written="2026-09-01",
              outcome="right", checked="2026-09-11"),
    ))
    out = key_result_rows(sections, [], since="2026-09-08", until="2026-09-14",
                          boards=[BOARDS["issue"], BOARDS["idea"]],
                          expectations=rows)
    assert [r["value"] for r in out] == [33]


def test_without_the_document_it_reports_not_measured_not_no_instrument():
    from tools.goal_measures import key_result_rows
    sections = {"product management": {
        "project": "Product management", "objective": {},
        "keyResults": [{"id": "pm-kr-calibration", "measure": "m"}],
        "kpis": []}}
    out = key_result_rows(sections, [], since="2026-09-08", until="2026-09-14",
                          boards=[BOARDS["issue"], BOARDS["idea"]],
                          expectations=None)
    assert out[0]["value"] is None
    assert out[0]["detail"].startswith("not measured")
    assert "no instrument" not in out[0]["detail"]


def test_one_board_is_missing_rather_than_a_crash():
    from tools.goal_measures import measure_pm_calibration
    value, why = measure_pm_calibration([], [BOARDS["issue"]],
                                        "2026-09-08", "2026-09-14")
    assert value is None
    assert "1 board(s) were read" in why
