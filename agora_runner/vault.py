"""Obsidian vault access (CouchDB direct + the daily GitHub backup mirror)."""

import base64
import json
import os
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from agora_runner.config import COUCHDB_URL, COUCHDB_USER, COUCHDB_PASSWORD, COUCHDB_DB, GITHUB_READONLY_TOKEN, VAULT_CONTEXT_CAP
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


def couch_get_doc(doc_id):
    return couch_req("GET", f"{COUCHDB_DB}/{urllib.parse.quote(doc_id, safe='')}")


def vault_assemble(doc):
    kids = doc.get("children") or []
    if kids:
        out = []
        for chunk_id in kids:
            status, chunk = couch_get_doc(chunk_id)
            out.append(chunk.get("data", "") if status == 200 else "")
        return "".join(out)
    return doc.get("data", "")


def vault_read_path(path):
    status, doc = couch_get_doc(path.lower())
    if status != 200:
        return None
    return vault_assemble(doc)


def vault_list_prefix(prefix=""):
    status, data = couch_req("GET", f"{COUCHDB_DB}/_all_docs")
    if status != 200:
        return []
    skip = ("_", "h:", "f:", "i:", "v:")
    out = []
    for row in data.get("rows", []):
        doc_id = row["id"]
        if doc_id.startswith(skip):
            continue
        if doc_id.lower().startswith(prefix.lower()):
            out.append(doc_id)
    return sorted(out)


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
    now_ms = int(time.time() * 1000)
    content_bytes = content.encode("utf-8")
    chunk_id = _chunk_id_for(content_bytes)
    lower_id = path

    if existing is None:
        status, found = couch_get_doc(lower_id)
        existing = found if status == 200 else None

    chunk_status, existing_chunk = couch_get_doc(chunk_id)
    chunk = {"_id": chunk_id, "data": content, "type": "leaf", "children": []}
    if chunk_status == 200:
        chunk["_rev"] = existing_chunk["_rev"]
    couch_req("PUT", f"{COUCHDB_DB}/{urllib.parse.quote(chunk_id, safe='')}", chunk)

    doc = {
        "_id": lower_id,
        "path": path,
        "data": "",
        "children": [chunk_id],
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
        "PUT", f"{COUCHDB_DB}/{urllib.parse.quote(lower_id, safe='')}", doc
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
            content = vault_read_path(target)
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
def _couch_batched(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def vault_bulk_fetch(prefix=""):
    """{path: content} for every vault file under `prefix`, assembled
    from batched bulk _all_docs POSTs (file docs, then their content
    chunks) instead of per-file couch_get_doc calls."""
    paths = vault_list_prefix(prefix)
    filedocs = {}
    for batch in _couch_batched(paths, 500):
        status, res = couch_req("POST", f"{COUCHDB_DB}/_all_docs?include_docs=true", {"keys": batch})
        if status != 200:
            continue
        for row in res.get("rows", []):
            doc = row.get("doc")
            if doc:
                filedocs[row["id"]] = doc
    chunk_ids = sorted({c for doc in filedocs.values() for c in (doc.get("children") or [])})
    chunks = {}
    for batch in _couch_batched(chunk_ids, 1000):
        status, res = couch_req("POST", f"{COUCHDB_DB}/_all_docs?include_docs=true", {"keys": batch})
        if status != 200:
            continue
        for row in res.get("rows", []):
            doc = row.get("doc")
            if doc:
                chunks[row["id"]] = doc.get("data", "")
    out = {}
    for doc_id, doc in filedocs.items():
        content = (
            "".join(chunks.get(c, "") for c in doc["children"])
            if doc.get("children") else doc.get("data", "")
        )
        if isinstance(content, str):
            out[doc.get("path") or doc_id] = content
    return out


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
