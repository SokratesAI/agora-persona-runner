"""What a cycle would take next, as a payload his phone can render.

Idea #38. The ranking these tests exercise is not new -- it moved out of
`tools/top_board_rows.py`, which the site could not import because
`tools/` is not in its image -- so `tests/test_top_board_rows.py` still
passes against the same functions through the CLI and is the proof the
move changed no behaviour. What is new is `next_payload`, and every test
here is about the composition: the order of the three lists, what an
unreadable ledger does, and what a project cell means.
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from agora_runner.nova_boards import PRIORITY_LABELS, STATUS_LABELS
from agora_runner.nova_claims import slug_for_row
from agora_runner import nova_boards, nova_next
from agora_runner.nova_boards import parse_board

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 8, 30, 17, 0, tzinfo=OSLO)

IMMEDIATE = PRIORITY_LABELS["immediate"]
HIGH = PRIORITY_LABELS["high"]
LOW = PRIORITY_LABELS["low"]
BACKLOG = STATUS_LABELS["backlog"]
DONE_STATUS = STATUS_LABELS["done"]


def next_payload(issues_markdown, ideas_markdown, *args, **kwargs):
    """Two board strings through the parser, then the payload the site builds.

    `nova_next.next_payload` was a markdown door over this and issue #203
    deleted it; the tests below were written against strings, so the parse
    lives here now, on the test side of the line.
    """
    return nova_next.next_payload_from_contents(
        parse_board(issues_markdown), parse_board(ideas_markdown),
        *args, **kwargs)


def board(*rows, captures=(), project=False):
    """A board file, optionally with the owner's `Project` column and captures."""
    head = []
    for bullet in captures:
        head.append("- " + bullet)
    if captures:
        head.append("")
    cols = "| # | Item | Status | Updated | Priority |"
    rule = "|---|---|---|---|---|"
    if project:
        cols = "| # | Item | Status | Updated | Priority | Project |"
        rule = "|---|---|---|---|---|---|"
    head += ["## Board", "", cols, rule]
    for row in rows:
        number, title, status, updated, priority = row[:5]
        line = (f"| [[#{number} — {title}\\|{number}]] | {title} "
                f"| {status} | {updated} | {priority} |")
        if project:
            line += f" {row[5]} |"
        head.append(line)
    head += ["", "## Done", "", "| # | Item | Updated | Where |", "|---|---|---|---|"]
    return "\n".join(head) + "\n"


def ledger(*claims):
    return json.dumps({"claims": list(claims)})


def test_the_ranked_list_is_the_order_a_cycle_would_take_them():
    issues = board((10, "a high issue", BACKLOG, "08-01", HIGH))
    ideas = board((64, "the immediate idea", BACKLOG, "08-12", IMMEDIATE))
    payload = next_payload(issues, ideas, ledger(), NOW)
    assert [(r["board"], r["number"]) for r in payload["next"]] == [
        ("idea", 64), ("issue", 10)]


def test_his_unfiled_captures_come_back_separately_from_the_board():
    """They outrank every row, so they cannot be mixed into the ranking."""
    issues = board((10, "a row", BACKLOG, "08-01", HIGH),
                   captures=["fix the thing on the NAS"])
    payload = next_payload(issues, board(), ledger(), NOW)
    assert [c["text"] for c in payload["captures"]] == ["fix the thing on the NAS"]
    assert [r["number"] for r in payload["next"]] == [10]


def test_a_capture_already_boarded_and_closed_is_not_unprocessed():
    """Cycle 1007 woke to two finished captures ranked above the whole board.

    `board_capture` cuts the bullet as it writes the row, so a bullet and a
    row with the same sentence only coexist when somebody boarded by hand --
    which is what happened to idea #258 and issue #189 on 2026-09-05.
    """
    issues = board((189, "the handover about merge_pr", DONE_STATUS, "09-05", HIGH),
                   (10, "a real row", BACKLOG, "08-01", HIGH),
                   captures=["the handover about merge_pr"])
    payload = next_payload(issues, board(), ledger(), NOW)
    assert payload["captures"] == []
    assert [r["number"] for r in payload["next"]] == [10]


def test_a_capture_boarded_on_an_open_row_is_kept_and_stamped():
    """Hiding it would lose real work; the reader is told where it lives."""
    issues = board((77, "move something to server2", BACKLOG, "09-05", HIGH),
                   captures=["move something to server2"])
    payload = next_payload(issues, board(), ledger(), NOW)
    assert [c["text"] for c in payload["captures"]] == ["move something to server2"]
    assert payload["captures"][0]["boardedAs"]["number"] == 77
    assert payload["captures"][0]["boardedAs"]["status"] == BACKLOG


def test_a_capture_that_matches_no_row_carries_no_boarding():
    """The stamp must not fire on a bullet he has just typed."""
    issues = board((189, "some other row", DONE_STATUS, "09-05", HIGH),
                   captures=["fix the thing on the NAS"])
    payload = next_payload(issues, board(), ledger(), NOW)
    assert [c["text"] for c in payload["captures"]] == ["fix the thing on the NAS"]
    assert payload["captures"][0]["boardedAs"] is None


def test_boarding_is_matched_across_line_wrapping_only():
    """Whitespace differs without anything being meant by it; wording does not."""
    issues = board((189, "a  long   sentence", DONE_STATUS, "09-05", HIGH),
                   captures=["a long sentence", "a long sentence but not really"])
    payload = next_payload(issues, board(), ledger(), NOW)
    assert [c["text"] for c in payload["captures"]] == ["a long sentence but not really"]


def test_a_closed_row_does_not_drop_a_capture_from_the_other_board():
    """The two boards are separate pages; a row on one says nothing about the other."""
    issues = board((189, "the handover about merge_pr", DONE_STATUS, "09-05", HIGH))
    ideas = board(captures=["the handover about merge_pr"])
    payload = next_payload(issues, ideas, ledger(), NOW)
    assert [c["text"] for c in payload["captures"]] == ["the handover about merge_pr"]
    assert payload["captures"][0]["boardedAs"] is None


def test_an_open_duplicate_row_wins_over_a_closed_one():
    """He re-captured the same sentence; the open row is the one to be sent to."""
    issues = board((189, "the same sentence", DONE_STATUS, "09-05", HIGH),
                   (190, "the same sentence", BACKLOG, "09-05", HIGH),
                   captures=["the same sentence"])
    payload = next_payload(issues, board(), ledger(), NOW)
    assert payload["captures"][0]["boardedAs"]["number"] == 190


def test_a_live_claim_says_who_is_on_it_and_names_the_row():
    issues = board((10, "a row somebody took", BACKLOG, "08-01", HIGH))
    held = ledger({"item": slug_for_row("issue", 10), "cycle": 668,
                   "at": "2026-08-30T16:58:00+02:00", "state": "open"})
    payload = next_payload(issues, board(), held, NOW)
    assert payload["active"] == [{"item": slug_for_row("issue", 10), "cycle": 668,
                                  "title": "a row somebody took",
                                  "board": "issue", "number": 10}]
    assert payload["next"][0]["heldBy"] == 668


def test_a_claim_on_something_that_is_not_a_board_row_carries_no_title():
    """Handoff and capture slugs hash their text; a title here would be invented."""
    held = ledger({"item": "capture-12a1f8d8c624", "cycle": 667,
                   "at": "2026-08-30T16:58:00+02:00", "state": "open"})
    payload = next_payload(board(), board(), held, NOW)
    assert payload["active"][0]["title"] == ""
    assert payload["active"][0]["number"] is None


def test_a_stale_claim_is_not_live_work():
    """45 minutes is the turn cap, so an older claim is a killed cycle."""
    held = ledger({"item": slug_for_row("issue", 10), "cycle": 600,
                   "at": "2026-08-30T15:00:00+02:00", "state": "open"})
    payload = next_payload(board((10, "a row", BACKLOG, "08-01", HIGH)),
                           board(), held, NOW)
    assert payload["active"] == []
    assert payload["next"][0]["heldBy"] is None


def test_an_unreadable_ledger_is_not_an_empty_one():
    payload = next_payload(board((10, "a row", BACKLOG, "08-01", HIGH)),
                           board(), "{not json", NOW)
    assert payload["claimsReadable"] is False
    assert payload["active"] == []
    assert [r["number"] for r in payload["next"]] == [10]


def test_projects_are_ordered_by_their_best_row_not_by_count():
    issues = board((10, "one urgent thing", BACKLOG, "08-01", IMMEDIATE, "NAS"),
                   (11, "a small thing", BACKLOG, "08-02", LOW, "Site"),
                   (12, "another small thing", BACKLOG, "08-03", LOW, "Site"),
                   project=True)
    payload = next_payload(issues, board(), ledger(), NOW)
    assert [(p["name"], p["open"]) for p in payload["projects"]] == [("NAS", 1), ("Site", 2)]
    assert payload["projects"][0]["top"] == "one urgent thing"


def test_a_group_carries_its_top_rows_address_and_not_only_its_title():
    """So a caller outside `next[:5]` can still point at the row.

    `next` is `ranked[:top]` -- five rows -- and on 2026-09-12 all five were
    Nova rows, so the landing page drew a blank task line on every other
    project that had open work. This grouping already walks every ranked row,
    so the address was in hand and only the title was published. The board and
    the number are what a link needs; the milestone is what the card prints
    above it.
    """
    issues = board((10, "one urgent thing", BACKLOG, "08-01", IMMEDIATE, "NAS"),
                   (11, "a small thing", BACKLOG, "08-02", LOW, "Site"),
                   project=True)
    groups = {p["name"]: p for p in next_payload(issues, board(), ledger(), NOW)["projects"]}

    assert groups["Site"]["top"] == "a small thing"
    assert groups["Site"]["topNumber"] == 11
    assert groups["Site"]["topBoard"] == "issue"
    # Same row, so the three `top*` fields and `top` can never disagree.
    assert groups["NAS"]["topNumber"] == 10


def test_a_group_keeps_the_best_rows_address_and_not_a_later_ones():
    """The group is seeded on first sight and the ranking is best-first.

    A grouping that overwrote on every row would publish the project's
    *worst* open row as its next task, and `top` would still read correctly
    because it was set in the same branch -- so this asserts the number
    rather than the title.
    """
    issues = board((20, "the urgent one", BACKLOG, "08-01", IMMEDIATE, "NAS"),
                   (21, "the quiet one", BACKLOG, "08-02", LOW, "NAS"),
                   project=True)
    groups = {p["name"]: p for p in next_payload(issues, board(), ledger(), NOW)["projects"]}

    assert groups["NAS"]["open"] == 2
    assert groups["NAS"]["topNumber"] == 20


def test_an_empty_project_cell_is_nova_because_that_is_the_board_default():
    """`nova_boards.DEFAULT_PROJECT`, not a second opinion decided here."""
    issues = board((10, "unfiled", BACKLOG, "08-01", HIGH, ""), project=True)
    payload = next_payload(issues, board(), ledger(), NOW)
    assert [p["name"] for p in payload["projects"]] == ["Nova"]


def test_a_board_with_no_project_column_still_files_every_row():
    """His boards grew the column; a board without one is not project-less."""
    payload = next_payload(board((10, "a row", BACKLOG, "08-01", HIGH)),
                           board(), ledger(), NOW)
    assert [(p["name"], p["open"]) for p in payload["projects"]] == [("Nova", 1)]


def test_the_ranked_list_is_cut_and_the_blocked_rows_are_not_in_it():
    blocked = STATUS_LABELS["blocked-on-edvard"]
    issues = board((10, "waiting on him", blocked, "08-01", IMMEDIATE),
                   (11, "actionable", BACKLOG, "08-02", LOW))
    payload = next_payload(issues, board(), ledger(), NOW, top=1)
    assert [r["number"] for r in payload["next"]] == [11]
    assert [r["number"] for r in payload["waiting"]] == [10]


PROJECTS = "\n".join([
    "# Projects",
    "",
    "| Project | Priority | Updated |",
    "|---|---|---|",
    f"| Marcus | {IMMEDIATE} | 09-05 |",
    f"| Nova | {HIGH} | 09-01 |",
    f"| Demos | {LOW} | 09-02 |",
    "",
]) + "\n"


def test_a_medium_row_in_his_top_project_outranks_a_high_row_below_it():
    """The whole of milestone M1: the project cell now decides the order.

    Both rows are the same age and neither is claimed or blocked, so under
    the flat ranking the High row wins on its own rating alone -- which is
    the disconnect the redesign note names, and the assertion that would
    have passed before this change.
    """
    issues = board((10, "a high row in a low project", BACKLOG, "08-01", HIGH,
                    "Demos"), project=True)
    ideas = board((64, "a medium row in his top project", BACKLOG, "08-01",
                   PRIORITY_LABELS["medium"], "Marcus"), project=True)

    flat = next_payload(issues, ideas, ledger(), NOW)
    assert flat["next"][0]["number"] == 10

    ranked = next_payload(issues, ideas, ledger(), NOW, projects_markdown=PROJECTS)
    assert ranked["next"][0]["number"] == 64
    assert ranked["next"][1]["number"] == 10


def test_immediately_skips_to_the_top_over_the_project_order():
    """Tier 2 sits above tier 3, which is the spec's order and not a guess.

    Without the skip-to-top key an Immediately row in his bottom-rated
    project would sort below every row in Marcus, and the one label he has
    to say "this, next" would mean less than it did before the change.
    """
    issues = board((10, "immediate, in a low project", BACKLOG, "08-01",
                    IMMEDIATE, "Demos"), project=True)
    ideas = board((64, "high, in his top project", BACKLOG, "08-01", HIGH,
                   "Marcus"), project=True)

    ranked = next_payload(issues, ideas, ledger(), NOW, projects_markdown=PROJECTS)
    assert ranked["next"][0]["number"] == 10


def test_a_project_missing_from_his_list_sorts_behind_an_unrated_listed_one():
    """Unlisted used to rank 5, the same number an unrated listed project gets.

    Cycle 1410 found Infra, Maintenance and Research level with Sokrates Docs
    that way. The unlisted row here is the OLDER one, so on a tie the age key
    puts it first and the assertion fails; only a separate last place for an
    unlisted project passes.
    """
    projects = PROJECTS.replace(f"| Demos | {LOW} | 09-02 |",
                                f"| Demos | {LOW} | 09-02 |\n| Docs |  | 09-03 |")
    assert nova_next.project_ranks(projects)["docs"] == 5, "the tie this test is about"
    issues = board((10, "a row in an unlisted project", BACKLOG, "08-01", LOW,
                    "Infra"), project=True)
    ideas = board((64, "a row in an unrated listed project", BACKLOG, "08-20",
                   LOW, "Docs"), project=True)

    ranked = next_payload(issues, ideas, ledger(), NOW, projects_markdown=projects)
    assert [r["number"] for r in ranked["next"]] == [64, 10]


def test_an_immediately_row_waiting_on_him_does_not_skip_the_queue():
    """The skip-to-top key sits under the blocked key, on purpose.

    His capture of 2026-09-11 asks for an Immediately row to be done by the
    very next cycle; a row blocked on him cannot be, so it must not take the
    top line from work a cycle can actually do. Idea #260 is exactly this
    row on his live board today.
    """
    issues = board((10, "immediate, but blocked on him", "⏸ Blocked on Edvard",
                    "08-01", IMMEDIATE, "Marcus"), project=True)
    ideas = board((64, "high, in his bottom project", BACKLOG, "08-20", HIGH,
                   "Demos"), project=True)

    ranked = next_payload(issues, ideas, ledger(), NOW, projects_markdown=PROJECTS)
    assert [r["number"] for r in ranked["next"]] == [64, 10]


def test_a_project_he_has_not_rated_sorts_below_every_rated_one():
    """Unrated is not Low, and it is not first either -- `_RANK`'s own rule.

    `NAS` is a real project on his board with no row in `projects.md`, and
    the row here is rated Immediately at the row level precisely so the
    only thing deciding the order is the project cell.
    """
    issues = board((10, "a low row in his lowest rated project", BACKLOG,
                    "08-01", LOW, "Demos"), project=True)
    ideas = board((64, "a low row in an unrated project", BACKLOG, "08-01",
                   LOW, "NAS"), project=True)

    ranked = next_payload(issues, ideas, ledger(), NOW, projects_markdown=PROJECTS)
    assert [r["number"] for r in ranked["next"]] == [10, 64]


def test_a_blocked_row_stays_below_an_actionable_one_in_a_worse_project():
    """The project order goes underneath the tiers that already existed.

    A row nobody can take is still a row nobody can take, whichever
    project it belongs to -- issue #94 topped this list for five days.
    """
    issues = board((10, "blocked, in his top project", STATUS_LABELS["blocked-on-edvard"],
                    "08-01", IMMEDIATE, "Marcus"), project=True)
    ideas = board((64, "actionable, in his lowest", BACKLOG, "08-01", LOW,
                   "Demos"), project=True)

    ranked = next_payload(issues, ideas, ledger(), NOW, projects_markdown=PROJECTS)
    assert ranked["next"][0]["number"] == 64


def test_an_empty_project_cell_ranks_as_nova_not_as_unrated():
    """`parse_board` fills a blank with `DEFAULT_PROJECT`, so there is no
    unfiled bucket -- and the fifteen rows still missing a cell therefore
    rank as Nova (High) rather than sinking below every rated project."""
    issues = board((10, "no project cell at all", BACKLOG, "08-01", LOW))
    ideas = board((64, "filed under a project he rates Low", BACKLOG, "08-01",
                   LOW, "Demos"), project=True)

    ranked = next_payload(issues, ideas, ledger(), NOW, projects_markdown=PROJECTS)
    assert ranked["next"][0]["number"] == 10


# --- Issue #203: the contents-taking seam under the three markdown doors ---
#
# The switchover replaces his two board tables with one CouchDB document
# per row, and `board_records.contents` returns exactly the four keys
# `parse_board` does. These tests are about the seam that lets a caller be
# converted one at a time: the rule moved into a `*_from_contents`
# function, and the old name is a door that parses and delegates.


def _parse_board_is_a_landmine(monkeypatch):
    """Make any markdown parse through `nova_boards` raise, loudly."""
    def refuse(*args, **kwargs):
        raise AssertionError(
            "a *_from_contents function parsed markdown: it is supposed to "
            "have no way to reach a board file at all")
    monkeypatch.setattr(nova_boards, "parse_board", refuse)


def test_the_contents_readers_never_reach_markdown(monkeypatch):
    """An assertion about the answer would pass on a function that re-parsed.

    Handing `parse_board(markdown)` to something that then calls
    `parse_board` again on a string it kept would return the same rows, so
    an assertion about the answer is a positive result guaranteed in
    advance. This is the check that can fail: with the parser replaced by
    something that raises, a `*_from_contents` that still touches markdown
    dies here.
    """
    markdown = board((10, "a row", BACKLOG, "08-01", HIGH),
                     captures=["a bullet he typed"])
    issues, ideas = parse_board(markdown), parse_board(board())
    _parse_board_is_a_landmine(monkeypatch)

    assert nova_next.unboarded_captures_from_contents(issues, "issues")
    assert nova_next.open_rows_from_contents(issues, "issue")
    assert nova_next.next_payload_from_contents(issues, ideas, ledger(), NOW)


def test_the_landmine_is_armed(monkeypatch):
    """The test above proves nothing if the patch misses the name it uses.

    `nova_next` used to import `parse_board` into its own namespace, where
    patching `nova_boards` would miss it. Issue #203 deleted that import
    with the last markdown door, so the one name left is `nova_boards`'s,
    and this fails first if the import ever comes back.
    """
    assert not hasattr(nova_next, "parse_board")
    _parse_board_is_a_landmine(monkeypatch)
    try:
        nova_boards.parse_board(board())
    except AssertionError:
        return
    raise AssertionError("the parser was not patched in nova_boards")


def test_a_row_and_its_comment_thread_come_out_of_one_contents():
    """The old markdown door read the file twice; this reads once.

    Safe on a string and not on a store -- a write landing between the row
    read and the thread read drops a comment on a waiting row out of the
    answer. So the details this function judges `waiting` on have to be the
    ones in the dict it was handed, not ones it went and fetched.
    """
    markdown = board((10, "a row", BACKLOG, "08-01", HIGH))
    contents = parse_board(markdown)
    contents["details"][10] = (
        "### #10 — a row\n\n**Nova, 09-01:** did it\n"
        "\n**Edvard, 09-02:** are you sure?\n")
    rows = nova_next.open_rows_from_contents(contents, "issue")
    assert [r["waiting"] for r in rows] == [True]
    assert rows[0]["replySlug"]

    del contents["details"][10]
    assert [r["waiting"] for r in nova_next.open_rows_from_contents(
        contents, "issue")] == [False]
