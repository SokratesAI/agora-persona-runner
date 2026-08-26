"""`tools.board_status` -- moving one row's status moves exactly that cell.

`tests/test_board_status.py` one file over covers `set_row_status` itself,
including the column shift Cycle 202 nearly wrote into the owner's file by
hand. What is tested here is the half that did not exist until now: the CLI
around it, and specifically `check`, which re-parses the whole document and
refuses the write unless the row named is the only thing that moved.

Every assertion is on `parse_board` output rather than on the string, for
the same reason `test_board_row` gives: these files are rendered through
that parser, so a shifted cell is still a well-formed table and reads as
plausible right up until the page draws a title in the status column.
"""

import pytest

from agora_runner import nova_boards
from agora_runner.nova_boards import parse_board, parse_notes
from tools.board_status import CLOSED_STATUS_KEYS, check, main, resolve_status

BOARD = """---
type: log
---

# Nova — Ideas

## Entries

- 2026-08-26 (Cycle 480) — a bullet nothing here may touch

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#100 — Weekly work\\|100]] | Weekly work | 🟡 In progress | 08-24 | 🟠 High |
| [[#104 — Metered API\\|104]] | Metered API | ⚪ Backlog | 08-24 | 🟠 High |

## Done

| # | Item | Landed | Where |
|---|------|--------|-------|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

# Details

### #100 — Weekly work

Three heartbeats, one prompt file each.

### #104 — Metered API

Body text nothing here may touch.
"""


def _run(tmp_path, board=BOARD, **overrides):
    path = tmp_path / "ideas.md"
    path.write_text(board, encoding="utf-8")
    argv = ["--file", str(path), "--number", "100", "--status", "done"]
    for flag, value in overrides.items():
        flag = "--" + flag.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value is not None:
            argv += [flag, str(value)]
    return main(argv), path


def _rows(path):
    return {item["number"]: item for item in parse_board(path.read_text(encoding="utf-8"))["items"]}


def test_the_named_row_moves_and_keeps_its_title(tmp_path):
    code, path = _run(tmp_path, dated="08-26")
    assert code == 0
    row = _rows(path)[100]
    assert row["status"] == "✅ Done"
    assert row["title"] == "Weekly work"
    assert row["updated"] == "08-26"


def test_closing_a_row_clears_its_rating(tmp_path):
    """`set_row_priority` refuses a finished row, so a chip left behind
    could never be cleared again -- the two functions have to agree."""
    _, path = _run(tmp_path, dated="08-26")
    assert _rows(path)[100]["priority"] == ""


def test_an_open_status_keeps_the_rating(tmp_path):
    _, path = _run(tmp_path, status="in-progress", number=104, dated="08-26")
    assert _rows(path)[104]["priority"] == "🟠 High"
    assert _rows(path)[104]["status"] == "🟡 In progress"


def test_every_other_row_is_untouched(tmp_path):
    before = _rows_from(BOARD)
    _, path = _run(tmp_path, dated="08-26")
    after = _rows(path)
    assert after[104] == before[104]
    assert after[51] == before[51]


def _rows_from(markdown):
    return {item["number"]: item for item in parse_board(markdown)["items"]}


def test_the_bullet_stream_and_other_write_ups_survive(tmp_path):
    _, path = _run(tmp_path, dated="08-26")
    parsed = parse_board(path.read_text(encoding="utf-8"))
    assert [n["text"] for n in parse_notes(path.read_text(encoding="utf-8"))] == \
        [n["text"] for n in parse_notes(BOARD)]
    assert parsed["details"][104] == parse_board(BOARD)["details"][104]


def test_a_note_is_appended_under_the_row_it_explains(tmp_path):
    code, path = _run(tmp_path, dated="08-26", note="all three heartbeats fire", cycle=498)
    assert code == 0
    body = parse_board(path.read_text(encoding="utf-8"))["details"][100]
    assert body.startswith(parse_board(BOARD)["details"][100])
    assert "all three heartbeats fire" in body


def test_a_note_without_a_date_is_refused(tmp_path, capsys):
    """It is written as a dated line, and `append_detail_note` takes the
    date rather than reaching for a clock, because these files are Oslo.

    **The message is asserted, not just the exit code, and that is the
    point of this test.** `append_detail_note` already returns `None` on a
    missing date, so deleting the guard in `main` leaves the exit code at
    1 and every other assertion here green -- I mutated it out and all 18
    tests still passed. What is actually lost is the caller being told
    *which* argument is missing: the fallback says only "could not append
    the note", which is the same thing it says for four other causes.
    """
    code, path = _run(tmp_path, note="no date here")
    assert code == 1
    assert "needs --dated" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == BOARD


@pytest.mark.parametrize("field,value", [
    ("dated", "08-26 | extra"),
    ("dated", " "),
    ("note", "why | not"),
])
def test_a_cell_delimiter_is_refused_before_anything_is_written(tmp_path, field, value):
    kwargs = {field: value}
    if field == "note":
        kwargs["dated"] = "08-26"
    code, path = _run(tmp_path, **kwargs)
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_an_unknown_status_is_refused(tmp_path):
    code, path = _run(tmp_path, status="nearly done")
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_a_row_only_in_the_done_table_is_refused(tmp_path):
    """`_row_span` will not reach it, and that table puts a date where
    this one writes a status."""
    code, path = _run(tmp_path, number=51)
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_a_row_that_does_not_exist_is_refused(tmp_path):
    code, path = _run(tmp_path, number=999)
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_dry_run_reports_but_does_not_write(tmp_path):
    code, path = _run(tmp_path, dated="08-26", dry_run=True)
    assert code == 0
    assert path.read_text(encoding="utf-8") == BOARD


def test_every_written_status_spelling_is_accepted():
    for key, label in nova_boards.STATUS_LABELS.items():
        assert resolve_status(key) == label
        assert resolve_status(label) == label
    assert resolve_status("Done") == "✅ Done"
    assert resolve_status("") is None
    assert resolve_status("nearly") is None


def test_the_closed_status_copy_matches_the_module():
    """This constant is hand-copied because the module's is private.
    Two hand-copied constants drift; this is the check that they cannot."""
    assert CLOSED_STATUS_KEYS == nova_boards._CLOSED_STATUS_KEYS


def test_check_catches_a_second_row_moving():
    """The guard, exercised directly: `check` is what stands between a
    bug in `set_row_status` and the owner's file."""
    before = BOARD
    after = BOARD.replace("| Metered API | ⚪ Backlog |", "| Metered API | ✅ Done |")
    after = after.replace("| Weekly work | 🟡 In progress |", "| Weekly work | ✅ Done |")
    problems = check(before, after, 100, "✅ Done", noted=False)
    assert any("#104 changed" in p for p in problems)


def test_check_catches_a_rewritten_write_up():
    before = BOARD
    after = BOARD.replace("| Weekly work | 🟡 In progress |", "| Weekly work | ✅ Done |")
    after = after.replace("Three heartbeats, one prompt file each.", "Something else entirely.")
    problems = check(before, after, 100, "✅ Done", noted=True)
    assert any("was rewritten, not appended to" in p for p in problems)
