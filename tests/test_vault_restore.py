"""What the restore tool must not do, and what it must not skip.

The drill against a scratch database is the real proof and it needs
CouchDB credentials, so it cannot run here. What can run here are the two
pure decisions the tool makes before any byte moves: which paths in the
backup tree are vault files, and whether it is allowed to write to the
database it was pointed at. Both are the kind of mistake that is only
visible after it has happened.
"""
import json

import pytest

from tools import vault_restore


def _tree(tmp_path):
    (tmp_path / "projects" / "sokrates").mkdir(parents=True)
    (tmp_path / "projects" / "sokrates" / "a.md").write_text("hello")
    (tmp_path / "nova_tickets-records").mkdir()
    (tmp_path / "nova_tickets-records" / "r1.json").write_text(
        json.dumps({"_id": "r1", "title": "a row"})
    )
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]")
    return tmp_path


def test_backup_files_skips_records_and_git(tmp_path):
    root = _tree(tmp_path)
    paths = [p for p, _ in vault_restore.backup_files(root)]
    assert paths == ["projects/sokrates/a.md"]


def test_backup_records_reads_the_record_folder(tmp_path):
    root = _tree(tmp_path)
    assert [p.name for p in vault_restore.backup_records(root, "nova_tickets")] == ["r1.json"]
    assert vault_restore.backup_records(root, "no_such_db") == []


@pytest.mark.parametrize(
    "db",
    [
        "obsidian",             # a live database the constant happens to name
        "_users",               # CouchDB's own credential store, which it never named
        "_replicator",
        "obsidian_restored",    # a name nobody has thought of yet
    ],
)
def test_any_database_but_the_drill_needs_the_explicit_flag(db):
    """The guard is an allowlist of one, not a denylist of seven.

    It was the other way round when this tool was opened. `_users` and
    `obsidian_restored` both pass a denylist and both must be refused here;
    if either stops failing, the guard has gone back to enumerating live
    databases, and the one that holds every credential in the estate is
    unguarded again.
    """
    with pytest.raises(SystemExit) as exc:
        vault_restore.check_target(db, False)
    assert "--i-mean-it" in str(exc.value)
    assert db in str(exc.value)


@pytest.mark.parametrize(
    "db,i_mean_it",
    [
        (vault_restore.DRILL_DB, False),   # the scratch database, always allowed
        ("obsidian", True),                # a live one, but asked for on purpose
        (None, False),                     # a drill run, which names no --db at all
    ],
)
def test_what_the_guard_lets_through(db, i_mean_it):
    """The positive control. Without it, a guard that refuses everything passes."""
    assert vault_restore.check_target(db, i_mean_it) is None


def test_a_target_is_required(tmp_path):
    root = _tree(tmp_path)
    with pytest.raises(SystemExit) as exc:
        vault_restore.main(["--backup", str(root)])
    assert "--drill" in str(exc.value)
