"""The opening read has to put a 🔴 above a ⚪ and say which board it is on.

Issue #88: a cycle skipped the only 🔴 Immediately row on either board
for three days because nothing ever printed it next to the 52 rows it
was competing with.
"""

import pytest

from agora_runner.nova_boards import PRIORITY_LABELS, STATUS_LABELS
from tools import top_board_rows


def board(*rows, done=()):
    """A board file with the live five-column `## Board` shape."""
    head = ["## Board", "", "| # | Item | Status | Updated | Priority |",
            "|---|---|---|---|---|"]
    for number, title, status, updated, priority in rows:
        head.append(f"| [[#{number} — {title}\\|{number}]] | {title} "
                    f"| {status} | {updated} | {priority} |")
    head += ["", "## Done", "", "| # | Item | Updated | Where |", "|---|---|---|---|"]
    for number, title, updated, where in done:
        head.append(f"| [[#{number} — {title}\\|{number}]] | {title} "
                    f"| {updated} | {where} |")
    return "\n".join(head) + "\n"


IMMEDIATE = PRIORITY_LABELS["immediate"]
HIGH = PRIORITY_LABELS["high"]
LOW = PRIORITY_LABELS["low"]
BACKLOG = STATUS_LABELS["backlog"]
IN_PROGRESS = STATUS_LABELS["in-progress"]
OUTDATED = STATUS_LABELS["outdated"]


def test_immediately_outranks_high_across_both_boards():
    issues = board((10, "a high issue", BACKLOG, "2026-08-01", HIGH))
    ideas = board((64, "the immediate idea", BACKLOG, "2026-08-12", IMMEDIATE))
    rows = (top_board_rows.open_rows(issues, "issue")
            + top_board_rows.open_rows(ideas, "idea"))
    top = top_board_rows.rank(rows)[0]
    assert (top["board"], top["number"]) == ("idea", 64)


def test_older_row_wins_at_equal_rating():
    text = board((4, "old and high", BACKLOG, "2026-08-04", HIGH),
                 (88, "new and high", IN_PROGRESS, "2026-08-15", HIGH))
    ranked = top_board_rows.rank(top_board_rows.open_rows(text, "issue"))
    assert [r["number"] for r in ranked] == [4, 88]


def test_a_full_date_sorts_against_the_short_form_the_boards_use():
    """Live rows write `08-04`; a hand-typed `2026-08-04` must not sink."""
    text = board((4, "old, written long", BACKLOG, "2026-08-04", HIGH),
                 (88, "new, written short", IN_PROGRESS, "08-15", HIGH))
    ranked = top_board_rows.rank(top_board_rows.open_rows(text, "issue"))
    assert [r["number"] for r in ranked] == [4, 88]


def test_a_row_with_no_usable_date_sorts_last_in_its_rating():
    text = board((1, "no date", BACKLOG, "", HIGH),
                 (2, "dated", BACKLOG, "08-14", HIGH))
    ranked = top_board_rows.rank(top_board_rows.open_rows(text, "issue"))
    assert [r["number"] for r in ranked] == [2, 1]


def test_unrated_sorts_below_low_rather_than_above_everything():
    text = board((1, "unrated", BACKLOG, "2026-08-01", ""),
                 (2, "rated low", BACKLOG, "2026-08-14", LOW))
    ranked = top_board_rows.rank(top_board_rows.open_rows(text, "issue"))
    assert [r["number"] for r in ranked] == [2, 1]


@pytest.mark.parametrize("status", [STATUS_LABELS["done"], OUTDATED])
def test_closed_rows_are_not_candidates(status):
    """Cycle 219's complaint was about an open 🔴; a shipped one must not pull."""
    text = board((5, "shipped", status, "2026-08-10", IMMEDIATE),
                 (6, "still open", BACKLOG, "2026-08-10", LOW))
    rows = top_board_rows.open_rows(text, "issue")
    assert [r["number"] for r in rows] == [6]


def test_done_table_rows_are_not_candidates():
    text = board((6, "still open", BACKLOG, "2026-08-10", LOW),
                 done=[(64, "already built", "2026-08-15", "runner#209")])
    rows = top_board_rows.open_rows(text, "issue")
    assert [r["number"] for r in rows] == [6]


def test_render_names_the_row_and_asks_for_a_reason():
    text = board((64, "comment threads", BACKLOG, "2026-08-12", IMMEDIATE))
    out = top_board_rows.render(top_board_rows.open_rows(text, "idea"))
    assert "idea #64" in out
    assert IMMEDIATE in out
    assert "why you did not" in out


def test_render_says_so_when_both_boards_are_empty():
    assert "no open rows" in top_board_rows.render([])


def test_main_reads_both_local_boards(tmp_path, capsys):
    issues = tmp_path / "issues.md"
    ideas = tmp_path / "ideas.md"
    issues.write_text(board((10, "a high issue", BACKLOG, "2026-08-01", HIGH)))
    ideas.write_text(board((64, "the immediate idea", BACKLOG, "2026-08-12", IMMEDIATE)))
    code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas)])
    out = capsys.readouterr().out
    assert code == 0
    assert "-> idea #64" in out
    assert "issue #10" in out          # the runner-up is still shown
    assert "COULD NOT READ" not in out


@pytest.mark.parametrize("stdout", [
    "[not found: projects/sokrates/projects/nova/ideas.md]\n",   # the live shape
    "  [not found: whatever]\n",                                 # leading space
    "",
    "   \n",
])
def test_a_missing_board_is_a_failed_read_even_though_the_client_exits_zero(monkeypatch, stdout):
    """`vault_tool.py get` prints `[not found: …]` and exits 0.

    Read as success it becomes an empty board, which ranks silently and
    lets a top row be chosen from one of two boards.
    """
    class Done:
        returncode = 0

    Done.stdout = stdout
    monkeypatch.setattr(top_board_rows.subprocess, "run",
                        lambda *a, **k: Done)
    assert top_board_rows._fetch("some/path.md") is None


def test_a_real_board_body_still_reads_as_success(monkeypatch):
    """The guard above must not reject the thing it is protecting."""
    class Done:
        returncode = 0
        stdout = board((1, "real row", BACKLOG, "08-01", HIGH))

    monkeypatch.setattr(top_board_rows.subprocess, "run", lambda *a, **k: Done)
    text = top_board_rows._fetch("some/path.md")
    assert text is not None
    assert [r["number"] for r in top_board_rows.open_rows(text, "issue")] == [1]


def test_an_unreadable_board_is_said_out_loud_and_exits_nonzero(tmp_path, capsys, monkeypatch):
    """A ranking built from one of two boards is the wrong answer in the right shape."""
    issues = tmp_path / "issues.md"
    issues.write_text(board((10, "a high issue", BACKLOG, "2026-08-01", HIGH)))
    monkeypatch.setattr(top_board_rows, "_fetch", lambda path: None)
    code = top_board_rows.main(["--issues", str(issues)])
    out = capsys.readouterr().out
    assert code == 1
    assert "COULD NOT READ" in out
    assert top_board_rows.IDEAS_PATH in out
