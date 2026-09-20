"""Generate Lyceum practice cards from claim atoms -- build step 8.

The spec is `projects/sokrates/projects/lyceum/lyceum.md`, build sequence
step 8: *"Practice. Generate cards from claims, carrying the claim's GRADE.
Ungrounded claims become 'what would test this?' cards, not fact cards.
Four types: multiple choice, true/false, cloze, written answer."*

    python3 -m tools.lyceum_cards analytics --dry-run
    python3 -m tools.lyceum_cards analytics --claims 40   # a slice, for a live check
    python3 -m tools.lyceum_cards analytics               # write card documents

Runs from the bridge pod, same as `lyceum_claims`: CouchDB through
`CDB_BASE`/`CDB_USER`/`CDB_PASS` and the model through the `claude` CLI,
which is the flat subscription and never the metered API (identity.md
rule 9). `ANTHROPIC_API_KEY` is stripped from the CLI's environment.

**A card never carries more confidence than the claim under it.** That is
the whole point of deriving practice from graded atoms rather than from
chapter prose, and it is mechanical here rather than asked for:

* `grade` is **copied** off the claim, never regenerated. The model is not
  asked how solid anything is; it only writes the question.
* A `grounded` claim becomes one of the four fact types.
* An `ungrounded` claim becomes a **design** card -- *"nothing in the
  sources tests this; what would?"* -- because drilling an untested
  assertion as a fact teaches that the whole course is equally solid,
  which is the failure the credibility system exists to prevent.
* A `contradicted` or `unverified` claim becomes **no card at all**. The
  source says otherwise, or the check never succeeded; either way there is
  nothing here it would be honest to practise. They are counted and
  reported, not silently dropped.

And the hallucination control from step 7 is ported rather than dropped:
every card must carry an `evidence` span **copied verbatim out of the claim
text**, checked mechanically against it. A card whose evidence is not in
its claim is discarded, because the model has then written a question about
something other than what it was given. Each type carries a second
structural check on top (an answer that is really in the text for cloze, an
answer that is really among the options for multiple choice, and so on) --
a card that fails any of them is refused, not repaired.

`--claims N` stops after N claims. A model call per batch is real work
against the subscription, and the spec asks for a prototype on one course
before all six.
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
import urllib.parse
import urllib.request

DB = "lyceum"
DEFAULT_MODEL = "claude-haiku-4-5"

#: Claims per model call. Small enough that one bad batch costs little,
#: large enough that a 460-claim course is not 460 CLI processes.
BATCH = 8

#: Concurrent model calls. Same shape as `lyceum_claims.WORKERS`.
WORKERS = 3

#: The spec's four fact types, plus the one an ungrounded claim becomes.
FACT_TYPES = ("multiple_choice", "true_false", "cloze", "written")
DESIGN_TYPE = "design"

#: Shorter than this is not a span of the claim, it is a word the model
#: happened to reuse. Same floor as step 7's quote check, same reason.
MIN_EVIDENCE_CHARS = 25


class LyceumCardsError(Exception):
    pass


# ---------------------------------------------------------------- CouchDB


def _couch(path, method="GET", body=None):
    base = os.environ.get("CDB_BASE", "").rstrip("/")
    user = os.environ.get("CDB_USER", "")
    password = os.environ.get("CDB_PASS", "")
    if not base or not user:
        raise LyceumCardsError("CDB_BASE / CDB_USER / CDB_PASS are not set -- run this from the bridge pod")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{base}/{path}", data=data, method=method)
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise LyceumCardsError(f"{method} {path} -> {error.code} {error.read()[:200]!r}") from None


def load_claims(slug):
    """Every claim document for a course, in reading order."""
    # Quoted, because CouchDB reads these as JSON out of the query string and
    # a bare `"` in a URL comes back as `invalid UTF-8 JSON`.
    start = urllib.parse.quote(json.dumps(f"claim:{slug}:"))
    end = urllib.parse.quote(json.dumps(f"claim:{slug};"))  # ';' follows ':' in codepoint order
    rows = _couch(f"{DB}/_all_docs?include_docs=true&startkey={start}&endkey={end}")["rows"]
    docs = [row["doc"] for row in rows if row.get("doc")]
    docs.sort(key=lambda d: d["_id"])
    return docs


# --------------------------------------------------------------- sorting


def partition(claims):
    """(fact, design, skipped) -- what each claim's status makes it eligible for."""
    fact, design, skipped = [], [], []
    for claim in claims:
        status = claim.get("status")
        if status == "grounded":
            fact.append(claim)
        elif status == "ungrounded":
            design.append(claim)
        else:
            skipped.append(claim)
    return fact, design, skipped


# --------------------------------------------------------------- prompts


_FACT_SCHEMA = (
    '{"n": <number>, "type": "multiple_choice"|"true_false"|"cloze"|"written",'
    ' "prompt": "<the question>", "answer": "<the correct answer>",'
    ' "options": ["<...>"], "why": "<one or two sentences explaining why>",'
    ' "evidence": "<text copied verbatim from the statement>"}'
)


def build_fact_prompt(claims):
    numbered = "\n".join(f"{n}. {text}" for n, text in claims)
    return (
        "You are writing one practice card for each statement below, for a single adult learner\n"
        "revising his own course. Each statement has already been checked against its source.\n\n"
        "STATEMENTS:\n"
        f"{numbered}\n\n"
        "Choose the card type that actually fits the statement:\n"
        '  "multiple_choice" - when there are plausible wrong answers worth discriminating between\n'
        '  "true_false"      - a single settled proposition; you may state it correctly or falsely\n'
        '  "cloze"           - terminology that has to be exact; write the prompt with ___ for the blank\n'
        '  "written"         - a mechanism or a chain of reasoning, answered in a sentence or two\n\n'
        "Output exactly one line of JSON per statement and nothing else:\n"
        f"{_FACT_SCHEMA}\n\n"
        'Rules. "evidence" must be a span of at least 25 characters copied character for character out of\n'
        "the statement itself -- not paraphrased, not from anywhere else. For \"cloze\" the answer must be a\n"
        "word or phrase that appears verbatim in the statement, and the prompt must contain ___ where it was.\n"
        'For "multiple_choice" give at least three distinct options and make "answer" exactly one of them.\n'
        'For "true_false" the prompt is a statement and the answer is "true" or "false"; use "false" sometimes,\n'
        "by stating it wrongly. For \"written\" the answer is a model answer of a sentence or two.\n"
        'Leave "options" as an empty list for every type except multiple_choice.\n'
        "Output one line per statement, in order, with no commentary."
    )


def build_design_prompt(claims):
    numbered = "\n".join(f"{n}. {text}" for n, text in claims)
    return (
        "Each statement below appears in the learner's own course, and nothing in the sources tests it.\n"
        "It is his assertion, not an established finding, so it must not be drilled as a fact.\n\n"
        "STATEMENTS:\n"
        f"{numbered}\n\n"
        "For each one, write a card that asks what evidence would settle it. The right answer is an\n"
        "evidence design -- what you would measure, on whom, and what result would count against it --\n"
        "not a recollection.\n\n"
        "Output exactly one line of JSON per statement and nothing else:\n"
        '{"n": <number>, "prompt": "<the question, naming what he asserted>",'
        ' "answer": "<a model evidence design, two or three sentences>",'
        ' "why": "<one sentence on why this is untested rather than wrong>",'
        ' "evidence": "<text copied verbatim from the statement>"}\n\n'
        '"evidence" must be a span of at least 25 characters copied character for character out of the\n'
        "statement itself. Output one line per statement, in order, with no commentary."
    )


def ask_model(prompt, model):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")}
    result = subprocess.run(
        ["claude", "-p", "--model", model, "--tools", "", "--no-session-persistence"],
        input=prompt, capture_output=True, text=True, timeout=900, env=env,
    )
    if result.returncode != 0:
        raise LyceumCardsError(f"claude exited {result.returncode}: {(result.stderr or result.stdout).strip()[:400]}")
    return result.stdout


def parse_rows(text):
    """{n: row} from the model's JSON lines, ignoring anything else."""
    out = {}
    for line in text.splitlines():
        line = line.strip().strip("`")
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row.get("n"), int):
            out[row["n"]] = row
    return out


# ---------------------------------------------------------------- checks


def _flat(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def evidence_is_real(evidence, claim_text):
    """Is the evidence span actually in the claim? The hallucination control."""
    flat = _flat(evidence)
    if len(flat) < MIN_EVIDENCE_CHARS:
        return False
    return flat in _flat(claim_text)


def check_fact(row, claim_text):
    """None if the card is sound, else the reason it is refused."""
    card_type = row.get("type")
    if card_type not in FACT_TYPES:
        return f"type {card_type!r}"
    prompt = (row.get("prompt") or "").strip()
    answer = (row.get("answer") or "").strip()
    if not prompt or not answer:
        return "empty prompt or answer"
    if not evidence_is_real(row.get("evidence"), claim_text):
        return "evidence not in the claim"
    if card_type == "cloze":
        if "___" not in prompt:
            return "cloze without a blank"
        if _flat(answer) not in _flat(claim_text):
            return "cloze answer not in the claim"
    elif card_type == "multiple_choice":
        options = [o for o in (row.get("options") or []) if isinstance(o, str) and o.strip()]
        if len(options) < 3:
            return "fewer than three options"
        if len({_flat(o) for o in options}) != len(options):
            return "duplicate options"
        if _flat(answer) not in {_flat(o) for o in options}:
            return "answer is not one of the options"
    elif card_type == "true_false":
        if _flat(answer) not in ("true", "false"):
            return "true/false answer is neither"
    elif card_type == "written" and len(answer) < 40:
        return "model answer too short"
    return None


def check_design(row, claim_text):
    """None if the design card is sound, else the reason it is refused."""
    if not (row.get("prompt") or "").strip():
        return "empty prompt"
    if len((row.get("answer") or "").strip()) < 40:
        return "evidence design too short"
    if not evidence_is_real(row.get("evidence"), claim_text):
        return "evidence not in the claim"
    return None


# ----------------------------------------------------------------- build


def card_id(claim_id):
    return "card:" + claim_id.split(":", 1)[1]


def make_card(claim, row, card_type):
    """One card document. `grade` is copied off the claim, never regenerated."""
    prompt = (row.get("prompt") or "").strip()
    answer = (row.get("answer") or "").strip()
    options = [o.strip() for o in (row.get("options") or []) if isinstance(o, str)] \
        if card_type == "multiple_choice" else []
    return {
        "_id": card_id(claim["_id"]),
        "type": "card",
        "cardType": card_type,
        "courseId": claim["courseId"],
        "chapterId": claim["chapterId"],
        "claimId": claim["_id"],
        "grade": claim.get("grade"),
        "claimStatus": claim.get("status"),
        "prompt": prompt,
        "answer": answer,
        "options": options,
        "why": (row.get("why") or "").strip(),
        "evidence": (row.get("evidence") or "").strip(),
        "sourceIds": list(claim.get("sourceIds") or []),
        "quote": claim.get("quote"),
        "contentHash": hashlib.sha256((prompt + "\x00" + answer).encode()).hexdigest()[:16],
    }


def cards_for_batch(claims, model, design, ask=None):
    """(cards, refusals) for one batch of claims of a single kind."""
    ask = ask or ask_model
    numbered = [(n, c["text"]) for n, c in enumerate(claims)]
    prompt = build_design_prompt(numbered) if design else build_fact_prompt(numbered)
    rows = parse_rows(ask(prompt, model))
    cards, refused = [], []
    for n, claim in enumerate(claims):
        row = rows.get(n)
        if row is None:
            refused.append((claim["_id"], "no answer"))
            continue
        reason = check_design(row, claim["text"]) if design else check_fact(row, claim["text"])
        if reason:
            refused.append((claim["_id"], reason))
            continue
        cards.append(make_card(claim, row, DESIGN_TYPE if design else row["type"]))
    return cards, refused


def generate(claims, model, design, ask=None, out=print):
    batches = [claims[i:i + BATCH] for i in range(0, len(claims), BATCH)]
    if not batches:
        return [], []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda b: cards_for_batch(b, model, design, ask=ask), batches))
    cards = [c for group, _ in results for c in group]
    refused = [r for _, group in results for r in group]
    kind = "design" if design else "fact"
    out(f"  {kind}: {len(cards)} card(s) from {len(claims)} claim(s), {len(refused)} refused")
    return cards, refused


def write_docs(docs):
    """Upsert, keeping `_rev`. Same contract as the importer: re-running is safe."""
    if not docs:
        return 0
    existing = _couch(f"{DB}/_all_docs?include_docs=true", "POST",
                      {"keys": [d["_id"] for d in docs]})["rows"]
    revs = {row["doc"]["_id"]: row["doc"]["_rev"] for row in existing if row.get("doc")}
    payload = [dict(d, _rev=revs[d["_id"]]) if d["_id"] in revs else d for d in docs]
    result = _couch(f"{DB}/_bulk_docs", "POST", {"docs": payload})
    failed = [r for r in result if r.get("error")]
    if failed:
        raise LyceumCardsError(f"{len(failed)} document(s) refused: {failed[:3]}")
    return len(payload)


def distribution(cards):
    counts = {}
    for card in cards:
        counts[card["cardType"]] = counts.get(card["cardType"], 0) + 1
    return counts


def run(course_slug, model=DEFAULT_MODEL, limit=None, dry_run=False, ask=None, out=print):
    claims = load_claims(course_slug)
    if limit:
        claims = claims[:limit]
    fact, design, skipped = partition(claims)
    out(f"{course_slug} -- {len(claims)} claim(s): {len(fact)} gradeable, "
        f"{len(design)} ungrounded, {len(skipped)} not practisable")

    cards, refused = [], []
    for group, is_design in ((fact, False), (design, True)):
        got, no = generate(group, model, is_design, ask=ask, out=out)
        cards.extend(got)
        refused.extend(no)

    counts = distribution(cards)
    out(f"{len(cards)} card(s): " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if refused:
        out(f"{len(refused)} refused, first few: " + "; ".join(f"{i} ({r})" for i, r in refused[:3]))
    if dry_run:
        out("dry run -- nothing written")
        return cards
    out(f"wrote {write_docs(cards)} card document(s)")
    return cards


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("course", help="course slug, e.g. analytics")
    parser.add_argument("--claims", type=int, default=None,
                        help="stop after this many claims (a prototype slice)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        run(args.course, model=args.model, limit=args.claims, dry_run=args.dry_run)
    except LyceumCardsError as error:
        print(f"FAILED -- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
