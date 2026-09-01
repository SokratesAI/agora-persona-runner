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
    # The project ratings are a second live vault read, added when a
    # project became something he can prioritise. Unrated by default here,
    # which is what every project on his board is today, so these tests
    # keep asserting the order the boards produce.
    monkeypatch.setattr(nova_site, "project_priorities", dict)


def test_index_lists_every_project_both_boards_name():
    payload = nova_site.project_payload()
    assert payload["projects"] == ["Nova", "Agora", "nova", "Sokrates Post"]
    assert payload["name"] is None


def test_a_rated_project_is_ranked_to_the_front_of_the_index(monkeypatch):
    """His capture's last line, at the level that makes it a priority.

    *"Each project should also be able to be assigned a priority, making
    one project and its tasks more important than others."* A rating the
    index does not order by is a label, so this asserts the reorder rather
    than the field.
    """
    monkeypatch.setattr(nova_site, "project_priorities", lambda: {
        "sokrates post": {"priority": "🔴 Immediately", "priorityKey": "immediate"},
        "agora": {"priority": "⚪ Low", "priorityKey": "low"},
    })
    payload = nova_site.project_payload()
    # Immediately first, the two unrated in board order under it, Low last.
    assert payload["projects"] == ["Sokrates Post", "Nova", "nova", "Agora"]
    # Keyed lowercase, because the cell is free text he types on a phone
    # and `nova` and `Nova` are two rows of his board but one project name
    # as far as a rating is concerned.
    assert payload["projectPriority"]["agora"]["priorityKey"] == "low"
    assert payload["projectPriority"]["nova"]["priority"] == ""
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


# --- The summary strip (idea #228, the burndown half) -------------------
#
# `_project_summary` is the answer to "how is this project going", and the
# two things worth pinning are both judgement calls rather than arithmetic.
# **A dropped row is not progress**: `outdated` means "will never be built",
# so counting it as done would let a project reach 100% by abandoning
# everything. And **open rows are counted by rating**, worst first, because
# the question a project page is asked is "is there anything red under
# this" and four status columns cannot answer it.


def test_summary_counts_across_both_boards():
    payload = nova_site.project_payload("Nova")
    summary = payload["summary"]
    # Three issues plus one idea are filed under Nova, across two boards
    # and two spellings of the name.
    assert summary["total"] == 4
    assert summary["done"] == 1
    assert summary["open"] == 3
    assert summary["blocked"] == 1
    assert summary["percentDone"] == 25


def test_a_dropped_row_is_not_counted_as_progress(monkeypatch):
    """`outdated` is scope removed, not work delivered.

    Pinned with a project whose *only* closed row is dropped: if the two
    closed statuses were folded together this reads 50% done, and the page
    would tell him half of Ghost had shipped when none of it had.
    """
    rows = [
        _row(20, "Dropped", "🗑 Outdated", "outdated", "Ghost"),
        _row(21, "Open", "⚪ Backlog", "backlog", "Ghost"),
    ]
    monkeypatch.setattr(
        nova_site, "board_payload",
        lambda name: {"items": rows if name == "issues" else []},
    )
    summary = nova_site.project_payload("Ghost")["summary"]
    assert summary["total"] == 2
    assert summary["dropped"] == 1
    assert summary["done"] == 0
    assert summary["open"] == 1
    assert summary["percentDone"] == 0


def test_a_project_that_dropped_everything_is_not_a_hundred_percent(monkeypatch):
    """The mirror of the test above, and the one that catches a `+ dropped`.

    One done row and one dropped row: counting the dropped one into the
    denominator would read 50%, folding it into `done` would read 100%.
    The honest answer is 100% of what is still tracked, with the drop
    reported beside it rather than inside it.
    """
    rows = [
        _row(30, "Shipped", "✅ Done", "done", "Ghost"),
        _row(31, "Dropped", "🗑 Outdated", "outdated", "Ghost"),
    ]
    monkeypatch.setattr(
        nova_site, "board_payload",
        lambda name: {"items": rows if name == "issues" else []},
    )
    summary = nova_site.project_payload("Ghost")["summary"]
    assert summary["percentDone"] == 100
    assert summary["dropped"] == 1
    assert summary["open"] == 0


def test_open_rows_are_counted_by_rating_worst_first(monkeypatch):
    """Worst news first, and unrated last with a word rather than a blank.

    `PRIORITY_LABELS[""]` is the empty string, so an unrated bucket that
    took its label from there would render a count beside nothing.
    """
    rows = [
        dict(_row(40, "A", "⚪ Backlog", "backlog", "Ghost"),
             priority="⚪ Low", priorityKey="low"),
        dict(_row(41, "B", "⚪ Backlog", "backlog", "Ghost"),
             priority="", priorityKey=""),
        dict(_row(42, "C", "🟡 In progress", "in-progress", "Ghost"),
             priority="🔴 Immediately", priorityKey="immediate"),
        # Closed rows carry no rating in the count: a chip on shipped work
        # would make a finished project look busy.
        dict(_row(43, "D", "✅ Done", "done", "Ghost"),
             priority="🟠 High", priorityKey="high"),
    ]
    monkeypatch.setattr(
        nova_site, "board_payload",
        lambda name: {"items": rows if name == "issues" else []},
    )
    summary = nova_site.project_payload("Ghost")["summary"]
    assert [(p["key"], p["count"]) for p in summary["priorities"]] == [
        ("immediate", 1), ("low", 1), ("", 1),
    ]
    assert summary["priorities"][0]["label"] == "🔴 Immediately"
    assert summary["priorities"][-1]["label"] == "Unrated"


def test_the_index_carries_no_summary():
    """`/projects` asks for no project, so there is nothing to summarise."""
    assert "summary" not in nova_site.project_payload()
