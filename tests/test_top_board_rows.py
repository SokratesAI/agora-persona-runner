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
    """Positional, not a count -- the owner, Nova, the owner is two each and waiting."""
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


# --- The owner's unboarded captures, which this tool could not see at all ---
#
# Cycle 241 ran the tool, took the row it named, and three of his captures
# were sitting unread above the board. `parse_board` had been returning
# them the whole time under a key `open_rows` dropped. Filed by that cycle
# as `[top-board-rows-blind-to-captures]`.

def with_captures(text, *bullets):
    """The owner's bare bullets above the first heading, plus his empty cursor."""
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

    **The note is Nova's on purpose.** One from the owner would mark the row
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
    walked past it, which is the tax `⏸ Blocked on the owner` exists to stop.
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
    assert "blocked on Edvard" in out
    # The row itself, in full, on its own line -- not a bare number folded
    # into the sentence. `#94` alone does not say which board.
    assert any(line.strip().startswith("issue #94") and "needs his click" in line
               for line in out.splitlines())


def test_the_nothing_to_build_line_cannot_be_read_as_a_verdict_on_the_ranking():
    """Cycle 400's exact shape, and the sixth time a cycle filed this line.

    Four cycles reported "Nothing for a cycle to build on these" as a false
    positive over a ranking they had just been told to take from. The verdict
    was never wrong -- "these" meant the blocked rows -- but the blocked rows
    are the ones sunk out of the printed ranking, so the sentence was only
    ever about rows the reader could not see. Cycle 400 hit the worst version:
    the blocked row was `issue #94` and `idea #94` was ranked directly above
    it, a different board sharing a number.

    So the assertion is about scope, not wording: the sentence must name what
    it applies to, and the ranked rows must not be inside it.
    """
    text = board((94, "needs his click", BLOCKED, "08-16", HIGH),
                 (7, "a real build", BACKLOG, "08-20", HIGH))
    out = top_board_rows.render(top_board_rows.open_rows(text, "issue"))
    assert "on these" not in out, "the dangling pronoun is what misread"
    # The claim is scoped to the rows printed under it...
    verdict = next(line for line in out.splitlines()
                   if "Nothing for a cycle to build" in line)
    assert verdict.rstrip().endswith(":"), "a scoped claim introduces its rows"
    # ...and says out loud that it is not about the ranking, because the
    # ranking is what four cycles read it against.
    assert "not a verdict on the ranking above" in verdict
    # The actionable row is the pick above the block, never inside it.
    lines = out.splitlines()
    assert lines[1].startswith("  -> issue #7")
    assert lines.index(verdict) > 1
    assert "#7" not in verdict


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


# --- Claims: parallel cycles must not both take the same row -------------
#
# the owner, `comments.md` 2026-08-23 13:31, on going from a 72-minute heartbeat
# to an 18-minute one: *"The average cycle is 18min, so we are guaranteed to
# have some paralell cycles run, and i want that."* Everything above this line
# assumes one reader at a time.

import json
from datetime import datetime, timedelta


def claims(*rows):
    """A ledger holding one open claim per (item, cycle) pair, stamped now."""
    now = datetime.now(top_board_rows.OSLO)
    return json.dumps({"claims": [
        {"item": item, "cycle": cycle, "state": "open",
         "at": (now - timedelta(minutes=age)).isoformat()}
        for item, cycle, age in rows]})


def _rendered(issues_md, ideas_md, ledger, cycle=None, tmp_path=None):
    """Run `main` end to end on local files and return what it printed."""
    paths = {}
    for name, text in (("issues.md", issues_md), ("ideas.md", ideas_md),
                       ("notes.md", "- \n\n## Read\n"), ("claims.json", ledger)):
        paths[name] = tmp_path / name
        paths[name].write_text(text, encoding="utf-8")
    argv = ["--issues", str(paths["issues.md"]), "--ideas", str(paths["ideas.md"]),
            "--notes", str(paths["notes.md"]), "--claims", str(paths["claims.json"])]
    if cycle is not None:
        argv += ["--cycle", str(cycle)]
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert top_board_rows.main(argv) == 0
    return buf.getvalue()


def test_row_held_by_another_live_cycle_sinks_below_a_lower_rated_free_one(tmp_path):
    issues = board((10, "held immediate", BACKLOG, "2026-08-01", IMMEDIATE),
                   (11, "free low", BACKLOG, "2026-08-02", LOW))
    out = _rendered(issues, board(), claims(("issue-10", 341, 2)), cycle=342,
                    tmp_path=tmp_path)
    top = [line for line in out.splitlines() if line.startswith("  -> ")][0]
    assert "#11" in top and "#10" not in top
    assert "🔒 HELD by cycle 341" in out
    assert "issue-10 (cycle 341)" in out


def test_my_own_claim_is_not_reported_back_to_me_as_taken(tmp_path):
    issues = board((10, "mine already", BACKLOG, "2026-08-01", IMMEDIATE),
                   (11, "free low", BACKLOG, "2026-08-02", LOW))
    out = _rendered(issues, board(), claims(("issue-10", 342, 2)), cycle=342,
                    tmp_path=tmp_path)
    top = [line for line in out.splitlines() if line.startswith("  -> ")][0]
    assert "#10" in top
    assert "🔒" not in out


def test_a_claim_older_than_the_turn_cap_does_not_fence_the_row_off(tmp_path):
    # A cycle that was killed mid-turn must not hold a row forever: an
    # unclaimable row looks exactly like one somebody is handling.
    issues = board((10, "stale claim", BACKLOG, "2026-08-01", IMMEDIATE),
                   (11, "free low", BACKLOG, "2026-08-02", LOW))
    out = _rendered(issues, board(), claims(("issue-10", 300, 90)), cycle=342,
                    tmp_path=tmp_path)
    top = [line for line in out.splitlines() if line.startswith("  -> ")][0]
    assert "#10" in top
    assert "🔒" not in out


def test_the_two_boards_are_numbered_separately_so_the_slug_says_which(tmp_path):
    issues = board((7, "issue seven", BACKLOG, "2026-08-01", HIGH))
    ideas = board((7, "idea seven", BACKLOG, "2026-08-02", HIGH))
    out = _rendered(issues, ideas, claims(("idea-7", 341, 2)), cycle=342,
                    tmp_path=tmp_path)
    top = [line for line in out.splitlines() if line.startswith("  -> ")][0]
    assert "issue #7" in top and "🔒" not in top
    assert "🔒 HELD by cycle 341" in out


def test_a_held_capture_sinks_below_a_free_one_and_keeps_its_slug(tmp_path):
    from agora_runner.nova_claims import slug_for_capture
    held_text = "the capture another cycle already took"
    issues = "- " + held_text + "\n- a capture nobody has taken\n\n" + board()
    out = _rendered(issues, board(),
                    claims((slug_for_capture(held_text), 341, 2)), cycle=342,
                    tmp_path=tmp_path)
    capture_lines = [line for line in out.splitlines()
                     if line.startswith("  -> issues.md")]
    assert "nobody has taken" in capture_lines[0]
    assert "🔒 HELD by cycle 341" in capture_lines[1]
    assert f"[claim: {slug_for_capture(held_text)}]" in capture_lines[1]


def test_an_unreadable_ledger_says_so_rather_than_printing_a_clean_board(tmp_path):
    issues = board((10, "a row", BACKLOG, "2026-08-01", HIGH))
    out = _rendered(issues, board(), "{not json at all", cycle=342,
                    tmp_path=tmp_path)
    assert "CLAIMS LEDGER UNREADABLE" in out


def test_the_claim_instruction_is_printed_even_when_nothing_is_held(tmp_path):
    # The mechanism fails open: a cycle that only claims once it sees
    # somebody else's claim never claims first, and every cycle is
    # somebody's first.
    out = _rendered(board((10, "a row", BACKLOG, "2026-08-01", HIGH)), board(),
                    claims(), cycle=342, tmp_path=tmp_path)
    assert "Claim before you work" in out
    assert "[claim: issue-10]" in out


def test_an_absent_ledger_is_an_empty_one_and_a_failed_read_is_not(monkeypatch):
    """The two answers `_fetch` deliberately collapses, kept apart here.

    For a board, "not there" and "the read failed" are the same answer --
    neither can be ranked. For the ledger they are opposite: absent is the
    normal state and means nobody holds anything, while a failed read means
    the 🔒 marks are missing rather than absent, and a cycle that reads a
    clean board while another cycle holds every row on it is the exact
    duplication this is here to stop. Reviewer finding, PR #301: the
    distinction this function exists for had no test.
    """
    class Done:
        returncode = 0
        stdout = "[not found: projects/.../claims.json]\n"

    monkeypatch.setattr(top_board_rows.subprocess, "run", lambda *a, **k: Done)
    assert top_board_rows.fetch_claims() == ("", True)

    Done.returncode = 1
    assert top_board_rows.fetch_claims() == ("", False)

    def boom(*a, **k):
        raise OSError("no vault client on this pod")

    monkeypatch.setattr(top_board_rows.subprocess, "run", boom)
    assert top_board_rows.fetch_claims() == ("", False)


def test_a_real_ledger_body_reads_as_success(monkeypatch):
    body = '{"claims": [{"item": "issue-7", "cycle": 341, "state": "open",\n' \
           ' "at": "2026-08-23T14:00:00+02:00"}]}\n'

    class Done:
        returncode = 0
        stdout = body

    monkeypatch.setattr(top_board_rows.subprocess, "run", lambda *a, **k: Done)
    assert top_board_rows.fetch_claims() == (body, True)


# --- Replying to a comment is its own claim -------------------------------
#
# Cycle 343 left this open as the last collision surface but one: two
# cycles both read `💬 UNANSWERED` before either replies, and both reply.
# The row claim never covered it, because `prompt.md` tells a cycle to
# reply "even if you do not take it as this cycle's work".

def _waiting_board(comment="**Edvard, 08-23:** is this really done?"):
    return board((7, "a row", BACKLOG, "2026-08-01", HIGH)) + details(
        (7, "a row", "Problem.\n\n" + comment))


def test_a_waiting_row_always_carries_a_reply_slug():
    """The guarantee `_reply_claim`'s fallback relies on.

    It prints nothing when a row has no `replySlug`, which is honest for a
    row built by hand and would be silent data loss if a real waiting row
    could reach it. This is where that cannot happen.
    """
    row = top_board_rows.open_rows(_waiting_board(), "issue")[0]
    assert row["waiting"] is True
    assert row["replySlug"].startswith("reply-issue-7-")


def test_an_unwaiting_row_has_no_reply_slug_to_claim():
    text = board((7, "a row", BACKLOG, "2026-08-01", HIGH)) + details(
        (7, "a row", "Problem.\n\n**Nova, 08-23 (Cycle 1):** answered"))
    assert top_board_rows.open_rows(text, "issue")[0]["replySlug"] is None


def test_the_reply_slug_is_not_the_row_slug():
    """Claiming the row must not fence off the reply, or the other way.

    `prompt.md`: reply "even if you do not take it as this cycle's work".
    One slug for both would make those the same act.
    """
    row = top_board_rows.open_rows(_waiting_board(), "issue")[0]
    assert row["replySlug"] != row["slug"] == "issue-7"


def test_a_second_comment_on_the_same_row_is_a_different_claim():
    """`take` refuses a slug that was ever released as done.

    So a reply slug derived from the row alone would make the second
    question the owner ever asks on a row permanently unclaimable.
    """
    first = top_board_rows.open_rows(_waiting_board(), "issue")[0]["replySlug"]
    second = top_board_rows.open_rows(
        _waiting_board("**Edvard, 08-23:** and one more thing"), "issue")[0]["replySlug"]
    assert first != second


def test_a_held_reply_stops_the_row_jumping_the_queue():
    """The raise exists to get him answered. Once somebody is answering, it
    is only pointing the next cycle at a duplicate."""
    rows = top_board_rows.open_rows(_waiting_board(), "issue")
    rows.append({"board": "issue", "number": 3, "title": "immediate", "status": BACKLOG,
                 "updated": "2026-08-02", "priority": IMMEDIATE,
                 "priorityKey": "immediate", "statusKey": "backlog", "waiting": False})
    slug = rows[0]["replySlug"]
    assert top_board_rows.rank(rows)[0]["number"] == 7
    top_board_rows.apply_claims(rows, {slug: 99}, my_cycle=1)
    assert top_board_rows.rank(rows)[0]["number"] == 3


def test_my_own_reply_claim_is_not_somebody_elses():
    rows = top_board_rows.open_rows(_waiting_board(), "issue")
    top_board_rows.apply_claims(rows, {rows[0]["replySlug"]: 344}, my_cycle=344)
    assert rows[0]["replyHeldBy"] is None


def test_render_prints_the_reply_slug_next_to_the_row_slug():
    rows = top_board_rows.apply_claims(
        top_board_rows.open_rows(_waiting_board(), "issue"), {})
    out = top_board_rows.render(rows)
    assert "💬 UNANSWERED" in out
    assert f"[claim: issue-7]  [reply-claim: {rows[0]['replySlug']}]" in out
    assert "claim the reply-claim slug first" in out


def test_a_held_reply_is_marked_and_dropped_from_the_go_and_reply_list():
    """The mark stays on the ranked line; the instruction to go and type a
    reply does not, because that is the line that produces the second one."""
    rows = top_board_rows.open_rows(_waiting_board(), "issue")
    top_board_rows.apply_claims(rows, {rows[0]["replySlug"]: 99}, my_cycle=344)
    out = top_board_rows.render(rows)
    assert "🔒 REPLY HELD by cycle 99" in out
    assert "waiting on a reply from you" not in out
    assert "1 reply(ies) already being written by a live cycle: issue #7 (cycle 99)" in out
    assert "reply-claim:" not in out


def test_a_closed_row_owed_a_reply_is_claimable_too():
    """`closed_rows_waiting` is the one path `apply_claims` did not cover,
    and a comment on a Done row is where idea #63 sat for nine cycles."""
    text = board(done=((63, "premature", "2026-08-22", "runner#1"),)) + details(
        (63, "premature", "**Edvard, 08-22:** this is not actually done"))
    closed = top_board_rows.closed_rows_waiting(text, "idea")
    assert closed[0]["replySlug"].startswith("reply-idea-63-")
    top_board_rows.apply_claims(closed, {closed[0]["replySlug"]: 99}, my_cycle=344)
    out = top_board_rows.render([], closed_waiting=closed)
    assert "idea #63 (cycle 99)" in out
    assert "waiting on a reply from you" not in out
    assert "The closed ones still need one" not in out


def test_main_applies_claims_to_the_closed_rows_too(tmp_path, capsys):
    """The one call a unit test on `apply_claims` cannot pin.

    `closed_rows_waiting` never enters the ranking, so it misses both of
    `main`'s `apply_claims` calls unless it gets its own — and a comment
    on a Done row is exactly the one idea #63 sat under for nine cycles.
    Driven through `main` on purpose: the direct test above passes whether
    or not `main` ever makes the call.
    """
    issues = tmp_path / "issues.md"
    issues.write_text(board((10, "a high issue", BACKLOG, "2026-08-01", HIGH)))
    ideas = tmp_path / "ideas.md"
    ideas.write_text(board(done=((63, "premature", "2026-08-22", "runner#1"),))
                     + details((63, "premature",
                                "**Edvard, 08-22:** this is not actually done")))
    notes = tmp_path / "notes.md"
    notes.write_text(NOTES.format(" "))
    slug = top_board_rows.closed_rows_waiting(ideas.read_text(), "idea")[0]["replySlug"]
    claims = tmp_path / "claims.json"
    claims.write_text('{"claims": [{"item": "%s", "cycle": 99, "state": "open",'
                      ' "at": "%s"}]}' % (slug, datetime.now(top_board_rows.OSLO).isoformat()))
    code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas),
                                "--notes", str(notes), "--claims", str(claims),
                                "--cycle", "344"])
    out = capsys.readouterr().out
    assert code == 0
    assert "idea #63 (cycle 99)" in out
    assert "waiting on a reply from you" not in out


# --- a claim slug the ledger has already spent -----------------------------
#
# Cycle 353, measured on the live board: the top capture and the only 🔴
# Immediately row both carried `[claim: <slug>]` for a slug `take` refuses
# forever, because an earlier cycle released each one `done` while the work
# was still live. Exit 2 reads as "somebody is doing this"; the honest
# answer was "somebody already did some of this, and here is what".


def _spent(item, cycle, outcome):
    return {"claims": [{"item": item, "cycle": cycle, "state": "done",
                        "at": "2026-08-23T15:23:26.424577+02:00",
                        "outcome": outcome}]}


def test_a_spent_slug_prints_the_outcome_instead_of_a_take_command():
    rows = top_board_rows.open_rows(
        board((63, "four cycles an hour", IN_PROGRESS, "2026-08-23", IMMEDIATE)), "idea")
    top_board_rows.apply_finished(rows, {"idea-63": {"cycle": 347, "outcome": "built the last piece"}})
    line = top_board_rows._line(rows[0])
    assert "[claim: idea-63]" not in line
    assert "claim spent by cycle 347" in line
    assert "built the last piece" in line


def test_an_unspent_slug_still_prints_the_take_command():
    rows = top_board_rows.open_rows(
        board((63, "four cycles an hour", IN_PROGRESS, "2026-08-23", IMMEDIATE)), "idea")
    top_board_rows.apply_finished(rows, {})
    assert "[claim: idea-63]" in top_board_rows._line(rows[0])


def test_a_spent_claim_with_no_outcome_says_so_rather_than_printing_nothing():
    rows = top_board_rows.open_rows(
        board((63, "four cycles an hour", IN_PROGRESS, "2026-08-23", IMMEDIATE)), "idea")
    top_board_rows.apply_finished(rows, {"idea-63": {"cycle": 347, "outcome": None}})
    assert "no outcome recorded" in top_board_rows._line(rows[0])


def test_a_spent_claim_does_not_move_the_row_down_the_ranking():
    """A spent claim is a fact about the ledger, never about the work.

    `heldBy` sinks a row because somebody is on it this minute. Nobody is
    on this one, and `prompt.md` still ranks a 🔴 above everything -- so
    hiding it would be the tool making the judgement the reader has to.
    """
    rows = (top_board_rows.open_rows(
                board((63, "four cycles an hour", IN_PROGRESS, "2026-08-23", IMMEDIATE)), "idea")
            + top_board_rows.open_rows(
                board((92, "a dashboard", BACKLOG, "2026-08-19", HIGH)), "idea"))
    top_board_rows.apply_finished(rows, {"idea-63": {"cycle": 347, "outcome": "part of it"}})
    assert top_board_rows.rank(rows)[0]["number"] == 63


def test_main_marks_a_spent_capture_from_the_ledger_it_reads(tmp_path, capsys):
    import json
    issues = tmp_path / "issues.md"
    issues.write_text("- switch to Claude 20x by 18:00\n\n" + board())
    ideas = tmp_path / "ideas.md"
    ideas.write_text(board((92, "a dashboard", BACKLOG, "2026-08-19", HIGH)))
    notes = tmp_path / "notes.md"
    notes.write_text(NOTES.format(" "))
    slug = top_board_rows.slug_for_capture("switch to Claude 20x by 18:00")
    claims = tmp_path / "claims.json"
    claims.write_text(json.dumps(_spent(slug, 343, "journal seq race closed")))

    code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas),
                                "--notes", str(notes), "--claims", str(claims),
                                "--cycle", "353"])
    out = capsys.readouterr().out
    assert code == 0
    # Still printed as a capture, still top of the page -- only the
    # unrunnable command is gone.
    assert "switch to Claude 20x by 18:00" in out
    assert f"[claim: {slug}]" not in out
    assert "claim spent by cycle 343: journal seq race closed" in out
    # The board row beside it is untouched.
    assert "[claim: idea-92]" in out


def test_an_unparseable_ledger_leaves_every_claim_command_printed(tmp_path, capsys):
    """The ranking survives an unreadable ledger and so must this.

    Narrower than it looks, and worth saying so: the parse fails inside
    `load_claims`, so `finished_claims` is never reached and this cannot
    show that the new half handles bad rows. What it does pin is the
    except branch's tuple, which this change widened -- leave `finished`
    out of it and `apply_finished` raises `NameError` on every run with
    an unreadable ledger. Mutation-checked that way, not assumed.
    """
    issues = tmp_path / "issues.md"
    issues.write_text(board((10, "a high issue", BACKLOG, "2026-08-01", HIGH)))
    ideas = tmp_path / "ideas.md"
    ideas.write_text(board())
    notes = tmp_path / "notes.md"
    notes.write_text(NOTES.format(" "))
    claims = tmp_path / "claims.json"
    claims.write_text("{ not json")

    code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas),
                                "--notes", str(notes), "--claims", str(claims)])
    out = capsys.readouterr().out
    assert code == 0
    assert "[claim: issue-10]" in out


def _progressed(item, cycle, outcome):
    return {"claims": [{"item": item, "cycle": cycle, "state": "progressed",
                        "at": "2026-08-23T15:23:26.424577+02:00",
                        "outcome": outcome}]}


def test_a_progressed_slug_keeps_its_take_command_and_gains_the_note():
    """The whole difference from a spent slug: `take` grants this one.

    Printing ⛔ here would be the same bug in reverse -- a row a cycle can
    take, read as one it cannot.
    """
    rows = top_board_rows.open_rows(
        board((63, "four cycles an hour", IN_PROGRESS, "2026-08-23", IMMEDIATE)), "idea")
    top_board_rows.apply_progress(
        rows, {"idea-63": {"cycle": 347, "outcome": "three of four pieces built"}})
    line = top_board_rows._line(rows[0])
    assert "[claim: idea-63]" in line
    assert "claim spent" not in line
    assert "cycle 347 left this open: three of four pieces built" in line


def test_a_spent_slug_wins_over_a_progressed_one_on_the_same_row():
    """Both keys can be stamped; only one of them can be true.

    `finished_claims` and `progressed_claims` read disjoint states out of
    one ledger, so this cannot arise from a real ledger -- it can arise
    from a caller that stamps stale data, and printing a take command for
    a slug `take` refuses is the failure runner#312 already fixed once.
    """
    rows = top_board_rows.open_rows(
        board((63, "four cycles an hour", IN_PROGRESS, "2026-08-23", IMMEDIATE)), "idea")
    top_board_rows.apply_finished(rows, {"idea-63": {"cycle": 347, "outcome": "done"}})
    top_board_rows.apply_progress(rows, {"idea-63": {"cycle": 340, "outcome": "half"}})
    line = top_board_rows._line(rows[0])
    assert "claim spent by cycle 347" in line
    assert "[claim: idea-63]" not in line


def test_a_progressed_outcome_cannot_split_the_row_either():
    rows = top_board_rows.open_rows(
        board((63, "four cycles an hour", IN_PROGRESS, "2026-08-23", IMMEDIATE)), "idea")
    top_board_rows.apply_progress(
        rows, {"idea-63": {"cycle": 347, "outcome": "did this\nand\tthat"}})
    line = top_board_rows._line(rows[0])
    assert "\n" not in line
    assert "did this and that" in line


def test_main_marks_a_progressed_capture_from_the_ledger_it_reads(tmp_path, capsys):
    import json
    issues = tmp_path / "issues.md"
    issues.write_text("- switch to Claude 20x by 18:00\n\n" + board())
    ideas = tmp_path / "ideas.md"
    ideas.write_text(board((92, "a dashboard", BACKLOG, "2026-08-19", HIGH)))
    notes = tmp_path / "notes.md"
    notes.write_text(NOTES.format(" "))
    slug = top_board_rows.slug_for_capture("switch to Claude 20x by 18:00")
    claims = tmp_path / "claims.json"
    claims.write_text(json.dumps(_progressed(slug, 343, "two collision surfaces remain")))

    code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas),
                                "--notes", str(notes), "--claims", str(claims),
                                "--cycle", "353"])
    out = capsys.readouterr().out
    assert code == 0
    assert f"[claim: {slug}]" in out
    assert "cycle 343 left this open: two collision surfaces remain" in out
    assert "claim spent" not in out


def test_a_multi_line_outcome_cannot_split_the_row_it_is_printed_on():
    """`release --outcome` is free shell text and this output is one item
    per line: a newline in there would read as a second board entry."""
    rows = top_board_rows.open_rows(
        board((63, "four cycles an hour", IN_PROGRESS, "2026-08-23", IMMEDIATE)), "idea")
    top_board_rows.apply_finished(
        rows, {"idea-63": {"cycle": 347, "outcome": "built it\nand broke\tthe line"}})
    line = top_board_rows._line(rows[0])
    assert "\n" not in line
    assert "built it and broke the line" in line


def test_the_capture_section_prints_the_address_to_answer_each_one(tmp_path, capsys):
    """The gap six handoffs filed: his bare bullets rank above every row and
    nothing could answer one, because the comment API is keyed by a row
    number a capture has not got. The address is `index` + the bullet, and
    it is printed filled in rather than as a shape, because a cycle that has
    to derive the index does what the last six did and answers in a journal
    entry instead."""
    ideas = tmp_path / "ideas.md"
    notes = tmp_path / "notes.md"
    ideas.write_text(board((64, "an idea", BACKLOG, "08-12", HIGH)))
    notes.write_text(NOTES.format("a note he left"))

    issues = tmp_path / "issues.md"
    issues.write_text("- the first thing he typed\n- the second thing he typed\n- \n\n"
                      + board((10, "a high issue", BACKLOG, "08-01", HIGH)))
    code = top_board_rows.main(["--issues", str(issues), "--ideas", str(ideas),
                                "--notes", str(notes)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "/api/capture/comment" in out
    assert "target issues, index 0  ->  the first thing he typed" in out
    assert "target issues, index 1  ->  the second thing he typed" in out
    # A note is a capture too, and the notes page is the one that already
    # draws the reply properly -- it must carry an address as well.
    assert "target notes, index 0  ->  a note he left" in out

    # **The whole bullet, rating glyph and all, never the display text.**
    # `text` is priority-stripped and was truncated at 60 chars; the route
    # matches the bullet exactly, so anything shortened here comes back 409
    # and the cycle answers in its journal instead -- which is the failure
    # this block exists to stop. Reviewer finding on runner#374.
    rated = tmp_path / "rated.md"
    long_one = "🔴 Immediately: " + ("a capture he typed out in full on his phone " * 3).strip()
    rated.write_text("- " + long_one + "\n- \n\n"
                     + board((11, "another issue", BACKLOG, "08-02", HIGH)))
    top_board_rows.main(["--issues", str(rated), "--ideas", str(ideas),
                         "--notes", str(notes)])
    rated_out = capsys.readouterr().out
    assert len(long_one) > 60, "the fixture has to be past the old truncation"
    assert f"target issues, index 0  ->  {long_one}" in rated_out

    # The help belongs to the captures, so it must not print when there are none.
    notes2 = tmp_path / "notes2.md"
    notes2.write_text(NOTES.format(""))
    issues2 = tmp_path / "issues2.md"
    issues2.write_text(board((10, "a high issue", BACKLOG, "08-01", HIGH)))
    top_board_rows.main(["--issues", str(issues2), "--ideas", str(ideas),
                         "--notes", str(notes2)])
    assert "/api/capture/comment" not in capsys.readouterr().out
