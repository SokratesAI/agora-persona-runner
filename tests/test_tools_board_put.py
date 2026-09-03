"""`tools.board_put` -- the vault write leads and the ticket store follows.

Four properties, and each one is a way this could have made the drift it
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

**An `--append` pushes the whole board, not the fragment it sent.** Step
6's capture note is three lines; the store holds boards. That read comes
back through `print`, so it is also the one path here that has to subtract
runner#673's newline.
"""

import subprocess

import pytest

from tools import board_put


BOARD = "projects/sokrates/projects/nova/ideas.md"
NOT_A_BOARD = "projects/sokrates/projects/agora/journal-digest.md"


def _run(returncode, stdout="written: ideas.md\n", stderr="",
         read_back=None, rev="7-abc"):
    """A stand-in for `subprocess.run` that records what it was asked.

    It has to tell a `get` from a `put`: since the revision stamp, every
    landed write is followed by a read-back, and a stand-in that answered
    `written: ideas.md` to both would make the guard below fire on every
    test rather than on the case it is for. `read_back` is what the vault
    holds afterwards; the default is the board file the write sent, which
    is the ordinary case.
    """
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if "get" in command:
            if "--rev-file" in command:
                rev_file = command[command.index("--rev-file") + 1]
                open(rev_file, "w", encoding="utf-8").write(rev or "")
            body = read_back
            if body is None:
                # The write that came first sent this file; the ordinary
                # case is that the vault now holds exactly it.
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


def _pushed(monkeypatch, result):
    """Record every `push_markdown` call; `result` is returned or raised."""
    seen = []

    def push_markdown(path, source, source_rev=None):
        seen.append((path, source, source_rev))
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
    assert seen == [(BOARD, open(board_file, encoding="utf-8").read(), "7-abc")]


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


MINE = "projects/sokrates/projects/agora/nova/resources/issues.md"


def test_append_pushes_the_whole_board_not_the_fragment(monkeypatch, board_file):
    """Step 6's capture note is a fragment; the store holds whole boards."""
    runner = _run(0, stdout="written: issues.md\n")
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    monkeypatch.setattr(board_put, "vault_get",
                        lambda path: ("# Issues\n\n- a note\n", "9-def"))
    seen = _pushed(monkeypatch, SUMMARY)
    assert board_put.main([MINE, board_file, "--append", "## Entries"]) == 0
    # The rev is the one the read-back was served at -- for an append that
    # read *is* the text being stored, so it always matches.
    assert seen == [(MINE, "# Issues\n\n- a note\n", "9-def")]
    # An append, with the marker passed through -- not a put.
    assert runner.calls[0][2:6] == ["append", MINE, board_file, "## Entries"]


def test_append_that_cannot_be_read_back_is_exit_4(monkeypatch, board_file):
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "vault_get", lambda path: (None, None))
    seen = _pushed(monkeypatch, SUMMARY)
    assert board_put.main([MINE, board_file, "--append", "## Entries"]) == 4
    assert seen == []


def test_append_with_a_rev_file_is_refused(monkeypatch, board_file):
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    seen = _pushed(monkeypatch, SUMMARY)
    assert board_put.main(
        [MINE, board_file, "--append", "## Entries", "--if-rev-file", "/tmp/x"]) == 1
    assert runner.calls == []
    assert seen == []


def test_the_read_back_subtracts_the_newline_vault_tool_prints(monkeypatch):
    """runner#673's byte, on the one path here that goes through `print`.

    Storing it would report a byte of drift that is not drift, on that
    board, every morning forever.
    """
    monkeypatch.setattr(board_put.subprocess, "run",
                        _run(0, read_back="# Issues\n\n- a note\n"))
    assert board_put.vault_get(MINE)[0] == "# Issues\n\n- a note\n"


def test_a_board_the_vault_does_not_hold_reads_as_absent(monkeypatch):
    monkeypatch.setattr(board_put.subprocess, "run", _run(0, read_back="[not found]"))
    assert board_put.vault_get(MINE) == (None, None)


def test_the_write_stamps_the_revision_the_board_now_has(monkeypatch, board_file):
    """Without this the verdict `currency` returns is wired to a constant.

    `push_markdown` with no rev *clears* the stamp, deliberately -- an
    unknown answer must never read as a current one. Every board write a
    cycle makes goes through this tool on the bridge pod, so until now
    every one of them cleared the stamp and `currency` answered `unknown`
    for the rest of the day.
    """
    monkeypatch.setattr(board_put.subprocess, "run", _run(0, rev="12-cafe"))
    seen = _pushed(monkeypatch, SUMMARY)
    assert board_put.main([BOARD, board_file]) == 0
    assert seen[0][2] == "12-cafe"


def test_a_vault_that_moved_under_the_write_stamps_nothing(monkeypatch, board_file):
    """The revision would belong to text the store is not holding.

    A stamp is a claim that the store was built from that revision. If
    somebody wrote between the put and the read-back, it was not, and a
    false `current` is the one verdict the three-way answer exists to
    make impossible.
    """
    monkeypatch.setattr(board_put.subprocess, "run",
                        _run(0, read_back="# Ideas\n\nsomebody else\n"))
    seen = _pushed(monkeypatch, SUMMARY)
    assert board_put.main([BOARD, board_file]) == 0
    # The board still went to the store -- the markdown is what landed and
    # the store must follow it. Only the claim of currency is withheld.
    assert seen[0][1] == open(board_file, encoding="utf-8").read()
    assert seen[0][2] is None


def test_a_bridge_that_reports_no_revision_stamps_nothing(monkeypatch, board_file):
    """`[absent]` is what the rev file carries for a path with no document.

    It is not a revision, and an older bridge writes nothing at all.
    Passing either through as a string would stamp the store with a
    revision the vault will never return.
    """
    monkeypatch.setattr(board_put.subprocess, "run", _run(0, rev="[absent]"))
    seen = _pushed(monkeypatch, SUMMARY)
    assert board_put.main([BOARD, board_file]) == 0
    assert seen[0][2] is None
