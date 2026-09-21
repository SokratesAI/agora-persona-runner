"""vault_move and vault_delete: a persona can reorganise the vault, and every
file it takes away is copied into agora/backups/ first (owner capture,
2026-09-21). The fake below is a dict of live files plus their revs, and the
tombstone PUT is recorded, so each test asserts what reached the database."""
import pytest

from agora_runner import vault
from agora_runner.tools_schemas import client_tool_schemas, TOOL_TO_CAPABILITY


@pytest.fixture
def fake(monkeypatch):
    files = {"a/note.md": ["hello", "1-a"]}
    tombstoned = []
    fail_backup = {"on": False}

    def read_rev(path):
        entry = files.get(path.lower())
        return (entry[0], entry[1]) if entry else (None, None)

    def write(path, content, if_rev=vault._ANY_REV, allow_shrink=False):
        path = path.lower()
        if fail_backup["on"] and path.startswith(vault.BACKUP_ROOT):
            return "FAILED(503)"
        if if_rev is None and path in files:
            return "FAILED(409 conflict)"
        files[path] = [content, "1-new"]
        return "written"

    def get_doc(path, db=None):
        entry = files.get(path)
        return (200, {"_id": path, "_rev": entry[1]}) if entry else (404, {})

    def req(method, path, body=None, timeout=60):
        assert method == "PUT" and body["deleted"] is True
        tombstoned.append(body["_id"])
        files.pop(body["_id"])
        return 201, {"ok": True}

    monkeypatch.setattr(vault, "vault_read_path_rev", read_rev)
    monkeypatch.setattr(vault, "vault_write_path", write)
    monkeypatch.setattr(vault, "couch_get_doc", get_doc)
    monkeypatch.setattr(vault, "couch_req", req)
    monkeypatch.setattr(vault, "db_for", lambda p: "db")
    return files, tombstoned, fail_backup


def _backups(files):
    return {k: v[0] for k, v in files.items() if k.startswith(vault.BACKUP_ROOT)}


def test_delete_backs_up_then_tombstones(fake):
    files, tombstoned, _ = fake
    result = vault.vault_delete_path("A/Note.md")
    assert result.startswith("deleted a/note.md")
    assert tombstoned == ["a/note.md"]
    backups = _backups(files)
    assert list(backups.values()) == ["hello"]
    assert next(iter(backups)).endswith("/a/note.md")


def test_delete_without_a_backup_deletes_nothing(fake):
    files, tombstoned, fail_backup = fake
    fail_backup["on"] = True
    assert vault.vault_delete_path("a/note.md").startswith("FAILED(backup")
    assert tombstoned == [] and files["a/note.md"][0] == "hello"


def test_delete_missing_file_fails(fake):
    assert vault.vault_delete_path("nope.md").startswith("FAILED(not found")


def test_delete_refuses_a_file_edited_since_the_read(fake, monkeypatch):
    files, tombstoned, _ = fake
    real_write = vault.vault_write_path

    def write_and_edit(path, content, **kw):
        files["a/note.md"][1] = "2-someone-else"
        return real_write(path, content, **kw)

    monkeypatch.setattr(vault, "vault_write_path", write_and_edit)
    assert "409 conflict" in vault.vault_delete_path("a/note.md")
    assert tombstoned == []


def test_move_writes_destination_backs_up_and_removes_source(fake):
    files, tombstoned, _ = fake
    result = vault.vault_move_path("a/note.md", "b/renamed.md")
    assert result.startswith("moved a/note.md -> b/renamed.md")
    assert files["b/renamed.md"][0] == "hello"
    assert "a/note.md" not in files and tombstoned == ["a/note.md"]
    assert list(_backups(files).values()) == ["hello"]


def test_move_refuses_to_overwrite_a_live_destination(fake):
    files, tombstoned, _ = fake
    files["b/taken.md"] = ["other", "1-b"]
    assert "already exists" in vault.vault_move_path("a/note.md", "b/taken.md")
    assert files["b/taken.md"][0] == "other" and tombstoned == []


def test_move_keeps_source_when_backup_fails(fake):
    files, tombstoned, fail_backup = fake
    fail_backup["on"] = True
    result = vault.vault_move_path("a/note.md", "b/renamed.md")
    assert result.startswith("FAILED(backup") and tombstoned == []
    assert files["a/note.md"][0] == "hello"


def test_tools_are_offered_only_with_vault_write():
    names = lambda caps: {t["name"] for t in client_tool_schemas(caps)}
    assert {"vault_move", "vault_delete"} <= names({"vaultWrite": True})
    assert not {"vault_move", "vault_delete"} & names({"vaultRead": True})
    assert TOOL_TO_CAPABILITY["vault_move"] == TOOL_TO_CAPABILITY["vault_delete"] == "vaultWrite"
