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
    # `roadmap.md` and `goals.md` are two more live vault reads, added when
    # the project page grew a roadmap. Empty by default: these tests are
    # about the regrouping, and the roadmap has its own block at the foot
    # of this file.
    monkeypatch.setattr(nova_site, "plans_payload", lambda: {"documents": []})


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


def test_the_page_summary_carries_the_projected_finish():
    """The wiring, not the arithmetic -- `test_project_pace` owns that.

    Deleting `_pace`'s call site here leaves every other test in this file
    green, because they all read `summary` keys that predate it.
    """
    pace = nova_site.project_payload("Nova")["summary"]["pace"]
    # Three open rows, one of them blocked on the owner -- the projection
    # is over the two I can actually close.
    assert pace["remaining"] == 2
    assert pace["assumes"] == "nothing new is added"


def test_the_index_standing_carries_the_projected_finish_too():
    """Same wiring on the index, which is a separate call site."""
    for standing in nova_site.project_payload()["projectSummary"].values():
        assert "pace" in standing


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


# --- The ordered backlog (idea #228, the backlog half) -------------------
#
# The columns say what state each row is in. `_project_backlog` is the only
# thing on the page that says which row is *next*, and it says it by
# reusing `nova_next.rank` -- the same function `tools.top_board_rows`
# ranks with when a cycle picks its work. The tests below are pointed at
# the two ways that could go quietly wrong: the page ordering by rating
# alone (which loses the raise that sinks a row blocked on him), and
# `project_payload` stamping `board` onto the cached board items in place.


def _rank_row(number, project, priority, priority_key, updated,
              status="⚪ Backlog", status_key="backlog"):
    return {
        "number": number,
        "title": "Row " + str(number),
        "status": status,
        "statusKey": status_key,
        "updated": updated,
        "where": "",
        "priority": priority,
        "priorityKey": priority_key,
        "project": project,
        "done": status_key == "done",
    }


def test_backlog_orders_by_rating_then_by_age(monkeypatch):
    """Worst rating first, and the oldest row first inside a rating.

    The rows are given in an order that is wrong on both counts, so a
    function that returned them untouched, sorted by number, or sorted by
    rating alone fails a different assertion each time.
    """
    rows = [
        _rank_row(50, "Ghost", "⚪ Low", "low", "08-01"),
        _rank_row(51, "Ghost", "🔴 Immediately", "immediate", "08-29"),
        _rank_row(52, "Ghost", "🟠 High", "high", "08-30"),
        _rank_row(53, "Ghost", "🟠 High", "high", "08-02"),
    ]
    monkeypatch.setattr(
        nova_site, "board_payload",
        lambda name: {"items": rows if name == "issues" else []},
    )
    backlog = nova_site.project_payload("Ghost")["backlog"]
    assert [row["number"] for row in backlog] == [51, 53, 52, 50]


def test_a_row_blocked_on_him_sinks_below_an_actionable_one(monkeypatch):
    """The raise a rating-only sort would lose.

    #60 is the worst-rated row on the project and there is nothing a cycle
    can do about it, so it must not be what the page calls next. Sorting
    by rating alone puts it first.
    """
    rows = [
        _rank_row(60, "Ghost", "🔴 Immediately", "immediate", "08-01",
                  status="⏸ Blocked on Edvard", status_key="blocked-on-edvard"),
        _rank_row(61, "Ghost", "⚪ Low", "low", "08-29"),
    ]
    monkeypatch.setattr(
        nova_site, "board_payload",
        lambda name: {"items": rows if name == "issues" else []},
    )
    backlog = nova_site.project_payload("Ghost")["backlog"]
    assert [row["number"] for row in backlog] == [61, 60]


def test_backlog_leaves_out_closed_rows(monkeypatch):
    """Delivered and dropped are both closed, and neither is next.

    This is the cut `_project_summary` makes for the bar above the list, so
    the count in this heading and the "open" count in the summary have to
    come out equal -- asserted here rather than assumed.
    """
    rows = [
        _rank_row(70, "Ghost", "🟠 High", "high", "08-01",
                  status="✅ Done", status_key="done"),
        _rank_row(71, "Ghost", "🟠 High", "high", "08-02",
                  status="🗑 Outdated", status_key="outdated"),
        _rank_row(72, "Ghost", "⚪ Low", "low", "08-03"),
    ]
    monkeypatch.setattr(
        nova_site, "board_payload",
        lambda name: {"items": rows if name == "issues" else []},
    )
    payload = nova_site.project_payload("Ghost")
    assert [row["number"] for row in payload["backlog"]] == [72]
    assert len(payload["backlog"]) == payload["summary"]["open"]


def test_backlog_spans_both_boards_and_breaks_a_tie_toward_the_issue(monkeypatch):
    """One queue, not one per board -- and each row says which board it is on.

    Both rows are the same rating on the same day, so the only thing left
    to order them is the board, and `rank` puts the issue first. The
    `board` key is asserted because the link the page draws is built from
    it: without it every row would point at `/ideas`.
    """
    issues = [_rank_row(80, "Ghost", "🟠 High", "high", "08-10")]
    ideas = [_rank_row(81, "Ghost", "🟠 High", "high", "08-10")]
    monkeypatch.setattr(
        nova_site, "board_payload",
        lambda name: {"items": issues if name == "issues" else ideas},
    )
    backlog = nova_site.project_payload("Ghost")["backlog"]
    assert [(row["board"], row["number"]) for row in backlog] == [
        ("issue", 80), ("idea", 81),
    ]


def test_backlog_does_not_stamp_the_board_onto_the_cached_rows(monkeypatch):
    """`board_payload`'s items are the Issues page's items too.

    `project_payload` needs a `board` key on each row to rank it, and the
    dicts it is handed come out of the shared board cache. Tagging them in
    place would put a key on the Issues and Ideas payloads that nothing
    there put there -- so the tag has to go on a copy, and this fails if it
    ever stops being one.
    """
    rows = [_rank_row(90, "Ghost", "🟠 High", "high", "08-10")]
    monkeypatch.setattr(
        nova_site, "board_payload",
        lambda name: {"items": rows if name == "issues" else []},
    )
    assert nova_site.project_payload("Ghost")["backlog"][0]["board"] == "issue"
    assert "board" not in rows[0]


def test_the_index_carries_no_backlog():
    """`/projects` asks for no project, so there is nothing to order."""
    assert "backlog" not in nova_site.project_payload()


# --- The roadmap (idea #228, the last half) -------------------------------
#
# `roadmap.md` is the order I would work in, and every ranked item names the
# rows it is about. The project page reads that order rather than inventing
# one, so these tests are pointed at the two ways it could quietly lie: an
# item appearing under a project none of its rows belong to, and the page
# showing a subset with no sign that anything was left out.


def _plan(*cards):
    return {"documents": [
        {"key": "goals", "ranked": []},
        {"key": "roadmap", "ranked": list(cards)},
    ]}


def _card(rank, title, board, claim="because", label="In progress"):
    return {
        "rank": rank, "title": title, "board": board, "claim": claim,
        "statusSymbol": "\U0001f7e1", "statusLabel": label, "finished": False,
    }


def test_roadmap_keeps_only_the_items_naming_a_row_in_this_project(monkeypatch):
    """An item is this project's roadmap when a row of its own is in it.

    Card 2 names only rows on other projects. Returning the whole ranked
    strip -- which is what reading `roadmap.md` and stopping would do --
    puts Agora's work on Nova's page.
    """
    monkeypatch.setattr(nova_site, "plans_payload", lambda: _plan(
        _card("1", "Memory", "issue #1, issue #3"),
        _card("2", "Chat", "issue #3"),
        _card("3", "Boards", "idea #10"),
    ))
    roadmap = nova_site.project_payload("Nova")["roadmap"]
    assert [item["title"] for item in roadmap["items"]] == ["Memory", "Boards"]


def test_roadmap_says_which_rows_are_here_and_how_many_are_not(monkeypatch):
    """The link targets, and the count that stops them reading as all of them.

    Card 1 names three rows and only #1 is under Nova; #3 is Agora's and
    #99 is on no board at all. A card that printed three links would send
    him to two rows that are not this project's.
    """
    monkeypatch.setattr(nova_site, "plans_payload", lambda: _plan(
        _card("1", "Memory", "issue #1, issue #3, issue #99"),
    ))
    item = nova_site.project_payload("Nova")["roadmap"]["items"][0]
    assert item["rows"] == [{"board": "issue", "number": 1}]
    assert item["elsewhere"] == 2


def test_roadmap_counts_the_items_that_can_appear_on_no_project_page(monkeypatch):
    """The complement, without which the list reads as the whole roadmap.

    An item with no `board:` field names no row, so no project can ever
    claim it and it is invisible on every project page. Dropping it
    silently is how a page shows two of four ranked items and looks
    complete.
    """
    monkeypatch.setattr(nova_site, "plans_payload", lambda: _plan(
        _card("1", "Memory", "issue #1"),
        _card("2", "Something", ""),
        _card("3", "Else", None),
    ))
    roadmap = nova_site.project_payload("Nova")["roadmap"]
    assert [item["title"] for item in roadmap["items"]] == ["Memory"]
    assert roadmap["unattributed"] == 2


def test_roadmap_matches_an_idea_and_an_issue_of_the_same_number(monkeypatch):
    """`issue #1` and `idea #1` are two different rows.

    Nova owns issue #1 and Sokrates Post owns idea #9. A matcher that
    compared numbers alone would put this card on both pages, and the
    boards genuinely re-use numbers across the two files.
    """
    monkeypatch.setattr(nova_site, "plans_payload", lambda: _plan(
        _card("1", "Memory", "idea #1"),
    ))
    assert nova_site.project_payload("Nova")["roadmap"]["items"] == []
    assert nova_site.project_payload("Nova")["roadmap"]["unattributed"] == 0


def test_roadmap_reads_the_plural_spelling_too(monkeypatch):
    """`issues #1` is the same row -- the field is prose, typed by hand."""
    monkeypatch.setattr(nova_site, "plans_payload", lambda: _plan(
        _card("1", "Memory", "issues #1"),
    ))
    rows = nova_site.project_payload("Nova")["roadmap"]["items"][0]["rows"]
    assert rows == [{"board": "issue", "number": 1}]


def test_roadmap_is_absent_from_the_index(monkeypatch):
    """`/projects` asks for no project, so there is no roadmap to build.

    The early return is what keeps the index off `roadmap.md` entirely --
    the index is one fetch of two board payloads and must stay that way.
    """
    def _boom():
        raise AssertionError("the index must not read the plan")
    monkeypatch.setattr(nova_site, "plans_payload", _boom)
    assert "roadmap" not in nova_site.project_payload()


def test_roadmap_is_empty_when_the_roadmap_document_is_missing(monkeypatch):
    """A vault with no `roadmap.md` in it answers, rather than erroring.

    `plan_payload` renders a missing document as a card with no `ranked`
    key at all, which is a different shape from an empty strip.
    """
    monkeypatch.setattr(nova_site, "plans_payload", lambda: {"documents": [
        {"key": "roadmap", "missing": True},
    ]})
    assert nova_site.project_payload("Nova")["roadmap"] == {
        "items": [], "unattributed": 0,
    }


class TestIndexStandings:
    """Where every project stands, on the index -- idea #228's PM pass.

    His idea asks for someone to *"pretend to be a project manager and
    really think 'what do i need'"*, and the index answered "what projects
    exist" and nothing else: the standing lived only on the page you had
    to open, one tap per project. `projectSummary` is that standing for all
    of them.

    The assertion that matters is the third one. This is a **regroup**, not
    a second measurement -- the whole reason it is computed off the same
    two board payloads is that the index and the project page must not be
    able to disagree -- so it is asserted as *equality with the page*,
    which is a test a second implementation would fail. Counting the rows
    here by hand would pass against two different definitions of "open".
    """

    def test_every_project_gets_a_standing_keyed_lowercase(self):
        payload = nova_site.project_payload()
        assert set(payload["projectSummary"]) == {
            "nova", "agora", "sokrates post"}

    def test_the_index_standing_is_the_project_pages_own_numbers(self):
        """Same rows, same summary -- not a second count of the same board."""
        index = nova_site.project_payload()["projectSummary"]
        for name in ("Nova", "Agora", "Sokrates Post"):
            page = nova_site.project_payload(name)["summary"]
            assert index[name.lower()] == page, name

    def test_the_two_spellings_of_one_project_share_one_standing(self):
        """`Nova` and `nova` are one project everywhere else on this page.

        `projectPriority` is keyed lowercase and `/project/nova` matches
        case-insensitively, so a standing that split them would report the
        same project twice with each half's rows missing from the other.
        Four rows carry a Nova spelling here -- three `Nova`, one `nova` --
        and the standing has to count all four.
        """
        summary = nova_site.project_payload()["projectSummary"]["nova"]
        assert summary["total"] == 4
        assert summary["done"] == 1
        assert summary["open"] == 3
        assert summary["blocked"] == 1

    def test_a_row_with_no_project_is_in_no_project(self, monkeypatch):
        """An empty `Project` cell is not a twelfth bucket.

        324 rows had an empty cell before Cycle 800 filled them, and the
        dashboard was showing all of them as Nova. A blank key here would
        put that bucket back, unnamed, on the index.

        The unprojected row is added here rather than assumed: every row in
        this module's fixture carries a project, so asserting against that
        fixture would pass whether or not the skip exists.
        """
        stray = _row(99, "Stray", "⚪ Backlog", "backlog", "")
        payloads = {
            "issues": {"items": ISSUES + [stray]},
            "ideas": {"items": IDEAS},
        }
        monkeypatch.setattr(nova_site, "board_payload", lambda n: payloads[n])
        payload = nova_site.project_payload()
        assert "" not in payload["projectSummary"]
        # And it is counted nowhere else either -- a stray row quietly
        # joining the biggest project is the failure this replaced.
        assert payload["projectSummary"]["nova"]["total"] == 4

    def test_a_standing_is_sent_with_a_single_project_too(self):
        """The pills and the index list are drawn on the project page too."""
        payload = nova_site.project_payload("Agora")
        assert payload["projectSummary"]["agora"]["open"] == 1
