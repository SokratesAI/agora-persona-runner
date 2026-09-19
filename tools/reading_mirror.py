"""Copy the documents written for the owner to read into his own vault.

Issue #238. Since 2026-09-02 `projects/sokrates/projects/nova/` routes to
Nova's database, so nothing in it reaches Obsidian or his phone. On
2026-09-15 he went looking for the goal drafts and `project-goals.md` in
Obsidian and found neither. His ask: *"make anything meant for me to read
on my phone land in my vault."*

This writes a reading copy of every document in that folder to
`MIRROR_PREFIX`, which is outside both Nova folders and so lands in the
`obsidian` database. Run it from the bridge pod; every cycle runs it once
(prompt.md step 1a), which is what keeps the copies current.

What is copied is decided by exclusion, not by a list of names, because a
list of what to mirror goes stale the day a cycle adds a document. The
capture files are left out: he uses them through the app, the boards are
generated views of ~870 KB, and a copy of `notes.md` would invite him to
type into a file nothing reads. `_context.md` is left out because he does
not read it.

Two guards, both against the conflict errors that made him move the folder
out of Obsidian in the first place:

- **A document still being edited is not copied.** Its source must be
  unchanged for `SETTLE_MINUTES`, so a document a cycle is rewriting in
  bursts lands once, not five times while he may have it open.
- **A copy he edited is never overwritten.** Each copy carries the sha256
  of the source it was made from in its frontmatter (`mirror_sha`). If the
  copy no longer matches that sha it has been edited in Obsidian, and it is
  reported and left alone.

A copy whose source was deleted is reported, not deleted.

Exit 0: every document was copied, current, settling, or deliberately left.
Exit 1: the folder could not be listed or a read/write failed.
"""
import argparse
import hashlib
import sys
import time

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

SOURCE_PREFIX = "projects/sokrates/projects/nova/"
MIRROR_PREFIX = "projects/sokrates/projects/nova-reading/"
EXCLUDED = frozenset({
    "_context.md", "issues.md", "ideas.md", "notes.md", "proposed-projects.md",
})
SETTLE_MINUTES = 30
SHA_KEY = "mirror_sha"
SOURCE_KEY = "mirror_of"
BRIDGE_DIR = "/app/bridge"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_mirror(source_text, source_path):
    """The copy: the source with two frontmatter keys added."""
    keys = f"{SOURCE_KEY}: {source_path}\n{SHA_KEY}: {sha(source_text)}\n"
    if source_text.startswith("---\n"):
        return "---\n" + keys + source_text[4:]
    return "---\n" + keys + "---\n\n" + source_text


def from_mirror(mirror_text):
    """(recorded sha, the source text the copy was made from) -- or
    (None, None) when the copy's frontmatter is not the shape to_mirror
    writes, which counts as edited."""
    lines = mirror_text.split("\n")
    if (len(lines) < 4 or lines[0] != "---"
            or not lines[1].startswith(SOURCE_KEY + ": ")
            or not lines[2].startswith(SHA_KEY + ": ")):
        return None, None
    recorded = lines[2][len(SHA_KEY) + 2:]
    rest = "\n".join(lines[3:])
    if rest.startswith("---\n\n"):
        return recorded, rest[5:]
    return recorded, "---\n" + rest


def plan(client, now_ms, settle_minutes=SETTLE_MINUTES):
    """[(verdict, name, content_or_None, rev)]. verdict is one of COPY,
    CURRENT, SETTLING, EDITED, ORPHAN; rev is the copy's revision, or None
    when there is no copy yet."""
    sources = client.file_docs(SOURCE_PREFIX)
    mirrors = client.file_docs(MIRROR_PREFIX)
    out = []
    names = set()
    for doc_id in sorted(sources):
        name = doc_id[len(SOURCE_PREFIX):]
        if "/" in name or not name.endswith(".md") or name in EXCLUDED:
            continue
        names.add(name)
        src_path = SOURCE_PREFIX + name
        text = client.read(src_path)
        if text is None:
            raise RuntimeError(f"could not read {src_path}")
        # The revision rides along to the write, so a copy he edits between
        # this read and that write is refused (409) instead of overwritten.
        # Read even when the listing has no copy: a deleted copy is a
        # tombstone with a live revision, and recreating it has to carry it.
        existing, rev = client.read_rev(MIRROR_PREFIX + name)
        if existing is not None:
            recorded, original = from_mirror(existing)
            if recorded is None or sha(original) != recorded:
                out.append(("EDITED", name, None, rev))
                continue
            if recorded == sha(text):
                out.append(("CURRENT", name, None, rev))
                continue
        age_min = (now_ms - (sources[doc_id].get("mtime") or 0)) / 60000
        if age_min < settle_minutes:
            out.append(("SETTLING", name, None, rev))
            continue
        out.append(("COPY", name, to_mirror(text, src_path), rev))
    for doc_id in sorted(mirrors):
        name = doc_id[len(MIRROR_PREFIX):]
        if name not in names and name not in EXCLUDED:
            out.append(("ORPHAN", name, None, None))
    return out


def _client():
    sys.path.insert(0, BRIDGE_DIR)
    import vault_tool  # noqa: E402 -- bridge pod only
    return vault_tool.VaultClient()


def main(argv=None, client=None, now_ms=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        client = client or _client()
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        rows = plan(client, now_ms)
    except Exception as e:  # noqa: BLE001 -- any failure is "no instrument"
        print(f"reading_mirror: COULD NOT READ: {e}")
        return 1
    failed = 0
    for verdict, name, content, rev in rows:
        note = {
            "COPY": "copied",
            "CURRENT": "copy is current",
            "SETTLING": f"changed in the last {SETTLE_MINUTES} min, next run",
            "EDITED": "copy was edited in Obsidian -- left alone",
            "ORPHAN": "source is gone -- copy kept",
        }[verdict]
        if verdict == "COPY" and args.dry_run:
            note = "would copy"
        elif verdict == "COPY":
            result = client.write(MIRROR_PREFIX + name, content, if_rev=rev)
            if result != "written":
                failed += 1
                note = f"WRITE FAILED: {result}"
        print(f"{verdict:8} {name}: {note}")
    counts = {v: sum(1 for r in rows if r[0] == v)
              for v in ("COPY", "CURRENT", "SETTLING", "EDITED", "ORPHAN")}
    print("reading_mirror: " + ", ".join(f"{k.lower()} {v}"
                                         for k, v in counts.items())
          + f" -> {MIRROR_PREFIX}" + (" (dry run)" if args.dry_run else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
