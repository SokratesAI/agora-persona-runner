"""`/project/<name>` -- idea #92's phase 3, the per-project view.

The plan the owner approved is explicit that this phase invents no data:
*"A kanban view is the board rows grouped by status, which is a
rendering of data phase 2 already produced. Nothing here is new data;
if it turns out to need new data, that is a signal the phase is
wrong."* So every test here holds `project_payload` to being a
**regrouping** -- the rows that come out are the rows that went in, and
the projects it lists are the ones the `Project` cells spell.

Two failures are what the assertions are actually pointed at. **A
project name is free text the owner types on a phone**, so `nova` and
`Nova` are one project and a case-sensitive match makes his own board
look empty. And **an unknown name must answer rather than 404**: he can
type a project into one cell before anything else is filed under it,
and a page that reads as a broken link on a name he just invented is
worse than one that says nothing is there yet.
"""

import pytest

from agora_runner import nova_site


def _row(number, title, status, status_key, project, priority="🟠 High"):
    return {
        "number": number,
        "title": title,
        "status": status,
        "statusKey": status_key,
        "updated": "08-28",
        "where": "",
        "priority": priority,
        "priorityKey": "high",
        "project": project,
        "done": status_key == "done",
    }


ISSUES = [
    _row(1, "One", "🟡 In progress", "in-progress", "Nova"),
    _row(2, "Two", "⚪ Backlog", "backlog", "Nova"),
    _row(3, "Three", "🟡 In progress", "in-progress", "Agora"),
    _row(4, "Four", "✅ Done", "done", "nova", priority=""),
]
IDEAS = [
    _row(9, "Nine", "⚪ Backlog", "backlog", "Sokrates Post"),
    _row(10, "Ten", "⏸ Blocked on Edvard", "blocked-on-edvard", "Nova"),
]


@pytest.fixture(autouse=True)
def _boards(monkeypatch):
    payloads = {
        "issues": {"items": ISSUES},
        "ideas": {"items": IDEAS},
    }
    monkeypatch.setattr(nova_site, "board_payload", lambda name: payloads[name])
    # `cached_payload` reaches CouchDB through `_refresh`; the grouping is
    # what is under test, so the cache is stepped over rather than primed.
    monkeypatch.setattr(nova_site, "cached_payload", lambda name, build: build())


def test_index_lists_every_project_both_boards_name():
    payload = nova_site.project_payload()
    assert payload["projects"] == ["Nova", "Agora", "nova", "Sokrates Post"]
    assert payload["name"] is None
    assert payload["boards"] == {}


def test_rows_are_grouped_by_status_open_columns_first():
    payload = nova_site.project_payload("Nova")
    issues = payload["boards"]["issues"]
    assert issues["total"] == 3
    assert [c["key"] for c in issues["columns"]] == ["in-progress", "backlog", "done"]
    assert [i["number"] for i in issues["columns"][0]["items"]] == [1]
    # Grouped, not rewritten: the row that comes out is the dict that went in.
    assert issues["columns"][0]["items"][0] is ISSUES[0]
    ideas = payload["boards"]["ideas"]
    assert [c["key"] for c in ideas["columns"]] == ["blocked-on-edvard"]


def test_column_label_comes_from_the_shared_status_vocabulary():
    columns = nova_site.project_payload("Nova")["boards"]["issues"]["columns"]
    assert [c["label"] for c in columns] == [
        "🟡 In progress", "⚪ Backlog", "✅ Done",
    ]


def test_empty_status_columns_are_dropped_not_rendered_empty():
    columns = nova_site.project_payload("Agora")["boards"]["issues"]["columns"]
    assert [c["key"] for c in columns] == ["in-progress"]


def test_project_match_is_case_insensitive_and_answers_in_the_boards_spelling():
    payload = nova_site.project_payload("NOVA")
    # Row 4 spells it `nova`; it is the same project and must not be lost.
    assert [i["number"] for i in payload["boards"]["issues"]["columns"][-1]["items"]] == [4]
    # The name that comes back is the one the rows use, so the heading
    # reads the way his board reads rather than the way he typed the URL.
    assert payload["name"] == "Nova"
    assert payload["asked"] == "NOVA"


def test_unknown_project_answers_with_an_empty_board_not_an_error():
    payload = nova_site.project_payload("Newspaper")
    assert payload["name"] is None
    assert payload["asked"] == "Newspaper"
    assert payload["boards"]["issues"]["total"] == 0
    assert payload["boards"]["ideas"]["columns"] == []
    # The index is still filled in, so the page can offer the projects
    # that do exist instead of dead-ending on a name with nothing under it.
    assert "Nova" in payload["projects"]


def test_a_status_nobody_planned_for_lands_at_the_end_rather_than_vanishing():
    rows = [_row(20, "Odd", "🟣 Parked", "parked", "Nova")] + ISSUES
    columns = nova_site._project_columns(rows)
    assert [c["key"] for c in columns][-1] == "parked"
    assert columns[-1]["label"] == "🟣 Parked"


def test_project_urls_are_served_the_shell():
    assert "/projects" in nova_site.PAGE_ROUTES
    assert "/project/" in nova_site.PAGE_ROUTE_PREFIXES
