"""`tools.board_priority` -- re-rating one row moves exactly that cell.

`tests/test_board_priority.py` one file over covers `set_row_priority`
itself. What is tested here is the half that did not exist until Cycle
1087: the CLI around it, and specifically `check`, which re-parses the
whole document and refuses the write unless the rating named is the only
thing that moved.

Every assertion is on `parse_board` output rather than on the string, for
the same reason `test_tools_board_status` gives: these files are rendered
through that parser, so a shifted cell is still a well-formed table and
reads as plausible right up until the page draws a title in the rating
column.
"""

import pytest

from agora_runner.nova_boards import parse_board, parse_notes
from tools.board_priority import check_from_contents, main, resolve_priority


def _texts(markdown):
    """The bullet stream `check_from_contents` compares, read the way `main` reads it."""
    return [note["text"] for note in parse_notes(markdown)]

BOARD = """---
type: log
---

# Nova — Ideas

## Entries

- 2026-09-06 (Cycle 1087) — a bullet nothing here may touch

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#260 — Redesign the picker\\|260]] | Redesign the picker | ⚪ Backlog | 09-06 | 🟠 High |
| [[#259 — Demos live two weeks\\|259]] | Demos live two weeks | 🟡 In progress | 09-06 | 🔵 Medium |
| [[#258 — Spread the load\\|258]] | Spread the load | ✅ Done | 09-05 | |

## Done

| # | Item | Landed | Where |
|---|------|--------|-------|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

# Details

### #260 — Redesign the picker

The full spec lives in its own note.

### #259 — Demos live two weeks

Body text nothing here may touch.
"""


def _run(tmp_path, board=BOARD, **overrides):
    path = tmp_path / "ideas.md"
    path.write_text(board, encoding="utf-8")
    argv = ["--file", str(path), "--number", "260", "--priority", "immediate"]
    for flag, value in overrides.items():
        flag = "--" + flag.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value is not None:
            argv += [flag, str(value)]
    return main(argv), path


def _rows(path):
    return {item["number"]: item for item in parse_board(path.read_text(encoding="utf-8"))["items"]}


def _rows_from(markdown):
    return {item["number"]: item for item in parse_board(markdown)["items"]}


def test_the_named_row_is_re_rated_and_keeps_everything_else(tmp_path):
    code, path = _run(tmp_path)
    assert code == 0
    row = _rows(path)[260]
    assert row["priority"] == "🔴 Immediately"
    assert row["title"] == "Redesign the picker"
    assert row["status"] == "⚪ Backlog"
    assert row["updated"] == "09-06"


def test_every_other_row_is_untouched(tmp_path):
    before = _rows_from(BOARD)
    _, path = _run(tmp_path)
    after = _rows(path)
    assert after[259] == before[259]
    assert after[258] == before[258]
    assert after[51] == before[51]


def test_the_bullet_stream_and_write_ups_survive(tmp_path):
    _, path = _run(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert [note["text"] for note in parse_notes(text)] == \
        [note["text"] for note in parse_notes(BOARD)]
    assert parse_board(text)["details"] == parse_board(BOARD)["details"]


@pytest.mark.parametrize(
    "spelling", ["immediate", "Immediately", "🔴 Immediately", "urgent", "now"]
)
def test_every_spelling_the_rest_of_the_system_treats_as_equal(spelling):
    assert resolve_priority(spelling) == "🔴 Immediately"


@pytest.mark.parametrize("spelling", ["", "   ", None, "critical", "P0", "🟣 Vital"])
def test_a_rating_the_system_does_not_have_is_refused(spelling):
    """Blank is the one `set_row_priority` itself accepts and this must not:
    it is the state that means nobody has looked."""
    assert resolve_priority(spelling) is None


def test_a_blank_rating_is_refused_at_the_cli(tmp_path, capsys):
    code, path = _run(tmp_path, priority="")
    assert code == 1
    assert _rows(path)[260]["priority"] == "🟠 High"
    assert "is not a rating" in capsys.readouterr().err


def test_a_finished_row_cannot_be_rated(tmp_path, capsys):
    """`set_row_priority` refuses one, because Cycle 188 left a closed row's
    chip deliberately empty and a chip written back could never be cleared."""
    code, path = _run(tmp_path, number=258)
    assert code == 1
    assert _rows(path)[258]["priority"] == ""
    assert "not an open row" in capsys.readouterr().err


def test_a_row_that_is_not_there_is_refused(tmp_path, capsys):
    code, path = _run(tmp_path, number=999)
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD
    assert "not an open row" in capsys.readouterr().err


def test_a_note_is_appended_to_that_rows_write_up(tmp_path):
    code, path = _run(tmp_path, dated="09-07", note="you asked for this", cycle=1087)
    assert code == 0
    details = parse_board(path.read_text(encoding="utf-8"))["details"]
    assert details[260].startswith(parse_board(BOARD)["details"][260])
    assert "you asked for this" in details[260]
    assert details[259] == parse_board(BOARD)["details"][259]


def test_a_note_without_a_date_is_refused(tmp_path, capsys):
    code, path = _run(tmp_path, note="no date here")
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD
    assert "needs --dated" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["a | b", "a\nb", " "])
def test_a_cell_splitting_note_is_refused(tmp_path, bad, capsys):
    code, path = _run(tmp_path, dated="09-07", note=bad)
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD
    assert "REFUSED" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["09|06", "09\n06", " "])
def test_a_cell_splitting_date_is_refused(tmp_path, bad, capsys):
    code, path = _run(tmp_path, dated=bad, note="fine")
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD
    assert "REFUSED" in capsys.readouterr().err


def test_dry_run_prints_the_move_and_writes_nothing(tmp_path, capsys):
    code, path = _run(tmp_path, dry_run=True)
    assert code == 0
    assert path.read_text(encoding="utf-8") == BOARD
    assert "🟠 High -> 🔴 Immediately" in capsys.readouterr().out


def test_out_leaves_the_source_alone(tmp_path):
    target = tmp_path / "copy.md"
    code, path = _run(tmp_path, out=str(target))
    assert code == 0
    assert path.read_text(encoding="utf-8") == BOARD
    assert _rows_from(target.read_text(encoding="utf-8"))[260]["priority"] == "🔴 Immediately"


def test_check_catches_a_second_row_moving_underneath_it():
    """The guard, driven directly: `check` is the only thing standing between
    a damaged table and his file, so it is tested against damage the CLI
    cannot produce on its own."""
    after = BOARD.replace(
        "| Redesign the picker | ⚪ Backlog | 09-06 | 🟠 High |",
        "| Redesign the picker | ⚪ Backlog | 09-06 | 🔴 Immediately |",
    ).replace("Demos live two weeks | 🟡 In progress", "Demos live two weeks | ⚪ Backlog")
    problems = check_from_contents(parse_board(BOARD), parse_board(after), _texts(BOARD), _texts(after), 260, "🔴 Immediately", noted=False)
    assert any("#259 changed underneath the re-rating" in p for p in problems)


def test_check_catches_the_target_row_losing_a_cell():
    after = BOARD.replace(
        "| Redesign the picker | ⚪ Backlog | 09-06 | 🟠 High |",
        "| Redesign the picker | 🟡 In progress | 09-06 | 🔴 Immediately |",
    )
    problems = check_from_contents(parse_board(BOARD), parse_board(after), _texts(BOARD), _texts(after), 260, "🔴 Immediately", noted=False)
    assert any("other than its rating" in p for p in problems)


def test_check_catches_a_lost_bullet():
    after = BOARD.replace(
        "| Redesign the picker | ⚪ Backlog | 09-06 | 🟠 High |",
        "| Redesign the picker | ⚪ Backlog | 09-06 | 🔴 Immediately |",
    ).replace("- 2026-09-06 (Cycle 1087) — a bullet nothing here may touch\n", "")
    problems = check_from_contents(parse_board(BOARD), parse_board(after), _texts(BOARD), _texts(after), 260, "🔴 Immediately", noted=False)
    assert any("bullet stream changed" in p for p in problems)


def test_check_catches_a_rewritten_write_up():
    after = BOARD.replace(
        "| Redesign the picker | ⚪ Backlog | 09-06 | 🟠 High |",
        "| Redesign the picker | ⚪ Backlog | 09-06 | 🔴 Immediately |",
    ).replace("The full spec lives in its own note.", "Something else entirely.")
    problems = check_from_contents(parse_board(BOARD), parse_board(after), _texts(BOARD), _texts(after), 260, "🔴 Immediately", noted=True)
    assert any("was rewritten, not appended to" in p for p in problems)


def test_check_passes_the_note_carrying_write_on_a_date_the_row_did_not_have(tmp_path):
    """The reviewer's finding, pinned. `append_detail_note` stamps `Updated`
    with `--dated`, and a re-rating carries a *new* date almost every time --
    the row is re-rated because time has passed. The first `check` excluded
    only the two rating fields, so it refused this, and every test I had
    written passed `--dated 09-06` against a row already dated 09-06, which
    is a positive result guaranteed in advance."""
    code, path = _run(tmp_path, dated="09-07", note="you asked for this", cycle=1087)
    assert code == 0
    text = path.read_text(encoding="utf-8")
    assert _rows_from(text)[260]["updated"] == "09-07"
    assert check_from_contents(parse_board(BOARD), parse_board(text), _texts(BOARD), _texts(text), 260, "🔴 Immediately", noted=True, dated="09-07") == []


def test_check_refuses_a_date_the_caller_did_not_ask_for():
    """The forgiveness asserts the new value rather than skipping the field:
    excluding `updated` outright would let any date through."""
    after = BOARD.replace(
        "| Redesign the picker | ⚪ Backlog | 09-06 | 🟠 High |",
        "| Redesign the picker | ⚪ Backlog | 01-01 | 🔴 Immediately |",
    )
    problems = check_from_contents(parse_board(BOARD), parse_board(after), _texts(BOARD), _texts(after), 260, "🔴 Immediately", noted=True, dated="09-07")
    assert any("came back updated" in p for p in problems)


def test_a_re_rating_without_a_note_may_not_move_the_date():
    after = BOARD.replace(
        "| Redesign the picker | ⚪ Backlog | 09-06 | 🟠 High |",
        "| Redesign the picker | ⚪ Backlog | 09-07 | 🔴 Immediately |",
    )
    problems = check_from_contents(parse_board(BOARD), parse_board(after), _texts(BOARD), _texts(after), 260, "🔴 Immediately", noted=False)
    assert any("other than its rating" in p for p in problems)


def test_main_actually_refuses_when_check_reports_a_problem(tmp_path, monkeypatch):
    """The reviewer's second finding: every test drove `check` as a function
    and nothing proved `main` wires its result into the write path. Deleting
    the gate left all 33 green. It does not now."""
    import tools.board_priority as module

    monkeypatch.setattr(module, "check_from_contents", lambda *a, **k: ["invented problem"])
    code, path = _run(tmp_path)
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_the_rating_cell_derives_exactly_the_keys_check_forgives():
    """`_RATING_KEYS` is a hand-written claim about `parse_board`'s output,
    and a hand-written claim about another module is how two copies drift.
    If the parser ever derives a third field from the rating cell, `check`
    would refuse a legitimate re-rating -- the safe direction, but silently,
    so this pins the pair instead."""
    from tools.board_priority import _RATING_KEYS

    after = set_row_priority_for_test()
    was = _rows_from(BOARD)[260]
    now = _rows_from(after)[260]
    assert {key for key in was if was[key] != now[key]} == set(_RATING_KEYS)


def set_row_priority_for_test():
    from agora_runner.nova_boards import set_row_priority

    return set_row_priority(BOARD, 260, "🔴 Immediately")
