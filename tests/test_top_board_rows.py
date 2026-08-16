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


def test_the_paths_are_the_ones_nova_boards_owns():
    """Reviewer finding, PR #210: these were hand-typed copies of `BOARD_PATHS`.

    Both moved on 2026-08-12 and a second copy is one that will be wrong
    the next time they move — quietly, because an unresolvable path reads
    as a board with no rows.
    """
    from agora_runner.nova_boards import BOARD_PATHS
    assert top_board_rows.ISSUES_PATH is BOARD_PATHS["issues"]["edvard"]
    assert top_board_rows.IDEAS_PATH is BOARD_PATHS["ideas"]["edvard"]


def details(*blocks):
    """A `# Details` section, as both live boards carry it."""
    out = ["", "# Details", ""]
    for number, title, body in blocks:
        out += [f"## {number} — {title}", "", body, ""]
    return "\n".join(out)


def test_an_unanswered_comment_outranks_an_immediate_rating():
    """He asked a question on a row; that beats a rating nobody typed today."""
    issues = board((10, "waiting", BACKLOG, "2026-08-01", LOW)) + details(
        (10, "waiting", "Problem.\n\n**Edvard, 08-15:** what about this?"))
    ideas = board((64, "the immediate idea", BACKLOG, "2026-08-12", IMMEDIATE))
    rows = (top_board_rows.open_rows(issues, "issue")
            + top_board_rows.open_rows(ideas, "idea"))
    top = top_board_rows.rank(rows)[0]
    assert (top["board"], top["number"]) == ("issue", 10)
    assert top["waiting"] is True


def test_a_thread_i_already_answered_is_not_waiting():
    text = board((7, "answered", BACKLOG, "2026-08-01", HIGH)) + details(
        (7, "answered", "Problem.\n\n**Edvard, 08-14:** first?\n\n"
                        "**Nova, 08-14 (Cycle 200):** answered."))
    assert top_board_rows.open_rows(text, "issue")[0]["waiting"] is False


def test_he_gets_the_last_word_after_my_reply_and_is_waiting_again():
    """Positional, not a count -- Edvard, Nova, Edvard is two each and waiting."""
    text = board((11, "reopened", BACKLOG, "2026-08-01", LOW)) + details(
        (11, "reopened", "Problem.\n\n**Edvard, 08-13:** one\n\n"
                         "**Nova, 08-13 (Cycle 1):** two\n\n**Edvard, 08-15:** three"))
    assert top_board_rows.open_rows(text, "issue")[0]["waiting"] is True


def test_my_own_status_notes_never_make_a_row_look_waiting():
    """Every closed row carries one of these; none of them is a question."""
    text = board((9, "noted", BACKLOG, "2026-08-01", HIGH)) + details(
        (9, "noted", "Problem.\n\n**Nova, 08-15 (Cycle 220):** status note."))
    assert top_board_rows.open_rows(text, "issue")[0]["waiting"] is False


def test_every_waiting_row_is_listed_even_below_the_runners_up_window():
    """The waiting row that is NOT the headline pick still has to be named.

    Reviewer finding, PR #212: the first version of this test had one
    waiting row among five, and waiting-first sort makes a lone waiting
    row `ranked[0]` -- inside the displayed window by construction. It
    passed with the summary line built from only the rows on screen, so
    it pinned nothing. Two waiting rows and `runners_up=0` is the shape
    where the second one is genuinely off the window.
    """
    rows = [{"board": "issue", "number": n, "title": f"row {n}", "status": BACKLOG,
             "priority": IMMEDIATE, "priorityKey": "immediate", "updated": "08-01",
             "waiting": False} for n in range(1, 6)]
    rows.append({"board": "idea", "number": 98, "title": "first waiting", "status": BACKLOG,
                 "priority": IMMEDIATE, "priorityKey": "immediate", "updated": "08-01",
                 "waiting": True})
    rows.append({"board": "idea", "number": 99, "title": "buried waiting", "status": BACKLOG,
                 "priority": LOW, "priorityKey": "low", "updated": "08-14",
                 "waiting": True})
    out = top_board_rows.render(rows, runners_up=0)
    # idea #98 is the headline pick; idea #99 is off the window entirely.
    assert "idea #99" not in out.split("waiting on a reply from you:")[0]
    assert "2 row(s) waiting on a reply from you: idea #98, idea #99" in out
    assert "UNANSWERED" in out


def test_two_questions_answered_by_one_reply_is_not_waiting():
    """A count rule would call this waiting forever. See test_nova_boards.py."""
    text = board((12, "twice", BACKLOG, "2026-08-01", LOW)) + details(
        (12, "twice", "**Edvard, 08-13:** one\n\n**Edvard, 08-13:** two\n\n"
                      "**Nova, 08-13 (Cycle 1):** both answered"))
    assert top_board_rows.open_rows(text, "issue")[0]["waiting"] is False


# --- Edvard's unboarded captures, which this tool could not see at all ---
#
# Cycle 241 ran the tool, took the row it named, and three of his captures
# were sitting unread above the board. `parse_board` had been returning
# them the whole time under a key `open_rows` dropped. Filed by that cycle
# as `[top-board-rows-blind-to-captures]`.

def with_captures(text, *bullets):
    """Edvard's bare bullets above the first heading, plus his empty cursor."""
    head = "---\ntype: log\n---\n\n" + "".join(f"- {b}\n" for b in bullets) + "- \n\n"
    return head + text


def test_a_bare_capture_is_read_off_the_board_file():
    text = with_captures(board((10, "a row", BACKLOG, "08-01", HIGH)),
                         "the thing I typed on my phone")
    got = top_board_rows.unboarded_captures(text, "issue")
    assert [c["text"] for c in got] == ["the thing I typed on my phone"]
    assert got[0]["board"] == "issue"


def test_a_rating_on_a_capture_is_read_off_the_front_of_the_bullet():
    text = with_captures(board(), f"{IMMEDIATE.split()[0]} this one is on fire")
    got = top_board_rows.unboarded_captures(text, "idea")
    assert got[0]["priority"] == IMMEDIATE
    assert got[0]["text"] == "this one is on fire"


def test_his_empty_cursor_bullet_is_not_a_capture():
    text = with_captures(board((10, "a row", BACKLOG, "08-01", HIGH)))
    assert top_board_rows.unboarded_captures(text, "issue") == []


def test_a_capture_is_printed_above_the_top_row_and_takes_the_contract():
    """The 'take this' sentence has to move, or the row still wins by default."""
    text = with_captures(board((64, "an immediate row", BACKLOG, "08-12", IMMEDIATE)),
                         "please look at the login page")
    out = top_board_rows.render(top_board_rows.open_rows(text, "idea"),
                                captures=top_board_rows.unboarded_captures(text, "idea"))
    assert out.index("please look at the login page") < out.index("idea #64")
    assert "these outrank every row below" in out
    # The row is still shown -- printing the capture must not throw the board away.
    assert "idea #64" in out
    assert IMMEDIATE in out


def test_the_contract_sentence_moves_onto_the_captures_and_only_then():
    """Both directions in one test, because either alone is unfailable.

    Asserting only the no-captures case passes with this whole feature
    reverted -- `render` took no `captures` argument, the header read the
    same, and `UNPROCESSED CAPTURES` was a string that had never existed.
    The claim worth pinning is that the header *switches*, so both sides
    of the switch are read here and the "not in" is what does the work.
    """
    text = with_captures(board((64, "an immediate row", BACKLOG, "08-12", IMMEDIATE)),
                         "something I typed")
    rows = top_board_rows.open_rows(text, "idea")
    caps = top_board_rows.unboarded_captures(text, "idea")
    without = top_board_rows.render(rows, captures=[])
    assert "why you did not" in without
    assert "UNPROCESSED CAPTURES" not in without
    withthem = top_board_rows.render(rows, captures=caps)
    assert "below the captures above" in withthem
    assert "why you did not" not in withthem


def test_a_capture_is_still_shown_when_neither_board_has_an_open_row():
    """The one case where a capture is the only thing there is to report."""
    text = with_captures(board(), "the only thing waiting on me")
    out = top_board_rows.render([], captures=top_board_rows.unboarded_captures(text, "issue"))
    assert "the only thing waiting on me" in out
    assert "no open rows" in out


def test_main_surfaces_captures_from_both_files(tmp_path, capsys):
    issues = tmp_path / "issues.md"
    ideas = tmp_path / "ideas.md"
    issues.write_text(with_captures(board((10, "a high issue", BACKLOG, "08-01", HIGH)),
                                    "an issue I typed"))
    ideas.write_text(with_captures(board((64, "the immediate idea", BACKLOG, "08-12", IMMEDIATE)),
                                   "an idea I typed"))
    code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas)])
    out = capsys.readouterr().out
    assert code == 0
    assert "an issue I typed" in out
    assert "an idea I typed" in out
    assert out.index("UNPROCESSED CAPTURES FROM EDVARD (2)") < out.index("idea #64")
