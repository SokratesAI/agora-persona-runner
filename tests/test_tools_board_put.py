"""`tools.board_put` -- the vault write leads and the ticket store follows.

Three properties, and each one is a way this could have made the drift it
exists to close worse.

**The store is never written when the vault write did not land.** A lost
compare-and-swap is a normal outcome with three cycles overlapping, and a
store pushed anyway would be ahead of the markdown that is the source of
truth.

**A failed push does not fail the board edit, and does not read as
success either.** Exit 4 says the board landed and the store did not.

**A path that is not a board is refused rather than quietly put.** A
command that silently did nothing for the ticket store would teach a
cycle that every vault write goes through here.
"""

import subprocess

import pytest

from tools import board_put


BOARD = "projects/sokrates/projects/nova/ideas.md"
NOT_A_BOARD = "projects/sokrates/projects/agora/journal-digest.md"


def _run(returncode, stdout="written: ideas.md\n", stderr=""):
    """A stand-in for `subprocess.run` that records what it was asked."""
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    runner.calls = calls
    return runner


@pytest.fixture
def board_file(tmp_path):
    path = tmp_path / "ideas.md"
    path.write_text("# Ideas\n\n| # | Title |\n", encoding="utf-8")
    return str(path)


def _pushed(monkeypatch, result):
    """Record every `push_markdown` call; `result` is returned or raised."""
    seen = []

    def push_markdown(path, source):
        seen.append((path, source))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(board_put.ticket_docs, "push_markdown", push_markdown)
    return seen


SUMMARY = {"written": 2, "deleted": 0, "unchanged": 239, "failures": []}


def test_a_landed_write_pushes_the_file_that_was_sent(monkeypatch, board_file):
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    seen = _pushed(monkeypatch, SUMMARY)
    assert board_put.main([BOARD, board_file]) == 0
    # The bytes the vault write sent, not a re-read of the vault -- there
    # is no `print` in this path, so there is no newline to subtract.
    assert seen == [(BOARD, open(board_file, encoding="utf-8").read())]


def test_a_lost_compare_and_swap_leaves_the_store_alone(monkeypatch, board_file):
    monkeypatch.setattr(board_put.subprocess, "run", _run(3, stdout="", stderr="conflict\n"))
    seen = _pushed(monkeypatch, SUMMARY)
    # The vault's own exit code is passed through: a caller that retries on
    # 3 must still see 3.
    assert board_put.main([BOARD, board_file]) == 3
    assert seen == []


def test_a_failed_push_is_exit_4_not_success(monkeypatch, board_file):
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    _pushed(monkeypatch, RuntimeError("writing ideas.md: 503"))
    assert board_put.main([BOARD, board_file]) == 4


def test_the_rev_file_reaches_the_vault_client(monkeypatch, board_file):
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    _pushed(monkeypatch, SUMMARY)
    assert board_put.main([BOARD, board_file, "--if-rev-file", "/tmp/x.rev"]) == 0
    assert runner.calls[0][-2:] == ["--if-rev-file", "/tmp/x.rev"]
    # And the put is a put of this file at this path, not of something else.
    assert runner.calls[0][2:5] == ["put", BOARD, board_file]


def test_a_path_that_is_not_a_board_is_refused_before_anything_is_written(
        monkeypatch, board_file):
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    seen = _pushed(monkeypatch, SUMMARY)
    assert board_put.main([NOT_A_BOARD, board_file]) == 1
    assert runner.calls == []
    assert seen == []


def test_every_board_is_accepted(monkeypatch, board_file):
    """The four paths are `ticket_docs.BOARDS`, not a second list here."""
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    _pushed(monkeypatch, SUMMARY)
    for board in board_put.ticket_docs.BOARDS:
        assert board_put.main([board, board_file]) == 0
