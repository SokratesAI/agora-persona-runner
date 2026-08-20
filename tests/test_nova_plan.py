"""The `/plan` page — `roadmap.md` and `goals.md` on Edvard's phone.

Issue #7 and `goals.md`'s own G2 measure: the two documents written so he
could argue with Nova's prioritisation are the two he has to leave the app
to read.

The cases here are the ones that would ship a wrong page silently. A
section rendered with the wrong heading level is visible the moment
anybody looks; frontmatter leaking into the body, a section disappearing
because a cycle renamed it, and a `##` inside a fenced example cutting the
document in half are all things that look like ordinary prose on screen.
"""

from agora_runner.md_sections import outline
from agora_runner.nova_plan import plan_payload

ROADMAP = """---
type: note
updated: 2026-08-16
contract: Nova writes this. Edvard argues with the reasoning here.
---

# Roadmap

Written by Nova, Cycle 226. Idea #4, pairs with issue #7.

## The five I would do next, in order

**1. Get CI back.** Not my work — yours, and it is two minutes.

- One bullet
- Another bullet

## What I would not do next, and why

> "Ideas #2 to #24 are not a backlog."
"""

GOALS = """---
type: note
updated: 2026-08-17
---

# Goals

So: below is a slate I am proposing, not assuming.

## The slate

**G1 — The loop works on what you asked for.**

## Weekly review

### 2026-08-17 — week of 08-16 to 08-17

What moved.

### 2026-08-16 — week of 08-09 to 08-16

What did not.
"""


def _doc(payload, key):
    return next(d for d in payload["documents"] if d["key"] == key)


def _text(section):
    """Every span in a section, flattened — what a reader would see."""
    out = []
    for block in section["blocks"]:
        if block["type"] == "code":
            out.append(block["text"])
        else:
            out.extend(span["text"] for span in block["spans"])
    return " ".join(out)


def test_both_documents_are_shaped():
    payload = plan_payload({"roadmap": ROADMAP, "goals": GOALS})
    assert [d["key"] for d in payload["documents"]] == ["roadmap", "goals"]
    assert _doc(payload, "roadmap")["title"] == "Roadmap"
    assert _doc(payload, "goals")["updated"] == "2026-08-17"
    assert not _doc(payload, "roadmap")["missing"]


def test_frontmatter_never_reaches_the_page():
    """The failure the first smoke test of this module actually found.

    `_skippable` says a heading cannot live in frontmatter; it does not
    say the frontmatter is not prose. Rendered without the cut, the
    roadmap opens on its own `contract:` line — a sentence addressed to
    Nova, on the page written for Edvard.
    """
    payload = plan_payload({"roadmap": ROADMAP})
    rendered = " ".join(_text(s) for s in _doc(payload, "roadmap")["sections"])
    assert "contract:" not in rendered
    assert "type: note" not in rendered
    assert "Written by Nova, Cycle 226." in rendered


def test_every_heading_becomes_its_own_section_at_its_own_level():
    payload = plan_payload({"goals": GOALS})
    sections = _doc(payload, "goals")["sections"]
    assert [(s["level"], s["heading"]) for s in sections if s["heading"]] == [
        (2, "The slate"),
        (2, "Weekly review"),
        (3, "2026-08-17 — week of 08-16 to 08-17"),
        (3, "2026-08-16 — week of 08-09 to 08-16"),
    ]


def test_the_standfirst_survives_with_no_heading():
    """`goals.md`'s opening paragraph says the slate is a proposal.

    It sits above the first `##`, so a parser that only kept named
    sections would drop the one sentence that stops Edvard reading five
    proposed goals as five settled ones.
    """
    payload = plan_payload({"goals": GOALS})
    intro = [s for s in _doc(payload, "goals")["sections"] if not s["heading"]]
    assert any("proposing, not assuming" in _text(s) for s in intro)


def test_a_missing_document_is_a_card_and_not_an_error():
    payload = plan_payload({"roadmap": ROADMAP})
    goals = _doc(payload, "goals")
    assert goals["missing"] is True
    assert goals["sections"] == []
    assert _doc(payload, "roadmap")["missing"] is False


def test_bullets_and_quotes_keep_their_block_type():
    """`render_blocks` already does this; the assertion is that the page
    passes it real bodies rather than pre-flattened text. Both documents
    carry Edvard's own words as blockquotes."""
    payload = plan_payload({"roadmap": ROADMAP})
    kinds = {
        block["type"]
        for section in _doc(payload, "roadmap")["sections"]
        for block in section["blocks"]
    }
    assert {"li", "quote", "p"} <= kinds


def test_a_heading_inside_a_fenced_block_does_not_split_the_document():
    """The failure `md_sections` exists for, reaching a new caller.

    A cycle documenting its own file format quotes a `##` line inside a
    fence. Cutting there would end the real section early and open one
    named after an example.
    """
    text = "# Roadmap\n\nIntro.\n\n```\n## Not a heading\n```\n\n## Real\n\nBody.\n"
    payload = plan_payload({"roadmap": text})
    headings = [s["heading"] for s in _doc(payload, "roadmap")["sections"] if s["heading"]]
    assert headings == ["Real"]


def test_outline_returns_the_intro_even_with_no_headings_at_all():
    """The empty-list edge every caller would otherwise special-case."""
    assert outline("Just a sentence.") == [(0, None, "Just a sentence.")]


ORDERED = """# Goals

## G5

Measure, rewritten Cycle 230 — three separate numbers:

1. **Share of spend on directed cycles.** A cycle is directed if it names a row.
2. **Median weighted tokens per cycle.** The efficiency half.
3. **CI minutes per merged pull request.** Unchanged; this one worked.

This week: 75% of spend was directed.
"""


def test_a_numbered_list_is_a_list_and_not_one_run_on_paragraph():
    """The reviewer's finding on this diff, and it was right.

    `render_blocks` was scoped by a survey of the journal, which uses
    bullets and never numbers. `goals.md`'s G5 is a real three-item
    numbered list with no blank lines between the items, so every line
    fell through to the paragraph branch and the three were space-joined
    into one block of prose with `1.` `2.` `3.` still typed inside it.

    Asserted as three separate blocks rather than on the joined text,
    because the joined text still *contains* all three sentences — a test
    that only looked for the words would have passed against the bug.
    """
    payload = plan_payload({"goals": ORDERED})
    blocks = [b for s in _doc(payload, "goals")["sections"] for b in s["blocks"]]
    ordered = [b for b in blocks if b["type"] == "oli"]
    assert len(ordered) == 3
    assert ordered[0]["spans"][0] == {"kind": "strong", "text": "Share of spend on directed cycles."}
    # The digit is dropped because the browser renders it from the `<ol>`.
    assert not any("1." in span["text"] for span in ordered[0]["spans"])
    # And the paragraphs around it are still paragraphs.
    assert [b["type"] for b in blocks] == ["p", "oli", "oli", "oli", "p"]


def test_a_bulleted_list_is_still_a_bulleted_list():
    """The mutation the fix above could plausibly have caused: a pattern
    loose enough to catch numbers can catch `-` too, and every other page
    on this server renders bullets through the same function."""
    payload = plan_payload({"roadmap": ROADMAP})
    blocks = [b for s in _doc(payload, "roadmap")["sections"] for b in s["blocks"]]
    assert [b["type"] for b in blocks if b["type"] in ("li", "oli")] == ["li", "li"]
