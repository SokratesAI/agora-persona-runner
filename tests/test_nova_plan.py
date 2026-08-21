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


# --- The goals scoreboard (issue #96, research/plan-page-design.md) ---
#
# The page's one structured input. Every case below is one that would ship a
# wrong number silently: a bar drawn from a sentence, a typo'd field the page
# ignores without saying so, a fence rendering underneath the meter it drew,
# and a half-written block eating the text around it.

SCORED = """---
type: note
updated: 2026-08-20
---

# Goals

## The slate

**G1 — The loop works on what you asked for.**

```goal
name: G1 — The loop works on what you asked for
measure: Merged PRs per board row closed
now: 2.8
target: 2.0
unit: PRs per closed row
direction: down
```

Some prose about G1 that must survive.

**G2 — Everything reaches your phone.**

```goal
name: G2 — Everything reaches your phone
measure: Things you still have to leave the app to do
now: 4
target: 0
direction: down
```
"""


def test_scoreboard_rows_come_off_the_fenced_blocks():
    goals = _doc(plan_payload({"goals": SCORED}), "goals")
    assert [row["name"] for row in goals["scoreboard"]] == [
        "G1 — The loop works on what you asked for",
        "G2 — Everything reaches your phone",
    ]
    first = goals["scoreboard"][0]
    assert first["measure"] == "Merged PRs per board row closed"
    assert first["unit"] == "PRs per closed row"
    assert (first["nowValue"], first["targetValue"]) == (2.8, 2.0)


def test_direction_decides_the_verdict_not_the_size_of_the_number():
    # 2.8 against a target of 2.0 is off target going down, and would be on
    # target going up. The verdict is the only thing `direction` changes.
    rows = plan_payload({"goals": SCORED})["documents"]
    goals = next(d for d in rows if d["key"] == "goals")
    assert goals["scoreboard"][0]["onTarget"] is False
    up = SCORED.replace("direction: down", "direction: up", 1)
    flipped = _doc(plan_payload({"goals": up}), "goals")
    assert flipped["scoreboard"][0]["onTarget"] is True


def test_a_goal_with_no_clean_number_gets_a_row_and_no_bar():
    # "about 2.8" is a legitimate thing to write when the number is not
    # clean, and the failure to avoid is a bar drawn from a sentence.
    vague = SCORED.replace("now: 2.8", "now: about 2.8", 1)
    goals = _doc(plan_payload({"goals": vague}), "goals")
    row = goals["scoreboard"][0]
    assert row["now"] == "about 2.8"
    assert row["nowValue"] is None
    assert row["onTarget"] is None


def test_the_block_does_not_also_render_as_a_code_block():
    goals = _doc(plan_payload({"goals": SCORED}), "goals")
    slate = next(s for s in goals["sections"] if s["heading"] == "The slate")
    assert "measure:" not in _text(slate)
    assert "Some prose about G1 that must survive." in _text(slate)


def test_an_unknown_field_is_dropped_and_a_nameless_block_is_not_a_row():
    typo = SCORED.replace("target: 2.0", "targt: 2.0", 1)
    goals = _doc(plan_payload({"goals": typo}), "goals")
    assert goals["scoreboard"][0]["target"] == ""
    assert "targt" not in goals["scoreboard"][0]

    nameless = SCORED.replace("name: G1 — The loop works on what you asked for\n", "", 1)
    assert len(_doc(plan_payload({"goals": nameless}), "goals")["scoreboard"]) == 1


def test_an_unterminated_block_keeps_its_text_rather_than_swallowing_the_rest():
    half = SCORED.replace("unit: PRs per closed row\ndirection: down\n```\n", "")
    goals = _doc(plan_payload({"goals": half}), "goals")
    body = " ".join(_text(s) for s in goals["sections"])
    assert "Some prose about G1 that must survive." in body
    assert "G2 — Everything reaches your phone" in body


def test_a_document_with_no_blocks_is_unchanged_and_scores_nothing():
    payload = plan_payload({"roadmap": ROADMAP, "goals": GOALS})
    assert _doc(payload, "roadmap")["scoreboard"] == []
    assert _doc(payload, "goals")["scoreboard"] == []
    assert _doc(payload, "goals")["title"] == "Goals"


# The roadmap's ranked strip (issue #96, design item 2). Same fence machinery
# as the scoreboard, deliberately -- these tests pin the parts that are not
# shared: the status vocabulary and the rank coming off the block.
RANKED = """---
updated: 2026-08-21
---

# Roadmap

## The five I would do next, in order

**1. Get CI back.** Prose that must survive.

```next
rank: 1
title: Get CI back
status: in progress
claim: Not my work — yours, and it is two minutes.
board: idea #73
```

**3. ~~Fix my vault write path~~ — done.**

```next
rank: 3
title: Fix my vault write path
status: done
claim: It was garbage collection, not a write bug.
board: idea #61
```
"""


def test_ranked_cards_come_off_the_fenced_blocks():
    roadmap = _doc(plan_payload({"roadmap": RANKED}), "roadmap")
    assert [r["title"] for r in roadmap["ranked"]] == [
        "Get CI back",
        "Fix my vault write path",
    ]
    assert roadmap["ranked"][0]["claim"] == "Not my work — yours, and it is two minutes."
    assert roadmap["ranked"][0]["board"] == "idea #73"


def test_a_status_always_carries_its_word_and_an_unknown_one_carries_neither():
    roadmap = _doc(plan_payload({"roadmap": RANKED}), "roadmap")
    assert roadmap["ranked"][0]["statusLabel"] == "In progress"
    assert roadmap["ranked"][0]["statusSymbol"] == "\U0001f7e1"
    assert roadmap["ranked"][1]["statusLabel"] == "Done"
    assert roadmap["ranked"][1]["statusSymbol"] == "\u2705"

    # A word this page has never seen gets no chip rather than a guessed one:
    # rendering `Backlog` for something a cycle called `blocked` would be the
    # page stating a fact the file does not.
    blocked = RANKED.replace("status: in progress", "status: blocked", 1)
    row = _doc(plan_payload({"roadmap": blocked}), "roadmap")["ranked"][0]
    assert row["statusLabel"] == "" and row["statusSymbol"] == ""


def test_the_rank_is_the_files_number_and_not_the_cards_position():
    # The file strikes item 3 through without renumbering 4 and 5, so the
    # second card really is rank 3. Counting positions would print "2".
    ranks = [r["rank"] for r in _doc(plan_payload({"roadmap": RANKED}), "roadmap")["ranked"]]
    assert ranks == ["1", "3"]


def test_a_next_block_does_not_also_render_as_a_code_block():
    roadmap = _doc(plan_payload({"roadmap": RANKED}), "roadmap")
    body = " ".join(_text(s) for s in roadmap["sections"])
    assert "status:" not in body
    assert "Prose that must survive." in body


def test_a_titleless_next_block_is_not_a_card():
    untitled = RANKED.replace("title: Get CI back\n", "", 1)
    assert len(_doc(plan_payload({"roadmap": untitled}), "roadmap")["ranked"]) == 1


def test_goal_and_next_blocks_do_not_eat_each_other():
    both = RANKED + SCORED.split("---", 2)[-1]
    doc = _doc(plan_payload({"roadmap": both}), "roadmap")
    assert len(doc["ranked"]) == 2
    assert len(doc["scoreboard"]) == 2
    body = " ".join(_text(s) for s in doc["sections"])
    assert "Some prose about G1 that must survive." in body
    assert "Prose that must survive." in body


# The test above concatenates two well-formed blobs, so every fence in it
# opens and closes correctly and it cannot see the failure its name promises.
# This is that failure, and it was real: a bare ``` closes whatever block is
# open regardless of what opened it, so scanning one fence name at a time let
# an unterminated ```goal eat the ```next after it -- card and prose both --
# and put a data-free scoreboard row on a card that has no scoreboard.
EATEN = """# Roadmap

## Five

```goal
name: Something
```next
rank: 1
title: Get CI back
status: done
```

Trailing prose that must survive.
"""


def test_a_forgotten_closing_fence_does_not_swallow_the_next_block():
    doc = _doc(plan_payload({"roadmap": EATEN}), "roadmap")
    assert [r["title"] for r in doc["ranked"]] == ["Get CI back"]
    assert doc["scoreboard"] == [], "a half-written goal block is not a row"
    body = " ".join(_text(s) for s in doc["sections"])
    assert "Trailing prose that must survive." in body


def test_a_forgotten_closing_fence_survives_in_the_other_direction_too():
    swapped = EATEN.replace("```goal\nname: Something", "```next\ntitle: Half written")
    swapped = swapped.replace("```next\nrank: 1", "```goal\nname: G1")
    doc = _doc(plan_payload({"roadmap": swapped}), "roadmap")
    assert [r["name"] for r in doc["scoreboard"]] == ["G1"]
    assert doc["ranked"] == []
    # The well-formed block on the other side of the mistake still parses,
    # and no text is lost -- which is what `abandon` promises and all it
    # promises. It does *not* promise the prose stays prose: the fence line
    # it restores opens a markdown code block that runs to the end of the
    # document, so the trailing paragraph is served as code. That is true of
    # a single fence type too and predates the second one; it is filed rather
    # than fixed here, and this assertion says which of the two it is so the
    # next reader does not have to re-derive it.
    body = " ".join(_text(s) for s in doc["sections"])
    assert "Trailing prose that must survive." in body
    assert "Half written" in body, "an abandoned block puts its text back"


def test_the_plan_and_the_boards_agree_on_every_status_word():
    # Two hand-kept copies of one vocabulary, in two modules, with nothing
    # detecting drift -- `outdated` was in the boards and missing here, while
    # a comment claimed the two were the same list. One assertion beats a
    # fourth restatement of the rule.
    from agora_runner.nova_boards import STATUS_LABELS
    from agora_runner.nova_plan import _STATUSES

    for key, label in STATUS_LABELS.items():
        symbol, word = _STATUSES[key]
        assert symbol + " " + word == label, key
