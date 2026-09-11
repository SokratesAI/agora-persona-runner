"""`tools.board_put` -- the vault write leads and the #203 records follow.

**The records are never written when the vault write did not land.** A
lost compare-and-swap is a normal outcome with three cycles overlapping,
and records resynced anyway would be ahead of the markdown that is the
source of truth.

**A failed resync does not fail the board edit, and does not read as
success either.** Exit 4 says the board landed and the records did not.

**A path that is not a board is refused rather than quietly put.**

**An `--append` resyncs from the whole board, not the fragment it sent.**
Step 6's capture note is three lines; the records hold boards. That read
comes back through `print`, so it is also the one path here that has to
subtract runner#673's newline.

A board with no records skips rather than fails, and so does one nobody
has seeded -- but a store that could not be *read* fails, because "never
seeded" and "CouchDB would not answer" mean opposite things. The
`nova_tickets` push that used to run before the resync is deleted with the
mirror (Cycle 1380).
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



RESYNC = {"written": 202, "deleted": 0, "captures_kept": 2,
          "captures_minted": 0, "layout_stored": 201}

# Bound at import, before the autouse fixture below replaces either name.
# Reading them off the module inside a test would hand back the stand-in,
# which is a test that stubs the thing it is testing and passes anyway.
REAL_FOLLOW_RECORDS = board_put.follow_records
REAL_SEEDED = board_put.seeded


@pytest.fixture(autouse=True)
def no_record_store(monkeypatch):
    """Keep every test in this file off CouchDB.

    `main` resyncs the #203 records after the vault write, and the
    board these tests use is one of the two that has them -- so without
    this each of them would open a real store. The default is the ordinary
    case: a seeded board whose resync lands.
    """
    monkeypatch.setattr(board_put, "seeded", lambda **kw: True)
    monkeypatch.setattr(board_put, "follow_records",
                        lambda board, source, **kw: (True, "resynced"))


def _resynced(monkeypatch, result):
    """Record every `resync` call; `result` is returned or raised."""
    seen = []

    def resync(markdown, board, apply=False, store=None):
        seen.append((markdown, board, apply))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(board_put.board_migrate, "resync", resync)
    return seen








def test_the_rev_file_reaches_the_vault_client(monkeypatch, board_file):
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    assert board_put.main([BOARD, board_file, "--if-rev-file", "/tmp/x.rev"]) == 0
    assert runner.calls[0][-2:] == ["--if-rev-file", "/tmp/x.rev"]
    # And the put is a put of this file at this path, not of something else.
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


MINE = "projects/sokrates/projects/agora/nova/resources/issues.md"






def test_append_with_a_rev_file_is_refused(monkeypatch, board_file):
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    assert board_put.main(
        [MINE, board_file, "--append", "## Entries", "--if-rev-file", "/tmp/x"]) == 1
    assert runner.calls == []


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








def test_a_landed_write_resyncs_the_records_from_the_same_markdown(
        monkeypatch, board_file):
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    seen = _resynced(monkeypatch, RESYNC)
    sent = open(board_file, encoding="utf-8").read()
    assert board_put.main([BOARD, board_file]) == 0
    assert seen == [(sent, "idea", True)]


def test_a_lost_compare_and_swap_leaves_the_records_alone(
        monkeypatch, board_file):
    monkeypatch.setattr(board_put.subprocess, "run",
                        _run(3, stdout="", stderr="conflict\n"))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    seen = _resynced(monkeypatch, RESYNC)
    assert board_put.main([BOARD, board_file]) == 3
    assert seen == []


def test_a_failed_resync_is_exit_4_not_success(monkeypatch, board_file):
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    _resynced(monkeypatch, RuntimeError("writing board:idea:41: 503"))
    assert board_put.main([BOARD, board_file]) == 4


def test_a_board_with_no_records_skips_the_resync_and_still_succeeds(
        monkeypatch, tmp_path):
    mine = "projects/sokrates/projects/agora/nova/resources/ideas.md"
    local = tmp_path / "mine.md"
    local.write_text("## Entries\n\n- one\n", encoding="utf-8")
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    seen = _resynced(monkeypatch, RESYNC)
    assert board_put.record_board(mine) is None
    assert board_put.main([mine, str(local)]) == 0
    assert seen == []


def test_a_board_nobody_has_seeded_skips_the_resync(monkeypatch, board_file):
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "seeded", lambda **kw: False)
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    seen = _resynced(monkeypatch, RESYNC)
    assert board_put.main([BOARD, board_file]) == 0
    assert seen == []


def test_a_store_that_cannot_be_read_is_exit_4_not_a_skip(
        monkeypatch, board_file):
    """An unreachable store and an unseeded one mean opposite things."""
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "seeded", lambda **kw: None)
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    seen = _resynced(monkeypatch, RESYNC)
    assert board_put.main([BOARD, board_file]) == 4
    assert seen == []




def test_a_store_error_reading_the_registry_is_not_a_seeded_board():
    class Boom:
        @staticmethod
        def read_registry():
            raise RuntimeError("503")

    assert REAL_SEEDED(store=Boom) is None


def test_an_unmigrated_registry_reads_as_never_seeded():
    class Empty:
        @staticmethod
        def read_registry():
            return {"projects": {}, "milestones": {}}

    assert REAL_SEEDED(store=Empty) is False


def test_the_path_is_matched_case_insensitively():
    assert board_put.record_board(BOARD.upper()) == "idea"
    assert board_put.record_board(None) is None


def _stamped(monkeypatch, answer=None):
    """Record every `board_records.stamp_source_rev` the run makes."""
    seen = []

    def fake(board, source_rev, store=None):
        seen.append((board, source_rev))
        if isinstance(answer, Exception):
            raise answer
        return {"sourceRev": source_rev}

    monkeypatch.setattr(board_put.board_records, "stamp_source_rev", fake)
    return seen


def test_a_landed_resync_stamps_the_revision_the_records_were_built_from(
        monkeypatch, board_file):
    """The whole point of the stamp: after the switchover `nova_site` has
    to ask whether the records are still current without re-reading his
    700KB `issues.md` to compare against."""
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    _resynced(monkeypatch, RESYNC)
    seen = _stamped(monkeypatch)

    assert board_put.main([BOARD, board_file]) == 0
    assert seen == [("idea", "7-abc")]




def test_a_resync_that_failed_does_not_stamp(monkeypatch, board_file):
    """The stamp is a claim about records that are already stored. One
    written after a failed write would certify a store that is behind."""
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    _resynced(monkeypatch, RuntimeError("writing board:idea:41: 503"))
    seen = _stamped(monkeypatch)

    assert board_put.main([BOARD, board_file]) == 4
    assert seen == []


def test_a_vault_that_moved_between_the_write_and_the_read_back_is_not_stamped(
        monkeypatch, board_file):
    """`main` clears the revision when somebody wrote in between, because
    it belongs to text the store is not about to hold. Stamping it would
    claim a currency the records cannot prove."""
    monkeypatch.setattr(board_put.subprocess, "run",
                        _run(0, read_back="somebody else's board\n"))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    _resynced(monkeypatch, RESYNC)
    seen = _stamped(monkeypatch)

    assert board_put.main([BOARD, board_file]) == 0
    assert seen == []


def test_a_stamp_that_failed_is_reported_and_is_not_exit_4(
        monkeypatch, board_file, capsys):
    """An unstamped board is one `currency` cannot speak for -- a weaker
    instrument, not a store that is behind. The records landed, so the
    write succeeded and the exit code has to say so."""
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    _resynced(monkeypatch, RESYNC)
    _stamped(monkeypatch, RuntimeError("writing board:source:idea: 503"))

    assert board_put.main([BOARD, board_file]) == 0
    assert "not stamped" in capsys.readouterr().err


def test_a_board_with_no_records_stamps_nothing(monkeypatch, tmp_path):
    mine = "projects/sokrates/projects/agora/nova/resources/ideas.md"
    local = tmp_path / "mine.md"
    local.write_text("## Entries\n\n- one\n", encoding="utf-8")
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    _resynced(monkeypatch, RESYNC)
    seen = _stamped(monkeypatch)

    assert board_put.main([mine, str(local)]) == 0
    assert seen == []


def test_the_ticket_push_is_gone():
    """The mirror is deleted; nothing may push a board into it."""
    assert not hasattr(board_put, "push")
    assert not hasattr(board_put.ticket_docs, "push_markdown")


def test_append_resyncs_from_the_whole_board_not_the_fragment(
        monkeypatch, board_file):
    """Step 6's capture note is a fragment; the records hold whole boards."""
    runner = _run(0)
    monkeypatch.setattr(board_put.subprocess, "run", runner)
    monkeypatch.setattr(board_put, "vault_get",
                        lambda path: ("# Ideas\n\n- a note\n", "9-def"))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    seen = _resynced(monkeypatch, RESYNC)
    stamped = _stamped(monkeypatch)
    assert board_put.main([BOARD, board_file, "--append", "## Entries"]) == 0
    assert seen == [("# Ideas\n\n- a note\n", "idea", True)]
    # For an append the read-back *is* the text stored, so its rev stamps.
    assert stamped == [("idea", "9-def")]
    # An append, with the marker passed through -- not a put.
    assert runner.calls[0][2:6] == ["append", BOARD, board_file, "## Entries"]


def test_append_that_cannot_be_read_back_is_exit_4(monkeypatch, board_file):
    monkeypatch.setattr(board_put.subprocess, "run", _run(0))
    monkeypatch.setattr(board_put, "vault_get", lambda path: (None, None))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    seen = _resynced(monkeypatch, RESYNC)
    assert board_put.main([BOARD, board_file, "--append", "## Entries"]) == 4
    assert seen == []


def test_my_own_board_is_put_and_never_read_back(monkeypatch, board_file):
    """No records behind it, so the read-back would be spent on nothing --
    and a failed one used to turn a landed append into exit 4."""
    runner = _run(0, stdout="written: issues.md\n")
    monkeypatch.setattr(board_put.subprocess, "run", runner)

    def no_read(path):
        raise AssertionError("read back a board with no records")

    monkeypatch.setattr(board_put, "vault_get", no_read)
    assert board_put.main([MINE, board_file, "--append", "## Entries"]) == 0
    assert board_put.main([MINE, board_file]) == 0
    assert [call[2] for call in runner.calls] == ["append", "put"]


def test_a_bridge_that_reports_no_revision_stamps_nothing(monkeypatch, board_file):
    """`[absent]` is what the rev file carries for a path with no document.

    It is not a revision, and an older bridge writes nothing at all.
    Passing either through as a string would stamp the records with a
    revision the vault will never return.
    """
    monkeypatch.setattr(board_put.subprocess, "run", _run(0, rev="[absent]"))
    monkeypatch.setattr(board_put, "follow_records", REAL_FOLLOW_RECORDS)
    seen = _resynced(monkeypatch, RESYNC)
    stamped = _stamped(monkeypatch)
    assert board_put.main([BOARD, board_file]) == 0
    assert len(seen) == 1
    assert stamped == []
