"""The landing page's single payload -- idea #274, walking-skeleton step 1.

`nova_home.home_payload` is a pure function over payloads the site already
serves, so every test here hands it dicts rather than touching a vault.
That is the point of the split: the composition is the new thing and it is
the thing that can be wrong.
"""

import pytest

from agora_runner.nova_home import TOP_PROJECTS, home_payload


def _projects(names, summaries=None, priorities=None):
    """A `/api/project` index build, as the route returns it."""
    return {
        "projects": list(names),
        "projectSummary": summaries or {},
        "projectPriority": priorities or {},
    }


def _row(project, number, title, milestone="", board="ideas"):
    return {"project": project, "number": number, "title": title,
            "milestone": milestone, "board": board}


def test_takes_the_first_three_projects_in_the_order_handed_over():
    """His hand-dragged order decides the cards, and nothing re-ranks it.

    `/api/project` has already applied `rank_projects`, which reads the
    `Order` column he drags in the app. A second opinion here would move a
    card he placed -- the same defect cycle 1453 fixed on the writer side.
    """
    payload = home_payload(
        {}, _projects(["Nova", "Marcus", "Agora", "NAS", "Infra"]), {}, [])

    assert [card["name"] for card in payload["projects"]] == [
        "Nova", "Marcus", "Agora"]


def test_three_is_the_spec_number_and_a_shorter_list_is_not_padded():
    assert TOP_PROJECTS == 3
    payload = home_payload({}, _projects(["Nova"]), {}, [])
    assert len(payload["projects"]) == 1


def test_a_card_carries_the_counts_the_project_index_already_computed():
    """`done`/`open`/`percentDone` come from `_project_summary`, unchanged.

    The landing page, the project index and the project page must not hold
    three definitions of "how far along is this", so this asserts the
    numbers travel rather than being recomputed: 12 done of 52 tracked is
    23%, and the two dropped rows are reported beside it rather than
    counted as progress.
    """
    payload = home_payload({}, _projects(
        ["Nova"],
        summaries={"nova": {"done": 12, "open": 40, "dropped": 2,
                            "percentDone": 23}},
    ), {}, [])

    card = payload["projects"][0]
    assert (card["done"], card["open"], card["dropped"]) == (12, 40, 2)
    assert card["percentDone"] == 23


def test_the_next_task_and_its_milestone_come_off_the_top_ranked_row():
    """`/api/next` already ranks his board the way a cycle picks from it.

    So the first row it lists under a project *is* the next task, and the
    milestone that row sits in is the milestone being worked. Both are
    read off it rather than derived a second time.
    """
    nxt = {"next": [
        _row("Nova", 274, "Make / a landing page", "Reading what needs him"),
        _row("Nova", 214, "Replace the project stack", "Picking and planning"),
        _row("Marcus", 99, "Draft swims into the week", "The coach"),
    ]}
    payload = home_payload({}, _projects(["Nova", "Marcus"]), nxt, [])

    nova, marcus = payload["projects"]
    assert nova["milestone"] == "Reading what needs him"
    assert nova["next"] == {"board": "ideas", "number": 274,
                            "title": "Make / a landing page"}
    assert marcus["next"]["number"] == 99


def test_a_project_with_no_open_row_says_so_instead_of_a_blank_task():
    """`next: None`, not an empty title.

    A card whose project is clear should read as clear. An empty-string
    title would draw a task line with nothing on it, which reads as a
    render bug rather than as good news.
    """
    payload = home_payload({}, _projects(["Nova"]), {"next": []}, [])
    assert payload["projects"][0]["next"] is None
    assert payload["projects"][0]["milestone"] == ""


def test_the_project_cell_is_matched_case_insensitively():
    """`Nova` and `nova` are two spellings in his cells and one project.

    Every other reader of these cells folds case -- `project_payload` does,
    `_project_summaries` keys lowercase -- so a card built here must find
    the rows whatever he typed, or the busiest project on the page shows
    no next task.
    """
    nxt = {"next": [_row("nova", 274, "Make / a landing page", "Reading")]}
    payload = home_payload({}, _projects(
        ["Nova"], summaries={"nova": {"done": 1, "open": 2, "percentDone": 33}},
    ), nxt, [])

    card = payload["projects"][0]
    assert card["name"] == "Nova"
    assert card["next"]["number"] == 274
    assert card["done"] == 1


def test_needs_you_carries_the_open_asks_and_its_own_count():
    """The page's rule is "only when non-empty", so it gets one field to test."""
    asks = [{"cycle": 1447, "question": "Yes or no?"}]
    payload = home_payload({}, _projects([]), {}, asks)

    assert payload["needsYou"]["asks"] == asks
    assert payload["needsYou"]["count"] == 1


def test_needs_you_is_empty_rather_than_absent_when_nothing_waits():
    payload = home_payload({}, _projects([]), {}, [])
    assert payload["needsYou"] == {"asks": [], "count": 0}


def test_the_recap_travels_whole():
    """The 12-hour card is passed through, not reshaped.

    It opens expanded on this page where it is collapsed on the journal --
    that is a rendering decision, and a server that trimmed the card to
    suit one of the two would make the other wrong.
    """
    recap = {"bullets": ["a", "b"], "stamp": "2026-09-12 14:00"}
    assert home_payload(recap, _projects([]), {}, [])["recap"] == recap


def test_the_live_claims_ride_along_as_the_galaxy_strips_fallback():
    """The strip polls separately; this is what the page draws if it cannot.

    The spec is explicit that the galaxy must never be what the page waits
    for, and that it falls back to the plain list. The list is already in
    `/api/next`, so carrying it costs nothing and removes the case where a
    failed poll leaves the page silent about what is running.
    """
    nxt = {"active": [{"item": "idea-274", "cycle": 1454, "title": "..."}],
           "claimsReadable": True}
    payload = home_payload({}, _projects([]), nxt, [])
    assert payload["active"] == nxt["active"]
    assert payload["claimsReadable"] is True


def test_an_unreadable_claims_ledger_is_reported_rather_than_read_as_empty():
    """An empty ledger and an unreadable one look identical and mean opposites.

    `top_board_rows` and `/api/next` both say which they got; a page that
    dropped the flag would draw "nothing is running" over a broken read.
    """
    payload = home_payload({}, _projects([]), {"claimsReadable": False}, [])
    assert payload["claimsReadable"] is False


@pytest.mark.parametrize("missing", [None, {}])
def test_every_input_may_be_absent_without_raising(missing):
    """A payload that failed to build must not take the whole page down.

    This is the one route the app lands on, so a composition that raised
    when one of four inputs came back empty would turn a single slow
    builder into a blank front page.
    """
    payload = home_payload(missing, missing, missing, missing)
    assert payload["projects"] == []
    assert payload["needsYou"]["count"] == 0
    assert payload["claimsReadable"] is True
