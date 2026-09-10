"""The three merged #203 primitives, run over one board instead of three suites.

Each of `rank_key`, `entity_id` and `board_view` is covered on its own.
What these cover is the seam between them -- the thing the migration
commit does and no test did until now.
"""
import json

import pytest

from agora_runner import board_store, board_view, entity_id, nova_boards
from tools import board_migration_preflight as preflight

HEADER = (
    "## Board\n\n"
    "| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |\n"
    "|---|------|--------|---------|----------|---------|------|-----------|-------|\n"
)


def board(rows):
    """Board markdown for `(number, project, milestone)` triples."""
    # `🔵 Medium`, not the legacy `🟡` spelling this fixture used to carry:
    # `parse_board` normalises a legacy glyph on the way out, so a board
    # written the old way does not render back word for word and
    # `document_round_trip` reports the difference -- correctly, and that is
    # a fact about the fixture rather than about the tool.
    lines = [
        f"| [[#{n} — Item {n}\\|{n}]] | Item {n} | ⚪ Backlog | 09-09 "
        f"| 🔵 Medium | {project} | | {milestone} | |"
        for n, project, milestone in rows
    ]
    return HEADER + "\n".join(lines) + "\n"


def compose(rows):
    items = preflight.board_items(board(rows))
    assert len(items) == len(rows), "the fixture board did not parse as rows"
    return preflight.compose(items)


def test_two_spellings_of_one_project_are_one_project():
    """The whole reason `entity_id` exists: `Nova` and `nova` are one thing,
    so eleven spellings of eleven projects must not become twelve ids."""
    report, problems = compose([(1, "Nova", ""), (2, "nova", ""), (3, "Agora", "")])
    assert report["rows"] == 3
    assert report["projects"] == 2
    assert problems == []


def test_projects_that_differ_by_more_than_spelling_stay_apart():
    report, problems = compose([(1, "Nova", ""), (2, "Marcus", ""), (3, "Infra", "")])
    assert report["projects"] == 3
    assert problems == []


def test_one_milestone_name_under_two_projects_is_two_milestones():
    """Milestones are minted inside a project, so `v1` on Nova and `v1` on
    Marcus are different rows to move and must not collapse to one id."""
    report, problems = compose([(1, "Nova", "v1"), (2, "Marcus", "v1")])
    assert report["milestones"] == 2
    assert problems == []


def test_a_blank_project_cell_is_minted_as_nova_rather_than_as_nothing():
    """The finding this module exists to carry, pinned against the real
    parser rather than restated: `parse_board` reads an empty `Project`
    cell as `DEFAULT_PROJECT`, so the migration hands that row a permanent
    `Nova` id. A preflight cannot count project-less rows because none
    ever reaches it."""
    items = preflight.board_items(board([(1, "Marcus", ""), (2, "", "")]))
    assert [item["project"] for item in items] == ["Marcus", nova_boards.DEFAULT_PROJECT]
    report, problems = preflight.compose(items)
    assert report["projects"] == 2
    assert problems == []


def test_every_gap_in_a_board_sized_run_still_takes_an_insert():
    """`between` is tested on pairs; this is `between` on every adjacent
    pair of a sequence the size of the real boards, which is where a
    midpoint runs out of room."""
    rows = [(n, "Nova", "") for n in range(1, 521)]
    report, problems = compose(rows)
    assert report["rank_keys"] == 520
    assert problems == []


def test_two_names_sharing_one_id_is_a_problem_the_report_names(monkeypatch):
    """The check has to be able to fire. Force `ensure_project` to hand two
    genuinely different names the same id and the pair is named."""
    monkeypatch.setattr(
        entity_id, "ensure_project", lambda registry, name: "prj-collapsed"
    )
    _, problems = compose([(1, "Nova", ""), (2, "Marcus", "")])
    assert any("prj-collapsed" in p and "Marcus" in p for p in problems)


def test_assert_clean_exits_two_only_when_something_would_not_compose(tmp_path, monkeypatch):
    clean = tmp_path / "clean.md"
    clean.write_text(board([(1, "Nova", "v1"), (2, "Marcus", "v1")]), encoding="utf-8")
    assert preflight.main(["--board", str(clean), "--assert-clean"]) == 0

    dirty = tmp_path / "dirty.md"
    dirty.write_text(board([(1, "Nova", ""), (2, "Marcus", "")]), encoding="utf-8")
    monkeypatch.setattr(
        entity_id, "ensure_project", lambda registry, name: "prj-collapsed"
    )
    assert preflight.main(["--board", str(dirty), "--assert-clean"]) == 2
    assert preflight.main(["--board", str(dirty)]) == 0


def test_a_board_with_no_rows_composes_to_nothing_rather_than_raising():
    report, problems = preflight.compose([])
    assert report["rows"] == 0 and report["rank_keys"] == 0 and report["projects"] == 0
    assert problems == []


def test_it_needs_a_board_to_look_at():
    with pytest.raises(SystemExit):
        preflight.main([])


class FakeStore(object):
    """A record store that round-trips through JSON, like `board_store`'s own
    fake does -- handing back the caller's own dicts makes a test pass against
    a store it mutated itself."""

    StoreError = RuntimeError

    def __init__(self, held=None):
        self.docs = json.loads(json.dumps(held or {}))
        self.writes = []

    def stored_documents(self, board):
        return json.loads(json.dumps(self.docs))

    def read_rows(self, board):
        return board_store.in_order(json.loads(json.dumps(list(self.docs.values()))))

    def write_rows(self, board, docs, prune=True):
        self.writes.append(len(docs))
        deleted = 0
        if prune:
            keep = {doc["_id"] for doc in docs}
            for doc_id in list(self.docs):
                if doc_id not in keep:
                    del self.docs[doc_id]
                    deleted += 1
        for doc in json.loads(json.dumps(docs)):
            self.docs[doc["_id"]] = doc
        return {"written": len(docs), "deleted": deleted, "unchanged": 0,
                "failures": []}


def items_of(rows):
    return preflight.board_items(board(rows))


def test_round_trip_through_the_store_renders_the_same_markdown():
    """The control the in-memory checks cannot take: a store that dropped a
    cell would compose identically and only show up on the way back."""
    store = FakeStore()
    report, problems = preflight.round_trip(
        items_of([(1, "Nova", "v1"), (2, "Agora", ""), (3, "Agora", "v1")]),
        "issue", store=store)
    assert problems == []
    assert report["rows"] == 3 and report["read_back"] == 3
    assert report["render_identical"] is True


def test_round_trip_names_the_project_cell_the_migration_would_rewrite():
    """A second spelling of one project is one id, and the id resolves back to
    one name -- so the row that used the other spelling comes back rewritten.

    That is the migration doing what `entity_id` is for, not a defect, and it
    is the one place a row's markdown legitimately changes. It has to be
    visible before the switchover rather than found afterwards in a diff of
    the generated backup. No board holds two spellings today (measured
    2026-09-09: 8 project names and 8 ids on issues, 11 and 11 on ideas), so
    this case exists only here."""
    store = FakeStore()
    report, problems = preflight.round_trip(
        items_of([(1, "Nova", ""), (2, "nova", "")]), "issue", store=store)
    assert report["render_identical"] is False
    assert any("differs" in text and "Nova" in text for text in problems)
    assert store.docs == {}, "it left rows behind after a mismatch"


def test_round_trip_restores_the_store_to_empty():
    """It writes into the live namespace, so leaving rows behind would be a
    migration nobody decided to run."""
    store = FakeStore()
    report, problems = preflight.round_trip(items_of([(1, "Nova", "")]), "issue",
                                            store=store)
    assert store.docs == {}
    assert report["restored_to"] == 0 and report["deleted_on_restore"] == 1
    assert problems == []


def test_round_trip_refuses_a_board_that_already_holds_records():
    """Restoring to empty is only a restore when it started empty. Against a
    migrated board this run would tombstone real rows and could not put them
    back, so it must not start."""
    store = FakeStore({"board:issue:1": {"_id": "board:issue:1", "number": 1}})
    with pytest.raises(preflight.RoundTripRefused):
        preflight.round_trip(items_of([(1, "Nova", "")]), "issue", store=store)
    assert store.writes == [], "it wrote before deciding it should not have"
    assert store.docs.keys() == {"board:issue:1"}


def test_round_trip_reports_a_store_that_loses_a_row():
    """A silent drop is the failure this is here to catch, so make the store
    do it rather than trusting that it never would."""

    class Lossy(FakeStore):
        def read_rows(self, board):
            return super().read_rows(board)[:-1]

    store = Lossy()
    report, problems = preflight.round_trip(
        items_of([(1, "Nova", ""), (2, "Agora", "")]), "issue", store=store)
    assert report["read_back"] == 1
    assert any("read back" in text for text in problems)
    assert store.docs == {}, "it left rows behind after a failed comparison"


def test_round_trip_needs_exactly_one_board_file(tmp_path, capsys):
    path = tmp_path / "b.md"
    path.write_text(board([(1, "Nova", "")]), encoding="utf-8")
    with pytest.raises(SystemExit):
        preflight.main(["--board", str(path), "--board", str(path),
                        "--round-trip", "issue"])


def test_round_trip_carries_the_detail_bodies_and_says_how_many():
    """`render_identical` cannot see a lost `# Details` body -- the two
    tables carry no prose -- so a round trip that dropped every one of them
    reported a clean migration. The count is the separate control."""
    store = FakeStore()
    report, problems = preflight.round_trip(
        items_of([(1, "Nova", ""), (2, "Agora", "")]), "issue", store=store,
        details={1: "the reasoning for row 1\n"})
    assert problems == []
    assert report["details_in"] == 1 and report["details_back"] == 1
    assert report["render_identical"] is True


def test_round_trip_reports_a_store_that_loses_a_detail_body():
    """The failure the count exists for, made to happen rather than assumed
    impossible: every other number in the report still reads clean."""

    class Stripped(FakeStore):
        def read_rows(self, board):
            return [{k: v for k, v in doc.items() if k != "detail"}
                    for doc in super().read_rows(board)]

    store = Stripped()
    report, problems = preflight.round_trip(
        items_of([(1, "Nova", "")]), "issue", store=store,
        details={1: "the reasoning for row 1\n"})
    assert report["read_back"] == 1 and report["render_identical"] is True
    assert report["details_in"] == 1 and report["details_back"] == 0
    assert any("detail body" in text for text in problems)


def test_board_details_reads_the_prose_bodies_off_a_board_file():
    """The reader `main` uses; `board_items` beside it returns rows only."""
    markdown = board([(1, "Nova", "")]) + (
        "\n# Details\n\n## #1 — Item 1\n\nthe reasoning for row 1\n")
    details = preflight.board_details(markdown)
    assert list(details) == [1]
    assert "the reasoning for row 1" in details[1]


# --- the whole document, not just its two tables ---------------------------


BOARD_DOC = """---
type: board
contract: Edvard writes in the bullets.
---

- something broke

## Board

| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |
|---|---|---|---|---|---|---|---|---|
| [[#1 — One\\|1]] | One | ⚪ Backlog | 09-09 |  | Nova |  |  |  |

# Details

### #1 — One

The write-up.
"""


def test_frontmatter_is_taken_off_the_document():
    assert preflight.frontmatter_of(BOARD_DOC).startswith("---\ntype: board")
    assert preflight.frontmatter_of(BOARD_DOC).endswith("---")
    assert preflight.frontmatter_of("no frontmatter here") == ""


def test_a_board_it_fully_models_round_trips_with_nothing_lost():
    report, problems = preflight.document_round_trip(BOARD_DOC)
    assert problems == []
    assert report["document_round_trip"] is True
    assert report["document_words_lost"] == 0


def test_a_section_parse_board_does_not_model_now_survives():
    """The failure this check was built to expose, and the fix for it.

    `render_document` used to be built out of `parse_board`'s four keys
    alone, so a section the parser does not model was absent from both
    sides of every comparison written in its own terms -- both live boards
    carried one and the four-key check called them clean. This renders
    through the document's own layout now, so the section comes back.
    """
    report, problems = preflight.document_round_trip(
        BOARD_DOC + "\n## Processed captures\n\n- DONE (Cycle 9): shipped it\n")
    assert report["document_words_lost"] == 0
    assert report["document_round_trip"] is True
    assert problems == []


def test_the_word_stream_still_catches_a_section_that_is_genuinely_dropped():
    """The detector, proved sharp on the render that has no layout.

    The test above no longer fails, and a check that has stopped failing is
    only good news if the instrument still works. So this renders the same
    board *without* a layout -- which is what the four-key renderer did --
    and asserts `words_lost` names the section.
    """
    damaged = BOARD_DOC + "\n## Processed captures\n\n- DONE (Cycle 9): shipped it\n"
    was = nova_boards.parse_board(damaged)
    lost = preflight.words_lost(
        damaged,
        board_view.render_document(was, preflight.frontmatter_of(damaged)))
    assert lost
    assert "Processed" in " ".join(lost)


def test_the_four_key_comparison_alone_would_have_passed_that_board():
    """Names why the word stream is the instrument: the keys agree."""
    damaged = BOARD_DOC + "\n## Processed captures\n\n- DONE (Cycle 9): shipped it\n"
    was = nova_boards.parse_board(damaged)
    now = nova_boards.parse_board(
        board_view.render_document(was, preflight.frontmatter_of(damaged)))
    assert all(was[key] == now[key]
               for key in ("captures", "captureReplies", "items", "details"))


def test_a_word_moved_rather_than_deleted_still_counts_as_lost():
    """A sequence diff, not a bag of words -- a multiset would call this clean."""
    assert preflight.words_lost("alpha beta", "beta alpha") == ["beta"]


def test_the_tail_a_caller_carries_closes_the_hole():
    extra = "## Processed captures\n\n- DONE (Cycle 9): shipped it"
    damaged = BOARD_DOC + "\n" + extra + "\n"
    was = nova_boards.parse_board(damaged)
    # The layout carries his header -- `board_view.board_width` is a floor,
    # so without it a header with `Order` and no positioned row loses the
    # column (#980). `document_round_trip` always passes it.
    rendered = board_view.render_document(
        was, preflight.frontmatter_of(damaged),
        layout=board_view.document_layout(BOARD_DOC), tail=extra)
    assert preflight.words_lost(damaged, rendered) == []


def test_a_reflowed_table_rule_is_not_counted_as_a_lost_word():
    """The renderer redraws every rule at three dashes; the source pads them
    to its column widths. Same rule, different word, no content moved."""
    assert preflight.words_lost("| # | Item |\n|---|------|", "| # | Item |\n|---|---|") == []


def test_a_dashed_token_that_is_not_a_rule_is_still_caught():
    """The one filter above must stay a filter on rules, not on prose."""
    assert preflight.words_lost("|--- important ---|", "") != []
