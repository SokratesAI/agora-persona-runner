"""`tools.roll_done_captures` -- finished captures leave, nothing else does.

The failure this guards is silent: these two files are the owner's own, the
app renders them through `nova_boards.parse_board`, and a rewrite that
tears a `### #N` write-up out of its heading makes it stop appearing on
his phone with every test still green. So the assertions below are on
`parse_board`'s output rather than on the text, except where the point
is specifically that the text moved verbatim.
"""

from agora_runner.nova_boards import parse_board
import tools.roll_done_captures as roll_done_captures
from tools.roll_done_captures import (
    PROCESSED_HEADING, check_from_contents, plan, rewrite,
)


def check(before, after, moved):
    """The old markdown-shaped signature, kept here and not in the tool.

    `check_from_contents` takes the two parsed boards on purpose (#203)
    and carries no door, so the parsing every existing test used to get
    for free lives in the test file that wants it.
    """
    return check_from_contents(
        parse_board(before), parse_board(after), before, after, moved)

BOARD = """---
type: board
---

- DONE (Cycle 4): shipped it — the header is bold now
- 🟠 High: the search bar closes my keyboard
- DONE (Cycle 9): answered on the row
-

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| #2 | The search bar closes my keyboard | ⚪ Backlog | 08-20 | 🟠 High |

# Details

### #2 — The search bar closes my keyboard
Every letter dismisses it.
"""


def test_only_the_done_bullets_are_planned_for_the_move():
    kept, moved = plan(BOARD)
    # The bare `-` is his cursor, not a capture; it rides along in `kept`
    # and `rewrite` is what re-lays it as the single trailing bullet.
    assert [b[0] for b in kept] == [
        "- 🟠 High: the search bar closes my keyboard",
        "-",
    ]
    assert len(moved) == 2


def test_the_finished_captures_leave_the_top_and_the_live_one_stays():
    after, moved = rewrite(BOARD)
    assert moved == 2
    assert parse_board(after)["captures"] == [
        "🟠 High: the search bar closes my keyboard"
    ]


def test_rows_and_write_ups_come_back_identical():
    after, _ = rewrite(BOARD)
    before, now = parse_board(BOARD), parse_board(after)
    assert before["items"] == now["items"]
    assert before["details"] == now["details"]


def test_every_moved_bullet_is_still_in_the_file_word_for_word():
    after, _ = rewrite(BOARD)
    head, _, archive = after.partition(PROCESSED_HEADING)
    assert "DONE (Cycle 4): shipped it — the header is bold now" in archive
    assert "DONE (Cycle 9): answered on the row" in archive
    assert "DONE (Cycle 4)" not in head


def test_the_cursor_bullet_survives_a_list_that_was_entirely_done():
    all_done = BOARD.replace("- 🟠 High: the search bar closes my keyboard\n", "")
    after, moved = rewrite(all_done)
    assert moved == 2
    assert parse_board(after)["captures"] == []
    assert "\n- \n" in after


def test_a_second_run_moves_nothing():
    once, _ = rewrite(BOARD)
    twice, moved = rewrite(once)
    assert moved == 0
    assert twice == once


def test_a_wrapped_capture_travels_with_its_own_bullet():
    wrapped = BOARD.replace(
        "- DONE (Cycle 9): answered on the row\n",
        "- DONE (Cycle 9): answered on the row\n  and here is the rest of it\n",
    )
    after, _ = rewrite(wrapped)
    head, _, archive = after.partition(PROCESSED_HEADING)
    assert "and here is the rest of it" in archive
    assert "and here is the rest of it" not in head


def test_a_file_with_no_finished_captures_is_returned_untouched():
    live = BOARD.replace("DONE (Cycle 4): ", "").replace("DONE (Cycle 9): ", "")
    after, moved = rewrite(live)
    assert moved == 0
    assert after == live


def test_check_catches_a_rewrite_that_dropped_a_write_up():
    after, moved = rewrite(BOARD)
    assert check(BOARD, after, moved) == []
    mangled = after.replace("### #2 — The search bar closes my keyboard\n", "")
    assert check(BOARD, mangled, moved)


def test_check_catches_a_rewrite_that_lost_a_live_capture():
    after, moved = rewrite(BOARD)
    mangled = after.replace("- 🟠 High: the search bar closes my keyboard\n", "")
    problems = check(BOARD, mangled, moved)
    assert any("expected" in p for p in problems)


def test_trailing_blank_lines_are_not_eaten():
    """Both real files end in ~100 blank lines. They are his, not mine."""
    padded = BOARD + "\n" * 40
    after, _ = rewrite(padded)
    assert len(after) - len(after.rstrip("\n")) == 41  # 40 + the file's own last newline
    plain, _ = rewrite(BOARD)
    assert len(plain) - len(plain.rstrip("\n")) == 1


def test_the_heading_guard_is_a_heading_not_a_substring():
    prose = BOARD.replace(
        "Every letter dismisses it.",
        "Every letter dismisses it. See the processed captures section.",
    )
    after, _ = rewrite(prose)
    assert after.count(PROCESSED_HEADING) == 1
    head, _, archive = after.partition("\n" + PROCESSED_HEADING)
    assert "DONE (Cycle 4)" in archive


def test_a_line_the_reader_ignores_is_not_dragged_along():
    """`_captures` only folds a non-blank line that is not `-`/`*`/`|`."""
    odd = BOARD.replace(
        "- DONE (Cycle 9): answered on the row\n",
        "- DONE (Cycle 9): answered on the row\n* a starred line\n",
    )
    after, _ = rewrite(odd)
    head, _, archive = after.partition("\n" + PROCESSED_HEADING)
    assert "* a starred line" in head
    assert "* a starred line" not in archive


# --- A marker that names where the work landed (runner#298) ---
#
# Reviewer finding on #298: widening `_CAPTURE_DONE_RE` to tolerate anything
# after the cycle number inside the bracket changes what this tool *writes*
# into the owner's own board files, and every fixture above still used the plain
# `DONE (Cycle N):` shape. The read path was covered; this write path was not.

NAMED_PR = """---
type: board
---

- DONE (Cycle 337, platform-config#516): the cascading model fallback
- 🟠 High: the search bar closes my keyboard
-

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| #2 | The search bar closes my keyboard | ⚪ Backlog | 08-20 | 🟠 High |

# Details

### #2 — The search bar closes my keyboard
Every letter dismisses it.
"""


def test_a_marker_naming_its_pr_is_rolled_out_of_his_file():
    """Cycle 337 wrote exactly this and it stayed stuck above his cursor."""
    after, moved = rewrite(NAMED_PR)
    assert moved == 1
    assert parse_board(after)["captures"] == [
        "🟠 High: the search bar closes my keyboard"
    ]
    head, _, archive = after.partition(PROCESSED_HEADING)
    assert "DONE (Cycle 337, platform-config#516): the cascading model fallback" in archive
    assert "platform-config#516" not in head


def test_a_bracket_with_no_cycle_number_is_left_where_he_typed_it():
    """The control. `[^)]*` must not have widened this into a marker -- rolling
    one of his live captures into an archive he does not read is the one
    failure this tool must never have."""
    text = NAMED_PR.replace("DONE (Cycle 337, platform-config#516):",
                            "DONE (nearly, I think):")
    after, moved = rewrite(text)
    assert moved == 0
    assert "DONE (nearly, I think): the cascading model fallback" in \
        parse_board(after)["captures"]


REPLIED = """---
type: board
---

- DONE (Cycle 413): the notes text is grey and hard to read
  - Fixed in runner#360 — say the word and the byline goes white too.
- 🟠 High: the search bar closes my keyboard
-

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| #2 | The search bar closes my keyboard | ⚪ Backlog | 08-20 | 🟠 High |

# Details

### #2 — The search bar closes my keyboard
Every letter dismisses it.
"""


def test_a_reply_written_under_a_capture_leaves_with_it():
    """The failure that put a cycle's own note at the top of his `issues.md`.

    A reply is written as an indented bullet under the capture it answers.
    `plan` used to start a new block on it, because it looked at the
    stripped line and an indented `- ` is still a `- `. The owner's bullet
    was `DONE` and moved; the reply under it was not and stayed -- alone
    above `## Board`, in the slot his contract reserves for him, where
    `top_board_rows` ranked it first as an unprocessed capture from him on
    every cycle after that.
    """
    kept, moved = plan(REPLIED)
    assert len(moved) == 1
    assert moved[0] == [
        "- DONE (Cycle 413): the notes text is grey and hard to read",
        "  - Fixed in runner#360 — say the word and the byline goes white too.",
    ]
    assert [b[0] for b in kept] == ["- 🟠 High: the search bar closes my keyboard", "-"]

    out, count = rewrite(REPLIED)
    assert count == 1
    assert "runner#360" not in out.split("## Board")[0]
    assert "runner#360" in out.split(PROCESSED_HEADING)[1]
    assert check(REPLIED, out, count) == []


def test_an_indented_bullet_with_nothing_above_it_is_still_his():
    """No preceding capture to attach to: keep it rather than lose it.

    An indented bullet only means "a reply to the line above" when there
    is a line above. Dropping it otherwise would delete text out of his
    file to satisfy a rule about a shape that is not there.
    """
    stray = BOARD.replace(
        "- DONE (Cycle 4): shipped it — the header is bold now",
        "  - a stray indented line\n- DONE (Cycle 4): shipped it — the header is bold now",
    )
    kept, _ = plan(stray)
    assert "  - a stray indented line" in [b[0] for b in kept]


def _run(tmp_path, extra=()):
    """One successful `--dry-run` roll of both finished captures."""
    board = tmp_path / "issues.md"
    board.write_text(BOARD, encoding="utf-8")
    return roll_done_captures.main(
        ["--file", str(board), "--dry-run", *extra])


def test_main_reads_each_version_once(tmp_path, monkeypatch):
    """Two versions of one document, one parse each -- #203's read-once rule.

    `check` used to take the two markdown strings and parse both itself,
    which on a string is free and on the record store is a round trip the
    guard takes behind `main`'s back.
    """
    count = {"n": 0}
    real = roll_done_captures.parse_board

    def counting(markdown):
        count["n"] += 1
        return real(markdown)

    monkeypatch.setattr(roll_done_captures, "parse_board", counting)
    assert _run(tmp_path) == 0
    assert count["n"] == 2


def test_the_guard_cannot_parse_a_document(tmp_path, monkeypatch):
    """`check_from_contents` is handed what it compares; it parses nothing.

    The count above is satisfied by a guard that parses once and a `main`
    that parses once, which is not the property that survives the
    switchover. It still takes both raw documents on purpose -- the
    capture-line half has no records-shaped question -- so what this pins
    is that it never parses one.
    """
    guard = roll_done_captures.check_from_contents

    def no_parsing_here(*a, **k):
        monkeypatch.setattr(roll_done_captures, "parse_board", _refuse)
        return guard(*a, **k)

    monkeypatch.setattr(roll_done_captures, "check_from_contents", no_parsing_here)
    assert _run(tmp_path) == 0


def _refuse(*a, **k):
    raise AssertionError("the guard parsed a document it was handed parsed")


def _damaged_main(tmp_path, monkeypatch, damage):
    real = roll_done_captures.rewrite

    def damaged(markdown):
        after, moved = real(markdown)
        return damage(after), moved

    monkeypatch.setattr(roll_done_captures, "rewrite", damaged)
    return _run(tmp_path)


def test_main_refuses_a_write_that_moved_one_of_his_rows(tmp_path, monkeypatch):
    """The guard driven through `main`, not called as a function.

    Every other test of it builds both sides itself, so nothing proved
    `main` hands it the right two documents -- a `main` that parsed
    `after` twice compares a board to itself and can never report
    anything, while every direct test stays green.
    """
    assert _damaged_main(
        tmp_path, monkeypatch,
        lambda t: t.replace("| ⚪ Backlog | 08-20 |", "| ✅ Done | 08-20 |")) == 1


def test_main_refuses_a_write_that_changed_one_of_his_write_ups(tmp_path, monkeypatch):
    """The `# Details` half, through `main` for the same reason."""
    assert _damaged_main(
        tmp_path, monkeypatch,
        lambda t: t.replace("Every letter dismisses it.", "Something else.")) == 1


def test_main_refuses_a_write_that_lost_a_capture_line(tmp_path, monkeypatch):
    """The raw-line half, which is why the guard still takes both texts."""
    assert _damaged_main(
        tmp_path, monkeypatch,
        lambda t: t.replace("DONE (Cycle 9): answered on the row\n", "")) == 1
