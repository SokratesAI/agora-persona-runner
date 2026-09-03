"""`tools.ticket_drift` -- the render is the comparison, and it is total.

Two failures are worth pinning apart and everything here is one of them.

**A stale store must not read as current.** That is the whole point: the
markdown moved and the documents did not, and a reader switched onto the
store would serve the owner a board a day old.

**A renderer difference must not read as drift.** `ticket_store` renders
an empty table cell as `|  |` where one board's markdown has `| |`, which
was true on the day of the migration and is not staleness. If this
reported it, I would learn to ignore the check, which costs more than the
check is worth.
"""

import pytest

from agora_runner import ticket_store
from tools import ticket_drift

from tests.test_ticket_store import BOARD


PATH = "projects/sokrates/projects/nova/issues.md"
# The revision the markdown was read at. `sync` stamps it on the store so
# `ticket_docs.currency` can answer without fetching the file again.
REV = "42-92a53ba0c59e7fa01057780c6af5e45e"


def _no_stamp(monkeypatch):
    """Stub the stamp out for a test that is about something else.

    `run` stamps every board it finds current, so a test that does not
    care would otherwise reach CouchDB -- which `tests/conftest.py`
    refuses, correctly. Returning False means "the stamp already said
    this", which prints nothing.
    """
    monkeypatch.setattr(ticket_drift.ticket_docs, "stamp_source_rev",
                        lambda path, rev: False)


def _stored(monkeypatch, render):
    """Point `render_from_couch` at whatever this test wants stored."""
    monkeypatch.setattr(
        ticket_drift.ticket_docs, "render_from_couch",
        render if callable(render) else (lambda path: render))


def test_a_store_that_renders_the_same_board_is_current(monkeypatch):
    _stored(monkeypatch, ticket_store.to_markdown(ticket_store.to_records(BOARD)))
    status, detail = ticket_drift.compare(PATH, BOARD)
    assert status == 0
    assert detail.startswith("CURRENT")


def test_a_store_missing_a_later_edit_is_drift_and_names_the_line(monkeypatch):
    # The exact shape of the real drift: a note the owner's file gained
    # after the migration ran, which the documents never saw.
    moved = BOARD.replace("  - Answered, Cycle 820.\n",
                          "  - Answered, Cycle 820.\n  - And a later reply.\n")
    _stored(monkeypatch, ticket_store.to_markdown(ticket_store.to_records(BOARD)))
    status, detail = ticket_drift.compare(PATH, moved)
    assert status == 2
    assert detail.startswith("DRIFTED")
    assert "And a later reply." in detail
    assert "first differing line" in detail


def test_padding_only_rendering_is_not_reported_as_drift(monkeypatch):
    """The load-bearing one. Both sides are rendered, so this subtracts.

    The markdown here is a file the renderer does *not* reproduce byte for
    byte -- an empty cell written `| |` comes back `|  |` -- which is the
    only shape that can tell "compare the renders" apart from "compare the
    file against the render". Using a board that round-trips exactly makes
    the two indistinguishable and the test vacuous; the mutation check
    said so before this comment did.
    """
    padded = BOARD.replace("| ✅ Done | 09-01 |  |", "| ✅ Done | 09-01 | |")
    assert padded != BOARD, "the fixture must contain the cell this is about"
    assert ticket_store.to_markdown(ticket_store.to_records(padded)) != padded, (
        "this board must be one the renderer normalises, or the test proves nothing")
    _stored(monkeypatch, ticket_store.to_markdown(ticket_store.to_records(padded)))
    status, detail = ticket_drift.compare(PATH, padded)
    assert status == 0, detail


def test_a_store_that_cannot_be_read_is_one_not_two(monkeypatch):
    """Unreadable never reads as clean, and never reads as a finding either."""
    def boom(path):
        raise RuntimeError("reading the layout of x: 401")
    _stored(monkeypatch, boom)
    status, detail = ticket_drift.compare(PATH, BOARD)
    assert status == 1
    assert detail.startswith("UNREADABLE")
    assert "401" in detail


def test_a_vault_miss_is_not_a_board_that_matches(monkeypatch):
    monkeypatch.setattr(ticket_drift, "read_vault", lambda path: (None, None))
    _stored(monkeypatch, "never reached")
    out = ticket_drift.run([PATH], do_sync=False)
    assert out == 1


def test_sync_verifies_by_reading_back_not_by_the_write_answer(monkeypatch):
    """A bulk write answers `ok` per document; that is not evidence."""
    monkeypatch.setattr(ticket_drift.ticket_docs, "ensure_database", lambda: (True, 201))
    monkeypatch.setattr(
        ticket_drift.ticket_docs, "write_board",
        lambda path, records, source_rev=None: {"written": 4, "deleted": 0, "failures": []})
    # The write claimed success and the store is still wrong.
    _stored(monkeypatch, "---\ntype: board\n---\n")
    status, detail = ticket_drift.sync(PATH, BOARD)
    assert status == 2
    assert detail.startswith("DRIFTED")


def test_sync_makes_a_drifted_board_current(monkeypatch):
    monkeypatch.setattr(ticket_drift.ticket_docs, "ensure_database", lambda: (True, 201))
    _no_stamp(monkeypatch)
    written = {}
    stamped = {}

    def write_board(path, records, source_rev=None):
        written[path] = records
        stamped[path] = source_rev
        return {"written": 4, "deleted": 0, "failures": []}

    monkeypatch.setattr(ticket_drift.ticket_docs, "write_board", write_board)
    _stored(monkeypatch,
            lambda path: ticket_store.to_markdown(written[path]) if path in written
            else "---\ntype: board\n---\n")
    monkeypatch.setattr(ticket_drift, "read_vault", lambda path: (BOARD, REV))
    out = ticket_drift.run([PATH], do_sync=True)
    assert out == 0
    assert written, "sync must actually have written the board"
    # The read-time revision reaches the write. Without it `to_documents`
    # omits the source-revision document, `write_board` tombstones it, and
    # `currency` answers `unknown` for the rest of that board's life --
    # the repair un-doing the thing it repairs.
    assert stamped[PATH] == REV


def test_a_rejected_document_is_not_a_successful_sync(monkeypatch):
    monkeypatch.setattr(ticket_drift.ticket_docs, "ensure_database", lambda: (True, 201))
    monkeypatch.setattr(
        ticket_drift.ticket_docs, "write_board",
        lambda path, records: {"written": 4, "deleted": 0,
                               "failures": [{"id": "ticket:x:1", "error": "conflict"}]})
    _stored(monkeypatch, ticket_store.to_markdown(ticket_store.to_records(BOARD)))
    status, detail = ticket_drift.sync(PATH, BOARD)
    assert status == 1
    assert "rejected" in detail


def test_a_database_that_cannot_be_created_stops_the_sync(monkeypatch):
    monkeypatch.setattr(ticket_drift.ticket_docs, "ensure_database",
                        lambda: (False, "403 forbidden"))
    monkeypatch.setattr(ticket_drift.ticket_docs, "write_board",
                        lambda path, records: pytest.fail("must not write"))
    status, detail = ticket_drift.sync(PATH, BOARD)
    assert status == 1
    assert "403" in detail


def test_the_exit_code_is_the_worst_board_not_the_last(monkeypatch):
    _no_stamp(monkeypatch)
    current = ticket_store.to_markdown(ticket_store.to_records(BOARD))
    monkeypatch.setattr(ticket_drift, "read_vault", lambda path: (BOARD, REV))
    _stored(monkeypatch,
            lambda path: current if path == "second" else "---\ntype: board\n---\n")
    out = ticket_drift.run(["first", "second"], do_sync=False)
    assert out == 2


def test_the_fix_line_is_printed_only_when_something_drifted(monkeypatch, capsys):
    _no_stamp(monkeypatch)
    monkeypatch.setattr(ticket_drift, "read_vault", lambda path: (BOARD, REV))
    _stored(monkeypatch, ticket_store.to_markdown(ticket_store.to_records(BOARD)))
    assert ticket_drift.run([PATH], do_sync=False) == 0
    assert "--sync" not in capsys.readouterr().out

    _stored(monkeypatch, "---\ntype: board\n---\n")
    assert ticket_drift.run([PATH], do_sync=False) == 2
    assert "--sync" in capsys.readouterr().out


def test_first_difference_finds_a_line_only_one_side_has():
    assert ticket_drift.first_difference("a\nb", "a\nb\nc") == (3, None, "c")
    assert ticket_drift.first_difference("a\nb", "a\nb") is None


def test_every_board_the_migration_wrote_is_a_board_this_checks():
    """One roster, so a fifth board cannot be migrated and left unwatched."""
    from tools import ticket_migrate
    assert ticket_drift.BOARDS is ticket_migrate.BOARDS


def test_the_ticket_store_answers_from_either_pod(monkeypatch):
    """The bridge pod spells CouchDB `CDB_*`; a check must still answer there."""
    from agora_runner import ticket_docs
    for name in ("COUCHDB_URL", "COUCHDB_USER", "COUCHDB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CDB_BASE", "http://couch:5984")
    monkeypatch.setenv("CDB_USER", "bridge")
    monkeypatch.setenv("CDB_PASS", "secret")
    assert ticket_docs.credentials() == ("http://couch:5984", "bridge", "secret")


def test_the_runner_spelling_wins_where_both_are_set(monkeypatch):
    from agora_runner import ticket_docs
    monkeypatch.setenv("COUCHDB_URL", "http://runner:5984")
    monkeypatch.setenv("COUCHDB_USER", "runner")
    monkeypatch.setenv("COUCHDB_PASSWORD", "runner-pass")
    monkeypatch.setenv("CDB_BASE", "http://bridge:5984")
    monkeypatch.setenv("CDB_USER", "bridge")
    monkeypatch.setenv("CDB_PASS", "bridge-pass")
    assert ticket_docs.credentials() == (
        "http://runner:5984", "runner", "runner-pass")


def test_the_sweep_runs_this_check():
    """A drift check nothing runs every morning is not a check."""
    from tools import preflight
    assert "ticket_drift" in preflight.CHECKS
    assert "ticket_drift" in preflight.SUBJECT


def test_a_vault_miss_is_read_as_unreadable_not_as_an_empty_board(monkeypatch):
    """`vault_tool.py get` prints `[not found]` and exits 0 for a missing path.

    Taken at face value that is a board with no tickets, which renders to
    almost nothing and reports every ticket in the store as drift -- a
    loud, wrong finding out of a path that simply moved.
    """
    class Done:
        returncode = 0
        stdout = "[not found]\n"
        stderr = ""

    monkeypatch.setattr(ticket_drift.board_put.subprocess, "run",
                        lambda *a, **k: Done())
    assert ticket_drift.read_vault(PATH) == (None, None)


def test_a_vault_client_that_will_not_run_is_unreadable_not_a_traceback(monkeypatch):
    """The reader this delegates to does not catch a failed subprocess.

    `read_vault` used to run `vault_tool.py` itself and swallow `OSError`,
    so a missing or hung vault client came out as `CANNOT READ` and exit
    1. Raising here instead would take the whole morning sweep down with a
    traceback, and a check that did not run must never read as clean.
    """
    def boom(*args, **kwargs):
        raise OSError("no vault client on this pod")

    monkeypatch.setattr(ticket_drift.board_put.subprocess, "run", boom)
    assert ticket_drift.read_vault(PATH) == (None, None)


def _sweep(monkeypatch, *, stored_render, source, source_rev, stamp):
    """One `run` over one board. Returns `(exit code, printed lines, stamps)`.

    `stamp` is whatever `ticket_docs.stamp_source_rev` should do; the
    calls it received come back so a test can assert the sweep did not
    stamp when it must not.
    """
    calls = []

    def stamp_source_rev(path, rev):
        calls.append((path, rev))
        return stamp(path, rev) if callable(stamp) else stamp

    _stored(monkeypatch, stored_render)
    monkeypatch.setattr(ticket_drift, "read_vault",
                        lambda path: (source, source_rev))
    monkeypatch.setattr(ticket_drift.ticket_docs, "stamp_source_rev",
                        stamp_source_rev)
    printed = []
    monkeypatch.setattr("builtins.print", lambda *a: printed.append(" ".join(map(str, a))))
    code = ticket_drift.run([PATH], False)
    return code, "\n".join(printed), calls


RENDERED = ticket_store.to_markdown(ticket_store.to_records(BOARD))


def test_a_clean_board_records_the_revision_it_matched(monkeypatch):
    """The hole: `--sync` only runs on a board that drifted.

    So a board that is *correct* had no way to say which revision it was
    built from, and `ticket_docs.currency` answered `unknown` for it
    forever. Measured live 2026-09-03 on `issues.md`, which is the 537KB
    board and the only fetch worth removing.
    """
    code, out, calls = _sweep(monkeypatch, stored_render=RENDERED, source=BOARD,
                              source_rev=REV, stamp=True)
    assert code == 0
    assert calls == [(PATH, REV)]
    assert f"stamped {REV}" in out


def test_a_stamp_that_was_already_right_is_not_announced(monkeypatch):
    # This runs every cycle in `preflight`. A line per board per morning
    # saying nothing changed is how a report stops being read.
    code, out, calls = _sweep(monkeypatch, stored_render=RENDERED, source=BOARD,
                              source_rev=REV, stamp=False)
    assert code == 0
    assert calls == [(PATH, REV)]
    assert "stamped" not in out


def test_a_drifted_board_is_not_stamped(monkeypatch):
    """The one that would break everything downstream.

    A stamp says "these documents were built from that revision". On a
    board that does not match the markdown that is false, and it is the
    exact false CURRENT the whole three-way verdict exists to make
    impossible -- a reader would then skip the fetch and serve a stale
    board with nothing reporting it.
    """
    moved = BOARD.replace("  - Answered, Cycle 820.\n",
                          "  - Answered, Cycle 820.\n  - And a later reply.\n")
    code, out, calls = _sweep(monkeypatch, stored_render=RENDERED, source=moved,
                              source_rev=REV, stamp=True)
    assert code == 2
    assert calls == []
    assert "DRIFTED" in out


def test_a_stamp_that_fails_raises_the_sweep_and_says_so(monkeypatch):
    """The board is current; the instrument is not. Those are different.

    Exit 1 rather than 0 because a currency check that could not be
    written is a check that will answer `unknown` -- and this sweep is the
    only thing that would ever have noticed.
    """
    def boom(path, rev):
        raise RuntimeError("stamping x: 409 conflict")

    code, out, calls = _sweep(monkeypatch, stored_render=RENDERED, source=BOARD,
                              source_rev=REV, stamp=boom)
    assert code == 1
    assert "CANNOT STAMP" in out
    assert "409" in out
    # Still reported as current, because it is.
    assert "CURRENT" in out


def test_a_vault_read_with_no_revision_stamps_nothing_and_stays_clean(monkeypatch):
    # Nothing is wrong with the board, so this is not a finding -- but the
    # line has to say why the verdict will not move, or the next cycle
    # re-measures it from scratch the way I just did.
    code, out, calls = _sweep(monkeypatch, stored_render=RENDERED, source=BOARD,
                              source_rev=None, stamp=True)
    assert code == 0
    assert calls == []
    assert "not stamped" in out


def test_the_check_only_ever_writes_the_stamp(monkeypatch):
    """`run` without `--sync` must not rewrite a ticket. Ever.

    The tool's contract is that `--sync` is the one command that changes
    the store, and this change bends it. `stamp_source_rev` is the only
    writer it is allowed to reach, so `write_board` is fenced off here
    rather than trusted.
    """
    def forbidden(*args, **kwargs):
        raise AssertionError("the check rewrote the board")

    monkeypatch.setattr(ticket_drift.ticket_docs, "write_board", forbidden)
    code, _, _ = _sweep(monkeypatch, stored_render=RENDERED, source=BOARD,
                        source_rev=REV, stamp=True)
    assert code == 0
