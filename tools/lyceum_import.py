"""Import the vault's wikis into Lyceum's CouchDB as courses -- build step 3.

The spec is `projects/sokrates/projects/lyceum/lyceum.md`, build sequence
step 3: *"Import the six wikis as courses. `projects/sokrates/wiki/*` ->
`raw/` becomes sources, `wiki/` becomes chapters, `wiki/index.md` becomes
the spine. Plus `work/platform/learn/a9s/`, which already has a `quiz/`.
This is what makes the app real on day one rather than empty. Skip
`garmin-routes` -- it is workshop research, not a course."*

    python3 -m tools.lyceum_import --dry-run     # print what would be written
    python3 -m tools.lyceum_import               # import every course
    python3 -m tools.lyceum_import analytics     # one course

Runs from the bridge pod: it reads the vault through
`/app/bridge/vault_tool.py` and writes CouchDB with the credentials in
`CDB_BASE` / `CDB_USER` / `CDB_PASS`. Those are the server admin, which is
the only credential on this pod; the `lyceum` database user created in
build step 2 lives in a SealedSecret the app reads and a cycle may not.

**The vault stays the source of truth.** Nothing here writes to the vault,
and a document carries the `vaultPath` it came from plus a `contentHash`
of the body. A re-run reads the existing document first and skips it when
the hash is unchanged, so importing twice costs reads and writes nothing
-- which is what makes this safe to put on a schedule later.

Three document types, one document per record (the spec's rule, taken from
Marcus): `course:<slug>`, `chapter:<course>:<slug>`, `source:<course>:<slug>`.
A course names its chapters in reading order and the chapter ids are
stable, so re-importing a topic whose pages were regenerated updates the
same documents rather than growing a second set.

Deliberately NOT written here: `claim` documents. Claim atoms are build
step 7, and the spec requires `claim.status` to carry
`unverified` / `ungrounded` / `contradicted` from the first migration --
so writing claim documents now, before extraction exists, would create
exactly the retrofit the spec forbids.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

VAULT_TOOL = "/app/bridge/vault_tool.py"

WIKI_ROOT = "projects/sokrates/wiki/"

# Workshop research rather than a course -- the spec names it and says skip.
SKIP_TOPICS = {"garmin-routes"}

# The a9s folder predates the raw/ + wiki/ convention and is laid out by
# hand: theory/ reads as chapters, resources/ as sources, quiz/ is build
# step 8's input and is not imported as either.
A9S_ROOT = "work/platform/learn/a9s/"

DB = "lyceum"


class LyceumImportError(Exception):
    pass


def _vault(*args):
    return subprocess.run(
        [sys.executable, VAULT_TOOL, *args],
        capture_output=True, text=True, timeout=180,
    )


def _ls(prefix):
    r = _vault("ls", prefix)
    if r.returncode != 0:
        raise LyceumImportError(f"ls {prefix} failed: {r.stderr.strip()}")
    return [p for p in r.stdout.splitlines() if p.startswith(prefix) and not p.endswith("/")]


def _get(path):
    r = _vault("get", path)
    if r.returncode != 0:
        raise LyceumImportError(f"get {path} failed: {r.stderr.strip()}")
    return r.stdout


def split_frontmatter(text):
    """Return (frontmatter dict, body). Only the keys this importer reads."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    head, body = text[4:end], text[end + 5:]
    meta, key = {}, None
    for line in head.splitlines():
        if line.startswith("  - ") and key:
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(line[4:].strip())
        elif ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            meta[key] = value.strip()
    return meta, body


def title_of(body, fallback):
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def slug_title(slug):
    return slug.replace("-", " ").replace("_", " ").strip().title()


def source_url(body):
    m = re.search(r"^Source:\s*(\S+)", body, re.M)
    return m.group(1) if m else None


def source_retrieved(body):
    m = re.search(r"^Retrieved:\s*(\S+)", body, re.M)
    return m.group(1) if m else None


def cited_sources(body):
    """The `[source: x.md]` citations a generated page carries, in first-use order.

    Read from the body and not from the page's `sources:` frontmatter: the
    generator writes the topic's WHOLE source list into every page's
    frontmatter, so that key says "these were in view", not "this page uses
    these". The citations are the per-chapter signal.
    """
    seen = []
    for name in re.findall(r"\[source:\s*([^\]]+?\.md)\s*\]", body):
        name = name.strip()[: -len(".md")]
        if name not in seen:
            seen.append(name)
    return seen


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def topics():
    """Every wiki topic with a wiki/index.md, minus the ones the spec skips."""
    found = {}
    for path in _ls(WIKI_ROOT):
        parts = path[len(WIKI_ROOT):].split("/")
        if len(parts) == 3 and parts[1] in ("raw", "wiki"):
            found.setdefault(parts[0], set()).add(parts[1])
    return sorted(t for t, kinds in found.items()
                  if t not in SKIP_TOPICS and "wiki" in kinds)


def build_wiki_course(topic):
    """Read one `projects/sokrates/wiki/<topic>/` into documents."""
    paths = _ls(f"{WIKI_ROOT}{topic}/")
    index_path = f"{WIKI_ROOT}{topic}/wiki/index.md"
    if index_path not in paths:
        raise LyceumImportError(f"{topic}: no wiki/index.md -- not a course")

    index_text = _get(index_path)
    index_meta, index_body = split_frontmatter(index_text)

    docs = []
    chapter_ids = []
    source_ids = []

    for path in sorted(p for p in paths if f"/{topic}/wiki/" in p):
        slug = path.rsplit("/", 1)[1][: -len(".md")]
        if slug == "index":
            continue
        meta, body = split_frontmatter(_get(path))
        doc_id = f"chapter:{topic}:{slug}"
        chapter_ids.append(doc_id)
        docs.append({
            "_id": doc_id,
            "type": "chapter",
            "courseId": f"course:{topic}",
            "slug": slug,
            "title": title_of(body, slug_title(slug)),
            "body": body.strip(),
            "sourceIds": [f"source:{topic}:{s}" for s in cited_sources(body)],
            "vaultPath": path,
            "contentHash": content_hash(body),
        })

    for path in sorted(p for p in paths if f"/{topic}/raw/" in p):
        slug = path.rsplit("/", 1)[1][: -len(".md")]
        body = _get(path)
        doc_id = f"source:{topic}:{slug}"
        source_ids.append(doc_id)
        docs.append({
            "_id": doc_id,
            "type": "source",
            "courseId": f"course:{topic}",
            "slug": slug,
            "title": title_of(body, slug_title(slug)),
            "url": source_url(body),
            "retrieved": source_retrieved(body),
            "body": body.strip(),
            "vaultPath": path,
            "contentHash": content_hash(body),
        })

    # Reading order is filename order, and that is the honest answer rather
    # than a fallback. I checked the spine for links first: `wiki/index.md`
    # is an overview page in prose and links no chapter at all -- its only
    # list is the `sources:` frontmatter, which names raw files. So ordering
    # by "where the index mentions this chapter" would have found nothing in
    # every topic and quietly returned filename order anyway, which is the
    # shape `prompt.md` warns about: a rule whose result was fixed in
    # advance. If a spine with real links ever gets written, order from it
    # here and this comment is the thing to delete.
    for position, doc_id in enumerate(chapter_ids):
        for doc in docs:
            if doc["_id"] == doc_id:
                doc["order"] = position

    docs.append({
        "_id": f"course:{topic}",
        "type": "course",
        "slug": topic,
        "title": title_of(index_body, slug_title(topic)),
        "spine": index_body.strip(),
        "chapterIds": chapter_ids,
        "sourceIds": source_ids,
        "generated": index_meta.get("generated"),
        "generatedBy": index_meta.get("generated_by"),
        "vaultPath": index_path,
        "vaultRoot": f"{WIKI_ROOT}{topic}/",
        "contentHash": content_hash(index_body),
    })
    return docs


def build_a9s_course():
    """`work/platform/learn/a9s/` -- hand-written, so a layout of its own."""
    paths = _ls(A9S_ROOT)
    docs, chapter_ids, source_ids = [], [], []

    for path in sorted(p for p in paths if "/theory/" in p):
        slug = path.rsplit("/", 1)[1][: -len(".md")]
        body = _get(path)
        doc_id = f"chapter:a9s:{slug}"
        chapter_ids.append(doc_id)
        docs.append({
            "_id": doc_id,
            "type": "chapter",
            "courseId": "course:a9s",
            "slug": slug,
            "title": title_of(body, slug_title(slug)),
            "body": body.strip(),
            "sourceIds": [],
            "order": len(chapter_ids) - 1,
            "vaultPath": path,
            "contentHash": content_hash(body),
        })

    for path in sorted(p for p in paths if "/resources/" in p):
        slug = path.rsplit("/", 1)[1][: -len(".md")]
        body = _get(path)
        doc_id = f"source:a9s:{slug}"
        source_ids.append(doc_id)
        docs.append({
            "_id": doc_id,
            "type": "source",
            "courseId": "course:a9s",
            "slug": slug,
            "title": title_of(body, slug_title(slug)),
            "url": source_url(body),
            "retrieved": source_retrieved(body),
            "body": body.strip(),
            "vaultPath": path,
            "contentHash": content_hash(body),
        })

    context = ""
    if f"{A9S_ROOT}_context.md" in paths:
        _, context = split_frontmatter(_get(f"{A9S_ROOT}_context.md"))

    docs.append({
        "_id": "course:a9s",
        "type": "course",
        "slug": "a9s",
        "title": title_of(context, "a9s"),
        "spine": context.strip(),
        "chapterIds": chapter_ids,
        "sourceIds": source_ids,
        "generated": None,
        "generatedBy": None,
        "vaultPath": f"{A9S_ROOT}_context.md",
        "vaultRoot": A9S_ROOT,
        "contentHash": content_hash(context),
    })
    return docs


def couch_request(method, path, body=None):
    base = os.environ.get("CDB_BASE", "").rstrip("/")
    user = os.environ.get("CDB_USER", "")
    password = os.environ.get("CDB_PASS", "")
    if not base or not user:
        raise LyceumImportError("CDB_BASE / CDB_USER / CDB_PASS are not set -- "
                                "this runs on the bridge pod, not the runner")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(f"{base}/{path}", data=data, method=method)
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def existing(doc_ids):
    """Current `_rev` and `contentHash` for ids that are already there."""
    status, body = couch_request("POST", f"{DB}/_all_docs?include_docs=true",
                                 {"keys": doc_ids})
    if status != 200:
        raise LyceumImportError(f"reading {DB} failed: HTTP {status} {body}")
    out = {}
    for row in body.get("rows", []):
        doc = row.get("doc")
        if doc:
            out[doc["_id"]] = (doc["_rev"], doc.get("contentHash"))
    return out


def import_docs(docs, dry_run=False):
    """Write the changed documents. Returns (written, unchanged)."""
    known = {} if dry_run else existing([d["_id"] for d in docs])
    changed = []
    unchanged = 0
    for doc in docs:
        rev, old_hash = known.get(doc["_id"], (None, None))
        if old_hash == doc["contentHash"]:
            unchanged += 1
            continue
        if rev:
            doc = dict(doc, _rev=rev)
        changed.append(doc)
    if dry_run or not changed:
        return changed, unchanged
    status, body = couch_request("POST", f"{DB}/_bulk_docs", {"docs": changed})
    if status not in (200, 201):
        raise LyceumImportError(f"writing {DB} failed: HTTP {status} {body}")
    failures = [r for r in body if r.get("error")]
    if failures:
        raise LyceumImportError(f"{len(failures)} document(s) refused: {failures[:3]}")
    return changed, unchanged


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("course", nargs="*", help="course slug(s); default is every one")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be written and write nothing")
    args = parser.parse_args(argv)

    available = topics() + ["a9s"]
    wanted = args.course or available
    unknown = [c for c in wanted if c not in available]
    if unknown:
        print(f"unknown course(s): {', '.join(unknown)} -- have {', '.join(available)}")
        return 1

    total_written = 0
    failed = 0
    for slug in wanted:
        try:
            docs = build_a9s_course() if slug == "a9s" else build_wiki_course(slug)
            written, unchanged = import_docs(docs, dry_run=args.dry_run)
        except LyceumImportError as e:
            print(f"FAILED  {slug} -- {e}")
            failed += 1
            continue
        chapters = sum(1 for d in docs if d["type"] == "chapter")
        sources = sum(1 for d in docs if d["type"] == "source")
        verb = "would write" if args.dry_run else "wrote"
        print(f"{slug}: {chapters} chapter(s), {sources} source(s) -- "
              f"{verb} {len(written)}, {unchanged} unchanged")
        total_written += len(written)

    print(f"{len(wanted) - failed} of {len(wanted)} course(s), "
          f"{total_written} document(s) {'planned' if args.dry_run else 'written'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
