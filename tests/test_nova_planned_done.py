"""Planned vs. done (idea #312): the join, the gaps, and both routes."""
import json
from datetime import date
from unittest.mock import patch

from agora_runner import nova_site, nova_sources
from agora_runner.nova_planned_done import planned_done, render_page
from tests.test_nova_site import _get

TODAY = date(2026, 9, 17)


def _history(*rows):
    return json.dumps({"rows": list(rows)})


def _claim(cycle, item, at="2026-09-17T04:00:00+02:00", **extra):
    return {"item": item, "cycle": cycle, "state": "done", "at": at, **extra}


def _entry(cycle, title, day="2026-09-17", kind=None):
    entry = {"cycle": cycle, "title": title, "date": day, "time": "04:00"}
    if kind:
        entry["kind"] = kind
    return entry


def test_a_cycle_with_no_entry_is_a_gap_line_not_a_missing_line():
    history = _history(_claim(12, "idea-1", note="build it", outcome="merged"),
                       _claim(10, "issue-2"))
    entries = [_entry(12, "Built it"), _entry(10, "Fixed it")]
    payload = planned_done(history, entries, today=TODAY)
    assert [line["cycle"] for line in payload["lines"]] == [12, 11, 10]
    gap = payload["lines"][1]
    assert gap["gap"] is True and gap["planned"] == []
    assert payload["lines"][0]["planned"][0] == {
        "item": "idea-1", "note": "build it", "state": "done", "outcome": "merged"}
    assert payload["lines"][0]["done"] == "Built it"
    assert (payload["shown"], payload["total"], payload["share"]) == (2, 3, 66.7)


def test_a_planned_cycle_that_wrote_nothing_is_still_a_gap():
    """The case the view exists for: it planned, then left no record."""
    payload = planned_done(_history(_claim(5, "idea-9")), [_entry(4, "x")], today=TODAY)
    five = payload["lines"][0]
    assert five["cycle"] == 5 and five["gap"] and five["planned"][0]["item"] == "idea-9"
    assert payload["share"] == 50.0


def test_the_journals_own_silence_marker_does_not_count_as_showing_up():
    entries = [_entry(3, "wrote"), _entry(2, "runner failed", kind="silence"), _entry(1, "wrote")]
    payload = planned_done("", entries, today=TODAY)
    assert [line["gap"] for line in payload["lines"]] == [False, True, False]


def test_the_window_is_three_months():
    entries = [_entry(2, "new"), _entry(1, "old", day="2026-06-01")]
    history = _history(_claim(1, "idea-old", at="2026-06-01T10:00:00+02:00"))
    payload = planned_done(history, entries, today=TODAY)
    assert [line["cycle"] for line in payload["lines"]] == [2]
    assert payload["share"] == 100.0


def test_empty_sources_give_no_number_rather_than_zero():
    payload = planned_done("", [], today=TODAY)
    assert payload["lines"] == [] and payload["share"] is None


def test_a_history_that_is_not_the_expected_shape_raises():
    try:
        planned_done('{"stops": []}', [], today=TODAY)
    except ValueError:
        return
    raise AssertionError("a wrong-shaped history read as empty")


def test_the_page_escapes_what_a_cycle_wrote():
    history = _history(_claim(7, "idea-1", note="<script>x</script>"))
    page = render_page(planned_done(history, [_entry(7, "a & b")], today=TODAY))
    assert "<script>x" not in page and "&lt;script&gt;" in page
    assert "a &amp; b" in page and "/cycle/7" in page


def test_the_planned_page_and_its_endpoint_both_answer():
    history = _history(_claim(1700, "idea-312", note="planned vs done"))
    journal = {"entries": [_entry(1700, "Kept every claim", day=date.today().isoformat()),
                           _entry(1698, "Earlier", day=date.today().isoformat())],
               "status": {}}
    with patch.object(nova_sources, "vault_read_path", return_value=history), \
            patch.object(nova_site, "journal_payload", return_value=journal):
        nova_site.reset_cache()
        status, _, body = _get("/api/planned")
        page_status, _, page = _get("/planned")
    nova_site.reset_cache()
    assert status == 200
    data = json.loads(body)
    assert [line["cycle"] for line in data["lines"]] == [1700, 1699, 1698]
    assert data["lines"][1]["gap"] is True
    assert page_status == 200 and b"Planned vs. done" in page and b"idea-312" in page
