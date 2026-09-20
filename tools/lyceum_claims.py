"""Split a Lyceum course's chapters into claim atoms and GRADE them -- build step 7.

The spec is `projects/sokrates/projects/lyceum/lyceum.md`, build sequence
step 7: *"Claim extraction and GRADE marking -- hard, and the main risk.
Split imported wiki pages into claim atoms, attach each to its `raw/`
source, assign a GRADE level. Verify load-bearing claims at build time and
the rest on tap; never assert a citation from model memory. This is the
piece most likely to underdeliver and the one to prototype on a single
course before doing all six."*

    python3 -m tools.lyceum_claims analytics --dry-run
    python3 -m tools.lyceum_claims analytics --chapters 2   # a slice, for a live check
    python3 -m tools.lyceum_claims analytics               # write claim documents

Runs from the bridge pod: CouchDB through `CDB_BASE`/`CDB_USER`/`CDB_PASS`
and the model through the `claude` CLI, which is the flat subscription and
never the metered API (identity.md rule 9). `ANTHROPIC_API_KEY` is stripped
from the CLI's environment so that cannot change by accident.

**Nothing here trusts the model with a citation.** That is the spec's own
line -- *"never assert a citation from model memory"* -- and it is the whole
design, in three parts:

1. **Which source a claim belongs to is read off the page, not asked.** A
   generated wiki chapter carries `[source: x.md]` markers; an atom's
   sources are the markers inside it, else the markers in its paragraph.
   The model is never asked "where does this come from".
2. **The model only ever judges text put in front of it.** It gets the
   source body verbatim and the atoms citing it, and answers supported /
   not_found / contradicted with a quote it must copy out of that body.
3. **The quote is then checked against the body mechanically.** A quote
   that is not in the source (whitespace-normalised) makes the verdict
   worthless, so the verdict is discarded and the claim stays `unverified`
   at GRADE `low`. This is the negative control for hallucination, and it
   runs on every single verdict rather than on a sample.

GRADE, per the spec's principle 5 -- high / moderate / low, plus ungrounded,
never percentages:

    high        supported, quote verified in the source, source has a URL
                and a retrieval date (a document that was actually fetched)
    moderate    supported, quote verified, source is a vault note with no URL
    low         model said supported but the quote is not in the source
    ungrounded  no source cited, or the source does not carry the claim

`status` carries the spec's three states from this first write --
`unverified` (not checked), `ungrounded` (checked, nothing found),
`contradicted` (checked, evidence goes the other way) -- plus `grounded`
for the supported case, which the three-state list needs as its fourth and
which the migration would otherwise have to add later.

`--chapters N` stops after N chapters. It is not a safety cap on my own
capability: a model call per (chapter, source) pair is real work against
the subscription, and the spec asks for a prototype on one course before
all six, so a slice is how the first live run is checked at all.
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

DB = "lyceum"
DEFAULT_MODEL = "claude-haiku-4-5"

#: How many (chapter, source) judging calls run at once. Same shape as
#: `llm_wiki`'s page writer: enough to not be serial, few enough that a
#: course does not open thirty CLI processes.
WORKERS = 3

#: A sentence shorter than this is a fragment, a heading echo or a lead-in
#: ("There are three of these:"), not a claim that can be true or false.
MIN_ATOM_CHARS = 40


class LyceumClaimsError(Exception):
    pass


# ---------------------------------------------------------------- CouchDB


def _couch(path, method="GET", body=None):
    base = os.environ.get("CDB_BASE", "").rstrip("/")
    user = os.environ.get("CDB_USER", "")
    password = os.environ.get("CDB_PASS", "")
    if not base or not user:
        raise LyceumClaimsError("CDB_BASE / CDB_USER / CDB_PASS are not set -- run this from the bridge pod")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{base}/{path}", data=data, method=method)
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise LyceumClaimsError(f"{method} {path} -> {error.code} {error.read()[:200]!r}") from None


def load_course(slug):
    """The course document plus its chapters and sources, keyed by id."""
    course = _couch(f"{DB}/course:{slug}")
    ids = list(course.get("chapterIds", [])) + list(course.get("sourceIds", []))
    rows = _couch(f"{DB}/_all_docs?include_docs=true", "POST", {"keys": ids})["rows"]
    docs = {row["doc"]["_id"]: row["doc"] for row in rows if row.get("doc")}
    chapters = [docs[i] for i in course.get("chapterIds", []) if i in docs]
    chapters.sort(key=lambda d: d.get("order", 0))
    sources = {i: docs[i] for i in course.get("sourceIds", []) if i in docs}
    return course, chapters, sources


# ------------------------------------------------------------ extraction


def strip_body(body):
    """The prose of a chapter: no frontmatter, no fenced code, no headings."""
    body = re.sub(r"^---\n.*?\n---\n", "", body, flags=re.S)
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    kept = [line for line in body.splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(kept)


CITATION = re.compile(r"\[source:\s*([^\]]+?)\.md\s*\]")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def atoms(body, course_slug):
    """Claim atoms in reading order: (text, [source ids cited for it]).

    A paragraph is the citation scope. Generated pages cite at the end of a
    sentence or at the end of the paragraph the sentence sits in, so an atom
    takes the markers inside itself and falls back to its paragraph's -- and
    an atom in a paragraph that cites nothing gets nothing rather than the
    page's list. The page's `sources:` frontmatter is deliberately not a
    fallback: `lyceum_import.cited_sources` already records that the
    generator writes the topic's whole source list into every page, so it
    says "these were in view", not "this page uses these".
    """
    found = []
    for paragraph in re.split(r"\n\s*\n", strip_body(body)):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        para_sources = [f"source:{course_slug}:{n.strip()}" for n in CITATION.findall(paragraph)]
        for raw in SENTENCE_END.split(re.sub(r"\s+", " ", paragraph)):
            own = [f"source:{course_slug}:{n.strip()}" for n in CITATION.findall(raw)]
            text = CITATION.sub("", raw).strip()
            text = re.sub(r"^[-*+]\s+|^\d+\.\s+|^>\s*", "", text).strip()
            text = re.sub(r"\s{2,}", " ", text)
            if len(text) < MIN_ATOM_CHARS or text.endswith(":"):
                continue
            cited = own or para_sources
            seen, ordered = set(), []
            for source_id in cited:
                if source_id not in seen:
                    seen.add(source_id)
                    ordered.append(source_id)
            found.append((text, ordered))
    return found


# --------------------------------------------------------------- judging


def build_prompt(source, claims):
    """One source's text, and the atoms that cite it, numbered."""
    numbered = "\n".join(f"{n}. {text}" for n, text in claims)
    return (
        "You are checking whether a source document supports each of the statements below.\n"
        "Judge ONLY against the source text given here. Do not use anything you know from elsewhere.\n\n"
        f"SOURCE: {source.get('title') or source['slug']}\n"
        "-----BEGIN SOURCE-----\n"
        f"{source.get('body', '')}\n"
        "-----END SOURCE-----\n\n"
        "STATEMENTS:\n"
        f"{numbered}\n\n"
        "For each statement, output exactly one line of JSON and nothing else:\n"
        '{"n": <number>, "verdict": "supported"|"not_found"|"contradicted", "quote": "<text copied verbatim from the source>"}\n\n'
        'Use "supported" only when the source itself states or directly implies it, and set "quote" to the\n'
        "exact span of the source that carries it -- copied character for character, not paraphrased.\n"
        'Use "not_found" when the source simply does not address it, and "contradicted" when the source says\n'
        'otherwise; for "contradicted" the quote is the span that conflicts. For "not_found" use an empty quote.\n'
        "Output one line per statement, in order, with no commentary."
    )


def ask_model(prompt, model):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")}
    result = subprocess.run(
        ["claude", "-p", "--model", model, "--tools", "", "--no-session-persistence"],
        input=prompt, capture_output=True, text=True, timeout=900, env=env,
    )
    if result.returncode != 0:
        raise LyceumClaimsError(f"claude exited {result.returncode}: {(result.stderr or result.stdout).strip()[:400]}")
    return result.stdout


def parse_verdicts(text):
    """{n: (verdict, quote)} from the model's lines, ignoring anything else."""
    out = {}
    for line in text.splitlines():
        line = line.strip().strip("`")
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row.get("n"), int) and row.get("verdict") in ("supported", "not_found", "contradicted"):
            out[row["n"]] = (row["verdict"], (row.get("quote") or "").strip())
    return out


def _flat(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def quote_is_real(quote, source_body):
    """Is the quote actually in the source? The hallucination control.

    Whitespace-normalised and case-insensitive, because a model reflowing a
    line break is not inventing a citation. Anything shorter than a few
    words is not evidence of having read the source at all.
    """
    flat_quote = _flat(quote)
    if len(flat_quote) < 25:
        return False
    return flat_quote in _flat(source_body)


def grade(verdict, quote_ok, source):
    """(status, grade) for one judged atom. The spec's levels, never a percentage."""
    if verdict == "contradicted":
        return "contradicted", "ungrounded" if not quote_ok else "low"
    if verdict == "not_found":
        return "ungrounded", "ungrounded"
    if not quote_ok:
        # It said supported and could not produce the sentence it read that
        # in. That is exactly the failure this step exists to prevent, so it
        # does not count as checked.
        return "unverified", "low"
    return "grounded", "high" if source.get("url") and source.get("retrieved") else "moderate"


# ----------------------------------------------------------------- build


def claims_for_chapter(chapter, sources, model, ask=None, out=print):
    """Claim documents for one chapter, judged against the sources it cites."""
    ask = ask or ask_model
    course_slug = chapter["courseId"].split(":", 1)[1]
    found = atoms(chapter["body"], course_slug)

    docs = []
    for index, (text, cited) in enumerate(found):
        docs.append({
            "_id": f"claim:{course_slug}:{chapter['slug']}:{index:03d}",
            "type": "claim",
            "courseId": chapter["courseId"],
            "chapterId": chapter["_id"],
            "order": index,
            "text": text,
            "sourceIds": cited,
            "status": "unverified" if cited else "ungrounded",
            "grade": "ungrounded",
            "quote": None,
            "checkedAgainst": None,
            "contentHash": hashlib.sha256(text.encode()).hexdigest()[:16],
        })

    by_source = {}
    for doc in docs:
        for source_id in doc["sourceIds"]:
            if source_id in sources:
                by_source.setdefault(source_id, []).append(doc)

    def judge(source_id):
        source = sources[source_id]
        pending = by_source[source_id]
        numbered = [(n, d["text"]) for n, d in enumerate(pending)]
        verdicts = parse_verdicts(ask(build_prompt(source, numbered), model))
        for n, doc in enumerate(pending):
            if doc["status"] == "grounded" or n not in verdicts:
                continue
            verdict, quote = verdicts[n]
            ok = quote_is_real(quote, source.get("body", ""))
            status, level = grade(verdict, ok, source)
            # A claim citing two sources is grounded if either carries it, so
            # a later not_found must not overwrite an earlier grounded one.
            if doc["status"] == "grounded":
                continue
            doc.update(status=status, grade=level, checkedAgainst=source_id,
                       quote=quote if ok else None)

    if by_source:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(judge, sorted(by_source)))

    out(f"  {chapter['slug']}: {len(docs)} atoms, {len(by_source)} source(s) judged")
    return docs


def write_docs(docs):
    """Upsert, keeping `_rev`. Same contract as the importer: re-running is safe."""
    if not docs:
        return 0
    existing = _couch(f"{DB}/_all_docs?include_docs=true", "POST",
                      {"keys": [d["_id"] for d in docs]})["rows"]
    revs = {row["doc"]["_id"]: row["doc"]["_rev"] for row in existing if row.get("doc")}
    payload = []
    for doc in docs:
        if doc["_id"] in revs:
            doc = dict(doc, _rev=revs[doc["_id"]])
        payload.append(doc)
    result = _couch(f"{DB}/_bulk_docs", "POST", {"docs": payload})
    failed = [r for r in result if r.get("error")]
    if failed:
        raise LyceumClaimsError(f"{len(failed)} document(s) refused: {failed[:3]}")
    return len(payload)


def distribution(docs):
    """The course page's GRADE distribution -- levels, never percentages."""
    counts = {}
    for doc in docs:
        counts[doc["grade"]] = counts.get(doc["grade"], 0) + 1
    return counts


def run(course_slug, model=DEFAULT_MODEL, limit=None, dry_run=False, ask=None, out=print):
    course, chapters, sources = load_course(course_slug)
    out(f"{course['title']} -- {len(chapters)} chapter(s), {len(sources)} source(s)")
    if limit:
        chapters = chapters[:limit]
        out(f"  prototype slice: first {len(chapters)} chapter(s)")

    docs = []
    for chapter in chapters:
        docs.extend(claims_for_chapter(chapter, sources, model, ask=ask, out=out))

    counts = distribution(docs)
    out(f"{len(docs)} claim(s): " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if dry_run:
        out("dry run -- nothing written")
        return docs
    out(f"wrote {write_docs(docs)} claim document(s)")
    return docs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("course", help="course slug, e.g. analytics")
    parser.add_argument("--chapters", type=int, default=None,
                        help="stop after this many chapters (a prototype slice)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        run(args.course, model=args.model, limit=args.chapters, dry_run=args.dry_run)
    except LyceumClaimsError as error:
        print(f"FAILED -- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
