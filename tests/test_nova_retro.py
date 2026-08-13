"""The Friday retrospective ledger and the page that reads it.

`nova_retro` does no I/O, so every test here is a pure function against a
literal document. The expected shapes are written as literals rather than
derived from the input for the reason `test_nova_costs` gives: a
transform compared against itself survives its own mutation.

The half worth stating out loud is the validator. This ledger gets one
row a week, written by a cycle with no memory of the previous one, and
the cost of a wrong column is not noticed until the following Friday --
so the tests that matter most here are the ones asserting that a wrong
row is *refused*, not the ones asserting a right row is accepted.
"""

import json

import pytest

from agora_runner import nova_retro, nova_sources
from agora_runner.nova_retro import (
    RetroError,
    SCORE_KEYS,
    append,
    load,
    retros_payload,
    validate_row,
)


def row(**overrides):
    base = {
        "date": "2026-08-14",
        "cycle": 181,
        "scores": {"going": 7, "effectiveness": 6, "feeling": 8},
        "overall": "Steady, and finally measurable.",
        "good": "The loop ships something most cycles.",
        "bad": "It re-derives the same facts every time it wakes.",
        "changes": ["Write the pace number into the entry when it changed the pick."],
    }
    base.update(overrides)
    return base


LEDGER = {"retros": [row(date="2026-08-07", cycle=120, scores={"going": 5, "effectiveness": 5, "feeling": 6}), row()]}


# --- the shape ---------------------------------------------------------


def test_the_three_scores_are_the_three_things_he_asked_to_be_rated():
    """Pinned as a literal, and this is the assertion the whole file is
    built around.

    Edvard named three ratings -- how it is going, how effective, the
    overall feeling -- and the last is the one he called the most
    important metric. A cycle renaming or dropping one would produce a
    ledger whose old rows and new rows plot different things on the same
    axis, and the graph would look fine."""
    assert SCORE_KEYS == (
        ("going", "How it's going"),
        ("effectiveness", "How effective"),
        ("feeling", "Overall feeling"),
    )


# --- the validator -----------------------------------------------------


def test_a_complete_row_is_accepted():
    validate_row(row())


@pytest.mark.parametrize(
    "bad, expected",
    [
        (row(date="14-08-2026"), "date must be YYYY-MM-DD"),
        (row(date="2026-02-30"), "not a real date"),
        (row(cycle=0), "cycle must be a positive integer"),
        (row(cycle="181"), "cycle must be a positive integer"),
        (row(cycle=True), "cycle must be a positive integer"),
        (row(scores={"going": 7, "effectiveness": 6}), "scores is missing feeling"),
        (
            row(scores={"going": 7, "effectiveness": 6, "feeling": 8, "mood": 4}),
            r"scores has unknown key\(s\) mood",
        ),
        (row(scores={"going": 7, "effectiveness": 6, "feeling": 11}), "must be 1-10"),
        (row(scores={"going": 7, "effectiveness": 6, "feeling": 0}), "must be 1-10"),
        (row(scores={"going": 7, "effectiveness": 6, "feeling": 8.5}), "whole number"),
        (row(scores={"going": 7, "effectiveness": 6, "feeling": True}), "whole number"),
        (row(overall="   "), "overall must be a non-empty string"),
        (row(good=None), "good must be a non-empty string"),
        (row(bad=""), "bad must be a non-empty string"),
        (row(changes="one change"), "changes must be a list"),
        (row(changes=["", "real"]), "non-empty string"),
        (row(mood="fine"), r"unknown field\(s\) mood"),
    ],
)
def test_a_row_that_would_corrupt_the_series_is_refused(bad, expected):
    """One case per way a row can be wrong, because "it raised" is not the
    assertion -- *which* thing it refused is.

    `mood` appears twice on purpose: once inside `scores`, where it would
    add a fourth line to a three-line chart, and once at the top level,
    where it would sit in the ledger forever with nothing reading it. Both
    are the same real failure -- a later cycle inventing a column -- and
    they arrive through different doors."""
    with pytest.raises(RetroError, match=expected):
        validate_row(bad)


def test_no_score_is_the_only_optional_one():
    """Each of the three, dropped in turn, is refused by name.

    Written as a loop rather than three cases because the point is that
    there is no favoured column: `feeling` being the most important metric
    does not make the other two optional, and a validator that only
    checked the important one would pass this file's other tests."""
    for key, _ in SCORE_KEYS:
        scores = {k: 6 for k, _ in SCORE_KEYS}
        del scores[key]
        with pytest.raises(RetroError, match=f"scores is missing {key}"):
            validate_row(row(scores=scores))


def test_changes_may_be_empty_but_must_be_present():
    """A retro that chose to change nothing is a real answer; a retro that
    forgot the question is not."""
    validate_row(row(changes=[]))
    missing = row()
    del missing["changes"]
    with pytest.raises(RetroError, match="changes must be a list"):
        validate_row(missing)


# --- append ------------------------------------------------------------


def test_the_first_retro_writes_the_first_ledger():
    text = append("", row())
    assert json.loads(text) == {"retros": [row()]}


def test_a_second_retro_on_the_same_date_is_refused():
    """Two cycles waking on one Friday morning is the realistic way this
    ledger gets a duplicate, and a duplicate is invisible: the chart draws
    two marks on one x and every later "compare to the previous retro"
    silently weights that week twice."""
    first = append("", row())
    with pytest.raises(RetroError, match="already in the ledger"):
        append(first, row(cycle=182, overall="A second opinion."))


def test_a_later_date_appends_and_the_file_stays_in_time_order():
    """The row arrives out of order on purpose. The chart draws straight
    through from the first row to the last, so an unsorted ledger puts a
    mark outside the plot box -- sorting on write is what means the page
    never has to."""
    ledger = append("", row(date="2026-08-21", cycle=300))
    ledger = append(ledger, row(date="2026-08-14", cycle=181))
    dates = [entry["date"] for entry in json.loads(ledger)["retros"]]
    assert dates == ["2026-08-14", "2026-08-21"]


def test_an_unreadable_ledger_is_not_quietly_started_over():
    """The dangerous repair. "Cannot parse it, so begin a fresh one" would
    drop every retro ever written, on the one file whose entire purpose is
    comparison across weeks."""
    with pytest.raises(json.JSONDecodeError):
        append("{not json", row())


def test_an_absent_ledger_and_an_empty_one_both_read_as_no_retros():
    assert load("") == []
    assert load("   \n") == []
    assert load('{"retros": []}') == []


# --- the payload -------------------------------------------------------


def test_the_payload_carries_what_the_page_plots():
    payload = retros_payload(json.dumps(LEDGER))
    assert payload["range"] == [1, 10]
    assert payload["scoreKeys"] == [
        {"key": "going", "label": "How it's going"},
        {"key": "effectiveness", "label": "How effective"},
        {"key": "feeling", "label": "Overall feeling"},
    ]
    assert [entry["date"] for entry in payload["retros"]] == ["2026-08-07", "2026-08-14"]
    newest = payload["retros"][-1]
    assert newest["scores"] == {"going": 7, "effectiveness": 6, "feeling": 8}
    assert newest["cycle"] == 181
    assert newest["overall"] == "Steady, and finally measurable."
    assert newest["changes"] == [
        "Write the pace number into the entry when it changed the pick."
    ]


def test_the_date_becomes_a_number_the_chart_can_place():
    """1786665600000 is 2026-08-14T00:00:00Z -- checked with `date -u -d
    @1786665600` and written as a literal rather than recomputed here, since
    a conversion checked against itself passes whatever it does."""
    payload = retros_payload(json.dumps({"retros": [row()]}))
    assert payload["retros"][0]["at"] == 1786665600000
    assert payload["retros"][0]["date"] == "2026-08-14"


def test_the_payload_sorts_and_skips_rather_than_trusting_the_file():
    """The ledger is hand-edited in a vault, so the page's assumptions have
    to be made true on this side of the wire. A row with no usable date has
    no place on a time axis and is dropped rather than plotted at zero,
    which would put a mark decades to the left of everything else."""
    payload = retros_payload(json.dumps({"retros": [
        row(date="2026-08-21", cycle=300),
        {"date": "not a date", "cycle": 1},
        row(date="2026-08-14"),
    ]}))
    assert [entry["date"] for entry in payload["retros"]] == ["2026-08-14", "2026-08-21"]


def test_an_absent_ledger_is_an_empty_page_and_not_an_error():
    """This is the state the page ships in: the first retro has not run
    yet. An exception here would 502 a nav tab from the moment it appears
    until Friday morning."""
    payload = retros_payload("")
    assert payload["retros"] == []
    assert payload["scoreKeys"][0]["key"] == "going"


def test_a_ledger_that_will_not_parse_raises_rather_than_reading_as_empty():
    """Empty and broken are deliberately different answers. A page saying
    "no retrospectives yet" is the wrong way to report a corrupted file --
    it is indistinguishable from the true state and nobody investigates
    it."""
    with pytest.raises(json.JSONDecodeError):
        retros_payload("{oops")
    with pytest.raises(RetroError, match="must be a JSON object"):
        retros_payload("[]")


# --- wiring ------------------------------------------------------------


def test_the_source_fetch_reads_the_ledger_path(monkeypatch):
    """The one thing between the vault and the page, and nothing else in
    this suite would notice it pointing at the wrong document."""
    asked = []
    monkeypatch.setattr(
        nova_sources, "vault_read_path", lambda path: asked.append(path) or '{"retros": []}'
    )
    assert nova_sources.retro_ledger_json() == '{"retros": []}'
    assert asked == [nova_retro.RETRO_LEDGER_PATH]
    assert nova_retro.RETRO_LEDGER_PATH.endswith("nova/resources/retro-ledger.json")


def test_a_missing_document_reaches_the_shaping_as_empty_text(monkeypatch):
    monkeypatch.setattr(nova_sources, "vault_read_path", lambda path: None)
    assert nova_sources.retro_ledger_json() == ""
