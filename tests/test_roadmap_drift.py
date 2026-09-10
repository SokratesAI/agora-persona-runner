"""The roadmap points at board rows and the boards move without it.

`roadmap.md` is rewritten when the reasoning changes -- in practice on
Mondays -- so between rewrites a ranked item can be entirely finished and
still sit at rank 1 on the page the owner opens. These pin the four ways
that judgement can go wrong: a finished item read as open, an open item
raised as finished, a row that has moved to `## Done`, and a sweep that
read one board of two and reported a clean roadmap anyway.
"""

import pytest

from tools import board_migration_preflight, roadmap_drift


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


def _board(rows, done_rows=()):
    """A minimal board file: `## Board` and `## Done` in the live shape."""
    text = ["# Board", "", "## Board", "",
            "| # | Title | Status | Updated | Priority |",
            "|---|---|---|---|---|"]
    for number, status in rows:
        text.append("| [[#%d — Row %d\\|%d]] | Row %d | %s | 2026-09-01 | 🟠 High |"
                    % (number, number, number, number, status))
    text += ["", "## Done", "",
             "| # | Title | Updated | Where |",
             "|---|---|---|---|"]
    for number, status in done_rows:
        text.append("| [[#%d — Row %d\\|%d]] | Row %d | %s | #1 |"
                    % (number, number, number, number, status))
    return "\n".join(text) + "\n"


def _contents(markdown):
    """A board markdown string as the four keys `board_contents` answers.

    The migration seam, not the parser: these tests judge the roadmap and
    the shape they feed it has to be the shape the record store hands over,
    or they pin the old reader while the tool reads the new one.
    """
    return board_migration_preflight.board_contents(markdown)


def _judge(roadmap, issues, ideas):
    items = roadmap_drift.next_items(roadmap)
    indexes = {"issues": roadmap_drift.board_index(_contents(issues)),
               "ideas": roadmap_drift.board_index(_contents(ideas))}
    return items, roadmap_drift.judge(items, indexes)


def _store(rows_by_board):
    """A stand-in for `board_records.board_store` that records what it was asked.

    `rows_by_board` is `{singular board: [row document]}`. It answers the
    three methods `board_records.contents` needs and nothing else, so a
    reader that reached for a fourth would fail here rather than silently
    fall back to a file.
    """
    asked = []

    class Store:
        def read_registry(self):
            asked.append("registry")
            return {"_rev": "1-abc", "projects": {}, "milestones": {}}

        def read_rows(self, board):
            asked.append(board)
            return list(rows_by_board.get(board, []))

        def read_captures(self, board):
            return []

    return Store(), asked


def test_open_item_standing_on_open_rows_is_not_a_finding():
    issues = _board([(10, "🟡 In progress")])
    ideas = _board([(20, "⚪ Backlog")])
    items, findings = _judge(ROADMAP, issues, ideas)
    # The precondition the negative depends on: rank 1 was actually read.
    assert [i["rank"] for i in items] == ["1", "2"]
    assert findings == []


def test_open_item_whose_every_row_is_closed_is_finished():
    issues = _board([(10, "✅ Done")])
    ideas = _board([(20, "✅ Done")])
    _, findings = _judge(ROADMAP, issues, ideas)
    assert [(kind, item["rank"]) for kind, item, _ in findings] == [("finished", "1")]


def test_one_open_row_is_enough_to_keep_an_item():
    """The rule is *every* named row closed, not *any*."""
    issues = _board([(10, "✅ Done")])
    ideas = _board([(20, "⚪ Backlog")])
    _, findings = _judge(ROADMAP, issues, ideas)
    assert findings == []


def test_blocked_on_edvard_is_open_work_not_a_closed_row():
    """A row waiting on him is exactly what a roadmap should keep naming."""
    issues = _board([(10, "✅ Done")])
    ideas = _board([(20, "⏸ Blocked on Edvard")])
    _, findings = _judge(ROADMAP, issues, ideas)
    assert findings == []


def test_a_row_in_the_done_table_counts_as_closed():
    """`## Done` carries no status column at all -- the table is the verdict."""
    issues = _board([], done_rows=[(10, "09-01")])
    ideas = _board([], done_rows=[(20, "09-01")])
    _, findings = _judge(ROADMAP, issues, ideas)
    assert [kind for kind, _, _ in findings] == ["finished"]


def test_an_item_the_roadmap_already_calls_done_is_never_a_finding():
    issues = _board([(10, "🟡 In progress"), (11, "✅ Done")])
    ideas = _board([(20, "⚪ Backlog")])
    _, findings = _judge(ROADMAP, issues, ideas)
    assert findings == []


def test_a_row_that_is_on_neither_board_is_missing():
    issues = _board([(10, "🟡 In progress")])
    ideas = _board([])
    _, findings = _judge(ROADMAP, issues, ideas)
    assert [(kind, detail) for kind, _, detail in findings] == \
        [("missing", [("idea", 20)])]


def test_an_item_naming_no_rows_is_not_read_as_all_closed():
    roadmap = "```next\nrank: 1\ntitle: No rows\nstatus: backlog\n```\n"
    items, findings = _judge(roadmap, _board([]), _board([]))
    assert len(items) == 1
    assert findings == []


def test_references_needs_both_the_word_and_the_hash():
    """A bare number names no board and there are two; a hashless `issue 12`
    is prose about a count, not a row, and reading it as one puts a MISSING
    finding on an item that is fine."""
    assert roadmap_drift.references(
        "issue #131, idea #179, #12, and issue 12 of them") == \
        [("issue", 131), ("idea", 179)]


def test_an_unreadable_board_exits_1_and_judges_nothing(tmp_path, capsys):
    roadmap = tmp_path / "roadmap.md"
    roadmap.write_text(ROADMAP, encoding="utf-8")
    issues = tmp_path / "issues.md"
    issues.write_text(_board([(10, "✅ Done")]), encoding="utf-8")
    code = roadmap_drift.main(["--roadmap", str(roadmap), "--issues", str(issues),
                               "--ideas", str(tmp_path / "absent.md")])
    out = capsys.readouterr().out
    assert code == 1
    assert "COULD NOT READ" in out
    # The half it *could* read said "finished" — it must not be reported.
    assert "FINISHED" not in out


def test_a_clean_roadmap_exits_0_and_a_drifted_one_exits_2(tmp_path):
    roadmap = tmp_path / "roadmap.md"
    roadmap.write_text(ROADMAP, encoding="utf-8")
    issues = tmp_path / "issues.md"
    ideas = tmp_path / "ideas.md"
    ideas.write_text(_board([(20, "⚪ Backlog")]), encoding="utf-8")
    issues.write_text(_board([(10, "🟡 In progress")]), encoding="utf-8")
    argv = ["--roadmap", str(roadmap), "--issues", str(issues), "--ideas", str(ideas)]
    assert roadmap_drift.main(argv) == 0
    ideas.write_text(_board([(20, "✅ Done")]), encoding="utf-8")
    issues.write_text(_board([(10, "✅ Done")]), encoding="utf-8")
    assert roadmap_drift.main(argv) == 2


def test_the_live_roadmap_parses_into_ranked_items():
    """A parser that returns nothing reports a clean roadmap forever."""
    items = roadmap_drift.next_items(ROADMAP)
    assert [i["title"] for i in items] == ["Something open", "Something already ticked"]
    assert items[1]["finished"] is True


def test_board_contents_reads_the_record_store_and_opens_no_file(tmp_path):
    """The switchover itself: with no `local`, the rows come out of the store.

    The assertion that carries it is *which door was opened* -- the store
    was asked for the singular board name and `open` was never called. A
    structural comparison of the two doors cannot fail, because both answer
    the same four keys by definition.
    """
    import builtins

    store, asked = _store({})
    saved = builtins.open

    def refuse(*a, **k):
        raise AssertionError("board_contents opened a file: %r" % (a,))

    builtins.open = refuse
    try:
        answer = roadmap_drift.board_contents("issue", store=store)
    finally:
        builtins.open = saved
    assert asked == ["registry", "issue"]
    assert answer["items"] == []


def test_a_local_board_file_still_goes_through_the_migration_seam(tmp_path):
    """`--issues`/`--ideas` are unchanged in what they take: a markdown path."""
    path = tmp_path / "issues.md"
    path.write_text(_board([(10, "🟡 In progress")]), encoding="utf-8")
    store, asked = _store({})
    contents = roadmap_drift.board_contents("issue", local=str(path), store=store)
    assert roadmap_drift.board_index(contents) == {10: "in-progress"}
    # The precondition the negative depends on: the store really was
    # available and really would have answered, so "the file won" is a
    # measurement rather than the only thing that could have happened.
    assert asked == []
    assert roadmap_drift.board_index(
        roadmap_drift.board_contents("issue", store=store)) == {}


def test_an_unmigrated_store_is_unreadable_and_never_a_drifted_roadmap(tmp_path,
                                                                      capsys):
    """A store that will not answer must not read as a board with no rows.

    Every roadmap item names a row, so an empty board turns all of them into
    MISSING findings -- a confident wrong verdict on a board the tool never
    saw, at exit 2, which is worse than saying nothing.
    """
    roadmap = tmp_path / "roadmap.md"
    roadmap.write_text(ROADMAP, encoding="utf-8")

    class Refusing:
        def read_registry(self):
            raise RuntimeError("couchdb said no")

    saved = roadmap_drift.board_records.board_store
    roadmap_drift.board_records.board_store = Refusing()
    try:
        code = roadmap_drift.main(["--roadmap", str(roadmap)])
    finally:
        roadmap_drift.board_records.board_store = saved
    out = capsys.readouterr().out
    assert code == 1
    assert "COULD NOT READ" in out
    assert "RuntimeError: couchdb said no" in out
    assert "MISSING" not in out


def test_the_default_run_reads_the_roadmap_from_the_vault_and_no_board_file(
        tmp_path, capsys):
    """The boards are not fetched at all any more -- only his roadmap is."""
    fetched = []
    saved_read = roadmap_drift.read_vault
    roadmap_drift.read_vault = lambda path: fetched.append(path) or ROADMAP
    store, asked = _store({})
    saved_store = roadmap_drift.board_records.board_store
    roadmap_drift.board_records.board_store = store
    try:
        code = roadmap_drift.main([])
    finally:
        roadmap_drift.read_vault = saved_read
        roadmap_drift.board_records.board_store = saved_store
    assert fetched == [roadmap_drift.ROADMAP_PATH]
    assert asked == ["registry", "issue", "registry", "idea"]
    # Both boards answered, empty and migrated: rank 1 names rows that are
    # on neither, so this is a real MISSING finding rather than a refusal.
    assert code == 2
    assert "MISSING" in capsys.readouterr().out


def test_board_index_raises_on_a_record_with_no_status_key():
    """The re-derivation that used to sit here was dead code and a mutation
    of it survived. A record missing `statusKey` is broken, not a row to
    guess a status for."""
    with pytest.raises(KeyError):
        roadmap_drift.board_index({"items": [{"number": 10, "status": "✅ Done"}]})


def test_an_unreadable_roadmap_exits_1_and_never_reads_as_an_empty_roadmap(capsys):
    """The roadmap is the only document still fetched, so its failure is
    its own branch now -- and `next_items(None)` on a roadmap that did not
    come back would report "0 ranked item(s) read" as a clean sweep at
    exit 0. A mutation that dropped this line survived until this test."""
    store, _ = _store({})
    saved_read = roadmap_drift.read_vault
    saved_store = roadmap_drift.board_records.board_store
    roadmap_drift.read_vault = lambda path: None
    roadmap_drift.board_records.board_store = store
    try:
        code = roadmap_drift.main([])
    finally:
        roadmap_drift.read_vault = saved_read
        roadmap_drift.board_records.board_store = saved_store
    out = capsys.readouterr().out
    assert code == 1
    assert roadmap_drift.ROADMAP_PATH in out
    assert "ranked item(s) read" not in out


def test_a_local_roadmap_that_is_not_there_exits_1(tmp_path, capsys):
    """`--roadmap` is how a cycle checks an edit before putting it, and a
    typo in that path must not read as a roadmap with nothing on it."""
    store, _ = _store({})
    saved_store = roadmap_drift.board_records.board_store
    roadmap_drift.board_records.board_store = store
    try:
        code = roadmap_drift.main(["--roadmap", str(tmp_path / "absent.md")])
    finally:
        roadmap_drift.board_records.board_store = saved_store
    out = capsys.readouterr().out
    assert code == 1
    assert "absent.md (FileNotFoundError)" in out
    assert "ranked item(s) read" not in out
