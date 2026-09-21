"""Re-check Lyceum practice cards from outside the tool that wrote them.

    python3 -m tools.lyceum_card_audit analytics
    python3 -m tools.lyceum_card_audit --all
    python3 -m tools.lyceum_card_audit analytics --control

`tools.lyceum_cards` refuses a card whose evidence span is not in its claim,
and it copies the grade across rather than asking the model for one. Both of
those are checks the generator runs on its own output, in the same process,
against the same in-memory objects -- so a bug in the generator's idea of
"the claim" passes them silently. This reads the stored documents back and
asks the same questions of the database, which is the only copy a reader
ever sees.

Five invariants, each one the spec's own:

* a card's ``grade`` equals the grade on the claim it names (step 7's whole
  point is that a settled finding and an untested assertion are not the same
  card, so a card that upgrades itself is the failure mode that matters),
* the card names a claim that exists,
* its ``evidence`` span appears in that claim's text,
* a cloze prompt has a blank and its answer comes out of the claim,
* a multiple-choice answer is among its own options.

Comparison is on the words, not the typography -- ``norm`` drops markdown
bold and case. The extractor keeps the chapter's ``**Bidirectional
causation.**`` verbatim and a model writing a span lowercases a leading
capital; neither is a card quoting something it was not given, and a checker
that reports those as findings trains a reader to ignore it.

``--control`` breaks one card of each kind in memory before checking, and is
the reason a clean run means anything: a clean sweep over untouched data
says nothing until the same code has been shown to fail on data it should
fail on. Nothing is ever written back -- this is a read-only audit, and the
mutation lives in a copy.

Runs from the bridge pod, same as `lyceum_cards`: CouchDB through
`CDB_BASE`/`CDB_USER`/`CDB_PASS`. No model calls, so it costs one range read
per course and is cheap enough to run over all six.

Exit 0 means every card checked holds all five. Exit 2 means at least one
does not. Exit 1 means something was unreadable, which never reads as clean.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from tools.lyceum_cards import DB, LyceumCardsError, _couch, load_claims

import json
import urllib.parse


def norm(text):
    """The words of a span, without the typography two writers disagree on."""
    return " ".join((text or "").replace("*", "").replace("’", "'").lower().split())


def load_cards(slug):
    """Every card document for a course, in id order."""
    start = urllib.parse.quote(json.dumps(f"card:{slug}:"))
    end = urllib.parse.quote(json.dumps(f"card:{slug};"))  # ';' follows ':' in codepoint order
    rows = _couch(f"{DB}/_all_docs?include_docs=true&startkey={start}&endkey={end}")["rows"]
    docs = [row["doc"] for row in rows if row.get("doc")]
    docs.sort(key=lambda d: d["_id"])
    return docs


def audit(cards, claims):
    """(failures, per-type counts, per-grade counts) over one course's cards.

    `failures` is a list of (card id, what is wrong), in card order, so a
    report can name the document rather than only count it.
    """
    by_id = {claim["_id"]: claim for claim in claims}
    failures = []
    types, grades = Counter(), Counter()

    for card in cards:
        card_id = card.get("_id", "<no id>")
        types[card.get("cardType")] += 1
        grades[card.get("grade")] += 1

        claim = by_id.get(card.get("claimId"))
        if claim is None:
            failures.append((card_id, "names a claim that is not in the database"))
            continue
        text = norm(claim.get("text", ""))

        if card.get("grade") != claim.get("grade"):
            failures.append((card_id, f"grade {card.get('grade')!r} but its claim is {claim.get('grade')!r}"))

        evidence = (card.get("evidence") or "").strip()
        if not evidence:
            failures.append((card_id, "carries no evidence span"))
        elif norm(evidence) not in text:
            failures.append((card_id, "evidence span is not in the claim it came from"))

        if card.get("cardType") == "cloze":
            if "___" not in (card.get("prompt") or ""):
                failures.append((card_id, "cloze prompt has no blank to fill"))
            if norm(card.get("answer")) not in text:
                failures.append((card_id, "cloze answer is not in the claim it came from"))

        if card.get("cardType") == "multiple_choice":
            options = [norm(option) for option in (card.get("options") or [])]
            if norm(card.get("answer")) not in options:
                failures.append((card_id, "answer is not among its own options"))

    return failures, types, grades


def break_one_of_each(cards):
    """A copy of `cards` with one card broken per invariant -- the control.

    Returns the copy; the caller's list is untouched, and nothing is written
    to CouchDB by this module at all.
    """
    broken = [dict(card) for card in cards]
    if not broken:
        return broken
    broken[0] = dict(broken[0], evidence="a span the claim never contained")
    if len(broken) > 1:
        broken[1] = dict(broken[1], claimId="claim:no-such-course:no-such-chapter:999")
    for card in broken:
        if card.get("cardType") == "multiple_choice":
            card["answer"] = "an answer that is in no option"
            break
    for card in broken:
        if card.get("cardType") == "cloze":
            card["prompt"] = (card.get("prompt") or "").replace("___", "x")
            card["answer"] = "a span the claim never contained"
            break
    for card in broken:
        card["grade"] = "moderate" if card.get("grade") != "moderate" else "high"
        break
    return broken


def courses():
    """Every course slug in the database, from the course documents."""
    start = urllib.parse.quote(json.dumps("course:"))
    end = urllib.parse.quote(json.dumps("course;"))
    rows = _couch(f"{DB}/_all_docs?startkey={start}&endkey={end}")["rows"]
    return sorted(row["id"].split(":", 1)[1] for row in rows)


def report(slug, control=False):
    """Print one course's verdict. Returns the number of failures."""
    cards = load_cards(slug)
    claims = load_claims(slug)
    if control:
        cards = break_one_of_each(cards)
    failures, types, grades = audit(cards, claims)

    carded = len({card.get("claimId") for card in cards})
    print(f"{slug}: {len(cards)} card(s) over {len(claims)} claim(s), {carded} claim(s) carded")
    print(f"  types  {dict(types)}")
    print(f"  grades {dict(grades)}")
    if failures:
        print(f"  {len(failures)} FAILURE(S):")
        for card_id, why in failures:
            print(f"    {card_id} -- {why}")
    else:
        print("  no card contradicts the claim it came from")
    return len(failures)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("course", nargs="?", help="course slug; omit with --all")
    parser.add_argument("--all", action="store_true", help="every course in the database")
    parser.add_argument("--control", action="store_true",
                        help="break one card per invariant in memory first, to prove the checks can see one")
    args = parser.parse_args(argv)

    try:
        slugs = courses() if args.all else ([args.course] if args.course else [])
        if not slugs:
            parser.error("name a course or pass --all")
        failures = sum(report(slug, control=args.control) for slug in slugs)
    except LyceumCardsError as error:
        print(f"UNREADABLE -- {error}", file=sys.stderr)
        return 1

    print()
    if failures:
        print(f"{failures} failure(s) across {len(slugs)} course(s).")
        return 2
    print(f"{len(slugs)} course(s) clean. Run again with --control to see these checks fail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
