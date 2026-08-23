"""Delete chunk documents that no file document references.

Idea #61, Cycle 232. The roadmap and `research/vault-storage-format.md`
both said the cause was Cycle 139's write-order bug in `_vault_put_raw`
-- chunks are PUT before the file doc, so a write losing on `if_rev` has
already stored a full orphan set. That bug is real and it is not what is
filling the database. Measured on the live `nova` database:

    file docs 311, chunk docs 3309, orphans 2734
    32.9 MB orphaned of 35.8 MB of chunk bytes
    1393 file revisions over 311 files = 1082 overwrites
    2734 / 1082 = 2.53 orphans per overwrite

Every overwrite of a multi-chunk file supersedes the chunks whose text
changed, and **nothing has ever deleted one** (`doc_del_count` was 0).
That arithmetic accounts for the orphans on its own and leaves no room
for a failure-path explanation. Cycle 227's "25 files written exactly
once, so supersession cannot explain it" does not follow: a chunk is
content-addressed and carries no back-pointer, so no orphan can be
attributed to any file, and a file sitting at revision 1 has its own
chunks referenced by definition.

So this is not a bug fix. It is the garbage collection a content-addressed
store needs and never had, and it has to be run again, not once.

Two facts make the delete safe, both measured rather than reasoned:

* `children` is the only reference path. All 311 file docs carry an
  empty `eden`, so no chunk is referenced inline, and the key census
  turns up no other field that could hold one.
* A deleted chunk id can be re-created by a later write with no `_rev`
  and CouchDB returns 201. This is the hazard worth naming: chunk ids are
  content hashes, so the *same* text will be written again, and had a
  recreate 409'd instead, `_vault_put_raw` would return
  `FAILED(chunk ...)` and that content could never be stored again.

Old revisions of file docs do point at orphans, and after this they point
at nothing. Nothing reads them -- `vault_read_path` reads the current
revision and `vault_git_revision_history` goes to GitHub, not CouchDB.

Dry run by default. `--apply` writes the backup first and refuses to
delete without one.

    python3 -m tools.gc_orphan_chunks                       # count only
    python3 -m tools.gc_orphan_chunks --apply --backup P    # delete

Restore is the backup file fed back through `_bulk_docs`; `--restore P`
does it, and the file records the command in its own header.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.vault import couch_req  # noqa: E402

# CouchDB accepts far larger, but a batch that fails is a batch you have to
# reason about, and 200 keeps the failure small enough to read.
BATCH = 200


def scan(db):
    """Return (file_docs, chunk_docs, orphan_ids).

    One `_all_docs` pass rather than a view: this runs rarely, and a view
    would be a design document to keep in step with the chunk shape.
    """
    status, body = couch_req("GET", f"{db}/_all_docs?include_docs=true&limit=1000000")
    if status != 200:
        raise SystemExit(f"could not read {db}: {status}")
    files, chunks = {}, {}
    for row in body.get("rows", []):
        doc = row.get("doc") or {}
        doc_id = doc.get("_id", "")
        if doc_id.startswith("_design"):
            continue
        if doc.get("type") == "leaf":
            chunks[doc_id] = doc
        elif "children" in doc:
            files[doc_id] = doc

    inline = [i for i, f in files.items() if f.get("eden")]
    if inline:
        # The whole delete rests on `children` being the only reference
        # path. If that stops being true the orphan set is wrong in the
        # one direction that destroys data, so stop rather than guess.
        raise SystemExit(
            f"refusing: {len(inline)} file doc(s) carry a non-empty `eden`, "
            f"which may reference chunks outside `children`: {inline[:5]}"
        )

    referenced = set()
    for f in files.values():
        referenced.update(f.get("children") or [])
    return files, chunks, sorted(set(chunks) - referenced)


def backup(path, db, chunks, orphan_ids):
    payload = {
        "database": db,
        "note": "orphan chunk documents removed by tools.gc_orphan_chunks",
        "restore": f"python3 -m tools.gc_orphan_chunks --restore {path}",
        "docs": [
            {k: v for k, v in chunks[i].items() if k != "_rev"} for i in orphan_ids
        ],
    }
    with open(path, "w") as fh:
        json.dump(payload, fh)
    return len(payload["docs"])


def delete(db, chunks, orphan_ids):
    deleted, failed = 0, []
    for start in range(0, len(orphan_ids), BATCH):
        batch = orphan_ids[start:start + BATCH]
        docs = [
            {"_id": i, "_rev": chunks[i]["_rev"], "_deleted": True} for i in batch
        ]
        status, body = couch_req("POST", f"{db}/_bulk_docs", {"docs": docs})
        if status not in (200, 201):
            failed.extend(batch)
            continue
        for row in body if isinstance(body, list) else []:
            if row.get("ok"):
                deleted += 1
            else:
                failed.append(row.get("id"))
    return deleted, failed


def verify(db, files):
    """Every file doc's children must still resolve after the delete.

    This is the check that would actually fail if the orphan set were
    wrong, which is why it re-reads rather than trusting the scan.
    """
    missing = {}
    wanted = set()
    for f in files.values():
        wanted.update(f.get("children") or [])
    present = set()
    wanted = sorted(wanted)
    for start in range(0, len(wanted), BATCH):
        batch = wanted[start:start + BATCH]
        status, body = couch_req(
            "POST", f"{db}/_all_docs", {"keys": batch}
        )
        if status != 200:
            raise SystemExit(f"verify could not read {db}: {status}")
        for row in body.get("rows", []):
            if row.get("id") and not (row.get("value") or {}).get("deleted"):
                present.add(row["id"])
    for path, f in files.items():
        gone = [c for c in (f.get("children") or []) if c not in present]
        if gone:
            missing[path] = gone
    return missing


def restore(path):
    with open(path) as fh:
        payload = json.load(fh)
    db = payload["database"]
    docs = payload["docs"]
    restored = 0
    for start in range(0, len(docs), BATCH):
        status, body = couch_req(
            "POST", f"{db}/_bulk_docs", {"docs": docs[start:start + BATCH]}
        )
        if status in (200, 201):
            restored += sum(1 for r in (body if isinstance(body, list) else []) if r.get("ok"))
    print(f"restored {restored} of {len(docs)} chunk docs into {db}")
    return 0 if restored == len(docs) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=os.environ.get("CDB_NOVA_DB", "nova"))
    ap.add_argument("--apply", action="store_true", help="actually delete")
    ap.add_argument("--backup", help="where to write the restore file")
    ap.add_argument("--restore", help="put a backup file's docs back")
    args = ap.parse_args(argv)

    if args.restore:
        return restore(args.restore)

    files, chunks, orphan_ids = scan(args.db)
    orphan_bytes = sum(len((chunks[i].get("data") or "").encode()) for i in orphan_ids)
    total_bytes = sum(len((c.get("data") or "").encode()) for c in chunks.values())
    print(f"{args.db}: {len(files)} file docs, {len(chunks)} chunk docs")
    print(
        f"orphans: {len(orphan_ids)} chunks, "
        f"{orphan_bytes / 1e6:.1f} MB of {total_bytes / 1e6:.1f} MB"
    )
    if not args.apply:
        print("dry run -- pass --apply --backup <path> to delete")
        return 0
    if not args.backup:
        print("refusing to delete without --backup", file=sys.stderr)
        return 2

    saved = backup(args.backup, args.db, chunks, orphan_ids)
    print(f"backed up {saved} chunk docs to {args.backup}")
    deleted, failed = delete(args.db, chunks, orphan_ids)
    print(f"deleted {deleted}, failed {len(failed)}")
    missing = verify(args.db, files)
    if missing:
        print(f"BROKEN: {len(missing)} file doc(s) lost a chunk", file=sys.stderr)
        for path, gone in list(missing.items())[:5]:
            print(f"  {path}: {gone}", file=sys.stderr)
        print(f"restore with --restore {args.backup}", file=sys.stderr)
        return 1
    print(f"verified: all {len(files)} file docs still resolve every chunk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
