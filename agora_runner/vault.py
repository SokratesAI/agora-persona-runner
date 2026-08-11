"""Obsidian vault access (CouchDB direct + the daily GitHub backup mirror)."""

import base64
import json
import os
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from agora_runner.config import COUCHDB_URL, COUCHDB_USER, COUCHDB_PASSWORD, COUCHDB_DB, COUCHDB_NOVA_DB, GITHUB_READONLY_TOKEN, VAULT_CONTEXT_CAP
from agora_runner.log import log, debug_log
from agora_runner.http_util import http_json


def couch_req(method, path, body=None):
    auth = base64.b64encode(f"{COUCHDB_USER}:{COUCHDB_PASSWORD}".encode()).decode()
    return http_json(
        method,
        f"{COUCHDB_URL}/{path}",
        body,
        {"Authorization": f"Basic {auth}"},
        timeout=60,
    )


# Nova's files live in their own CouchDB database rather than in Edvard's
# vault (his ask, 2026-08-11: "You have outgrown a poc project that is
# allowed to use my Vault as a database. Move out and get your own space").
# The document id in a LiveSync vault IS the lowercased file path, so which
# database holds a document is a pure function of its path and needs no
# lookup — which is what makes one rule in one place possible at all.
#
# `issues.md` and `ideas.md` deliberately stay in `obsidian`. He offered
# them ("Take all of 'my' files aswell with you if you want"), but they are
# the two files Obsidian LiveSync may still write, and a second writer that
# cannot see the routing rule would silently re-create them in the vault
# Nova had stopped reading.
NOVA_DB_PREFIXES = (
    "projects/sokrates/projects/agora/nova/",
    "projects/sokrates/projects/agora/journal-digest.md",
)


def db_for(path):
    """Which database holds `path`. One rule, one place, so the answer
    cannot drift between the nine call sites that need it.

    Note this takes a *path*. Chunk ids (`h:...`) are content hashes with
    no path at all, so they can never be routed by this function — every
    chunk lives in the same database as the document that points at it,
    and the chunk call sites take an explicit `db` argument for exactly
    that reason. Routing a chunk id through here would silently resolve
    it to `obsidian` and turn every chunked read of a Nova file into a
    VaultIncompleteDocument.
    """
    if not COUCHDB_NOVA_DB:
        return COUCHDB_DB
    return COUCHDB_NOVA_DB if (path or "").lower().startswith(NOVA_DB_PREFIXES) else COUCHDB_DB


def dbs_for_prefix(prefix):
    """Every database that could hold a document under `prefix`.

    Three cases, and the middle one is the one worth naming: a prefix
    wholly inside Nova's folder needs only Nova's database; a prefix that
    is an *ancestor* of it (`""`, or `projects/`) straddles both and has
    to query both or a whole-vault listing quietly loses 162 files; and
    anything else is Edvard's alone.
    """
    if not COUCHDB_NOVA_DB:
        return [COUCHDB_DB]
    lowered = (prefix or "").lower()
    if lowered.startswith(NOVA_DB_PREFIXES):
        return [COUCHDB_NOVA_DB]
    if any(p.startswith(lowered) for p in NOVA_DB_PREFIXES):
        return [COUCHDB_DB, COUCHDB_NOVA_DB]
    return [COUCHDB_DB]


def couch_get_doc(doc_id, db=None):
    return couch_req("GET", f"{db or db_for(doc_id)}/{urllib.parse.quote(doc_id, safe='')}")


def _couch_batched(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


# The largest code point there is, so `prefix + _ID_MAX` sorts above every
# id that starts with `prefix` and below the next one that doesn't.
# `_all_docs` collates ids by raw UTF-8 bytes, and U+10FFFF encodes to
# F4 8F BF BF -- the maximum any valid UTF-8 sequence can begin with. The
# obvious `￰` is the common idiom and is wrong here: it encodes to
# EF BF B0, so a filename starting with an emoji (F0 9F ...) would sort
# above it and be silently dropped from the listing.
_ID_MAX = "\U0010FFFF"

# Obsidian LiveSync's own bookkeeping docs -- chunks, file/index/version
# entries -- plus CouchDB's `_design`. Never files a human wrote. One
# definition rather than two: the two listing functions below sit twenty
# lines apart and look almost identical, which is exactly the distance at
# which a new prefix gets added to one of them.
_INTERNAL_PREFIXES = ("_", "h:", "f:", "i:", "v:")


def _id_range(prefix):
    """`startkey`/`endkey` restricting `_all_docs` to one folder.

    Doc ids in a LiveSync vault are the lowercased file paths, so a folder
    is a contiguous key range and CouchDB can seek straight to it instead
    of returning the whole database for the caller to filter. Measured on
    the live vault 2026-08-09: the unrestricted scan is 12174 rows in
    1905ms; the same 70 rows by range is 11ms.

    An empty prefix keeps the old behaviour -- `""` to U+10FFFF is every
    document there is.
    """
    return urllib.parse.urlencode({
        "startkey": json.dumps(prefix),
        "endkey": json.dumps(prefix + _ID_MAX),
    })


def _vault_file_docs(prefix=""):
    """{doc_id: doc} for every file under `prefix` that still exists.

    Obsidian LiveSync does not delete a document when you delete the note
    — it keeps the doc and sets `deleted: true`, which is how peers learn
    to remove their local copy. So a deleted note stays in `_all_docs`
    forever, and any tool that reads ids without reading the flag serves
    files Edvard has thrown away. On 2026-08-07 that was 309 of 897
    documents, a third of the vault, including a `kanban.md` last touched
    2026-07-29 that Nova's own prompt still told every cycle to read as
    "the real backlog".

    `_all_docs` alone cannot tell — it returns ids and revs, not fields —
    so the ids are re-fetched with `include_docs=true` in batches. That is
    the same first phase `vault_bulk_fetch` already ran, so the seven
    tools built on it pay nothing new, and the cost scales with the prefix
    asked for (measured: 0.46s for a project folder, 5.0s for the whole
    vault). A Mango `_find` on the flag was the obvious alternative and is
    worse — unindexed, it scans all 10939 docs in 8.5s no matter how small
    the prefix.
    """
    prefix = prefix.lower()
    # Keyed by database, never flattened: a batch is one POST to one
    # database, so mixing ids from both into a single list of 500 would
    # send half of them to a database that has never heard of them.
    keys_by_db = {}
    for db in dbs_for_prefix(prefix):
        status, data = couch_req("GET", f"{db}/_all_docs?{_id_range(prefix)}")
        if status != 200:
            continue
        keys_by_db[db] = [
            row["id"] for row in data.get("rows", [])
            if not row["id"].startswith(_INTERNAL_PREFIXES)
            and row["id"].lower().startswith(prefix)
        ]
    out = {}
    batches = [
        (db, batch)
        for db, keys in keys_by_db.items()
        for batch in _couch_batched(keys, 500)
    ]
    for db, batch in batches:
        status, res = couch_req(
            "POST", f"{db}/_all_docs?include_docs=true", {"keys": batch}
        )
        if status != 200:
            # Dropping the batch silently would make live files vanish from
            # vault_list and vault_search with no signal at all, and "that
            # file does not exist" is a conclusion an agent writes into its
            # permanent memory. Skipping is still the safer half of the
            # choice -- failing open would serve tombstones, which is the
            # bug this function exists to fix -- but it does not get to be
            # quiet about it.
            log(f"vault: _all_docs include_docs batch failed ({status}); "
                f"{len(batch)} file(s) under {prefix!r} omitted from this listing")
            continue
        for row in res.get("rows", []):
            doc = row.get("doc")
            if doc and not doc.get("deleted"):
                out[row["id"]] = doc
    return out


class VaultIncompleteDocument(RuntimeError):
    """A file doc references content chunks that are not in the database.

    Raised rather than returned because the text this would otherwise
    produce is *plausible*: LiveSync stores a note as an ordered list of
    content chunks, so a missing one drops a span out of the middle and
    splices the surviving neighbours together mid-word. There is no
    marker at the seam and the result parses fine.

    Measured, 2026-08-10: `projects/sokrates/projects/agora/ideas.md` was
    re-chunked by a LiveSync client into 184 chunks, 6 of which never
    reached CouchDB. Every reader silently served the other 178 — 1238
    characters gone, including Edvard's `## Board` heading, its table
    header, and rows #57 to #50. Nothing reported an error, and one of
    the casualties was the tail of the capture sentence he had just
    typed. A scan of all 686 file docs found exactly that one damaged,
    so this is rare; it is also unsurvivable when it happens, because
    several callers read-modify-write, and an append onto a silently
    truncated read persists the truncation.

    `RuntimeError` is the base class deliberately: `nova_site` already
    turns a RuntimeError out of `vault_read_path` into a 502 carrying
    the message, so the site reports the damage instead of rendering it.
    """


def _fetch_chunks(chunk_ids, db=None):
    """`{chunk_id: data}` for every chunk that exists, in one request.

    An id absent from the result is genuinely missing -- that is what
    `vault_assemble` turns into VaultIncompleteDocument, so this must
    never report a chunk as absent for any reason other than absence. A
    non-200 from `_all_docs` therefore falls back to per-chunk GETs
    rather than returning an empty map, which would make every read of
    every file look like corruption."""
    keys = sorted(set(chunk_ids))
    if not keys:
        return {}
    db = db or COUCHDB_DB
    status, body = couch_req(
        "POST", f"{db}/_all_docs", {"keys": keys, "include_docs": True}
    )
    if status != 200:
        out = {}
        for chunk_id in keys:
            chunk_status, chunk = couch_get_doc(chunk_id, db)
            if chunk_status == 200:
                out[chunk_id] = chunk.get("data", "")
        return out
    return {
        row["key"]: (row["doc"] or {}).get("data", "")
        for row in body.get("rows", [])
        if "error" not in row and row.get("doc")
    }


def vault_assemble(doc, path=None):
    kids = doc.get("children") or []
    if kids:
        # One request for every chunk, not one per chunk. This reduces a
        # regression that chunked writes (Cycle 117) introduce; it does not
        # erase it, and the honest numbers belong here rather than in a
        # commit message. Medians of 7 against the live vault, on the same
        # 134KB file: 1 chunk 9ms either way; the same file as 16 chunks is
        # 196ms bulk against 301ms one-GET-per-chunk. So a large file does
        # get slower for nova_site to read -- roughly 9ms to 196ms -- and
        # this recovers about a third of that. The trade is deliberate: the
        # write side was leaving a full dead copy behind on every save.
        # The chunks live wherever their file doc lives. `path` is the
        # routing key and the doc carries it, so a caller that fetched
        # this doc out of either database gets the matching chunks with
        # no extra argument to forget.
        by_id = _fetch_chunks(kids, db_for(path or doc.get("path") or doc.get("_id")))
        out = []
        missing = []
        for chunk_id in kids:
            if chunk_id not in by_id:
                missing.append(chunk_id)
            out.append(by_id.get(chunk_id, ""))
        if missing:
            raise VaultIncompleteDocument(
                f"{path or doc.get('path') or doc.get('_id')}: {len(missing)} of "
                f"{len(kids)} content chunks missing from the vault "
                f"({', '.join(missing[:5])}"
                f"{', …' if len(missing) > 5 else ''}) — refusing to serve a "
                f"partial document; recover with vault_git_revision_history"
            )
        return "".join(out)
    return doc.get("data", "")


def vault_read_path(path):
    status, doc = couch_get_doc(path.lower())
    if status != 200:
        return None
    # A LiveSync tombstone still has its content chunks attached, so this
    # returns the old text unless the flag is checked — see _vault_file_docs.
    # Deleted means gone; vault_git_revision_history is the way back.
    if doc.get("deleted"):
        return None
    return vault_assemble(doc, path.lower())


def vault_list_prefix(prefix=""):
    return sorted(_vault_file_docs(prefix))


def vault_list_ids(prefix=""):
    """Paths under `prefix`, from ids alone -- no document bodies at all.

    `vault_list_prefix` above fetches every file doc with
    `include_docs=true` so it can drop tombstones, which is what a
    *listing* has to do: a deleted file reappearing in `vault_list` or
    `vault_search` is how an agent writes "that file exists" into its
    permanent memory. Measured on the journal folder 2026-08-11: 0.701s
    for 103 docs, against **0.045s** for the same 103 ids by key range.

    That 0.65s is worth paying for a listing and is pure waste for a
    lookup, which is the only thing this is for: when the caller is about
    to `vault_read_path` exactly one of these paths, the tombstone check
    happens there instead -- that function returns None for a deleted doc
    -- so the id being stale costs a miss the caller already has to
    handle, not a wrong answer. Do not use it to *show* anyone a list.
    """
    prefix = prefix.lower()
    ids = []
    for db in dbs_for_prefix(prefix):
        status, data = couch_req("GET", f"{db}/_all_docs?{_id_range(prefix)}")
        if status != 200:
            continue
        ids.extend(
            row["id"] for row in data.get("rows", [])
            if not row["id"].startswith(_INTERNAL_PREFIXES)
            and row["id"].lower().startswith(prefix)
        )
    return sorted(ids)


# Content-defined chunking, in bytes. LiveSync -- the client that wrote
# every file in this vault Nova didn't -- averages ~4KB a chunk, and
# these are picked to land there.
#
# Why content-defined and not a fixed stride: `vault_append_path` inserts
# under a heading near the TOP of the file, so a fixed stride would shift
# every boundary after the insertion and rewrite the whole file anyway. A
# boundary chosen by the content of the line it follows re-syncs within a
# chunk or two of the edit, so an append rewrites the tail and nothing
# else. Measured 2026-08-11 (Cycle 116, research/vault-storage-format.md):
# one-blob writes left 38.8MB of dead copies in Edvard's database against
# 1.4MB of live content -- 27.6x -- because every write stored the whole
# file again under a new content hash and deleted nothing.
#
# Kept byte-identical to bridge/vault_tool.py in agora-claude-bridge. The
# two clients write the same database; if they chunk differently they
# stop reusing each other's chunks and the amplification comes back for
# whichever file they take turns writing.
CHUNK_MIN_BYTES = 2048
CHUNK_MAX_BYTES = 16384
# 1 line in 32 is a boundary candidate once past CHUNK_MIN_BYTES.
CHUNK_BOUNDARY_MASK = 0x1F


def _is_chunk_boundary(line):
    # zlib.crc32, not the builtin hash(): str hashing is salted per
    # process, so the same file would chunk differently on every run and
    # reuse nothing.
    import zlib
    return (zlib.crc32(line.encode("utf-8")) & CHUNK_BOUNDARY_MASK) == 0


def _bytes_prefix(text, limit):
    """How many characters of `text` fit in `limit` UTF-8 bytes.

    Slicing a long line by character count is wrong: CHUNK_MAX_BYTES is a
    byte budget, and 20,000 emoji measured 65,536 bytes in a single
    "chunk" -- four times the cap the chunker claims to enforce. Cutting
    on a code-point boundary is still required, so this finds the
    boundary rather than assuming one character is one byte."""
    lo, hi = 0, min(len(text), limit)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(text[:mid].encode("utf-8")) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return max(lo, 1)


def _split_chunks(content):
    """Split `content` into content-defined pieces.

    Concatenating the result reproduces `content` byte for byte --
    `vault_assemble()` does exactly that, so this is the whole contract."""
    if not content:
        return [""]
    units = []
    for line in content.splitlines(keepends=True):
        # A single line can be longer than a chunk (a one-line JSON
        # ledger is the real case). Cut on a code-point boundary, but
        # count bytes while doing it -- see _bytes_prefix.
        while len(line.encode("utf-8")) > CHUNK_MAX_BYTES:
            cut = _bytes_prefix(line, CHUNK_MAX_BYTES)
            units.append(line[:cut])
            line = line[cut:]
        units.append(line)

    chunks, current, size = [], [], 0
    for unit in units:
        unit_bytes = len(unit.encode("utf-8"))
        # Close the chunk BEFORE the unit that would overflow it, not
        # after. Closing after lets a chunk reach almost CHUNK_MAX_BYTES
        # and then take one more whole unit -- measured live on mixed
        # text plus one long emoji line, 20,692 bytes against a 16,384
        # cap. Every unit is itself capped by the loop above, so this
        # makes the invariant hold rather than nearly hold.
        if current and size + unit_bytes > CHUNK_MAX_BYTES:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(unit)
        size += unit_bytes
        if size >= CHUNK_MIN_BYTES and _is_chunk_boundary(unit):
            chunks.append("".join(current))
            current, size = [], 0
    if current:
        chunks.append("".join(current))
    return chunks


def _existing_chunk_ids(chunk_ids, db=None):
    """Which of `chunk_ids` are already in the database.

    One `_all_docs` POST instead of a GET per chunk. A row for a missing
    id carries `error`; a row for a deleted one carries `value.deleted`,
    and both have to be rewritten."""
    keys = sorted(set(chunk_ids))
    if not keys:
        return set()
    status, body = couch_req("POST", f"{db or COUCHDB_DB}/_all_docs", {"keys": keys})
    if status != 200:
        return set()
    return {
        row["key"] for row in body.get("rows", [])
        if "error" not in row and not (row.get("value") or {}).get("deleted")
    }


def _chunk_id_for(content_bytes):
    # LiveSync uses xxhash64 chunk ids; the vault-bridge image ships it for
    # the vault CronJobs. If it's ever missing, a sha-derived id still
    # assembles correctly (children ids are opaque to assemble()), it just
    # opts out of LiveSync's chunk dedup for that write.
    try:
        import xxhash
        return f"h:{xxhash.xxh64(content_bytes).hexdigest()}"
    except Exception:
        import hashlib
        return f"h:{hashlib.sha256(content_bytes).hexdigest()[:16]}"


def vault_write_path(path, content):
    """LiveSync v0.25+ chunked write, mirroring vault_tool.seed_file.

    2026-08-06: this used to snapshot the previous content into
    `agora/backups/<timestamp> <basename>` in the vault before every
    overwrite. Edvard asked for that to stop -- it doubled the document
    count of every edit and left 272 stray files behind, and the folder
    has been deleted. Recovery comes from the daily snapshot of the
    whole vault into the `SokratesAI/vault` GitHub repo (see
    `vault_git_revision_history` below), which keeps every version in
    git history instead of beside the original.

    2026-07-24: `path` is normalized to lowercase inside `_vault_put_raw`
    (the single place that actually persists a doc) for BOTH the CouchDB
    `_id` and the stored `path` field -- previously only `_id` was
    lowercased, while `path` kept whatever casing the caller passed
    verbatim. Obsidian/LiveSync renders using the `path` field, not
    `_id`, so a write with different casing than a file's established
    name silently flipped that one document's display casing (same doc,
    no new copy -- but broke the phone's rendering, which looked to
    Edvard like duplicated folders). Enforcing lowercase everywhere
    (Edvard's call, 2026-07-24) makes `_id` and `path` structurally
    identical by construction, closing this bug class for good."""
    lower_id = path.lower()
    status, existing = couch_get_doc(lower_id)
    return _vault_put_raw(path, content, existing if status == 200 else None)


def vault_append_path(path, content, after_marker=""):
    """Add `content` to an EXISTING file without losing what's already
    there -- vault_write_path is a full overwrite, and a run that reads
    a file then calls it with only its own new bit (easy for a small
    model to do without noticing) silently destroys every prior entry.
    Found live 2026-07-31: the Evolve-Coder persona's cycle journal
    entries were replacing each other one-for-one, run after run,
    because nothing enforced "combine with the old content" -- the
    convention lived only in prompt text. At the time that was recoverable
    from vault_write_path's own per-write backups; since 2026-08-06 those
    are gone and the only fallback is the *daily* GitHub snapshot, so a
    clobber-and-restore now loses up to a day rather than nothing. That
    makes this function the real protection, not a convenience.

    If `after_marker` is a line that exists verbatim in the current
    file, `content` is inserted directly after it (one blank line
    between). With no marker given, `content` is appended at the true
    end of the file. A marker that matches no line fails loudly and
    writes nothing -- see below. Fails loudly (does not silently fall
    back to vault_write's create-new-file behavior) if the file doesn't
    exist yet, since "append" implies something to append to.

    That marker-not-found case used to append at the end instead, which
    is how the identical bug in the bridge's own vault tool buried three
    of Nova's journal entries at the bottom of a file whose header
    promises newest-first (SokratesAI/agora-claude-bridge#10). Edvard
    read it as the loop having stopped writing entirely. Asking for a
    position and silently getting the opposite end of the file is the
    same class of mistake as appending to a file that doesn't exist,
    which this function already refuses to do -- and here the caller is
    a model, which can read the FAILED string and retry with a real
    marker."""
    existing_content = vault_read_path(path)
    if existing_content is None:
        return f"FAILED(not found: {path} -- use vault_write to create a new file)"
    if after_marker:
        lines = existing_content.split("\n")
        for i, line in enumerate(lines):
            if line.strip() == after_marker.strip():
                lines[i + 1:i + 1] = ["", content.strip("\n")]
                return vault_write_path(path, "\n".join(lines))
        return (f"FAILED(after_marker not found in {path}: {after_marker!r} "
                f"-- nothing written; omit after_marker to append at the end)")
    sep = "" if existing_content.endswith("\n\n") else ("\n" if existing_content.endswith("\n") else "\n\n")
    return vault_write_path(path, existing_content + sep + content.strip("\n") + "\n")


def _vault_put_raw(path, content, existing=None):
    path = path.lower()
    db = db_for(path)
    now_ms = int(time.time() * 1000)
    content_bytes = content.encode("utf-8")
    chunk_texts = _split_chunks(content)
    chunk_ids = [_chunk_id_for(t.encode("utf-8")) for t in chunk_texts]
    lower_id = path

    if existing is None:
        status, found = couch_get_doc(lower_id, db)
        existing = found if status == 200 else None

    # Chunks are content-addressed, so one that already exists holds
    # exactly this text and does not need rewriting -- that reuse is the
    # entire point of chunking, and it is what stops an append from
    # leaving a whole extra copy of the file behind.
    already = _existing_chunk_ids(chunk_ids, db)
    written = set()
    for chunk_id, text in zip(chunk_ids, chunk_texts):
        if chunk_id in already or chunk_id in written:
            continue
        chunk = {"_id": chunk_id, "data": text, "type": "leaf", "children": []}
        chunk_status, _ = couch_req(
            "PUT", f"{db}/{urllib.parse.quote(chunk_id, safe='')}", chunk
        )
        if chunk_status == 409:
            # Content-addressed, so a conflict means this exact chunk
            # was created between the existence check above and this
            # PUT -- by the other client, or by a reply turn running
            # alongside a cycle. The id IS the hash of the content, so
            # whoever won stored exactly this text. That is success.
            # Treating it as failure aborts a perfectly good write,
            # and does so most often on the common path: a non-200
            # from the existence check reports "nothing exists", which
            # makes every unchanged chunk a blind PUT and every one of
            # them a 409.
            written.add(chunk_id)
            continue
        if chunk_status not in (200, 201):
            # Never point a file doc at a chunk that isn't there -- that
            # is the VaultIncompleteDocument failure, and it is silent on
            # read. Leaving the old revision intact is the safe outcome.
            return f"FAILED(chunk {chunk_id}: {chunk_status})"
        written.add(chunk_id)

    doc = {
        "_id": lower_id,
        "path": path,
        "data": "",
        "children": chunk_ids,
        "size": len(content_bytes),
        "ctime": now_ms,
        "mtime": now_ms,
        "type": "plain",
        "eden": {},
    }
    if existing is not None:
        doc["_rev"] = existing["_rev"]
        doc["ctime"] = existing.get("ctime", now_ms)
    put_status, _ = couch_req(
        "PUT", f"{db}/{urllib.parse.quote(lower_id, safe='')}", doc
    )
    return "written" if put_status in (200, 201) else f"FAILED({put_status})"


def fetch_vault_context(paths):
    """Heartbeat context injection — folders end with '/', capped total
    (critique #8: a folder pointer must not inject megabytes)."""
    sections = []
    total = 0
    for raw in paths:
        targets = vault_list_prefix(raw.lower()) if raw.endswith("/") else [raw]
        for target in targets:
            if total >= VAULT_CONTEXT_CAP:
                sections.append("[...vault context truncated at cap...]")
                return "\n\n".join(sections)
            try:
                content = vault_read_path(target)
            except VaultIncompleteDocument as e:
                # One damaged file must not kill the run. This function
                # builds the prompt for every heartbeat -- including Nova's
                # own cycle -- and it is called before run_heartbeat's try
                # block, on a bare daemon thread. An exception escaping here
                # takes the thread down with no reply posted, no audit chip,
                # and the heartbeat's lastResult stuck on "running" forever.
                # That is a worse silence than the splice this exception
                # exists to prevent, so it degrades the same way a missing
                # file already does -- one visible marker, keep going.
                sections.append(f"### {target}\n[unreadable: {e}]")
                continue
            if content is None:
                sections.append(f"### {target}\n[not found]")
                continue
            room = VAULT_CONTEXT_CAP - total
            snippet = content[:room]
            if len(content) > room:
                snippet += "\n[...truncated...]"
            total += len(snippet)
            sections.append(f"### {target}\n{snippet}")
    return "\n\n".join(sections)


# --------------------------------------------------------------------------
# Vault-tools.md tool suite (2026-07-26) — full-text search, frontmatter
# querying/validation/batch-editing, stub/duplicate detection, token
# metrics, and git history off the daily backup mirror. All read tools
# go through vault_bulk_fetch (batched _all_docs, mirroring
# vault_pull_bulk.py) rather than one couch_get_doc per file/chunk —
# fetching hundreds of files one at a time is exactly what the vault's
# own CLAUDE.md says never to do.
# --------------------------------------------------------------------------
def vault_bulk_fetch(prefix="", with_mtimes=False):
    """{path: content} for every vault file under `prefix`, assembled
    from batched bulk _all_docs POSTs (file docs, then their content
    chunks) instead of per-file couch_get_doc calls.

    With `with_mtimes`, returns `(contents, {path: mtime_ms})` instead --
    the file docs are already in hand here, so the caller gets the write
    times for free rather than paying a second listing for them."""
    filedocs = _vault_file_docs(prefix)
    # Grouped by the database of the file doc that points at them. A flat
    # set across both would send Nova's chunk ids to Edvard's database,
    # find nothing, and surface as every Nova file coming back empty.
    chunk_ids_by_db = {}
    for doc_id, doc in filedocs.items():
        for chunk_id in (doc.get("children") or []):
            chunk_ids_by_db.setdefault(db_for(doc_id), set()).add(chunk_id)
    chunks = {}
    chunk_batches = [
        (db, batch)
        for db, ids in chunk_ids_by_db.items()
        for batch in _couch_batched(sorted(ids), 1000)
    ]
    for db, batch in chunk_batches:
        status, res = couch_req("POST", f"{db}/_all_docs?include_docs=true", {"keys": batch})
        if status != 200:
            continue
        for row in res.get("rows", []):
            doc = row.get("doc")
            if doc:
                chunks[row["id"]] = doc.get("data", "")
    out = {}
    mtimes = {}
    for doc_id, doc in filedocs.items():
        kids = doc.get("children")
        if kids:
            missing = [c for c in kids if c not in chunks]
            if missing:
                # Omitted rather than raised: this feeds the journal page and
                # the whole search/stub/duplicate suite, and one damaged file
                # should not take down a listing of several hundred. That is
                # the same call the failed-batch branch above makes, and it
                # gets the same condition attached — silence is what let a
                # spliced ideas.md read as intact all morning.
                log(f"vault: {doc.get('path') or doc_id} omitted from bulk "
                    f"fetch — {len(missing)} of {len(kids)} content chunks "
                    f"missing from the vault ({', '.join(missing[:5])})")
                continue
            content = "".join(chunks[c] for c in kids)
        else:
            content = doc.get("data", "")
        if isinstance(content, str):
            path = doc.get("path") or doc_id
            out[path] = content
            mtimes[path] = doc.get("mtime")
    return (out, mtimes) if with_mtimes else out


FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def parse_frontmatter(content):
    """Minimal YAML-subset frontmatter parser -- stdlib only, no PyYAML
    in this image. Handles the flat `key: value` / `key: [a, b, c]`
    shape this vault's OKF frontmatter actually uses; nested maps and
    multi-line block scalars are left as opaque strings rather than
    mis-parsed, since no tool here needs them. Returns (fields, body)."""
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    body = content[match.end():]
    fields = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            fields[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            fields[key] = value.strip("'\"")
    return fields, body


# Root capture files (CLAUDE.md: "headers + capture zones only, no
# instructional prose") are exempt from vault_validate_frontmatter_schema
# -- they're Edvard's own quick-capture files, not agent-owned content.
FRONTMATTER_EXEMPT_BASENAMES = {"inbox.md", "ideas.md", "todos.md", "heartbeat tasks.md"}


def vault_search(query, prefix="", max_results=20):
    if not query.strip():
        return "[vault_search: empty query]"
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
    results = []
    for path, content in sorted(vault_bulk_fetch(prefix).items()):
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                results.append(f"{path}:{lineno}: {line.strip()[:200]}")
                if len(results) >= max_results:
                    return "\n".join(results)
    return "\n".join(results) if results else f"[vault_search: no matches for {query!r}]"


def vault_query_frontmatter(field, value="", prefix=""):
    if not field.strip():
        return "[vault_query_frontmatter: field is required]"
    results = []
    for path, content in sorted(vault_bulk_fetch(prefix).items()):
        fields, _ = parse_frontmatter(content)
        if field not in fields:
            continue
        actual = fields[field]
        actual_str = ", ".join(actual) if isinstance(actual, list) else str(actual)
        if value and value.lower() not in actual_str.lower():
            continue
        results.append(f"{path}: {field}={actual_str}")
    if not results:
        return f"[vault_query_frontmatter: no files with {field}={value or '*'}]"
    return "\n".join(results[:200])


def vault_validate_frontmatter_schema(prefix=""):
    files = vault_bulk_fetch(prefix)
    issues = []
    for path, content in sorted(files.items()):
        if path.rsplit("/", 1)[-1].lower() in FRONTMATTER_EXEMPT_BASENAMES:
            continue
        fields, _ = parse_frontmatter(content)
        if not fields:
            issues.append(f"{path}: no frontmatter block found")
            continue
        if not str(fields.get("type", "")).strip():
            issues.append(f"{path}: missing required 'type' key")
    if not issues:
        return f"[vault_validate_frontmatter_schema: {len(files)} file(s) checked, no issues]"
    return f"{len(issues)} issue(s) out of {len(files)} file(s):\n" + "\n".join(issues[:200])


def vault_update_frontmatter_batch(field, value, prefix="", match_field="", match_value=""):
    if not field.strip():
        return "[vault_update_frontmatter_batch: field is required]"
    updated = []
    for path, content in sorted(vault_bulk_fetch(prefix).items()):
        match = FRONTMATTER_RE.match(content)
        if not match:
            continue
        fields, body = parse_frontmatter(content)
        if match_field:
            actual = fields.get(match_field)
            actual_str = ", ".join(actual) if isinstance(actual, list) else str(actual or "")
            if match_value.lower() not in actual_str.lower():
                continue
        # Rewrite only the matching key's line inside the existing
        # frontmatter block (or append it) rather than regenerating the
        # whole block from the parsed dict -- any formatting/keys this
        # parser doesn't understand survive untouched.
        fm_text = match.group(1)
        key_re = re.compile(rf"(?m)^{re.escape(field)}\s*:.*$")
        new_line = f"{field}: {value}"
        if key_re.search(fm_text):
            fm_text = key_re.sub(new_line, fm_text, count=1)
        else:
            fm_text = fm_text.rstrip("\n") + f"\n{new_line}"
        new_content = f"---\n{fm_text}\n---\n{body}"
        if vault_write_path(path, new_content) == "written":
            updated.append(path)
    if not updated:
        return "[vault_update_frontmatter_batch: no matching files updated]"
    return f"updated {field}={value!r} on {len(updated)} file(s):\n" + "\n".join(updated[:200])


def vault_find_stub_notes(prefix="", min_chars=40):
    files = vault_bulk_fetch(prefix)
    stubs = []
    for path, content in sorted(files.items()):
        _, body = parse_frontmatter(content)
        stripped = body.strip()
        if len(stripped) < min_chars:
            stubs.append(f"{path}: {len(stripped)} body char(s)")
    if not stubs:
        return f"[vault_find_stub_notes: {len(files)} file(s) checked, no stubs found]"
    return f"{len(stubs)} stub(s) out of {len(files)}:\n" + "\n".join(stubs[:200])


def vault_find_duplicate_titles(prefix=""):
    files = vault_bulk_fetch(prefix)
    titles = {}
    for path, content in files.items():
        _, body = parse_frontmatter(content)
        h1 = re.search(r"(?m)^#\s+(.+)$", body)
        title = h1.group(1).strip() if h1 else path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        titles.setdefault(title.lower(), []).append(path)
    dupes = {t: p for t, p in titles.items() if len(p) > 1}
    if not dupes:
        return f"[vault_find_duplicate_titles: {len(files)} file(s) checked, no duplicate titles]"
    lines = [f"{len(dupes)} duplicate title(s):"]
    for title, paths in sorted(dupes.items()):
        lines.append(f"- {title!r}: {', '.join(sorted(paths))}")
    return "\n".join(lines[:200])


def vault_get_token_metrics(prefix=""):
    files = vault_bulk_fetch(prefix)
    if not files:
        return "[vault_get_token_metrics: no files under that prefix]"
    rows = []
    total_tokens = 0
    for path, content in files.items():
        words = len(content.split())
        tokens = max(1, len(content) // 4)  # rough chars/4 heuristic -- no real tokenizer in this image
        total_tokens += tokens
        rows.append((tokens, words, path))
    rows.sort(reverse=True)
    lines = [f"{len(files)} file(s), ~{total_tokens:,} tokens total (chars/4 heuristic, not an exact tokenizer)."]
    lines.append("Largest files:")
    for tokens, words, path in rows[:20]:
        flag = "  ⚠ large" if tokens > 20000 else ""
        lines.append(f"- {path}: ~{tokens:,} tokens, {words:,} words{flag}")
    return "\n".join(lines)


VAULT_BACKUP_REPO = "SokratesAI/vault"  # daily CronJob's markdown mirror


def _gh_api_get(query):
    """GET against the GitHub API via `gh api`, same read-only
    token/degradation posture as github_read -- but hardcoded to the
    vault's own backup mirror, not an arbitrary repo, so it's safe to
    offer under vaultRead rather than requiring the separate githubRead
    grant. Returns (parsed_json, None) or (None, error_string)."""
    if not GITHUB_READONLY_TOKEN:
        return None, "no token configured (GITHUB_READONLY_TOKEN not set)"
    env = dict(os.environ)
    env["GH_TOKEN"] = GITHUB_READONLY_TOKEN
    cmd = ["gh", "api", query]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=env)
    except FileNotFoundError:
        return None, "gh: binary not installed in this image"
    except Exception as e:
        return None, f"gh error: {e}"
    if result.returncode != 0:
        return None, f"gh api {query} exited {result.returncode}: {(result.stderr or result.stdout)[:400]}"
    try:
        return json.loads(result.stdout), None
    except Exception as e:
        return None, f"gh api {query}: invalid JSON response: {e}"


def vault_git_revision_history(path="", limit=10, sha=""):
    """Commit log (optionally scoped to `path`) from the vault's daily
    backup mirror -- or, when `sha` is given, that commit's own file
    diffs (optionally filtered to `path`) instead of a log."""
    if sha:
        data, err = _gh_api_get(f"repos/{VAULT_BACKUP_REPO}/commits/{urllib.parse.quote(sha)}")
        if err:
            return f"[vault_git_revision_history: {err}]"
        files = data.get("files") or []
        if path:
            files = [f for f in files if f.get("filename", "").lower() == path.lower()]
        if not files:
            return f"[vault_git_revision_history: no file changes for sha={sha} path={path or '(any)'}]"
        parts = []
        for f in files[:5]:
            patch = f.get("patch", "[no textual diff available]")
            parts.append(
                f"### {f.get('filename')} (+{f.get('additions', 0)}/-{f.get('deletions', 0)})\n{patch[:3000]}"
            )
        return "\n\n".join(parts)
    limit = max(1, min(int(limit or 10), 50))
    query = f"repos/{VAULT_BACKUP_REPO}/commits?per_page={limit}"
    if path:
        query += f"&path={urllib.parse.quote(path)}"
    data, err = _gh_api_get(query)
    if err:
        return f"[vault_git_revision_history: {err}]"
    if not data:
        return f"[vault_git_revision_history: no commits found for {path or '(repo)'}]"
    lines = []
    for c in data:
        sha_ = c.get("sha", "")[:7]
        msg = (c.get("commit", {}).get("message", "").splitlines() or [""])[0]
        date = c.get("commit", {}).get("author", {}).get("date", "")
        lines.append(f"{sha_} {date} {msg}")
    return "\n".join(lines)


def vault_summarize_recent_agent_work(hours=24):
    """Changelog of vault activity over the last `hours`, from the daily
    backup mirror's commit log -- expands the file list for the most
    recent commits (bounded, one extra API call each) so this reads as
    a real "what happened" summary, not just a bare git log."""
    hours = max(1, min(int(hours or 24), 24 * 30))
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data, err = _gh_api_get(f"repos/{VAULT_BACKUP_REPO}/commits?since={since}&per_page=100")
    if err:
        return f"[vault_summarize_recent_agent_work: {err}]"
    if not data:
        return f"[vault_summarize_recent_agent_work: no commits in the last {hours}h]"
    lines = [f"{len(data)} commit(s) in the last {hours}h:"]
    expand = data[:15]
    for c in expand:
        sha = c.get("sha", "")
        msg = (c.get("commit", {}).get("message", "").splitlines() or [""])[0]
        date = c.get("commit", {}).get("author", {}).get("date", "")
        detail, derr = _gh_api_get(f"repos/{VAULT_BACKUP_REPO}/commits/{sha}")
        files = ", ".join(f.get("filename", "?") for f in (detail.get("files") or [])[:10]) if not derr else "?"
        lines.append(f"- {date} {sha[:7]} {msg} — files: {files}")
    if len(data) > len(expand):
        lines.append(f"... and {len(data) - len(expand)} more commit(s) (message only, not expanded)")
    return "\n".join(lines)
