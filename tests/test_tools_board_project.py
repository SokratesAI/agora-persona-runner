"""`tools.board_project` -- tagging rows with a project moves exactly those cells.

`tests/test_board_project.py` one file over covers `set_row_project` itself,
including the header widening that stops a six-cell row landing under a
five-cell header. What is tested here is the half that did not exist until
Cycle 700: the CLI around it, and specifically two things the library call
cannot do on its own.

The first is `check`, which re-parses the whole document and refuses the
write unless the rows named are the only things that moved -- same shape as
`tools.board_status.check`, and tighter, because setting a project may not
change a title, a status, a rating, a write-up or the bullet stream.

The second is that **several rows are set in one process**. A project is by
definition more than one row, and tagging them one run at a time would mean
one compare-and-swap pair per row on the same document; losing one of those
halfway leaves a project that exists on some of its rows and not others.
So the all-or-nothing behaviour is asserted here rather than assumed: a run
naming a good row and a bad one must leave the file exactly as it found it.

Every assertion is on `parse_board` output rather than on the string, for
the same reason `test_tools_board_status` gives: these files are rendered
through that parser, so a shifted cell is still a well-formed table and
reads as plausible right up until the page draws a title in a status column.
"""

from agora_runner.nova_boards import (
    DEFAULT_PROJECT,
    board_projects,
    parse_board,
    parse_notes,
)
from tools.board_project import check, main

BOARD = """---
type: log
---

# Nova — Issues

## Entries

- 2026-08-26 (Cycle 480) — a bullet nothing here may touch

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#122 — k3s on the NAS\\|122]] | k3s on the NAS | ⏸ Blocked on Edvard | 08-30 | 🔴 Immediately |
| [[#131 — server1 memory\\|131]] | server1 memory | 🟡 In progress | 08-31 | 🔴 Immediately |
| [[#104 — Metered API\\|104]] | Metered API | ⚪ Backlog | 08-24 | 🟠 High |

## Done

| # | Item | Landed | Where |
|---|------|--------|-------|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

# Details

### #122 — k3s on the NAS

Body text nothing here may touch.

### #131 — server1 memory

More body text nothing here may touch.

### #104 — Metered API

Yet more body text nothing here may touch.
"""


def _run(tmp_path, board=BOARD, numbers=("122",), project="NAS", **overrides):
    path = tmp_path / "issues.md"
    path.write_text(board, encoding="utf-8")
    argv = ["--file", str(path), "--project", project]
    for number in numbers:
        argv += ["--number", str(number)]
    for flag, value in overrides.items():
        flag = "--" + flag.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value is not None:
            argv += [flag, str(value)]
    return main(argv), path


def _rows(path):
    text = path.read_text(encoding="utf-8")
    return {item["number"]: item for item in parse_board(text)["items"]}


def test_one_row_gets_the_project_and_keeps_everything_else(tmp_path):
    code, path = _run(tmp_path)
    assert code == 0
    row = _rows(path)[122]
    assert row["project"] == "NAS"
    assert row["title"] == "k3s on the NAS"
    assert row["status"] == "⏸ Blocked on Edvard"
    assert row["priority"] == "🔴 Immediately"


def test_every_named_row_lands_in_one_run(tmp_path):
    code, path = _run(tmp_path, numbers=("122", "131"))
    assert code == 0
    rows = _rows(path)
    assert rows[122]["project"] == "NAS"
    assert rows[131]["project"] == "NAS"
    # The row nobody named keeps the board's default rather than inheriting
    # the one being written -- a blank sixth cell reads as `DEFAULT_PROJECT`,
    # which is why "untagged" and "under NAS" are distinguishable at all.
    assert rows[104]["project"] == DEFAULT_PROJECT


def test_the_project_list_is_read_back_off_the_rows(tmp_path):
    code, path = _run(tmp_path, numbers=("122", "131"))
    assert code == 0
    items = parse_board(path.read_text(encoding="utf-8"))["items"]
    # Row order decides the list order, and #122 and #131 come first, so the
    # two tagged rows lead and the untagged one falls back to the default.
    assert board_projects(items) == ["NAS", DEFAULT_PROJECT]


def test_the_header_grows_a_sixth_column(tmp_path):
    """A six-cell row under a five-cell header is dropped by Obsidian."""
    code, path = _run(tmp_path)
    assert code == 0
    text = path.read_text(encoding="utf-8")
    header = [line for line in text.split("\n") if line.startswith("| # |")][0]
    assert header.count("|") == 7, header


def test_a_bad_row_in_a_multi_row_run_writes_nothing(tmp_path, capsys):
    """All or nothing: a half-tagged project is worse than an untagged one.

    The assertion is on *which* refusal fires, not only on the exit code.
    `check` would also refuse this run — it reports every named row that did
    not come back under the project — so stopping at the first bad row is
    invisible from the exit code alone, and a mutation replacing the `return`
    with a `continue` passes a test that reads only the code and the file.
    What the loop actually buys is the message: the row that could not be
    reached is named, with the reason, instead of arriving as a downstream
    parse comparison the reader has to work backwards from.
    """
    code, path = _run(tmp_path, numbers=("122", "999"))
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD
    err = capsys.readouterr().err
    assert "#999 is not a row in '## Board'" in err, err


def test_a_row_only_in_done_is_out_of_reach(tmp_path):
    code, path = _run(tmp_path, numbers=("51",))
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_a_pipe_in_the_project_name_is_refused(tmp_path):
    code, path = _run(tmp_path, project="NAS | server1")
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD


def test_a_blank_project_is_refused(tmp_path, capsys):
    """And refused *here*, by name, rather than falling through to the library.

    `set_row_project` also rejects an empty name, so the exit code is 1
    either way and a test that reads only the code cannot tell the CLI's own
    guard from the one underneath it. The two produce different messages, and
    the one that says "--project may not be blank" is the one that names the
    argument the caller actually typed.
    """
    code, path = _run(tmp_path, project="   ")
    assert code == 1
    assert path.read_text(encoding="utf-8") == BOARD
    assert "--project may not be blank" in capsys.readouterr().err


def test_dry_run_prints_and_writes_nothing(tmp_path):
    code, path = _run(tmp_path, dry_run=True)
    assert code == 0
    assert path.read_text(encoding="utf-8") == BOARD


def test_check_catches_a_status_changed_underneath_the_project_move(tmp_path):
    """`check` reads the document, not the diff it was handed."""
    after = BOARD.replace("🟡 In progress", "✅ Done")
    problems = check(BOARD, after, [122], "NAS")
    assert any("#131" in problem for problem in problems), problems


def test_check_catches_a_row_that_did_not_get_the_project(tmp_path):
    problems = check(BOARD, BOARD, [122], "NAS")
    assert any("asked for 'NAS'" in problem for problem in problems), problems


def test_check_catches_an_edited_write_up(tmp_path):
    code, path = _run(tmp_path)
    assert code == 0
    good = path.read_text(encoding="utf-8")
    bad = good.replace("Body text nothing here may touch.", "rewritten")
    problems = check(BOARD, bad, [122], "NAS")
    assert any("write-up" in problem for problem in problems), problems


def test_check_catches_a_lost_bullet(tmp_path):
    code, path = _run(tmp_path)
    assert code == 0
    good = path.read_text(encoding="utf-8")
    bad = good.replace("- 2026-08-26 (Cycle 480) — a bullet nothing here may touch\n", "")
    assert len(parse_notes(bad)) < len(parse_notes(BOARD))
    problems = check(BOARD, bad, [122], "NAS")
    assert any("bullet stream" in problem for problem in problems), problems
