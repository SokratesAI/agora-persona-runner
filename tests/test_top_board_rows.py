"""The opening read has to put a 🔴 above a ⚪ and say which board it is on.

Issue #88: a cycle skipped the only 🔴 Immediately row on either board
for three days because nothing ever printed it next to the 52 rows it
was competing with.
"""

import pytest
from unittest.mock import patch

from agora_runner.nova_boards import CAPTURE_PRIORITY_SEP, PRIORITY_LABELS, STATUS_LABELS
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
    notes = tmp_path / "notes.md"
    notes.write_text(NOTES.format(" "))
    code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas),
                                "--notes", str(notes)])
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
    text = with_captures(board(), f"{IMMEDIATE}{CAPTURE_PRIORITY_SEP}this one is on fire")
    got = top_board_rows.unboarded_captures(text, "idea")
    assert got[0]["priority"] == IMMEDIATE
    assert got[0]["text"] == "this one is on fire"


def test_a_capture_a_cycle_already_closed_is_not_unprocessed():
    """Cycle 251: all five captures on `issues.md` were finished work, and
    this tool printed every one of them under "take one of these"."""
    text = with_captures(board((10, "a row", BACKLOG, "08-01", HIGH)),
                         "DONE (Cycle 247): shipped in runner#228 — the old ask",
                         "the thing I typed on my phone")
    got = top_board_rows.unboarded_captures(text, "issue")
    assert [c["text"] for c in got] == ["the thing I typed on my phone"]


def test_a_rating_survives_being_written_behind_a_done_marker():
    """The marker is prefixed in front of his bullet, glyph and all, so
    reading the rating before stripping it reports the capture unrated.

    Only the two calls below can see that. A first draft opened with
    `unboarded_captures(...) == []`, which reads like it belongs here and
    does not: a closed capture is dropped whichever order the two
    matchers run in, so that assertion is true either way. Reviewer
    finding on #234.
    """
    from agora_runner.nova_boards import split_capture_done, split_capture_priority
    done, rest = split_capture_done(
        f"DONE (Cycle 9): {IMMEDIATE}{CAPTURE_PRIORITY_SEP}on fire")
    assert done == "Cycle 9"
    assert split_capture_priority(rest) == (IMMEDIATE, "on fire")


def test_the_word_done_inside_his_sentence_is_prose_not_a_marker():
    text = with_captures(board(), "I am DONE (Cycle whatever) with this page")
    got = top_board_rows.unboarded_captures(text, "issue")
    assert [c["text"] for c in got] == ["I am DONE (Cycle whatever) with this page"]


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
    notes = tmp_path / "notes.md"
    notes.write_text(NOTES.format(" "))
    code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas),
                                "--notes", str(notes)])
    out = capsys.readouterr().out
    assert code == 0
    assert "an issue I typed" in out
    assert "an idea I typed" in out
    assert out.index("UNPROCESSED CAPTURES FROM EDVARD (2)") < out.index("idea #64")


# --- notes.md, the third capture file (Cycle 253) ---------------------------

NOTES = """---
type: log
---

- {}

## Read

- an old note
  - Read Cycle 1. Did the thing.
"""


def test_main_surfaces_an_unread_note(tmp_path, capsys):
    """`notes.md` was the last thing in the opening read a cycle could only
    reach by hand -- and unlike `issues.md` there is no board row to carry a
    note it walked past. It is not a board: a note is never numbered and
    never rated, so it is printed with the captures rather than ranked."""
    issues = tmp_path / "issues.md"
    ideas = tmp_path / "ideas.md"
    notes = tmp_path / "notes.md"
    issues.write_text(board((10, "a high issue", BACKLOG, "08-01", HIGH)))
    ideas.write_text(board((64, "an idea", BACKLOG, "08-12", HIGH)))
    notes.write_text(NOTES.format("Stop using the metered API for anything scheduled."))
    code = top_board_rows.main(
        ["--issues", str(issues), "--ideas", str(ideas), "--notes", str(notes)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Stop using the metered API" in out
    assert "notes.md" in out
    # Printed with the captures, above the ranking -- not as a board row.
    assert out.index("Stop using the metered API") < out.index("issue #10")
    # A note has no rating cell, so it must not be labelled as unrated.
    note_line = [l for l in out.splitlines() if "Stop using the metered API" in l][0]
    assert "(unrated)" not in note_line


def test_a_note_already_moved_under_read_is_not_unread(tmp_path, capsys):
    """The other half of step 1a's contract: a cycle acts on a note and
    moves it under `## Read`. Everything below that heading is answered,
    and re-surfacing it would make the list grow forever -- the failure
    the done-capture marker already had to fix once."""
    issues = tmp_path / "issues.md"
    ideas = tmp_path / "ideas.md"
    notes = tmp_path / "notes.md"
    issues.write_text(board((10, "a high issue", BACKLOG, "08-01", HIGH)))
    ideas.write_text(board((64, "an idea", BACKLOG, "08-12", HIGH)))
    notes.write_text(NOTES.format(" "))  # his empty cursor bullet
    assert top_board_rows.main(
        ["--issues", str(issues), "--ideas", str(ideas), "--notes", str(notes)]) == 0
    out = capsys.readouterr().out
    assert "an old note" not in out
    assert "UNPROCESSED CAPTURES" not in out


def test_a_notes_file_that_cannot_be_read_is_said_out_loud(tmp_path, capsys):
    """Silence here would read as "he has left no notes", which is the
    same wrong-answer-wearing-the-right-shape the boards already guard."""
    issues = tmp_path / "issues.md"
    ideas = tmp_path / "ideas.md"
    issues.write_text(board((10, "a high issue", BACKLOG, "08-01", HIGH)))
    ideas.write_text(board((64, "an idea", BACKLOG, "08-12", HIGH)))
    with patch.object(top_board_rows, "_fetch", return_value=None):
        code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas)])
    out = capsys.readouterr().out
    assert code == 1
    assert "COULD NOT READ" in out and "notes.md" in out


# --- The end of the loop this tool sits in --------------------------------


def test_a_nova_note_moves_the_row_it_was_written_on_down_the_ranking():
    """`append_detail_note` writes the cell; `age_key` reads it. Nothing
    tested that the two agree.

    This is the failure the stamp was built for, run end to end: two rows
    at the same rating, the older one worked, and the ranking still naming
    the one that was just worked. Measured live on 2026-08-20, issue #7 sat
    at `Updated 08-16` and topped this tool four hours after Cycle 270
    appended a note to it.

    **The note is Nova's on purpose.** One from Edvard would mark the row
    `waiting`, which outranks every rating and would move it to the top for
    a reason that has nothing to do with the date -- a positive result
    guaranteed in advance, pointing the wrong way.
    """
    from agora_runner.nova_boards import append_detail_note

    issues = board(
        (10, "worked this morning", IN_PROGRESS, "08-16", HIGH),
        (11, "genuinely untouched", IN_PROGRESS, "08-17", HIGH),
    ) + "\n# Details\n\n## 10 — worked this morning\n\nHis statement of it.\n"

    stale = top_board_rows.rank(top_board_rows.open_rows(issues, "issue"))
    assert [r["number"] for r in stale] == [10, 11]

    fresh_md = append_detail_note(issues, 10, "Shipped the first half.", "08-20",
                                  cycle=273)
    fresh = top_board_rows.rank(top_board_rows.open_rows(fresh_md, "issue"))
    assert [r["number"] for r in fresh] == [11, 10]
    assert fresh[1]["updated"] == "08-20"
    # And the note did not do it by marking the row as waiting on a reply.
    assert not any(r.get("waiting") for r in fresh)


BLOCKED = STATUS_LABELS["blocked-on-edvard"]


def test_a_blocked_row_sinks_below_a_lower_rated_actionable_one():
    """Issue #94's exact shape: 🟠 High, oldest, and nothing a cycle can do.

    It topped this list for five days on rating and age while every cycle
    walked past it, which is the tax `⏸ Blocked on Edvard` exists to stop.
    """
    text = board((94, "needs his click", BLOCKED, "08-16", HIGH),
                 (99, "a low one I can actually take", BACKLOG, "08-20", LOW))
    ranked = top_board_rows.rank(top_board_rows.open_rows(text, "issue"))
    assert [r["number"] for r in ranked] == [99, 94]


def test_a_blocked_row_is_still_open_and_keeps_its_rating():
    """Ranked down, never closed — the row comes back the moment he acts."""
    text = board((94, "needs his click", BLOCKED, "08-16", HIGH))
    rows = top_board_rows.open_rows(text, "issue")
    assert [(r["number"], r["priority"]) for r in rows] == [(94, HIGH)]


def test_an_unanswered_comment_still_beats_blocked():
    """If he has just written on a blocked row, that is likely the unblock.

    The blocked row is rated **below** the other one and is **newer**, so
    every tiebreak except the unanswered comment says it should lose. That
    is the only way this test can tell the two sort terms apart: written
    the obvious way -- blocked row rated High and oldest -- it wins for
    three independent reasons and pins none of them.
    """
    text = (board((94, "needs his click", BLOCKED, "08-20", LOW),
                  (95, "ordinary", BACKLOG, "08-01", HIGH))
            + "\n# Details\n\n### #94 — needs his click\n\n"
              "**Edvard, 08-21:** done, I clicked it.\n")
    ranked = top_board_rows.rank(top_board_rows.open_rows(text, "issue"))
    assert [r["number"] for r in ranked] == [94, 95]


def test_render_names_the_blocked_rows_rather_than_hiding_them():
    text = board((94, "needs his click", BLOCKED, "08-16", HIGH),
                 (99, "actionable", BACKLOG, "08-20", LOW))
    out = top_board_rows.render(top_board_rows.open_rows(text, "issue"))
    assert "issue #99" in out.splitlines()[1]
    assert "blocked on Edvard: issue #94" in out


# --- A comment on a row already closed, which the read discarded ---
#
# `open_rows` computed `waiting` for every row in the file and then
# dropped every closed one, so a question asked on a ✅ Done row was read
# and thrown away in the same function. Sokrates filed it on `issues.md`
# 2026-08-23 after a comment on `ideas #63` sat through nine cycles
# (328-336) with no reply and no change.

DONE_STATUS = STATUS_LABELS["done"]


def test_a_comment_on_a_done_row_is_not_lost_with_the_row():
    text = board((10, "an open row", BACKLOG, "08-01", HIGH),
                 (63, "a finished row", DONE_STATUS, "08-22", HIGH)) + details(
        (63, "a finished row", "Problem.\n\n"
                               "**Edvard, 08-22:** this Done looks premature?"))
    # Against a literal, not against another call to `open_rows` -- a
    # mutation moves both sides of that equally (rubric item 13).
    assert [r["number"] for r in top_board_rows.open_rows(text, "idea")] == [10]
    got = top_board_rows.closed_rows_waiting(text, "idea")
    assert [(r["board"], r["number"]) for r in got] == [("idea", 63)]


def test_a_done_row_i_already_answered_is_not_waiting():
    """The control. Without it the function above could return every closed
    row and this file would still be green."""
    text = board((63, "a finished row", DONE_STATUS, "08-22", HIGH)) + details(
        (63, "a finished row", "Problem.\n\n**Edvard, 08-22:** premature?\n\n"
                               "**Nova, 08-23 (Cycle 338):** answered."))
    assert top_board_rows.closed_rows_waiting(text, "idea") == []


def test_a_closed_waiting_row_is_named_but_never_ranked_as_a_pick():
    """It is a reply that is owed, not work. Putting it in the ranking would
    be the opposite failure -- a Done row named as this cycle's pick."""
    rows = [{"board": "issue", "number": 7, "title": "real work", "status": BACKLOG,
             "priority": HIGH, "priorityKey": "high", "updated": "08-01",
             "waiting": False}]
    closed = [{"board": "idea", "number": 63, "title": "a finished row",
               "status": DONE_STATUS, "updated": "08-22"}]
    out = top_board_rows.render(rows, closed_waiting=closed)
    assert "issue #7" in out.split("waiting on a reply from you:")[0]
    assert f"1 row(s) waiting on a reply from you: idea #63 ({DONE_STATUS})" in out
    assert "-> idea #63" not in out
    assert "this is not actually done" in out


def test_open_and_closed_waiting_rows_share_one_list():
    """One place to look. A second section is a second thing to remember."""
    rows = [{"board": "issue", "number": 7, "title": "asked on an open row",
             "status": BACKLOG, "priority": LOW, "priorityKey": "low",
             "updated": "08-01", "waiting": True}]
    closed = [{"board": "idea", "number": 63, "title": "a finished row",
               "status": DONE_STATUS, "updated": "08-22"}]
    out = top_board_rows.render(rows, closed_waiting=closed)
    assert (f"2 row(s) waiting on a reply from you: issue #7, idea #63 "
            f"({DONE_STATUS})") in out


def test_a_closed_waiting_row_survives_both_boards_being_otherwise_empty():
    """The early return for "no open rows" used to end the whole render."""
    closed = [{"board": "idea", "number": 63, "title": "a finished row",
               "status": DONE_STATUS, "updated": "08-22"}]
    out = top_board_rows.render([], closed_waiting=closed)
    assert "no open rows on either board" in out
    assert "idea #63" in out


def test_main_surfaces_a_comment_on_a_closed_row(tmp_path, capsys):
    issues = tmp_path / "issues.md"
    ideas = tmp_path / "ideas.md"
    notes = tmp_path / "notes.md"
    issues.write_text(board((7, "real work", BACKLOG, "08-01", HIGH)),
                      encoding="utf-8")
    ideas.write_text(board((63, "a finished row", DONE_STATUS, "08-22", HIGH))
                     + details((63, "a finished row",
                                "Problem.\n\n**Edvard, 08-22:** premature?")),
                     encoding="utf-8")
    notes.write_text("## Read\n", encoding="utf-8")
    code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas),
                                "--notes", str(notes)])
    out = capsys.readouterr().out
    assert code == 0
    assert f"idea #63 ({DONE_STATUS})" in out


# --- The DONE marker, which stopped matching the moment a cycle named a PR ---

def test_a_done_marker_may_name_where_the_work_landed():
    """Cycle 337 wrote `DONE (Cycle 337, platform-config#516):` and this tool
    printed the finished capture under "take one of these" at 09:05 on
    2026-08-23. Measured, not hypothetical."""
    text = with_captures(board((10, "a row", BACKLOG, "08-01", HIGH)),
                         "DONE (Cycle 337, platform-config#516): the old ask",
                         "the thing I typed on my phone")
    got = top_board_rows.unboarded_captures(text, "issue")
    assert [c["text"] for c in got] == ["the thing I typed on my phone"]


def test_the_cycle_number_is_still_the_only_thing_a_done_marker_yields():
    """Every caller reads group 1; widening the bracket must not widen that."""
    from agora_runner.nova_boards import split_capture_done
    assert split_capture_done("DONE (Cycle 337, platform-config#516): the ask") \
        == ("Cycle 337", "the ask")


def test_a_bracket_with_no_cycle_number_is_still_prose():
    """The control for the widened bracket: `[^)]*` must not swallow the
    requirement that a real cycle number is there."""
    from agora_runner.nova_boards import split_capture_done
    assert split_capture_done("DONE (nearly, I think): not a marker") \
        == ("", "DONE (nearly, I think): not a marker")


def test_a_row_in_the_done_table_is_waiting_too():
    """Reviewer finding on #298: every closed-row fixture above leaves the
    status in `## Board`, and a row moved into the `## Done` table is the
    other real shape. `parse_board` synthesises the status for those, so the
    rendered line must still name one rather than print an empty bracket."""
    text = board(done=[(63, "a finished row", "08-22", "runner#1")]) + details(
        (63, "a finished row", "Problem.\n\n**Edvard, 08-22:** premature?"))
    got = top_board_rows.closed_rows_waiting(text, "idea")
    assert [(r["number"], r["status"]) for r in got] == [(63, DONE_STATUS)]
    assert f"idea #63 ({DONE_STATUS})" in top_board_rows.render([], closed_waiting=got)
