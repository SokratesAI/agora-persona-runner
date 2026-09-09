"""The roadmap points at board rows and the boards move without it.

`roadmap.md` is rewritten when the reasoning changes -- in practice on
Mondays -- so between rewrites a ranked item can be entirely finished and
still sit at rank 1 on the page the owner opens. These pin the four ways
that judgement can go wrong: a finished item read as open, an open item
raised as finished, a row the owner has ticked off, and a sweep that read
one board of two and reported a clean roadmap anyway.

The boards here are **records, not markdown** (issue #203), and the
fixtures mint documents through `board_document.to_document` rather than
writing a table and parsing it. A fixture that went through `parse_board`
would only outlive the parser by accident, and the switchover deletes it.
"""

import pathlib

import pytest

from agora_runner import board_document, board_store, entity_id
from tests.test_board_records import FakeStore
from tools import roadmap_drift


ROADMAP = """# Roadmap

Prose the tool must not judge.

```next
rank: 1
title: Something open
status: in progress
claim: whatever
board: issue #10, idea #20
```

```next
rank: 2
title: Something already ticked
status: done
board: issue #11
```
"""


def _row(board, number, status="⚪ Backlog", done=False):
    """One board row as its record document."""
    return board_document.to_document(
        {"number": number, "status": status, "done": done}, board)


def _store(*docs):
    """A store holding exactly these rows and a *stored* empty registry.

    `_rev` is what makes it stored, and `board_records.contents` refuses a
    registry without one -- an unmigrated store answers `[]` for every board
    and would otherwise read here as a board with no drift.
    """
    return FakeStore(docs, dict(entity_id.new_registry(), _rev="1-abc"))


def _judge(roadmap, store):
    items = roadmap_drift.next_items(roadmap)
    indexes = {board: roadmap_drift.board_index(board, store=store)
               for board in roadmap_drift.BOARDS}
    return items, roadmap_drift.judge(items, indexes)


def test_open_item_standing_on_open_rows_is_not_a_finding():
    store = _store(_row("issue", 10, "🟡 In progress"),
                   _row("idea", 20, "⚪ Backlog"))
    items, findings = _judge(ROADMAP, store)
    # The precondition the negative depends on: rank 1 was actually read.
    assert [i["rank"] for i in items] == ["1", "2"]
    assert findings == []


def test_open_item_whose_every_row_is_closed_is_finished():
    store = _store(_row("issue", 10, "✅ Done"), _row("idea", 20, "✅ Done"))
    _, findings = _judge(ROADMAP, store)
    assert [(kind, item["rank"]) for kind, item, _ in findings] == [("finished", "1")]


def test_one_open_row_is_enough_to_keep_an_item():
    """The rule is *every* named row closed, not *any*."""
    store = _store(_row("issue", 10, "✅ Done"), _row("idea", 20, "⚪ Backlog"))
    _, findings = _judge(ROADMAP, store)
    assert findings == []


def test_blocked_on_edvard_is_open_work_not_a_closed_row():
    """A row waiting on him is exactly what a roadmap should keep naming."""
    store = _store(_row("issue", 10, "✅ Done"),
                   _row("idea", 20, "⏸ Blocked on Edvard"))
    _, findings = _judge(ROADMAP, store)
    assert findings == []


def test_a_row_marked_done_counts_as_closed_whatever_its_status_says():
    """`done` is the old `## Done` table, which carried no status column at
    all -- so the flag has to win over whatever the status cell reads."""
    store = _store(_row("issue", 10, "🟡 In progress", done=True),
                   _row("idea", 20, "⚪ Backlog", done=True))
    _, findings = _judge(ROADMAP, store)
    assert [kind for kind, _, _ in findings] == ["finished"]


def test_an_item_the_roadmap_already_calls_done_is_never_a_finding():
    store = _store(_row("issue", 10, "🟡 In progress"),
                   _row("issue", 11, "✅ Done"),
                   _row("idea", 20, "⚪ Backlog"))
    _, findings = _judge(ROADMAP, store)
    assert findings == []


def test_a_row_that_is_on_neither_board_is_missing():
    store = _store(_row("issue", 10, "🟡 In progress"))
    _, findings = _judge(ROADMAP, store)
    assert [(kind, detail) for kind, _, detail in findings] == \
        [("missing", [("idea", 20)])]


def test_a_row_on_the_other_board_is_still_missing():
    """`issue #10` and `idea #10` are two rows, and the index is per board:
    keying them together would read a finished idea as a finished issue."""
    store = _store(_row("idea", 10, "🟡 In progress"), _row("idea", 20))
    _, findings = _judge(ROADMAP, store)
    assert [(kind, detail) for kind, _, detail in findings] == \
        [("missing", [("issue", 10)])]


def test_an_item_naming_no_rows_is_not_read_as_all_closed():
    roadmap = "```next\nrank: 1\ntitle: No rows\nstatus: backlog\n```\n"
    items, findings = _judge(roadmap, _store())
    assert len(items) == 1
    assert findings == []


def test_references_needs_both_the_word_and_the_hash():
    """A bare number names no board and there are two; a hashless `issue 12`
    is prose about a count, not a row, and reading it as one puts a MISSING
    finding on an item that is fine."""
    assert roadmap_drift.references(
        "issue #131, idea #179, #12, and issue 12 of them") == \
        [("issue", 131), ("idea", 179)]


def test_the_reference_words_are_exactly_the_boards_that_exist():
    """Two lists that have to agree: what the roadmap may write and what the
    store holds. A third board added to one and not the other either KeyErrors
    in `judge` or is silently unjudgeable, so they are held together here."""
    words = {alt for alt in
             roadmap_drift._REF_RE.pattern.split("(")[1].split(")")[0].split("|")}
    assert words == set(board_document.BOARDS) == set(roadmap_drift.BOARDS)


class _HalfReadableStore:
    """The issue board reads; the idea board raises, as an outage does."""

    def __init__(self, store):
        self.store = store

    def read_rows(self, board):
        if board == "idea":
            raise board_store.StoreError("listing idea records: 503 {}")
        return self.store.read_rows(board)

    def read_captures(self, board):
        # The outage is the board, not the query: `contents` asks for the
        # row range and the capture range separately, and a fake that only
        # fails one of them is describing an outage CouchDB does not have.
        if board == "idea":
            raise board_store.StoreError("listing idea captures: 503 {}")
        return self.store.read_captures(board)

    def read_registry(self):
        return self.store.read_registry()


def test_a_board_that_will_not_read_exits_1_and_judges_nothing(tmp_path, capsys):
    roadmap = tmp_path / "roadmap.md"
    roadmap.write_text(ROADMAP, encoding="utf-8")
    store = _HalfReadableStore(_store(_row("issue", 10, "✅ Done")))
    code = roadmap_drift.main(["--roadmap", str(roadmap)], store=store)
    out = capsys.readouterr().out
    assert code == 1
    assert "COULD NOT READ" in out
    # The half it *could* read said "finished" — it must not be reported.
    assert "FINISHED" not in out


def test_a_contradictory_document_is_unread_not_judged_around(tmp_path, capsys):
    """A `RecordError` is a document that disagrees with its own board. The
    rows around it parsed fine, and judging the roadmap against those would
    report every row inside the broken one as missing."""
    roadmap = tmp_path / "roadmap.md"
    roadmap.write_text(ROADMAP, encoding="utf-8")
    bad = dict(_row("idea", 20), type="something-else")
    store = _store(_row("issue", 10, "✅ Done"), bad)
    code = roadmap_drift.main(["--roadmap", str(roadmap)], store=store)
    out = capsys.readouterr().out
    assert code == 1
    assert "COULD NOT READ" in out and "FINISHED" not in out


def test_an_unreadable_roadmap_exits_1_too(tmp_path, capsys):
    code = roadmap_drift.main(["--roadmap", str(tmp_path / "absent.md")],
                              store=_store())
    assert code == 1
    assert "COULD NOT READ" in capsys.readouterr().out


def test_a_clean_roadmap_exits_0_and_a_drifted_one_exits_2(tmp_path):
    roadmap = tmp_path / "roadmap.md"
    roadmap.write_text(ROADMAP, encoding="utf-8")
    argv = ["--roadmap", str(roadmap)]
    open_store = _store(_row("issue", 10, "🟡 In progress"), _row("idea", 20))
    assert roadmap_drift.main(argv, store=open_store) == 0
    closed = _store(_row("issue", 10, "✅ Done"), _row("idea", 20, "✅ Done"))
    assert roadmap_drift.main(argv, store=closed) == 2


def test_the_boards_are_never_read_as_markdown_again(tmp_path):
    """The switchover's own assertion: no board flag, and nothing left that
    could take one. A `--issues` that still worked would be the second live
    source of truth `board-records.md` rules out."""
    with pytest.raises(SystemExit):
        roadmap_drift.main(["--issues", str(tmp_path / "issues.md")],
                           store=_store())
    source = pathlib.Path(roadmap_drift.__file__).read_text(encoding="utf-8")
    assert "parse_board" not in source


def test_the_live_roadmap_parses_into_ranked_items():
    """A parser that returns nothing reports a clean roadmap forever."""
    items = roadmap_drift.next_items(ROADMAP)
    assert [i["title"] for i in items] == ["Something open", "Something already ticked"]
    assert items[1]["finished"] is True
