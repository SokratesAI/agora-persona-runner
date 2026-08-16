"""Tests for `tools.gc_orphan_chunks` (idea #61, Cycle 232).

The failure these guard against is not "the tool crashes". It is the tool
deleting a chunk some file doc still points at, which is silent on write
and only shows up later as an unreadable note. So the fake below stores
real documents and answers `_all_docs` from them, rather than returning a
canned orphan list -- a fake that just hands back ids would keep passing
if the reference walk stopped looking at `children` entirely.
"""
import json

import pytest

from tools import gc_orphan_chunks


class FakeCouch:
    """Enough CouchDB to answer the three calls the tool makes."""

    def __init__(self, docs):
        self.docs = {d["_id"]: dict(d) for d in docs}
        self.bulk_failures = set()

    def req(self, method, path, body=None, timeout=60):
        db, _, rest = path.partition("/")
        if method == "GET" and rest.startswith("_all_docs"):
            return 200, {
                "rows": [
                    {"id": i, "doc": dict(d)} for i, d in sorted(self.docs.items())
                ]
            }
        if method == "POST" and rest == "_all_docs":
            rows = []
            for key in body["keys"]:
                if key in self.docs:
                    rows.append({"id": key, "value": {"rev": self.docs[key]["_rev"]}})
                else:
                    rows.append({"key": key, "error": "not_found"})
            return 200, {"rows": rows}
        if method == "POST" and rest == "_bulk_docs":
            out = []
            for doc in body["docs"]:
                doc_id = doc["_id"]
                if doc_id in self.bulk_failures:
                    out.append({"id": doc_id, "error": "conflict"})
                    continue
                if doc.get("_deleted"):
                    self.docs.pop(doc_id, None)
                else:
                    self.docs[doc_id] = dict(doc, _rev="1-restored")
                out.append({"id": doc_id, "ok": True})
            return 200, out
        raise AssertionError(f"unexpected {method} {path}")


def leaf(doc_id, data):
    return {"_id": doc_id, "_rev": "1-a", "type": "leaf", "data": data, "children": []}


def note(path, children):
    return {
        "_id": path, "_rev": "3-a", "path": path, "type": "plain",
        "data": "", "children": children, "size": 1, "eden": {},
    }


@pytest.fixture
def couch(monkeypatch):
    docs = [
        note("notes/kept.md", ["chunk-live-1", "chunk-live-2"]),
        note("notes/other.md", ["chunk-live-2"]),          # shared chunk
        leaf("chunk-live-1", "alpha"),
        leaf("chunk-live-2", "beta"),
        leaf("chunk-dead-1", "superseded text"),
        leaf("chunk-dead-2", "also superseded"),
    ]
    fake = FakeCouch(docs)
    monkeypatch.setattr(gc_orphan_chunks, "couch_req", fake.req)
    return fake


def test_only_unreferenced_chunks_are_orphans(couch):
    files, chunks, orphans = gc_orphan_chunks.scan("nova")
    assert orphans == ["chunk-dead-1", "chunk-dead-2"]
    assert len(files) == 2 and len(chunks) == 4


def test_a_chunk_shared_by_two_notes_is_not_an_orphan(couch):
    # Content-addressed storage means two notes can legitimately point at
    # one chunk. Deleting it because the first note "already covered it"
    # would break the second, silently.
    _, _, orphans = gc_orphan_chunks.scan("nova")
    assert "chunk-live-2" not in orphans


def test_refuses_when_a_file_doc_has_inline_eden(couch):
    # The whole delete rests on `children` being the only reference path.
    couch.docs["notes/kept.md"]["eden"] = {"chunk-dead-1": "alpha"}
    with pytest.raises(SystemExit) as excinfo:
        gc_orphan_chunks.scan("nova")
    assert "eden" in str(excinfo.value)


def test_apply_deletes_orphans_and_leaves_live_chunks(couch, tmp_path):
    backup = tmp_path / "pin.json"
    rc = gc_orphan_chunks.main(["--db", "nova", "--apply", "--backup", str(backup)])
    assert rc == 0
    assert set(couch.docs) == {
        "notes/kept.md", "notes/other.md", "chunk-live-1", "chunk-live-2",
    }


def test_apply_refuses_without_a_backup(couch):
    assert gc_orphan_chunks.main(["--db", "nova", "--apply"]) == 2
    assert "chunk-dead-1" in couch.docs


def test_dry_run_deletes_nothing(couch):
    assert gc_orphan_chunks.main(["--db", "nova"]) == 0
    assert "chunk-dead-1" in couch.docs


def test_verify_fails_loudly_when_a_live_chunk_went_missing(couch, tmp_path):
    # The check has to be able to fail, or the "verified" line at the end
    # of a real run means nothing. Drop a referenced chunk behind the
    # tool's back and the run must report it and point at the restore.
    real_delete = gc_orphan_chunks.delete

    def delete_too_much(db, chunks, orphan_ids):
        couch.docs.pop("chunk-live-1")
        return real_delete(db, chunks, orphan_ids)

    gc_orphan_chunks.delete = delete_too_much
    try:
        rc = gc_orphan_chunks.main(
            ["--db", "nova", "--apply", "--backup", str(tmp_path / "pin.json")]
        )
    finally:
        gc_orphan_chunks.delete = real_delete
    assert rc == 1


def test_backup_restores_every_deleted_chunk(couch, tmp_path):
    backup = tmp_path / "pin.json"
    gc_orphan_chunks.main(["--db", "nova", "--apply", "--backup", str(backup)])
    assert "chunk-dead-1" not in couch.docs

    assert gc_orphan_chunks.main(["--restore", str(backup)]) == 0
    assert couch.docs["chunk-dead-1"]["data"] == "superseded text"


def test_backup_file_records_its_own_restore_command(tmp_path, couch):
    backup = tmp_path / "pin.json"
    gc_orphan_chunks.main(["--db", "nova", "--apply", "--backup", str(backup)])
    payload = json.loads(backup.read_text())
    assert payload["database"] == "nova"
    assert str(backup) in payload["restore"]
    # `_rev` must not be carried into the backup: restoring one would be a
    # PUT against a revision the database no longer has.
    assert all("_rev" not in d for d in payload["docs"])
