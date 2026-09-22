"""Put a vault backup back into CouchDB, and prove it round-trips.

The hourly `vault-backup` CronJob writes every LiveSync file doc out as a
plain file and every record doc as one JSON file under `<db>-records/`.
Nothing in this estate ever read that tree back. Before the 2026-09-24
pause, section 1 of `after-hibernation.md` said the honest version of
that out loud -- the copies exist, nobody has practised a restore, so
nobody knows whether they restore or how long it takes. This is the
answer to both halves.

There is no new wire format here, and that is the point: a LiveSync file
is written by `bridge/vault_tool.py`'s own `put`, the same call every
cycle already makes thousands of times, and a record is a `_bulk_docs`
POST of the JSON the backup wrote. So the restore path was never missing
-- it was untested, unnamed and undocumented, which in an outage is the
same as missing.

Drill it against a scratch database, never a live one::

    python3 -m tools.vault_restore --drill --sample 40

`--drill` creates `nova_restore_drill`, restores a sample of files and
records into it, reads every one back through the same client, compares
bytes, prints a rate, and deletes the database again. It refuses to write
to `obsidian`, `nova` or any other live database unless `--db` names one
explicitly *and* `--i-mean-it` is passed, because the failure this guards
against is a half-restore over live data that nobody asked for.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BRIDGE_VAULT_TOOL = "/app/bridge/vault_tool.py"
DRILL_DB = "nova_restore_drill"
LIVE_DBS = {"obsidian", "nova", "lyceum", "marcus", "newspaper", "nova_tickets", "claude-config"}


def load_vault_tool():
    """Import the bridge pod's vault client as a module.

    It is not on `sys.path` and never will be -- the runner package has no
    dependency on the bridge image. Loading it by path keeps the one
    implementation of the LiveSync write that the whole loop already uses.
    """
    if not Path(BRIDGE_VAULT_TOOL).exists():
        raise SystemExit(
            f"{BRIDGE_VAULT_TOOL} not found. This runs in the BRIDGE pod (the Bash "
            "tool), not the runner pod -- the runner has no vault client at all."
        )
    spec = importlib.util.spec_from_file_location("bridge_vault_tool", BRIDGE_VAULT_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def couch(path, method="GET", body=None, timeout=120):
    base = os.environ["CDB_BASE"].rstrip("/")
    auth = base64.b64encode(
        f'{os.environ["CDB_USER"]}:{os.environ["CDB_PASS"]}'.encode()
    ).decode()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}/{path}",
        data=data,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:300]


def backup_files(root: Path):
    """Every LiveSync file in the backup tree, as (vault path, local path).

    `<db>-records/` holds record JSON, not vault files, and `.git` is the
    mirror's own plumbing -- neither is a vault path.
    """
    for local in sorted(root.rglob("*")):
        if not local.is_file():
            continue
        rel = local.relative_to(root)
        head = rel.parts[0]
        if head.startswith(".") or head.endswith("-records"):
            continue
        yield rel.as_posix(), local


def backup_records(root: Path, db: str):
    folder = root / f"{db}-records"
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.json"))


def restore_files(vt, pairs, verbose=False):
    """Write each backup file back through the LiveSync client.

    `allow_shrink` is on: the collapse guard exists to stop a cycle
    replacing a live document with a truncated read, and a restore into an
    empty database is the one case where a document legitimately arrives
    at a size the target knows nothing about.
    """
    vault = vt.VaultClient()
    done = 0
    for path, local in pairs:
        content = local.read_text(encoding="utf-8")
        vault.write(path, content, allow_shrink=True)
        done += 1
        if verbose:
            print(f"  file {path} ({len(content.encode())} B)", flush=True)
    return done


def restore_records(files, db, batch=200):
    """POST record docs back in bulk. The backup already stripped `_rev`."""
    docs, done = [], 0
    for local in files:
        docs.append(json.loads(local.read_text(encoding="utf-8")))
        if len(docs) >= batch:
            couch(f"{db}/_bulk_docs", "POST", {"docs": docs})
            done += len(docs)
            docs = []
    if docs:
        couch(f"{db}/_bulk_docs", "POST", {"docs": docs})
        done += len(docs)
    return done


def verify_files(vt, pairs):
    """Read every restored file back through the same client and compare bytes.

    Reading back through the client rather than out of CouchDB is
    deliberate: it is the assembled document that has to match, chunking
    included, and that is what anything downstream will ask for.
    """
    vault = vt.VaultClient()
    bad = []
    for path, local in pairs:
        want = local.read_text(encoding="utf-8")
        got = vault.read(path)
        if got != want:
            bad.append((path, len(want.encode()), len(got.encode()) if got else 0))
    return bad


def verify_records(files, db):
    bad = []
    for local in files:
        want = json.loads(local.read_text(encoding="utf-8"))
        status, got = couch(f"{db}/{urllib.parse.quote(want['_id'], safe='')}")
        if status != 200:
            bad.append((want["_id"], f"HTTP {status}"))
            continue
        got.pop("_rev", None)
        if got != want:
            bad.append((want["_id"], "content differs"))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backup", default="/tmp/vault-backup",
                    help="checkout of SokratesAI/vault (the backup mirror)")
    ap.add_argument("--drill", action="store_true",
                    help=f"restore a sample into {DRILL_DB}, verify it, drop it")
    ap.add_argument("--sample", type=int, default=40,
                    help="files (and records) to restore in a drill")
    ap.add_argument("--records-db", default="nova_tickets",
                    help="record database to sample in a drill")
    ap.add_argument("--db", help="restore into this database for real")
    ap.add_argument("--i-mean-it", action="store_true",
                    help="required with --db when --db names a live database")
    args = ap.parse_args(argv)

    root = Path(args.backup)
    if not root.is_dir():
        raise SystemExit(f"no backup tree at {root}. Clone SokratesAI/vault there first.")

    if not args.drill and not args.db:
        raise SystemExit("pass --drill (safe) or --db <name> (writes).")
    if args.db and args.db in LIVE_DBS and not args.i_mean_it:
        raise SystemExit(
            f"{args.db} is a live database. A half-restore over live data is worse "
            "than no restore. Pass --i-mean-it if that is genuinely what you want."
        )

    db = DRILL_DB if args.drill else args.db
    all_files = list(backup_files(root))
    all_records = backup_records(root, args.records_db)
    print(f"backup at {root}: {len(all_files)} file(s), "
          f"{len(all_records)} record(s) in {args.records_db}-records/")

    if args.drill:
        # Stride the sample rather than take the first N: the first N of a
        # sorted tree are all one folder, and a folder is not the vault.
        step = max(1, len(all_files) // args.sample)
        files = all_files[::step][: args.sample]
        records = all_records[:: max(1, len(all_records) // args.sample)][: args.sample]
        couch(db, "DELETE")
        status, body = couch(db, "PUT")
        if status not in (201, 202):
            raise SystemExit(f"could not create {db}: HTTP {status} {body}")
    else:
        files, records = all_files, all_records

    # Routing off and the target named, so every put lands in `db` and
    # nothing reaches the live vault through CDB_NOVA_DB.
    os.environ["CDB_DB"] = db
    os.environ["CDB_NOVA_DB"] = ""
    vt = load_vault_tool()

    t0 = time.time()
    n_files = restore_files(vt, files)
    t_files = time.time() - t0
    t1 = time.time()
    n_records = restore_records(records, db)
    t_records = time.time() - t1

    bytes_restored = sum(local.stat().st_size for _, local in files)
    print(f"restored {n_files} file(s) ({bytes_restored / 1024:.0f} KB) in {t_files:.1f}s"
          + (f", {n_records} record(s) in {t_records:.1f}s" if n_records else ""))

    bad = verify_files(vt, files)
    bad_records = verify_records(records, db) if n_records else []
    for path, want, got in bad:
        print(f"  MISMATCH {path}: backup {want} B, restored {got} B")
    for doc_id, why in bad_records:
        print(f"  MISMATCH record {doc_id}: {why}")

    ok = not bad and not bad_records
    print(f"verify: {n_files - len(bad)}/{n_files} file(s) and "
          f"{n_records - len(bad_records)}/{n_records} record(s) byte-identical")

    if t_files > 0 and n_files:
        rate = bytes_restored / t_files / 1024
        total_bytes = sum(local.stat().st_size for _, local in all_files)
        print(f"rate {rate:.0f} KB/s -> the whole {total_bytes / 1024 / 1024:.0f} MB "
              f"file tree extrapolates to about {total_bytes / (bytes_restored / t_files) / 60:.0f} min. "
              "Extrapolated from the sample, not measured on a full restore.")

    if args.drill:
        couch(db, "DELETE")
        print(f"dropped {db}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
