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


def couch_req(method, path, body=None, timeout=60):
    auth = base64.b64encode(f"{COUCHDB_USER}:{COUCHDB_PASSWORD}".encode()).decode()
    return http_json(
        method,
        f"{COUCHDB_URL}/{path}",
        body,
        {"Authorization": f"Basic {auth}"},
        timeout=timeout,
    )


# `database_health` only, and deliberately far below the 60s every other
# call gets. A health check that can block for two minutes is the slow
# uncertain wait it was built to replace: /api/health probes each database
# in turn, so two unreachable ones cost 2 x timeout before anything is
# reported. The question it answers -- "can I reach this database" -- is
# also the one question where a slow answer and no answer mean the same
# thing operationally, so failing fast loses nothing. A local CouchDB that
# cannot respond in 5s is unhealthy by any definition this endpoint cares
# about.
HEALTH_TIMEOUT_SECONDS = 5


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
# Folders match by prefix; single files must match exactly. Keeping them in
# one tuple and testing everything with startswith routed
# `journal-digest.md.bak` — and any other file merely *beginning* with that
# name — into Nova's database, which is a file Edvard owns being answered
# by the wrong store.
NOVA_DB_FOLDERS = (
    "projects/sokrates/projects/agora/nova/",
)
NOVA_DB_FILES = (
    "projects/sokrates/projects/agora/journal-digest.md",
)
NOVA_DB_TARGETS = NOVA_DB_FOLDERS + NOVA_DB_FILES

# Stamped onto a fetched doc by _vault_file_docs so later chunk lookups use
# the database the doc was really read from. Private; never written back --
# _vault_put_raw builds its document from scratch.
_SRC_DB_KEY = "_nova_src_db"


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
    lowered = (path or "").lower()
    if lowered.startswith(NOVA_DB_FOLDERS) or lowered in NOVA_DB_FILES:
        return COUCHDB_NOVA_DB
    return COUCHDB_DB


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
    if lowered.startswith(NOVA_DB_FOLDERS):
        return [COUCHDB_NOVA_DB]
    # Deliberately not `lowered in NOVA_DB_FILES -> [nova]`: as a *prefix*,
    # a single file's path also matches its own neighbours (a `.bak` beside
    # it), and those live in Edvard's database. Querying both is the
    # conservative answer and costs one extra request on a listing nobody
    # actually makes.
    if any(t.startswith(lowered) for t in NOVA_DB_TARGETS):
        return [COUCHDB_DB, COUCHDB_NOVA_DB]
    return [COUCHDB_DB]


def couch_get_doc(doc_id, db=None):
    return couch_req("GET", f"{db or db_for(doc_id)}/{urllib.parse.quote(doc_id, safe='')}")


# Paths whose routing this process reports on demand. Five distinct
# behaviours of `db_for`, two of which are regressions rather than
# examples: a `.bak` beside the digest must NOT follow it into Nova's
# database (caught in the review of #103), and the Nova folder Edvard
# asked to keep in his own vault must stay there (Cycle 121 found that
# anything under `agora/nova/` would have been routed away from him).
# The other three are the folder rule, the exact-file rule and a file of
# his that must never move.
#
# **These are real paths and must stay real.** The first one is a live
# journal entry, and journal filenames are `<sequence>-cycle-<n>.md`
# where the two numbers diverge -- `121-cycle-121.md` looks plausible,
# has never existed, and was in this tuple until a reviewer listed the
# folder. A probe pointing at a document nobody can open turns the one
# endpoint built to remove ambiguity into a second thing to disambiguate.
HEALTH_PROBE_PATHS = (
    "projects/sokrates/projects/agora/nova/journal/138-cycle-121.md",
    "projects/sokrates/projects/agora/journal-digest.md",
    "projects/sokrates/projects/agora/journal-digest.md.bak",
    "projects/sokrates/projects/nova/nova.md",
    # Was `agora/issues.md` until 2026-08-12, when his three capture files
    # moved into the Nova folder in his own vault at his ask. The rule it
    # probes is unchanged -- a file he writes by hand must resolve to his
    # database -- but the path it probes had to move with the file, or
    # this tuple would be pointing at a document nobody can open, which is
    # the exact failure the paragraph above is about.
    "projects/sokrates/projects/nova/issues.md",
)


def database_health():
    """What this process resolved and what it can actually reach.

    Two different questions, and until now answering either one took a
    write probe against the live site — append a note, poll `/api/board`
    for twenty seconds, and hope you outlast a 15-second cache. Cycle 121
    did exactly that and its first four reads all returned the pre-write
    number, which is indistinguishable from a failed migration.

    So this reports configuration *and* reachability separately. A name in
    `COUCHDB_NOVA_DB` only says which database this process would ask; it
    says nothing about whether the answer would come back, and during a
    migration the gap between those two is the whole risk.
    """
    names = {"main": COUCHDB_DB}
    if COUCHDB_NOVA_DB:
        names["nova"] = COUCHDB_NOVA_DB
    databases = {}
    for role, name in names.items():
        entry = {"name": name, "reachable": False, "doc_count": None, "error": None}
        try:
            status, info = couch_req(
                "GET", urllib.parse.quote(name, safe=""),
                timeout=HEALTH_TIMEOUT_SECONDS,
            )
            if status == 200:
                entry["reachable"] = True
                # Includes chunk documents, not just files -- a Nova file is
                # stored as one doc plus ~4KB content chunks. Named
                # `doc_count` because that is CouchDB's own field and
                # renaming it here would be a second name for one number.
                entry["doc_count"] = info.get("doc_count")
            else:
                entry["error"] = f"HTTP {status}"
        except Exception as e:
            entry["error"] = str(e)[:200]
        databases[role] = entry
    return {
        "routing_enabled": bool(COUCHDB_NOVA_DB),
        "databases": databases,
        "routes": [{"path": p, "database": db_for(p)} for p in HEALTH_PROBE_PATHS],
    }


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


class VaultFiles(dict):
    """A vault read that remembers what it could not see.

    A bulk read that loses a database logs the failure and returns what it
    got. That is the right call for the website -- one unreachable database
    should not blank the journal page -- and it is the wrong shape for
    anything that draws a conclusion from emptiness, because a refusal and
    an empty folder arrive as the same `{}`. `{}` has no gaps in it, no
    stubs, no duplicate titles and no search matches, so every tool built on
    this reports a clean result and means "I read nothing". Cycle 136 shipped
    a loop health check that said the loop was perfectly healthy while the
    journal folder visibly skipped a cycle; it had read zero entries.

    A `dict` subclass rather than a second return value so every existing
    caller is untouched and only the ones that care have to look.
    `unreadable` holds the same human-readable lines that go to the log, so a
    caller can put the reason in front of a person rather than an exit code.
    Used for the private `{doc_id: doc}` listing as well as the public
    `{path: content}`, because both need exactly this channel.
    """

    def __init__(self, *args, unreadable=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.unreadable = list(unreadable)


def unreadable_note(files, tool):
    """`"[tool: INCOMPLETE READ -- ...]\\n"` when `files` is a partial read, else "".

    Prefixed onto a tool's answer rather than replacing it: the files that
    *were* read are still real, and a search that found three matches out of
    two databases should hand over the three and say the third database is
    missing. Only the emptiness is a lie, and this is what stops it being
    told silently.
    """
    missed = getattr(files, "unreadable", ())
    if not missed:
        return ""
    return f"[{tool}: INCOMPLETE READ -- {'; '.join(missed)}]\n"


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
    unreadable = []
    for db in dbs_for_prefix(prefix):
        status, data = couch_req("GET", f"{db}/_all_docs?{_id_range(prefix)}")
        if status != 200:
            # Before routing there was one database, so this returned {} —
            # visibly, uselessly empty. With two, one failing leaves the
            # other's rows in place and the caller gets a partial listing
            # that looks entirely healthy. That is the failure mode this
            # module exists to prevent, so it does not get to be silent —
            # and the log alone was still silent to every caller, which is
            # why the same line now rides back on the result.
            note = (f"_all_docs listing failed on database {db!r} ({status}); "
                    f"files under {prefix!r} in that database are missing "
                    f"from this listing")
            log(f"vault: {note}")
            unreadable.append(note)
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
            note = (f"_all_docs include_docs batch failed on database {db!r} "
                    f"({status}); {len(batch)} file(s) under {prefix!r} "
                    f"omitted from this listing")
            log(f"vault: {note}")
            unreadable.append(note)
            continue
        for row in res.get("rows", []):
            doc = row.get("doc")
            if doc and not doc.get("deleted"):
                # Where it actually came from, not where db_for predicts it
                # should be. Those agree in steady state and disagree during
                # a migration -- which is exactly when a doc's chunks would
                # be looked up in a database that does not hold them.
                doc[_SRC_DB_KEY] = db
                out[row["id"]] = doc
    return VaultFiles(out, unreadable=unreadable)


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


def _fetch_chunks(chunk_ids, db):
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
    return vault_read_path_rev(path)[0]


def vault_read_path_rev(path):
    """`(content, rev)` — the text, and the revision it was read at.

    Every write this loop makes is a read-modify-write, and until now the
    revision the caller read at was thrown away: `vault_write_path` looked
    up a *fresh* `_rev` immediately before the PUT, so a writer that landed
    in between was adopted and overwritten with no error anywhere. CouchDB
    already solves this — a PUT carrying a stale `_rev` is rejected with a
    409 — and this is the half of it the client was discarding. Hand the
    `rev` back to `vault_write_path` as `if_rev` and a losing write fails
    loudly instead of silently winning.

    `rev` is None only when no document exists at that path. Content is
    None for a missing file *and* for a tombstone, but a tombstone has a
    revision and writing over it has to carry it — so the two cases are
    `(None, None)` and `(None, "<rev>")`, and they are not the same.
    """
    status, doc = couch_get_doc(path.lower())
    if status != 200:
        return None, None
    # A LiveSync tombstone still has its content chunks attached, so this
    # returns the old text unless the flag is checked — see _vault_file_docs.
    # Deleted means gone; vault_git_revision_history is the way back.
    if doc.get("deleted"):
        return None, doc.get("_rev")
    return vault_assemble(doc, path.lower()), doc.get("_rev")


class VaultPaths(list):
    """A sorted listing that remembers what it could not see.

    `sorted()` on a `VaultFiles` returns a plain list and drops the flag, so
    the listing tools were left exactly as blind as before -- "[no files
    under that prefix]" for a database that refused to answer. Same channel,
    same reason, different container.
    """

    def __init__(self, *args, unreadable=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.unreadable = list(unreadable)


def vault_list_prefix(prefix=""):
    docs = _vault_file_docs(prefix)
    return VaultPaths(sorted(docs), unreadable=docs.unreadable)


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
            log(f"vault: id listing failed on database {db!r} ({status}); "
                f"ids under {prefix!r} in that database are missing")
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


def _existing_chunk_ids(chunk_ids, db):
    """Which of `chunk_ids` are already in the database.

    One `_all_docs` POST instead of a GET per chunk. A row for a missing
    id carries `error`; a row for a deleted one carries `value.deleted`,
    and both have to be rewritten."""
    keys = sorted(set(chunk_ids))
    if not keys:
        return set()
    status, body = couch_req("POST", f"{db}/_all_docs", {"keys": keys})
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


#: `if_rev` default: "I have no expectation about the current revision,
#: overwrite whatever is there." Deliberately not `None`, which is a real
#: and different expectation — "there should be no document here yet".
_ANY_REV = object()

#: How many times an append re-reads and retries after losing a conflict.
#: Three, matching nova_capture's WRITE_ATTEMPTS. A conflict means another
#: writer won, so the retry is against a moving target and bounding it is
#: what stops two writers livelocking on one hot file.
APPEND_ATTEMPTS = 3


def vault_write_path(path, content, if_rev=_ANY_REV):
    """LiveSync v0.25+ chunked write, mirroring vault_tool.seed_file.

    2026-08-12: `if_rev` makes the write conditional. Pass the `rev` from
    `vault_read_path_rev` and CouchDB rejects the PUT with 409 if anything
    changed since that read, instead of this function quietly picking up
    the winner's revision and overwriting them. Pass `None` to mean "this
    file should not exist yet". Omit it and the write is unconditional,
    which is what every caller got before and still gets.

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
    return _vault_put_raw(
        path, content, existing if status == 200 else None, if_rev=if_rev
    )


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
    result = ""
    for _ in range(APPEND_ATTEMPTS):
        existing_content, rev = vault_read_path_rev(path)
        if existing_content is None:
            return f"FAILED(not found: {path} -- use vault_write to create a new file)"
        merged = _appended(existing_content, content, after_marker)
        if merged is None:
            return (f"FAILED(after_marker not found in {path}: {after_marker!r} "
                    f"-- nothing written; omit after_marker to append at the end)")
        # The whole point of an append is "add mine to whatever is there",
        # so losing a conflict is not a failure -- it means the file grew
        # under us and the merge has to be redone against the new text.
        # Retrying the *write* alone would resend a body built from the
        # text we lost the race to, which is the clobber written out long
        # hand. Re-read, re-merge, re-write.
        result = vault_write_path(path, merged, if_rev=rev)
        if "409 conflict" not in result:
            return result
    return result


def _appended(existing_content, content, after_marker):
    """The file's new text, or None if `after_marker` matches no line."""
    if after_marker:
        lines = existing_content.split("\n")
        for i, line in enumerate(lines):
            if line.strip() == after_marker.strip():
                lines[i + 1:i + 1] = ["", content.strip("\n")]
                return "\n".join(lines)
        return None
    sep = "" if existing_content.endswith("\n\n") else ("\n" if existing_content.endswith("\n") else "\n\n")
    return existing_content + sep + content.strip("\n") + "\n"


def _vault_put_raw(path, content, existing=None, if_rev=_ANY_REV):
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
    if if_rev is not _ANY_REV:
        # The caller's expectation beats whatever the lookup above found —
        # that lookup exists to carry `ctime` forward, not to decide who
        # wins. Adopting the current revision here is precisely the silent
        # clobber `if_rev` was added to stop. `None` means "no document
        # expected", and a PUT with no `_rev` against a live document is
        # CouchDB's own way of saying that: it 409s.
        if if_rev is None:
            doc.pop("_rev", None)
        else:
            doc["_rev"] = if_rev
    put_status, _ = couch_req(
        "PUT", f"{db}/{urllib.parse.quote(lower_id, safe='')}", doc
    )
    if put_status in (200, 201):
        return "written"
    if put_status == 409:
        # Named, not just numbered. A caller deciding whether to retry has
        # to tell "someone else wrote first, re-read and try again" apart
        # from "the vault refused you", and 409 is the only status where
        # retrying is the right answer rather than a spin.
        return f"FAILED(409 conflict: {path} changed since it was read)"
    return f"FAILED({put_status})"


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
def vault_bulk_list(prefix=""):
    """`(paths, {path: mtime_ms})` under `prefix`, without reading a byte
    of content. `paths` is a `VaultFiles` so `.unreadable` still answers
    what the listing lost.

    `vault_bulk_fetch(..., with_mtimes=True)` returns the same two things
    and then some, but the mtimes it hands back come from the file docs of
    its *first* phase alone -- the second phase, which pulls every content
    chunk of every file, contributes nothing to them. A caller that only
    wants filenames and write times was paying for the whole folder's body
    to get them, growing by one entry an hour (`cycle_health`, on a journal
    folder already past 1MB).

    It is also the more truthful read for that caller, which is the part
    worth keeping. `vault_bulk_fetch` drops a file whose content chunks are
    missing, because it has no string to return for it -- so a document that
    lost its chunks disappears from the listing entirely, and anything
    reasoning about which files exist concludes it was never written. Here
    the file doc *is* the answer, so a chunk-level loss cannot make a file
    vanish.
    """
    filedocs = _vault_file_docs(prefix)
    paths = {}
    mtimes = {}
    for doc_id, doc in filedocs.items():
        path = doc.get("path") or doc_id
        paths[path] = None
        mtimes[path] = doc.get("mtime")
    return VaultFiles(paths, unreadable=list(filedocs.unreadable)), mtimes


def vault_bulk_fetch(prefix="", with_mtimes=False):
    """{path: content} for every vault file under `prefix`, assembled
    from batched bulk _all_docs POSTs (file docs, then their content
    chunks) instead of per-file couch_get_doc calls.

    With `with_mtimes`, returns `(contents, {path: mtime_ms})` instead --
    the file docs are already in hand here, so the caller gets the write
    times for free rather than paying a second listing for them.

    The contents are a `VaultFiles`, so a caller that is about to conclude
    something from an empty or short result can ask `.unreadable` what was
    lost on the way. Everything that could not be read ends up there: a
    failed listing, a failed document batch, and a file whose content chunks
    are missing. Only the mapping carries it -- `mtimes` stays a plain dict,
    because the two are always read together and one flag is enough."""
    filedocs = _vault_file_docs(prefix)
    unreadable = list(filedocs.unreadable)
    # Grouped by the database of the file doc that points at them. A flat
    # set across both would send Nova's chunk ids to Edvard's database,
    # find nothing, and surface as every Nova file coming back empty.
    chunk_ids_by_db = {}
    for doc_id, doc in filedocs.items():
        src = doc.get(_SRC_DB_KEY) or db_for(doc_id)
        for chunk_id in (doc.get("children") or []):
            chunk_ids_by_db.setdefault(src, set()).add(chunk_id)
    chunks = {}
    chunk_batches = [
        (db, batch)
        for db, ids in chunk_ids_by_db.items()
        for batch in _couch_batched(sorted(ids), 1000)
    ]
    for db, batch in chunk_batches:
        status, res = couch_req("POST", f"{db}/_all_docs?include_docs=true", {"keys": batch})
        if status != 200:
            # Every file in this batch loses its body and drops out below as
            # "missing chunks" -- which reads as damaged files rather than as
            # a database that would not answer. Say which it was.
            note = (f"content chunk batch failed on database {db!r} ({status}); "
                    f"{len(batch)} chunk(s) under {prefix!r} could not be read")
            log(f"vault: {note}")
            unreadable.append(note)
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
                note = (f"{doc.get('path') or doc_id} omitted from bulk fetch — "
                        f"{len(missing)} of {len(kids)} content chunks missing "
                        f"from the vault ({', '.join(missing[:5])})")
                log(f"vault: {note}")
                unreadable.append(note)
                continue
            content = "".join(chunks[c] for c in kids)
        else:
            content = doc.get("data", "")
        if isinstance(content, str):
            path = doc.get("path") or doc_id
            out[path] = content
            mtimes[path] = doc.get("mtime")
    files = VaultFiles(out, unreadable=unreadable)
    return (files, mtimes) if with_mtimes else files


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
    files = vault_bulk_fetch(prefix)
    note = unreadable_note(files, "vault_search")
    results = []
    for path, content in sorted(files.items()):
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                results.append(f"{path}:{lineno}: {line.strip()[:200]}")
                if len(results) >= max_results:
                    return note + "\n".join(results)
    if results:
        return note + "\n".join(results)
    return note + f"[vault_search: no matches for {query!r}]"


def vault_query_frontmatter(field, value="", prefix=""):
    if not field.strip():
        return "[vault_query_frontmatter: field is required]"
    files = vault_bulk_fetch(prefix)
    note = unreadable_note(files, "vault_query_frontmatter")
    results = []
    for path, content in sorted(files.items()):
        fields, _ = parse_frontmatter(content)
        if field not in fields:
            continue
        actual = fields[field]
        actual_str = ", ".join(actual) if isinstance(actual, list) else str(actual)
        if value and value.lower() not in actual_str.lower():
            continue
        results.append(f"{path}: {field}={actual_str}")
    if not results:
        return note + f"[vault_query_frontmatter: no files with {field}={value or '*'}]"
    return note + "\n".join(results[:200])


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
    note = unreadable_note(files, "vault_validate_frontmatter_schema")
    if not issues:
        return note + f"[vault_validate_frontmatter_schema: {len(files)} file(s) checked, no issues]"
    return note + f"{len(issues)} issue(s) out of {len(files)} file(s):\n" + "\n".join(issues[:200])


def vault_update_frontmatter_batch(field, value, prefix="", match_field="", match_value=""):
    if not field.strip():
        return "[vault_update_frontmatter_batch: field is required]"
    files = vault_bulk_fetch(prefix)
    note = unreadable_note(files, "vault_update_frontmatter_batch")
    updated = []
    for path, content in sorted(files.items()):
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
        return note + "[vault_update_frontmatter_batch: no matching files updated]"
    return note + f"updated {field}={value!r} on {len(updated)} file(s):\n" + "\n".join(updated[:200])


def vault_find_stub_notes(prefix="", min_chars=40):
    files = vault_bulk_fetch(prefix)
    stubs = []
    for path, content in sorted(files.items()):
        _, body = parse_frontmatter(content)
        stripped = body.strip()
        if len(stripped) < min_chars:
            stubs.append(f"{path}: {len(stripped)} body char(s)")
    note = unreadable_note(files, "vault_find_stub_notes")
    if not stubs:
        return note + f"[vault_find_stub_notes: {len(files)} file(s) checked, no stubs found]"
    return note + f"{len(stubs)} stub(s) out of {len(files)}:\n" + "\n".join(stubs[:200])


def vault_find_duplicate_titles(prefix=""):
    files = vault_bulk_fetch(prefix)
    titles = {}
    for path, content in files.items():
        _, body = parse_frontmatter(content)
        h1 = re.search(r"(?m)^#\s+(.+)$", body)
        title = h1.group(1).strip() if h1 else path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        titles.setdefault(title.lower(), []).append(path)
    dupes = {t: p for t, p in titles.items() if len(p) > 1}
    note = unreadable_note(files, "vault_find_duplicate_titles")
    if not dupes:
        return note + f"[vault_find_duplicate_titles: {len(files)} file(s) checked, no duplicate titles]"
    lines = [f"{len(dupes)} duplicate title(s):"]
    for title, paths in sorted(dupes.items()):
        lines.append(f"- {title!r}: {', '.join(sorted(paths))}")
    return note + "\n".join(lines[:200])


def vault_get_token_metrics(prefix=""):
    files = vault_bulk_fetch(prefix)
    note = unreadable_note(files, "vault_get_token_metrics")
    if not files:
        return note + "[vault_get_token_metrics: no files under that prefix]"
    rows = []
    total_tokens = 0
    for path, content in files.items():
        words = len(content.split())
        tokens = max(1, len(content) // 4)  # rough chars/4 heuristic -- no real tokenizer in this image
        total_tokens += tokens
        rows.append((tokens, words, path))
    rows.sort(reverse=True)
    lines = [note.rstrip("\n")] if note else []
    lines.append(f"{len(files)} file(s), ~{total_tokens:,} tokens total (chars/4 heuristic, not an exact tokenizer).")
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
