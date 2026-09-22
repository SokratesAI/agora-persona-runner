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


def test_live_database_needs_the_explicit_flag(tmp_path):
    root = _tree(tmp_path)
    with pytest.raises(SystemExit) as exc:
        vault_restore.main(["--backup", str(root), "--db", "obsidian"])
    assert "live database" in str(exc.value)


def test_a_target_is_required(tmp_path):
    root = _tree(tmp_path)
    with pytest.raises(SystemExit) as exc:
        vault_restore.main(["--backup", str(root)])
    assert "--drill" in str(exc.value)
