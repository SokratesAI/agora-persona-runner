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
    # `cached_payload` reaches CouchDB through `_refresh`, so it is stepped
    # over -- but it is replaced by something with **its own return shape**,
    # `(payload, body, etag)`, not by something that returns the payload
    # alone. The old fake returned the payload, and that is what hid a live
    # 500 on this endpoint from the day it shipped: `project_payload` bound
    # the whole tuple and called `.get` on it, and every test passed because
    # no test ever saw the real shape. A fake that is easier to use than the
    # function it replaces is not a simplification, it is a second
    # implementation that the tests agree with instead of the code.
    monkeypatch.setattr(
        nova_site, "cached_payload",
        lambda name, build: (build(), b"", "etag"),
    )
    # The thread is a live vault read and is not what these tests are about;
    # `test_project_thread.py` holds it to its own behaviour.
    monkeypatch.setattr(nova_site, "comments_markdown", lambda: "")


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


THREAD = """# Comments

## New

### Project Nova · 2026-08-28 10:40

Is the pool refilling?

#### Nova · 2026-08-28 10:55

Three times a week.

### Cycle 572 · 2026-08-28 10:20

Good measurement.

## Acknowledged
"""


def test_the_project_page_carries_its_own_conversation_and_nobody_elses(monkeypatch):
    """The bubbles, in the shape `renderRowConversation` already draws.

    A comment and its replies come out as sibling messages rather than
    nested, because that is a conversation on a page even though it is a
    reply inside a comment in the file. And a comment keyed on a cycle is
    not on this page at all -- it belongs to a journal card.
    """
    monkeypatch.setattr(nova_site, "comments_markdown", lambda: THREAD)
    payload = nova_site.project_payload("nova")
    assert [(m["author"], m["stamp"]) for m in payload["comments"]] == [
        ("Edvard", "2026-08-28 10:40"),
        ("Nova", "2026-08-28 10:55"),
    ]
    assert payload["comments"][0]["blocks"]


def test_a_project_with_a_thread_but_no_rows_still_shows_the_conversation(monkeypatch):
    """The thread hangs off the name he asked for, not off the matched one.

    A project only becomes "known" once a row carries it in a `Project`
    cell, so a project he has started talking about and not yet filed
    anything under has `name: null` -- and reading the thread off that
    would blank the conversation he is standing in.
    """
    monkeypatch.setattr(nova_site, "comments_markdown", lambda: THREAD.replace(
        "### Project Nova ·", "### Project Newspaper ·"))
    payload = nova_site.project_payload("Newspaper")
    assert payload["name"] is None
    assert [m["author"] for m in payload["comments"]] == ["Edvard", "Nova"]


def test_a_project_with_nothing_said_about_it_gets_an_empty_thread(monkeypatch):
    monkeypatch.setattr(nova_site, "comments_markdown", lambda: THREAD)
    assert nova_site.project_payload("Agora")["comments"] == []


def test_the_index_does_not_pay_for_a_thread_it_cannot_show(monkeypatch):
    """`/projects` names no project, so there is no thread to read.

    The guard matters because the read is a live vault fetch: doing it
    before the early return would cost every visit to the index a request
    for a document it has nothing to do with.
    """
    def refuse():
        raise AssertionError("the index read comments.md")

    monkeypatch.setattr(nova_site, "comments_markdown", refuse)
    assert "comments" not in nova_site.project_payload()


# --- `/api/project/comment`, the write half -------------------------------


def _post_project_comment(payload):
    """The route through the real handler, so the guards under test are the
    ones a request actually meets."""
    import json as _json
    from tests.test_nova_site import _post

    status, _, body = _post("/api/project/comment", payload)
    return status, _json.loads(body or b"{}")


@pytest.fixture
def _never_writes(monkeypatch):
    """Any call to the vault writer is a test failure.

    Every case below is a *refusal*, and a refusal that still wrote is the
    failure worth catching -- a 400 with the comment stored anyway reads
    identically from the client.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("a refused request wrote to comments.md anyway")

    monkeypatch.setattr(nova_site, "add_project_comment", refuse)


@pytest.mark.parametrize("payload, expected", [
    ({"text": "hi"}, "project must be a name"),
    ({"project": "   ", "text": "hi"}, "project must be a name"),
    ({"project": "Nova", "text": 7}, "text must be a string"),
    ({"project": "Nova", "text": "   "}, "nothing to comment"),
    ({"project": "N" * 121, "text": "hi"}, "at most"),
    # A newline would split the `###` heading and file his text outside any
    # comment; a `·` is the separator the heading parses the stamp on.
    ({"project": "Nova\nAgora", "text": "hi"}, "one line"),
    ({"project": "Nova · Agora", "text": "hi"}, "one line"),
])
def test_a_name_that_would_damage_the_heading_is_refused(payload, expected, _never_writes):
    status, body = _post_project_comment(payload)
    assert status == 400
    assert expected in body["error"]


def test_a_good_comment_is_written_with_the_name_as_typed(monkeypatch):
    seen = {}

    def store(project, text):
        seen["project"] = project
        seen["text"] = text
        return True, "commented"

    monkeypatch.setattr(nova_site, "add_project_comment", store)
    status, body = _post_project_comment({"project": "  Sokrates Post  ", "text": "hi"})
    assert status == 200 and body["ok"]
    # Trimmed, not normalised: the spelling is his and `project_comments`
    # already matches case-insensitively.
    assert seen == {"project": "Sokrates Post", "text": "hi"}
