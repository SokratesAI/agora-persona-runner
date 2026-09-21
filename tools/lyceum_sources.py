"""Find real scholarly sources for a Lyceum topic -- build step 9's ingestion half.

The spec is `projects/sokrates/projects/lyceum/lyceum.md`, build sequence
step 9: *"The build cycle for new topics -- hard. Backward design ...
Source ingestion: OpenAlex / Semantic Scholar / Crossref first, URL
readability second, YouTube via share-from-phone only."*

    python3 -m tools.lyceum_sources "cohort analysis retention" --topic analytics --dry-run
    python3 -m tools.lyceum_sources "double diamond design process" --topic design --limit 8

Runs from the bridge pod: it reads and writes the vault through
`/app/bridge/vault_tool.py`. No API key is needed and none is read --
OpenAlex and Crossref are both open and answer an unauthenticated request.

No contact address is sent to either API -- see `USER_AGENT` below.

**It writes `raw/` and nothing else.** A topic's `raw/` folder is the
source material every later stage reads: `tools.llm_wiki` turns it into
`wiki/` pages, `tools.lyceum_import` turns those into a course, and
`tools.lyceum_claims` traces every claim back to the `raw/` file it came
from. So dropping real sources into `raw/` is the whole ingestion step --
there is no bespoke pipeline here on purpose, which is what the spec
means by reusing the existing job infrastructure.

**It never overwrites a `raw/` file that already exists.** Source material
is the one thing in this chain that is not derived, so a re-run can add to
a topic and can never quietly rewrite what a claim already cites.

**What it will not do: assert a citation from model memory.** No model is
called here at all. Every field written -- title, authors, year, DOI, venue,
abstract -- is copied out of the API response for one work, and the file
records which API answered and when. That is the same rule build step 7
follows, applied one stage earlier: the provenance is read, never recalled.

**Abstracts, not full text, and the file says so.** OpenAlex ships an
abstract as an inverted index and Crossref ships one as JATS-ish markup;
both are reconstructed here verbatim. A work whose abstract is missing or
shorter than `--min-abstract` is dropped rather than written as a stub,
and the run prints how many went that way -- a `raw/` file with a title and
no text teaches the wiki generator nothing and would still be cited.

Exit codes: 0 means sources were written or every match was already there,
1 means an API or the vault could not be read, 2 means the query matched
nothing usable (which is a finding, not a clean run).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

VAULT_TOOL = "/app/bridge/vault_tool.py"
WIKI_ROOT = "projects/sokrates/wiki/"

# OpenAlex and Crossref both offer a faster "polite pool" to a request that
# names a contact address. Deliberately not used: the only address this box
# holds is the owner's personal one, and putting it in a query string sends
# it to two services that have no reason to hold it. The default pool
# answered every request this tool makes, measured 2026-09-21.
USER_AGENT = "lyceum-sources/1.0 (+https://github.com/SokratesAI)"

TIMEOUT = 30


class SourceError(RuntimeError):
    """An API or the vault could not be read."""


# ---------------------------------------------------------------- pure parts


def slugify(title: str) -> str:
    """A stable, readable file name for a work's title."""
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    words = s.split("-")
    # Long academic titles make unreadable file names; the first eight words
    # of a title are what a person recognises it by.
    return "-".join(words[:8]) or "untitled"


def reconstruct_abstract(inverted: dict | None) -> str:
    """OpenAlex ships an abstract as {word: [positions]}; put it back in order."""
    if not inverted:
        return ""
    slots: dict[int, str] = {}
    for word, positions in inverted.items():
        for pos in positions:
            slots[pos] = word
    if not slots:
        return ""
    return " ".join(slots[i] for i in sorted(slots))


def strip_markup(text: str) -> str:
    """Crossref abstracts carry JATS tags; keep the words, drop the tags."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def dedupe(works: list[dict]) -> list[dict]:
    """One row per work: a DOI is the identity when there is one, else the title.

    OpenAlex and Crossref index the same literature, so a two-API run
    returns the same paper twice far more often than not.
    """
    seen_doi: set[str] = set()
    seen_title: set[str] = set()
    out = []
    for w in works:
        doi = (w.get("doi") or "").lower().strip()
        key_title = normalise_title(w.get("title", ""))
        if doi and doi in seen_doi:
            continue
        if not doi and key_title and key_title in seen_title:
            continue
        # A DOI match still shadows a later title-only duplicate.
        if key_title and key_title in seen_title:
            continue
        if doi:
            seen_doi.add(doi)
        if key_title:
            seen_title.add(key_title)
        out.append(w)
    return out


def render_source(work: dict, retrieved: str) -> str:
    """The markdown a `raw/` file holds, in the convention the wikis already use."""
    lines = [f"# {work['title']}", ""]
    if work.get("url"):
        lines.append(f"Source: {work['url']}")
    lines.append(f"Retrieved: {retrieved}")
    lines.append(f"Via: {work['api']}")
    lines.append("")
    lines.append("## Reference")
    lines.append("")
    authors = work.get("authors") or []
    if authors:
        shown = ", ".join(authors[:10])
        if len(authors) > 10:
            shown += f", and {len(authors) - 10} more"
        lines.append(f"- **Authors**: {shown}")
    if work.get("year"):
        lines.append(f"- **Year**: {work['year']}")
    if work.get("venue"):
        lines.append(f"- **Published in**: {work['venue']}")
    if work.get("doi"):
        lines.append(f"- **DOI**: {work['doi']}")
    if work.get("cited_by") is not None:
        lines.append(f"- **Cited by**: {work['cited_by']}")
    lines.append("")
    lines.append("## Abstract")
    lines.append("")
    lines.append(work["abstract"])
    lines.append("")
    lines.append(
        "*This file is the work's published abstract and metadata, copied "
        "verbatim from the API named above. It is not the full text.*"
    )
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ fetching


def _get_json(url: str, fetch=None) -> dict:
    if fetch is not None:
        return fetch(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network shape
        raise SourceError(f"{url} answered HTTP {exc.code}") from exc
    except Exception as exc:  # pragma: no cover - network shape
        raise SourceError(f"{url} could not be read: {exc}") from exc


def openalex_search(query: str, limit: int, fetch=None) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "search": query,
            "per-page": min(max(limit * 3, limit), 50),
            "sort": "relevance_score:desc",
        }
    )
    data = _get_json(f"https://api.openalex.org/works?{params}", fetch)
    out = []
    for w in data.get("results", []):
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        host = (w.get("primary_location") or {}).get("source") or {}
        out.append(
            {
                "title": (w.get("title") or "").strip(),
                "year": w.get("publication_year"),
                "authors": [
                    (a.get("author") or {}).get("display_name", "")
                    for a in (w.get("authorships") or [])
                    if (a.get("author") or {}).get("display_name")
                ],
                "doi": doi,
                "venue": host.get("display_name") or "",
                "cited_by": w.get("cited_by_count"),
                "url": w.get("doi") or (w.get("ids") or {}).get("openalex", ""),
                "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
                "api": "OpenAlex",
            }
        )
    return out


def crossref_search(query: str, limit: int, fetch=None) -> list[dict]:
    params = urllib.parse.urlencode(
        {"query": query, "rows": min(max(limit * 3, limit), 50)}
    )
    data = _get_json(f"https://api.crossref.org/works?{params}", fetch)
    out = []
    for w in (data.get("message") or {}).get("items", []):
        title = " ".join(w.get("title") or []).strip()
        if not title:
            continue
        issued = ((w.get("issued") or {}).get("date-parts") or [[None]])[0]
        out.append(
            {
                "title": title,
                "year": issued[0] if issued else None,
                "authors": [
                    " ".join(x for x in [a.get("given"), a.get("family")] if x)
                    for a in (w.get("author") or [])
                ],
                "doi": w.get("DOI") or "",
                "venue": " ".join(w.get("container-title") or []),
                "cited_by": w.get("is-referenced-by-count"),
                "url": w.get("URL") or "",
                "abstract": strip_markup(w.get("abstract") or ""),
                "api": "Crossref",
            }
        )
    return out


# ---------------------------------------------------------------- vault side


def vault_ls(folder: str) -> list[str]:
    proc = subprocess.run(
        [sys.executable, VAULT_TOOL, "ls", folder],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # A folder that does not exist yet is the normal case for a new topic.
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def vault_put(path: str, body: str) -> None:
    tmp = f"/tmp/lyceum-source-{os.getpid()}.md"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(body)
    proc = subprocess.run(
        [sys.executable, VAULT_TOOL, "put", path, tmp],
        capture_output=True,
        text=True,
    )
    os.unlink(tmp)
    if proc.returncode != 0:
        raise SourceError(f"could not write {path}: {proc.stderr.strip()}")


# ------------------------------------------------------------------- the run


def collect(query: str, limit: int, apis: list[str], min_abstract: int, fetch=None):
    """Return (kept, dropped_no_abstract, per_api_counts)."""
    found: list[dict] = []
    counts: dict[str, int] = {}
    for api in apis:
        rows = openalex_search(query, limit, fetch) if api == "openalex" else crossref_search(query, limit, fetch)
        counts[api] = len(rows)
        found.extend(rows)
    found = dedupe(found)
    kept, dropped = [], []
    for w in found:
        if len(w.get("abstract") or "") >= min_abstract:
            kept.append(w)
        else:
            dropped.append(w)
    # Relevance order is the API's own ranking and it is the best signal here.
    # An earlier version re-sorted by citation count and that was a real
    # regression rather than a refinement: for "cohort analysis customer
    # retention" it promoted the most-cited unrelated papers in the result set
    # and pushed the on-topic ones off the end.
    return kept[:limit], dropped, counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("query", help="what the topic is about, in plain words")
    ap.add_argument("--topic", required=True, help="wiki topic slug to write raw/ under")
    ap.add_argument("--limit", type=int, default=6, help="how many sources to keep")
    ap.add_argument("--min-abstract", type=int, default=200, help="shortest abstract worth keeping, in characters")
    ap.add_argument("--api", choices=["openalex", "crossref", "both"], default="both")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written, write nothing")
    args = ap.parse_args(argv)

    apis = ["openalex", "crossref"] if args.api == "both" else [args.api]
    try:
        kept, dropped, counts = collect(args.query, args.limit, apis, args.min_abstract)
    except SourceError as exc:
        print(f"UNREADABLE: {exc}")
        return 1

    for api, n in counts.items():
        print(f"{api}: {n} result(s)")
    print(f"{len(dropped)} dropped for no usable abstract (< {args.min_abstract} chars)")

    if not kept:
        print(f"NOTHING USABLE for {args.query!r} -- no work carried an abstract this run")
        return 2

    folder = f"{WIKI_ROOT}{args.topic}/raw/"
    existing = {p.rsplit("/", 1)[-1] for p in vault_ls(folder)}
    retrieved = datetime.date.today().isoformat()

    wrote = skipped = 0
    for w in kept:
        name = f"{slugify(w['title'])}.md"
        path = f"{folder}{name}"
        if name in existing:
            print(f"  already there  {name}")
            skipped += 1
            continue
        body = render_source(w, retrieved)
        if args.dry_run:
            print(f"  would write    {name}  ({len(body)} bytes, {w['api']}, {w.get('year')})")
        else:
            try:
                vault_put(path, body)
            except SourceError as exc:
                print(f"  FAILED         {name}: {exc}")
                return 1
            print(f"  wrote          {name}  ({len(body)} bytes, {w['api']}, {w.get('year')})")
        wrote += 1

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {wrote}, {skipped} already in {folder}")
    if wrote and not args.dry_run:
        print(f"next: python3 -m tools.llm_wiki --stale   then   python3 -m tools.lyceum_import {args.topic}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
