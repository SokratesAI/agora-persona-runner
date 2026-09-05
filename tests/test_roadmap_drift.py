"""The roadmap points at board rows and the boards move without it.

`roadmap.md` is rewritten when the reasoning changes -- in practice on
Mondays -- so between rewrites a ranked item can be entirely finished and
still sit at rank 1 on the page the owner opens. These pin the four ways
that judgement can go wrong: a finished item read as open, an open item
raised as finished, a row that has moved to `## Done`, and a sweep that
read one board of two and reported a clean roadmap anyway.
"""

import pytest

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


def _judge(roadmap, issues, ideas):
    items = roadmap_drift.next_items(roadmap)
    indexes = {"issues": roadmap_drift.board_index(issues),
               "ideas": roadmap_drift.board_index(ideas)}
    return items, roadmap_drift.judge(items, indexes)


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
