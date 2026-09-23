"""What do I already know about this, including the 87% I was not handed?

My persistent memory is one fact per file in the auto-memory store, and
`MEMORY.md` is the index the CLI injects into every session. That index
is capped -- 200 lines and 25,000 characters, whichever binds first
(`tools.memory_index_health` watches it) -- so the overflow was moved
into `MEMORY-archive.md`, which nothing loads.

Measured cycle 2112: **817 memory files, 108 index lines loaded, 715
only in the archive.** So the working assumption a session runs on --
"my memory is what the index in my context says" -- is wrong about six
facts in seven, and the archive's own header says to grep it, which is
an instruction rather than a mechanism. Idea #186 ("one shared brain
across every surface") is exactly this: a brain whose recall depends on
remembering to look.

This is the look, as one call. It searches every memory file, not the
index, and it says for each hit whether that memory was in the index
I was handed or only in the archive -- because "I already knew this"
and "I have known this for a month and it never reached me" are
different findings and the index cannot tell them apart.

**Terms are ANDed across the whole record** (name, description, body),
which is what makes a two-word query useful in a store this size;
`--any` ORs them. Ranking is where the term matched: the slug counts
3, the description 2, the body 1, summed over terms, ties broken by
newest file first.

**The bound on the output is real and named, not a flinch.** A
one-term query like "memory" matches about a hundred files here, and
printing every body would be ~60KB into the turn that asked. So every
hit is always listed on one line -- nothing is hidden -- and full
bodies print for the top `--full N` (default 5). `--all` prints them
all. That is the interface answer rather than a silent truncation.

Exit 0 means the search ran, hits or none, and the report says which.
**Exit 1 means the store could not be read**, which must never be
confused with "you remember nothing about this" -- that is the whole
failure this tool exists to stop, one level up.

Runs on the bridge pod: the store is on its PVC. From the runner pod
there is no `/data/claude-home` and this exits 1, correctly.
"""
import argparse
import os
import re
import sys

DEFAULT_STORE = os.path.join(
    os.environ.get("CLAUDE_HOME", "/data/claude-home"), "nova-memory")

LOADED_INDEX = "MEMORY.md"
ARCHIVE_INDEX = "MEMORY-archive.md"
INDEX_FILES = (LOADED_INDEX, ARCHIVE_INDEX)

# The index line shape both files use: `- [Title](file.md) — hook`.
_INDEX_LINE = re.compile(r"^- \[[^\]]*\]\(([^)]+)\)")

_FRONT_NAME = re.compile(r"^name:\s*(.+?)\s*$", re.M)
_FRONT_DESC = re.compile(r"^description:\s*(.+?)\s*$", re.M)

WEIGHT_SLUG = 3
WEIGHT_DESC = 2
WEIGHT_BODY = 1


def index_targets(store, name):
    """The filenames one index file points at, or an empty set if absent.

    An index that is missing is not an error here -- it only costs the
    `loaded`/`archived` label. The memory files are the record.
    """
    path = os.path.join(store, name)
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return set()
    return {m.group(1) for m in (_INDEX_LINE.match(line)
                                 for line in text.splitlines()) if m}


def _body(text):
    """Everything after the frontmatter block, or the whole file."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].strip()
    return text.strip()


def load(store):
    """Every memory record in the store, newest first.

    Raises OSError if the store cannot be listed -- the caller turns
    that into exit 1 rather than an empty result.
    """
    names = [n for n in os.listdir(store)
             if n.endswith(".md") and n not in INDEX_FILES]
    records = []
    for name in names:
        path = os.path.join(store, name)
        try:
            text = open(path, encoding="utf-8").read()
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        slug = _FRONT_NAME.search(text)
        desc = _FRONT_DESC.search(text)
        records.append({
            "file": name,
            "slug": slug.group(1) if slug else name[:-3],
            "description": desc.group(1) if desc else "",
            "body": _body(text),
            "mtime": mtime,
        })
    records.sort(key=lambda r: r["mtime"], reverse=True)
    return records


def score(record, terms, require_all=True):
    """Where the terms matched, weighted. 0 means this record is not a hit."""
    slug = (record["slug"] + " " + record["file"]).lower()
    desc = record["description"].lower()
    body = record["body"].lower()
    total = 0
    matched = 0
    # Lower here rather than trusting the caller: `main` already lowers, and a
    # direct `report(["Kubernetes"])` that silently matched nothing would be a
    # negative guaranteed in advance.
    for term in (t.lower() for t in terms):
        hit = 0
        if term in slug:
            hit = max(hit, WEIGHT_SLUG)
        if term in desc:
            hit = max(hit, WEIGHT_DESC)
        if term in body:
            hit = max(hit, WEIGHT_BODY)
        if hit:
            matched += 1
            total += hit
    if require_all and matched != len(terms):
        return 0
    return total


def search(records, terms, require_all=True):
    scored = [(score(r, terms, require_all), r) for r in records]
    hits = [(s, r) for s, r in scored if s > 0]
    hits.sort(key=lambda pair: (-pair[0], -pair[1]["mtime"]))
    return hits


def report(terms, store=DEFAULT_STORE, require_all=True, full=5, out=None):
    out = out if out is not None else sys.stdout
    try:
        records = load(store)
    except OSError as err:
        print("CANNOT READ THE MEMORY STORE at %s -- %s." % (store, err),
              file=out)
        print("This is not 'nothing remembered'. On the runner pod the store "
              "does not exist; run this from the bridge pod.", file=out)
        return 1

    loaded = index_targets(store, LOADED_INDEX)
    archived = index_targets(store, ARCHIVE_INDEX)
    hits = search(records, terms, require_all)

    print("%d memory file(s) in %s; %d indexed in %s (loaded into every "
          "session), %d only in %s (loaded by nothing)."
          % (len(records), store, len(loaded), LOADED_INDEX,
             len(archived), ARCHIVE_INDEX), file=out)
    print("query: %s (%s)" % (" ".join(terms), "all terms" if require_all
                              else "any term"), file=out)

    if not hits:
        print("No memory matches. That is a measured negative over every "
              "file in the store, not over the index.", file=out)
        return 0

    print("%d hit(s), best first:" % len(hits), file=out)
    for rank, (points, rec) in enumerate(hits, 1):
        where = ("loaded" if rec["file"] in loaded
                 else "ARCHIVED -- this one never reached me"
                 if rec["file"] in archived else "not in either index")
        print("  %2d. [%d] %s  (%s)" % (rank, points, rec["file"], where),
              file=out)
        if rec["description"]:
            print("      %s" % rec["description"], file=out)

    show = hits if full is None else hits[:full]
    if show:
        print("", file=out)
        print("Full text of the top %d:" % len(show), file=out)
    for points, rec in show:
        print("", file=out)
        print("===== %s =====" % rec["file"], file=out)
        print(rec["body"], file=out)
    if full is not None and len(hits) > len(show):
        print("", file=out)
        print("%d more hit(s) listed above without their text -- rerun with "
              "--full N or --all." % (len(hits) - len(show)), file=out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("terms", nargs="+", help="search terms")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--any", action="store_true",
                    help="match any term instead of all of them")
    ap.add_argument("--full", type=int, default=5,
                    help="how many hits to print in full (default 5)")
    ap.add_argument("--all", action="store_true",
                    help="print every hit in full")
    args = ap.parse_args(argv)
    terms = [t.lower() for t in args.terms]
    return report(terms, store=args.store, require_all=not args.any,
                  full=None if args.all else args.full)


if __name__ == "__main__":
    sys.exit(main())
