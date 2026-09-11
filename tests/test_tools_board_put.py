"""`tools.board_put` -- a vault write, and after the flip nothing follows it.

**His two boards are the #203 records now.** Until Cycle 1395 a write to
either file was followed by `board_migrate.resync`, markdown in and records
out. Once `nova_site` reads only the records that direction overwrites his
app edits with a stale drawing, so a landed write must never reach the
store -- not through `resync`, not through a stamp.

**A path that is not a board is refused rather than quietly put.**
"""

import subprocess

import pytest

from tools import board_put


BOARD = "projects/sokrates/projects/nova/ideas.md"
NOT_A_BOARD = "projects/sokrates/projects/agora/journal-digest.md"
MINE = "projects/sokrates/projects/agora/nova/resources/issues.md"


def _run(returncode, stdout="written: ideas.md\n", stderr="",
         read_back=None, rev="7-abc"):
    """A stand-in for `subprocess.run` that records what it was asked."""
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if "get" in command:
            if "--rev-file" in command:
                rev_file = command[command.index("--rev-file") + 1]
                open(rev_file, "w", encoding="utf-8").write(rev or "")
            body = read_back
            if body is None:
                body = open(calls[0][4], encoding="utf-8").read()
            if body == "":
                return subprocess.CompletedProcess(command, returncode, stdout, stderr)
            # `vault_tool.py get` ends in `print`.
            return subprocess.CompletedProcess(command, 0, body + "\n", "")
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    runner.calls = calls
    return runner


@pytest.fixture
def board_file(tmp_path):
    path = tmp_path / "ideas.md"
    path.write_text("# Ideas\n\n| # | Title |\n", encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def records_are_untouchable(monkeypatch):
    """Any write to the record store from this tool is the bug.

    Patched on the modules the store is reached through, not on
    `board_put`, so a hook that came back under a new name still trips it.
    """
    from agora_runner import board_records, board_store
    from tools import board_migrate

    def refuse(*args, **kwargs):
        raise AssertionError("board_put wrote the #203 records")

    monkeypatch.setattr(board_migrate, "resync", refuse)
    monkeypatch.setattr(board_records, "stamp_source_rev", refuse)
    for name in ("write_rows", "write_captures", "write_layout",
                 "write_registry"):
        monkeypatch.setattr(board_store, name, refuse)


def test_the_rev_file_reaches_the_vault_client(monkeypatch, board_file):
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    assert board_put.main([BOARD, board_file, "--if-rev-file", "/tmp/x.rev"]) == 0
    assert runner.calls[0][-2:] == ["--if-rev-file", "/tmp/x.rev"]
    assert runner.calls[0][2:5] == ["put", BOARD, board_file]


def test_a_path_that_is_not_a_board_is_refused_before_anything_is_written(
        monkeypatch, board_file):
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    assert board_put.main([NOT_A_BOARD, board_file]) == 1
    assert runner.calls == []


def test_every_board_is_accepted(monkeypatch, board_file):
    """The four paths are `ticket_docs.BOARDS`, not a second list here."""
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    for board in board_put.ticket_docs.BOARDS:
        assert board_put.main([board, board_file]) == 0


def test_append_with_a_rev_file_is_refused(monkeypatch, board_file):
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    assert board_put.main(
        [MINE, board_file, "--append", "## Entries", "--if-rev-file", "/tmp/x"]) == 1
    assert runner.calls == []


def test_the_read_back_subtracts_the_newline_vault_tool_prints(monkeypatch):
    """runner#673's byte, on the one path here that goes through `print`."""
    monkeypatch.setattr(board_put.subprocess, "run",
                        _run(0, read_back="# Issues\n\n- a note\n"))
    assert board_put.vault_get(MINE)[0] == "# Issues\n\n- a note\n"


def test_a_board_the_vault_does_not_hold_reads_as_absent(monkeypatch):
    monkeypatch.setattr(board_put.subprocess, "run", _run(0, read_back="[not found]"))
    assert board_put.vault_get(MINE) == (None, None)


def test_the_path_is_matched_case_insensitively():
    assert board_put.record_board(BOARD.upper()) == "idea"
    assert board_put.record_board(None) is None


def test_a_landed_write_to_his_board_never_reaches_the_records(
        monkeypatch, board_file, capsys):
    """The flip: the autouse fixture raises on any store write, so exit 0
    here means the put landed and nothing followed it."""
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    assert board_put.main([BOARD, board_file]) == 0
    assert [call[2] for call in runner.calls] == ["put"]
    assert "records: not touched" in capsys.readouterr().err


def test_an_append_to_his_board_never_reaches_the_records(
        monkeypatch, board_file):
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    assert board_put.main([BOARD, board_file, "--append", "## Entries"]) == 0
    assert runner.calls[0][2:6] == ["append", BOARD, board_file, "## Entries"]
    assert [call[2] for call in runner.calls] == ["append"]


def test_the_resync_hook_is_gone():
    """Deleted with the migration window, not left callable."""
    for name in ("follow", "follow_records", "stamp", "seeded"):
        assert not hasattr(board_put, name), name


def test_a_lost_compare_and_swap_is_passed_through(monkeypatch, board_file):
    monkeypatch.setattr(board_put.subprocess, "run",
                        _run(3, stdout="", stderr="conflict\n"))
    assert board_put.main([BOARD, board_file]) == 3


def test_the_ticket_push_is_gone():
    """The mirror is deleted; nothing may push a board into it."""
    assert not hasattr(board_put, "push")
    assert not hasattr(board_put.ticket_docs, "push_markdown")


def test_my_own_board_is_put_and_never_read_back(monkeypatch, board_file):
    runner = _run(0, stdout="written: issues.md\n")
    monkeypatch.setattr(board_put.subprocess, "run", runner)

    def no_read(path):
        raise AssertionError("read back a board with no records")

    monkeypatch.setattr(board_put, "vault_get", no_read)
    assert board_put.main([MINE, board_file, "--append", "## Entries"]) == 0
    assert board_put.main([MINE, board_file]) == 0
    assert [call[2] for call in runner.calls] == ["append", "put"]
