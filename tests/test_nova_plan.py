"""The `/plan` page — `roadmap.md` and `goals.md` on the owner's phone.

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


def test_every_document_is_shaped_in_reading_order():
    payload = plan_payload({"roadmap": ROADMAP, "goals": GOALS})
    # `projects` joined these two on 2026-09-14 (issue #227) and is last on
    # purpose; a fetch that found nothing still gets a card.
    assert [d["key"] for d in payload["documents"]] == [
        "roadmap", "goals", "projects"]
    assert _doc(payload, "roadmap")["title"] == "Roadmap"
    assert _doc(payload, "goals")["updated"] == "2026-08-17"
    assert not _doc(payload, "roadmap")["missing"]


def test_frontmatter_never_reaches_the_page():
    """The failure the first smoke test of this module actually found.

    `_skippable` says a heading cannot live in frontmatter; it does not
    say the frontmatter is not prose. Rendered without the cut, the
    roadmap opens on its own `contract:` line — a sentence addressed to
    Nova, on the page written for the owner.
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
    sections would drop the one sentence that stops the owner reading five
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
    carry the owner's own words as blockquotes."""
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
**4. Build the weekly goal review.** More prose.

```next
rank: 4
title: Build the weekly goal review
status: in progress
claim: A document is not a habit.
board: idea #38
```

**5. ~~The two board-editing gaps~~ — done.**

```next
rank: 5
title: The two board-editing gaps, together
status: done
claim: One page, two controls.
board: issues #89, #91
```
"""


def _all_ranked(doc):
    """Both halves of the strip, in document order.

    The parsing tests below are about the fence and want every card the
    document produced; which of the two lists a card lands in is
    `test_a_finished_item_leaves_the_list_that_says_it_is_next`'s job.
    """
    return list(doc["ranked"]) + list(doc["rankedDone"])


def test_ranked_cards_come_off_the_fenced_blocks():
    roadmap = _doc(plan_payload({"roadmap": RANKED}), "roadmap")
    assert [r["title"] for r in _all_ranked(roadmap)] == [
        "Get CI back",
        "Build the weekly goal review",
        "Fix my vault write path",
        "The two board-editing gaps, together",
    ]
    assert roadmap["ranked"][0]["claim"] == "Not my work — yours, and it is two minutes."
    assert roadmap["ranked"][0]["board"] == "idea #73"


def test_a_ranked_card_carries_its_board_reference_as_a_link():
    """Issue #96: the row a card came from is a page in this app, not text.

    The plain `board` string stays -- `tools.roadmap_drift` reads it -- and
    the spans are the second copy the page renders. The href is built here
    so nothing in the browser ever parses a number out of the text.
    """
    roadmap = _doc(plan_payload({"roadmap": RANKED}), "roadmap")
    assert roadmap["ranked"][0]["boardSpans"] == [
        {"kind": "link", "text": "idea #73", "url": "/ideas#73"},
    ]
    # `issues #89, #91` is the case the journal footer's parser was written
    # for and it must not mean something new here: the written-out reference
    # links, and the bare `#91` after it stays plain text, because a bare
    # number could be either board and the two are different pages.
    both = _doc(plan_payload({"roadmap": RANKED}), "roadmap")["rankedDone"][1]
    assert both["boardSpans"] == [
        {"kind": "link", "text": "issues #89", "url": "/issues#89"},
        {"kind": "text", "text": ", #91"},
    ]


def test_a_card_with_no_board_reference_gets_no_spans():
    """A `board:` line the file does not carry must not invent an empty link."""
    no_board = RANKED.replace("board: idea #73\n", "")
    card = _doc(plan_payload({"roadmap": no_board}), "roadmap")["ranked"][0]
    assert card["board"] == ""
    assert card["boardSpans"] == []


def test_a_status_always_carries_its_word_and_an_unknown_one_carries_neither():
    roadmap = _doc(plan_payload({"roadmap": RANKED}), "roadmap")
    assert roadmap["ranked"][0]["statusLabel"] == "In progress"
    assert roadmap["ranked"][0]["statusSymbol"] == "\U0001f7e1"
    assert roadmap["rankedDone"][0]["statusLabel"] == "Done"
    assert roadmap["rankedDone"][0]["statusSymbol"] == "\u2705"

    # A word this page has never seen gets no chip rather than a guessed one:
    # rendering `Backlog` for something a cycle called `blocked` would be the
    # page stating a fact the file does not.
    blocked = RANKED.replace("status: in progress", "status: blocked", 1)
    row = _doc(plan_payload({"roadmap": blocked}), "roadmap")["ranked"][0]
    assert row["title"] == "Get CI back"
    assert row["statusLabel"] == "" and row["statusSymbol"] == ""


def test_the_rank_is_the_files_number_and_not_the_cards_position():
    # The file strikes item 3 through without renumbering 4 and 5, so the
    # second card really is rank 3. Counting positions would print "2".
    doc = _doc(plan_payload({"roadmap": RANKED}), "roadmap")
    assert [r["rank"] for r in doc["ranked"]] == ["1", "4"]
    assert [r["rank"] for r in doc["rankedDone"]] == ["3", "5"]


def test_a_next_block_does_not_also_render_as_a_code_block():
    roadmap = _doc(plan_payload({"roadmap": RANKED}), "roadmap")
    body = " ".join(_text(s) for s in roadmap["sections"])
    assert "status:" not in body
    assert "Prose that must survive." in body


def test_a_titleless_next_block_is_not_a_card():
    untitled = RANKED.replace("title: Get CI back\n", "", 1)
    assert len(_all_ranked(_doc(plan_payload({"roadmap": untitled}), "roadmap"))) == 3


def test_goal_and_next_blocks_do_not_eat_each_other():
    both = RANKED + SCORED.split("---", 2)[-1]
    doc = _doc(plan_payload({"roadmap": both}), "roadmap")
    assert len(_all_ranked(doc)) == 4
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
    assert [r["title"] for r in _all_ranked(doc)] == ["Get CI back"]
    assert doc["scoreboard"] == [], "a half-written goal block is not a row"
    body = " ".join(_text(s) for s in doc["sections"])
    assert "Trailing prose that must survive." in body


def test_a_forgotten_closing_fence_survives_in_the_other_direction_too():
    swapped = EATEN.replace("```goal\nname: Something", "```next\ntitle: Half written")
    swapped = swapped.replace("```next\nrank: 1", "```goal\nname: G1")
    doc = _doc(plan_payload({"roadmap": swapped}), "roadmap")
    assert [r["name"] for r in doc["scoreboard"]] == ["G1"]
    assert _all_ranked(doc) == []
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


def _open(payload, key):
    """`[(heading, open)]` for one document, standfirst included as `None`."""
    return [(s["heading"], s["open"]) for s in _doc(payload, key)["sections"]]


def test_every_headed_section_arrives_collapsed():
    """Issue #96: 4,961 words in one scroll, no entry point but the top.

    The fold is the whole point of the change, so this is the assertion
    that fails if a later cycle "simplifies" it away.
    """
    payload = plan_payload({"roadmap": ROADMAP})
    headed = [(h, o) for h, o in _open(payload, "roadmap") if h]
    assert headed, "the fixture must have headings for this to mean anything"
    assert all(o is False for _h, o in headed), headed


def test_the_standfirst_is_never_folded():
    """NN/g's binding rule: crucial information does not go behind a fold.

    In `goals.md` the standfirst is the paragraph saying the slate is a
    proposal, and it has no heading -- so a `<summary>` would have nothing
    to print and the sentence would be behind a click for no gain.
    """
    payload = plan_payload({"goals": GOALS})
    assert [o for h, o in _open(payload, "goals") if h is None] == [True]


def test_the_newest_of_a_dated_stack_opens_and_the_rest_fold():
    payload = plan_payload({"goals": GOALS})
    assert _open(payload, "goals") == [
        (None, True),
        ("The slate", False),
        ("Weekly review", False),
        ("2026-08-17 — week of 08-16 to 08-17", True),
        ("2026-08-16 — week of 08-09 to 08-16", False),
    ]


def test_a_lone_dated_section_stays_folded():
    """One entry is not a stack -- there is nothing for it to be newer than.

    Opening it would be this module having an opinion about a single
    section, which is the thing the "discovered, never named" rule exists
    to stop.
    """
    lone = """# Goals

## Weekly review

### 2026-08-17 — the first one

Only entry.
"""
    payload = plan_payload({"goals": lone})
    assert _open(payload, "goals") == [
        ("Weekly review", False),
        ("2026-08-17 — the first one", False),
    ]


def test_a_dated_heading_at_another_level_is_not_the_same_stack():
    """Adjacency and level both, or a `###` under a dated `##` opens itself."""
    mixed = """# Goals

## 2026-08-17 — a dated section

Prose.

### 2026-08-16 — a child that happens to carry a date

More prose.
"""
    payload = plan_payload({"goals": mixed})
    assert all(o is False for _h, o in _open(payload, "goals"))


def test_a_parent_with_its_own_prose_still_folds_above_the_open_newest():
    """The real shape of `goals.md`, which the shared fixture does not have.

    `## Weekly review` carries a one-line standfirst of its own, so it is a
    *non-empty* closed fold sitting directly above an *open* one. Reviewer
    finding on #269: every fixture here had that heading empty, and an
    empty headed section takes the other branch in `planSection` entirely
    -- it renders plain rather than as a `<details>`. So the composition
    that is actually on the owner's screen was the one composition untested.
    """
    real_shape = """# Goals

## Weekly review

Appended once a week, newest first.

### 2026-08-17 — the newest

Body.

### 2026-08-16 — the older

Body.
"""
    sections = _doc(plan_payload({"goals": real_shape}), "goals")["sections"]
    assert [(s["heading"], s["open"], bool(s["blocks"])) for s in sections] == [
        ("Weekly review", False, True),
        ("2026-08-17 — the newest", True, True),
        ("2026-08-16 — the older", False, True),
    ]


# The split (issue #96, 2026-08-25). The strip is headed "What I would do
# next, in order" and on that morning three of its five cards were finished
# — the page told the owner that work closed nine days earlier was what
# happened next. A chip on a card does not retract the heading above it.
def test_a_finished_item_leaves_the_list_that_says_it_is_next():
    doc = _doc(plan_payload({"roadmap": RANKED}), "roadmap")
    # The fixture is the real shape of `roadmap.md` on the morning this was
    # written: five items, ranks 1-5, three of them finished. A one-and-one
    # fixture cannot see an implementation that grouped by status or
    # reversed a bucket, because with one item per list every order is the
    # same order.
    assert [r["rank"] for r in doc["ranked"]] == ["1", "4"]
    assert [r["rank"] for r in doc["rankedDone"]] == ["3", "5"]
    assert [r["title"] for r in doc["ranked"]] == [
        "Get CI back",
        "Build the weekly goal review",
    ]
    assert [r["title"] for r in doc["rankedDone"]] == [
        "Fix my vault write path",
        "The two board-editing gaps, together",
    ]


def test_the_split_keeps_document_order_rather_than_sorting_by_rank():
    # Rank is a string off the file and the file is free to be out of order.
    # Sorting on it would look identical against a well-ordered document and
    # would silently reorder the owner's own argument the first time it was
    # not.
    shuffled = RANKED.replace("rank: 4", "rank: 0")
    doc = _doc(plan_payload({"roadmap": shuffled}), "roadmap")
    assert [r["rank"] for r in doc["ranked"]] == ["1", "0"]


def test_the_finished_flag_is_on_the_wire_and_is_the_field_the_split_reads():
    # The renderer does not read this; which list a card is in already says
    # it. It is left on the payload deliberately rather than stripped, so
    # the one fact the split turns on is visible to anything reading
    # `/api/plan` -- including a future page that wants to render a done
    # card differently without re-deriving the rule from the chip.
    doc = _doc(plan_payload({"roadmap": RANKED}), "roadmap")
    assert [r["finished"] for r in doc["ranked"]] == [False, False]
    assert [r["finished"] for r in doc["rankedDone"]] == [True, True]


def test_outdated_counts_as_finished_and_an_unknown_status_does_not():
    outdated = RANKED.replace("status: done", "status: outdated", 1)
    doc = _doc(plan_payload({"roadmap": outdated}), "roadmap")
    assert [r["title"] for r in doc["rankedDone"]] == [
        "Fix my vault write path",
        "The two board-editing gaps, together",
    ]

    # A status this module has never seen stays in the open list. The card
    # already declines to guess at a chip for it; putting it in the finished
    # half would be the same guess with worse consequences, because a
    # finished card is one the owner stops reading.
    unknown = RANKED.replace("status: done", "status: shipped-ish", 1)
    doc = _doc(plan_payload({"roadmap": unknown}), "roadmap")
    assert [r["title"] for r in doc["ranked"]] == [
        "Get CI back",
        "Fix my vault write path",
        "Build the weekly goal review",
    ]
    assert [r["title"] for r in doc["rankedDone"]] == [
        "The two board-editing gaps, together",
    ]


def test_a_document_whose_every_item_is_finished_has_an_empty_open_list():
    # This is what a `roadmap.md` nobody has rewritten looks like from the
    # outside, and it is the case the old page could not show at all: five
    # ✅ cards under a heading promising five next steps.
    everything = RANKED.replace("status: in progress", "status: done")
    doc = _doc(plan_payload({"roadmap": everything}), "roadmap")
    assert doc["ranked"] == []
    assert len(doc["rankedDone"]) == 4


def test_a_missing_document_carries_both_ranked_lists():
    # Same call the rest of this payload makes: every key the renderer reads
    # is present whether or not the fetch found anything, so the page has one
    # branch instead of two.
    doc = _doc(plan_payload({}), "roadmap")
    assert doc["missing"] is True
    assert doc["ranked"] == [] and doc["rankedDone"] == []


# --- The owner ticking a goal (idea #38's remaining half) ---------------


GOALS_WITH_BLOCKS = """---
type: note
---

# Goals

## The slate

**G1 — one.**

```goal
name: G1 — one
measure: things per week
now: 3
target: 1
direction: down
```

Prose under G1 that nothing parses.

**G2 — two.**

```goal
name: G2 — two
now: 5
status: declined
```

Prose under G2.
"""


def test_a_goal_with_no_status_line_reads_as_proposed():
    """The compatibility case, and it is every block in the live file.

    `goals.md` was written on 2026-08-16 with no `status:` anywhere, so a
    default of anything but "proposed" would misreport five real goals as
    settled the moment this ships.
    """
    payload = plan_payload({"goals": GOALS_WITH_BLOCKS})
    goals = [d for d in payload["documents"] if d["key"] == "goals"][0]
    rows = {row["name"]: row for row in goals["scoreboard"]}
    assert rows["G1 — one"]["status"] == "proposed"
    assert rows["G2 — two"]["status"] == "declined"


def test_an_unreadable_status_reads_as_proposed_not_as_a_fourth_state():
    """The row is a control he taps. A value nothing understands has to
    render as the one state whose next tap writes a value that is."""
    payload = plan_payload({"goals": GOALS_WITH_BLOCKS.replace("status: declined", "status: maybe")})
    goals = [d for d in payload["documents"] if d["key"] == "goals"][0]
    rows = {row["name"]: row for row in goals["scoreboard"]}
    assert rows["G2 — two"]["status"] == "proposed"


def test_setting_a_status_inserts_the_line_and_touches_nothing_else():
    from agora_runner.nova_plan import set_status_in_goals

    out = set_status_in_goals(GOALS_WITH_BLOCKS, "G1 — one", "approved")
    assert "status: approved" in out
    # Every other line of his document survives, in order.
    before = [line for line in GOALS_WITH_BLOCKS.split("\n")]
    after = [line for line in out.split("\n")]
    assert [line for line in after if line != "status: approved"] == before
    assert after.index("status: approved") < after.index("```", after.index("direction: down"))


def test_setting_a_status_replaces_an_existing_one_in_place():
    from agora_runner.nova_plan import set_status_in_goals

    out = set_status_in_goals(GOALS_WITH_BLOCKS, "G2 — two", "approved")
    assert "status: declined" not in out
    assert out.count("status: approved") == 1
    assert len(out.split("\n")) == len(GOALS_WITH_BLOCKS.split("\n"))


def test_a_goal_that_is_not_there_is_a_moved_address_not_a_write():
    from agora_runner.nova_plan import set_status_in_goals

    assert set_status_in_goals(GOALS_WITH_BLOCKS, "G9 — renamed", "approved") is None
    assert set_status_in_goals(GOALS_WITH_BLOCKS, "", "approved") is None
    assert set_status_in_goals(GOALS_WITH_BLOCKS, "G1 — one", "settled") is None


def test_an_unterminated_fence_is_never_edited():
    """`_fenced` deliberately puts an unterminated block's lines back as
    prose, because it is a half-written edit. Writing inside one would move
    the owner's own text into a block he is still typing."""
    from agora_runner.nova_plan import set_status_in_goals

    broken = "```goal\nname: G1 — one\nnow: 3\n\n**G2**\n"
    assert set_status_in_goals(broken, "G1 — one", "approved") is None


def test_the_status_line_matches_the_indent_of_the_block_it_joins():
    from agora_runner.nova_plan import set_status_in_goals

    indented = "  ```goal\n  name: G1\n  now: 3\n  ```\n"
    out = set_status_in_goals(indented, "G1", "approved")
    assert "  status: approved" in out


def test_two_goals_with_the_same_name_are_refused_rather_than_guessed_at():
    """My reviewer's finding on runner#418, and it is a real divergence:
    two blocks sharing a `name:` render as two rows he can tap separately,
    nothing on the wire tells them apart, and editing the first would
    return 200 on the goal he did not touch."""
    from agora_runner.nova_plan import set_status_in_goals

    twice = "```goal\nname: Foo\nnow: 1\n```\n\n```goal\nname: Foo\nnow: 2\n```\n"
    assert set_status_in_goals(twice, "Foo", "approved") is None


def test_a_block_carrying_two_status_lines_ends_up_with_one_the_page_agrees_with():
    """`_goal` assigns over every line, so the *last* `status:` is what the
    page renders. Rewriting only the first returns 200 and changes nothing
    he can see."""
    from agora_runner.nova_plan import _fenced, _goal, set_status_in_goals

    doubled = "```goal\nname: Foo\nstatus: proposed\nnow: 1\nstatus: declined\n```\n"
    out = set_status_in_goals(doubled, "Foo", "approved")
    assert out.count("status:") == 1
    rows, _ = _fenced(out, {"goal": _goal})
    assert rows["goal"][0]["status"] == "approved"


def test_setting_an_arbitrary_field_replaces_it_in_place():
    """`now:` is edited by the same surgery as `status:` since Cycle 563 —
    the number the scoreboard shows is written by the instrument, not typed."""
    from agora_runner.nova_plan import set_field_in_goals

    out = set_field_in_goals(GOALS_WITH_BLOCKS, "G1 — one", "now", 8.2)
    assert "now: 8.2" in out
    assert "now: 3" not in out
    # The rest of the block, and his prose, are untouched.
    assert "measure: things per week" in out
    assert "Prose under G1 that nothing parses." in out
    assert out.count("now:") == GOALS_WITH_BLOCKS.count("now:")


def test_a_field_name_or_value_the_fence_could_not_parse_back_is_refused():
    """A newline in the value would write a second field the caller never
    asked for and still return a string, which reads as success."""
    from agora_runner.nova_plan import set_field_in_goals

    assert set_field_in_goals(GOALS_WITH_BLOCKS, "G1 — one", "now", "3\nmeasure: x") is None
    assert set_field_in_goals(GOALS_WITH_BLOCKS, "G1 — one", "no w", "3") is None
    assert set_field_in_goals(GOALS_WITH_BLOCKS, "G1 — one", "", "3") is None


def test_a_status_that_is_not_a_status_is_still_refused_through_the_wrapper():
    from agora_runner.nova_plan import set_status_in_goals

    assert set_status_in_goals(GOALS_WITH_BLOCKS, "G1 — one", "settled") is None


# --- `project-goals.md` on the same page (issue #227) ---------------------

PROJECT_GOALS = """---
type: board
updated: 2026-09-14
contract: Nova writes this.
---

# Project goals

Standfirst nothing parses.

## Nova

```objective
statement: The loop spends its cycles on work you asked for
status: discussing
conversation: d62b6aca
```

```key-result
id: nova-kr-your-rows
name: The work closes your rows
measure: Merged pull requests per board row closed
now: 5.5
target: 2.0
unit: PRs per closed row
direction: down
status: discussing
```
`now` is measured, as of Cycle 1533.

```kpi
id: nova-kpi-cost
name: What a cycle costs
measure: Weighted tokens per cycle
now: 1.63
low: 0.8
high: 2.0
unit: M
```
"""


def _projects_doc(markdown=PROJECT_GOALS):
    documents = plan_payload({"projects": markdown})["documents"]
    return [doc for doc in documents if doc["key"] == "projects"][0]


def _paragraphs(doc):
    out = []
    for section in doc["sections"]:
        for block in section["blocks"]:
            out.append("".join(span.get("text", "")
                               for span in block.get("spans", ())))
    return out


def test_project_goals_is_one_of_the_plan_documents():
    """It was written to be argued with and reached no screen he owns."""
    from agora_runner.nova_plan import PLAN_DOCUMENTS
    from agora_runner.project_goals import PROJECT_GOALS_PATH

    assert ("projects", "Project goals", PROJECT_GOALS_PATH) in PLAN_DOCUMENTS
    # Every document appears whether or not the fetch found it.
    keys = [doc["key"] for doc in plan_payload({})["documents"]]
    assert keys.count("projects") == 1
    assert plan_payload({})["documents"][keys.index("projects")]["missing"]


def test_every_fence_becomes_prose_and_none_survives_as_a_code_block():
    """A fence left in the text renders as a code block on his phone, which
    is the Obsidian view this page exists to replace."""
    doc = _projects_doc()

    assert "```" not in PROJECT_GOALS.replace("```", "") + "".join(_paragraphs(doc))
    assert not any("statement:" in text for text in _paragraphs(doc))
    assert [section["heading"] for section in doc["sections"]] == [None, "Nova"]


def test_a_block_renders_where_it_stood_not_gathered_at_the_top():
    """The note explaining where a number came from sits directly under its
    own block, so order is the only thing tying the two together."""
    texts = _paragraphs(_projects_doc())
    key_result = next(i for i, t in enumerate(texts) if "The work closes your rows" in t)
    note = next(i for i, t in enumerate(texts) if "as of Cycle 1533" in t)
    objective = next(i for i, t in enumerate(texts) if t.startswith("Objective"))
    kpi = next(i for i, t in enumerate(texts) if t.startswith("KPI"))
    assert objective < key_result < note < kpi
    # And the note is its own paragraph rather than merged into the block
    # above it, which is what happens without the blank line either side.
    assert texts[note].startswith("now")


def test_a_key_result_carries_its_status_word_and_its_bounds_in_words():
    texts = _paragraphs(_projects_doc())
    key_result = next(t for t in texts if "The work closes your rows" in t)
    assert "Now 5.5 PRs per closed row." in key_result
    assert "Target 2.0 PRs per closed row." in key_result
    assert "Lower is better." in key_result
    objective = next(t for t in texts if t.startswith("Objective"))
    assert objective.startswith("Objective — discussing.")
    assert "d62b6aca" in objective
    kpi = next(t for t in texts if t.startswith("KPI"))
    assert "In bounds 0.8 to 2.0 M." in kpi


def test_a_key_result_with_no_target_says_so_rather_than_printing_nothing():
    """`project_goals.problems` refuses a key result with no target, so the
    page he opens has to show the defect. A target line that simply vanishes
    renders a broken key result as a tidy one."""
    markdown = PROJECT_GOALS.replace("target: 2.0\n", "")
    key_result = next(t for t in _paragraphs(_projects_doc(markdown))
                      if "The work closes your rows" in t)
    assert "No target set." in key_result
    assert "Target" not in key_result.replace("No target set.", "")


def test_a_kpi_never_says_no_target_set():
    """The missing-target sentence belongs to key results only. A KPI has a
    range by design, so saying "No target set." on one would put issue #227's
    forbidden word on every guardrail on the page."""
    kpi = next(t for t in _paragraphs(_projects_doc()) if t.startswith("KPI"))
    assert "No target set." not in kpi


def test_a_kpi_never_prints_a_target_even_when_the_document_carries_one():
    """Issue #227's own rule: a guardrail that carries a target gets
    optimised instead of the work. `KPI_FIELDS` reads the key in only so
    `project_goals.problems` can refuse it, so drawing it here would put
    the forbidden thing on the page."""
    markdown = PROJECT_GOALS.replace("high: 2.0\n", "high: 2.0\ntarget: 0.9\n")
    kpi = next(t for t in _paragraphs(_projects_doc(markdown)) if t.startswith("KPI"))
    assert "0.9" not in kpi
    assert "Target" not in kpi


def test_a_blank_now_reads_as_not_measured_and_never_as_zero():
    """Four numbers in this document have been deliberately blank because no
    instrument existed. An unmeasured guardrail and a perfect score must not
    render the same."""
    markdown = PROJECT_GOALS.replace("now: 5.5\n", "now:\n").replace("now: 1.63\n", "now:\n")
    texts = _paragraphs(_projects_doc(markdown))
    key_result = next(t for t in texts if "The work closes your rows" in t)
    kpi = next(t for t in texts if t.startswith("KPI"))
    assert "Not measured yet." in key_result and "Now 0" not in key_result
    assert "Not measured yet." in kpi and "Now 0" not in kpi


def test_a_key_result_never_becomes_a_scoreboard_row_he_can_tap():
    """A scoreboard row is a control whose tap writes `goals.md` through
    `set_status_in_goals`. He deleted the approve/decline gate for project
    goals on 2026-09-13, so a button re-proposing it is worse than none."""
    doc = _projects_doc()
    assert doc["scoreboard"] == []
    assert doc["ranked"] == [] and doc["rankedDone"] == []


def test_a_half_written_block_is_put_back_rather_than_swallowed():
    """`_fenced.abandon`'s measured rule: text disappearing is worse than a
    stray fence appearing."""
    from agora_runner.nova_plan import _inline_goal_blocks

    unterminated = "## Nova\n\n```objective\nstatement: half typed\n\n## Marcus\n\nprose\n"
    assert _inline_goal_blocks(unterminated) == unterminated
    nameless = "```key-result\nid: x\nnow: 3\n```\n\nafter\n"
    assert _inline_goal_blocks(nameless) == nameless


def test_the_goal_and_next_fences_are_left_for_the_scoreboard():
    """Two scans over one text, and a bare ``` closes whatever is open --
    so this one must not touch a fence `_fenced` owns."""
    from agora_runner.nova_plan import _inline_goal_blocks

    goals = "```goal\nname: G1\nnow: 3\n```\n"
    assert _inline_goal_blocks(goals) == goals
    assert plan_payload({"goals": GOALS_WITH_BLOCKS})["documents"][1]["scoreboard"]


SEATS = """| Project | Milestone | Position | Updated | Serves | Keeps |
| --- | --- | --- | --- | --- | --- |
| Nova | Seeing the loop work | 1 | 2026-09-14 | nova-kr-your-rows | |
| Nova | Board records | 2 | 2026-09-14 | nova-kr-your-rows | |
| Nova | Keeping the lights on | 3 | 2026-09-14 | | nova-kpi-cost |
| Nova | Nothing here | 4 | 2026-09-14 | | |
"""


def _projects_doc_with_seats(seats, markdown=PROJECT_GOALS):
    documents = plan_payload({"projects": markdown}, None, seats)["documents"]
    return [doc for doc in documents if doc["key"] == "projects"][0]


def test_a_key_result_says_how_many_milestones_serve_it():
    """Issue #227's chain -- objective, key result, milestone -- read on the
    page instead of in a tool only a cycle runs."""
    texts = _paragraphs(_projects_doc_with_seats(SEATS))
    key_result = next(t for t in texts if t.startswith("Key result"))
    assert "Served by 2 milestones." in key_result
    kpi = next(t for t in texts if t.startswith("KPI"))
    assert "Kept by 1 milestone." in kpi
    # Singular, because "1 milestones" on his phone is the kind of thing he
    # reads as the page being broken.
    assert "1 milestones" not in " ".join(texts)


def test_the_coverage_sentence_sits_before_the_id():
    """The id is the last thing in the paragraph and stays that way: it is the
    handle, and a sentence after it reads as belonging to the next block."""
    texts = _paragraphs(_projects_doc_with_seats(SEATS))
    key_result = next(t for t in texts if t.startswith("Key result"))
    assert key_result.rstrip().endswith(
        "Served by 2 milestones. nova-kr-your-rows")


def test_a_goal_nothing_points_at_says_so_in_bold():
    """A key result no milestone serves is an outcome nobody is pursuing, and
    a KPI no milestone keeps is a number nobody is accountable for."""
    empty = """| Project | Milestone | Position | Updated | Serves | Keeps |
| --- | --- | --- | --- | --- | --- |
| Nova | Nothing here | 1 | 2026-09-14 | | |
"""
    texts = _paragraphs(_projects_doc_with_seats(empty))
    assert "No milestone serves this yet." in next(
        t for t in texts if t.startswith("Key result"))
    assert "No milestone keeps this in bounds." in next(
        t for t in texts if t.startswith("KPI"))


def test_a_kpi_named_in_serves_is_not_counted_as_kept():
    """Issue #227's rule that a guardrail may never be a key result is
    enforced one column at a time, so pooling the two would report a pointer
    `serves_problems` already refuses as coverage."""
    crossed = """| Project | Milestone | Position | Updated | Serves | Keeps |
| --- | --- | --- | --- | --- | --- |
| Nova | Wrong column | 1 | 2026-09-14 | nova-kpi-cost | nova-kr-your-rows |
"""
    texts = _paragraphs(_projects_doc_with_seats(crossed))
    assert "No milestone keeps this in bounds." in next(
        t for t in texts if t.startswith("KPI"))
    assert "No milestone serves this yet." in next(
        t for t in texts if t.startswith("Key result"))


def test_no_seats_text_prints_no_coverage_sentence_at_all():
    """An unread seats file rendering as "no milestone serves this" against
    every goal on the page is the worst failure this page can have -- a fetch
    that did not happen printing as the finding a cycle is meant to act on.
    `milestone_seats_markdown` returns "" for both missing and empty, so the
    two cannot be told apart and both stay silent."""
    for seats in (None, ""):
        texts = " ".join(_paragraphs(_projects_doc_with_seats(seats)))
        assert "milestone" not in texts.lower(), seats


def test_an_objective_gets_no_coverage_sentence():
    """Nothing points at an objective; its key results are what a seat names,
    so a count on it would be a number with no column behind it."""
    texts = _paragraphs(_projects_doc_with_seats(SEATS))
    objective = next(t for t in texts if t.startswith("Objective"))
    assert "milestone" not in objective.lower()


def test_seat_counts_counts_each_column_separately():
    """The unit under both sentences, asserted on its own so a renderer change
    cannot quietly take the separation with it."""
    from agora_runner.nova_boards import (
        parse_milestone_keeps, parse_milestone_serves,
    )
    from agora_runner.nova_plan import seat_counts

    counts = seat_counts(parse_milestone_serves(SEATS),
                         parse_milestone_keeps(SEATS))
    assert counts == {"served": {"nova-kr-your-rows": 2},
                      "kept": {"nova-kpi-cost": 1}}


def test_the_plan_route_hands_the_seats_file_to_the_payload(monkeypatch):
    """The wiring, not the shaping: `nova_plan` cannot print a count the site
    never fetched, and that seam is invisible from either side alone. Driven
    through the real route function with both fetches stubbed, because
    grepping the module for the call name passes on the three other calls to
    it that were already there."""
    from agora_runner import nova_site

    monkeypatch.setattr(nova_site, "plan_markdown",
                        lambda: {"projects": PROJECT_GOALS})
    monkeypatch.setattr(nova_site, "goal_history_json", lambda: "")
    monkeypatch.setattr(nova_site, "milestone_seats_markdown", lambda: SEATS)
    doc = [d for d in nova_site.plans_payload()["documents"]
           if d["key"] == "projects"][0]
    assert any("Served by 2 milestones." in text
               for text in _paragraphs(doc))


def test_an_objective_prints_the_month_it_covers_but_never_judges_it():
    """Issue #227's seventh rule reaches his phone, and stops there. This
    builder has no clock on purpose: whether the month is over is a
    question about today, and a page answering it would say one thing in
    Oslo and another in UTC. `tools.project_goals_check` does that
    arithmetic against a date its caller names."""
    from agora_runner.nova_plan import _objective_prose
    out = _objective_prose(
        ["statement: Be good", "status: agreed", "period: 2026-09"])
    assert "Covers September 2026." in out
    assert "past" not in out.lower() and "ended" not in out.lower()


def test_an_objective_with_no_period_says_nothing_about_a_month():
    """An unread field must not speak. Rendering a missing period as a
    month -- any month -- would put a date on his screen that no document
    carries."""
    from agora_runner.nova_plan import _objective_prose
    out = _objective_prose(["statement: Be good", "status: discussing"])
    assert "Covers" not in out


def test_a_key_result_prints_the_word_that_says_it_is_not_settled():
    """`discussing` means he and I have not agreed this yet, and a key result
    printed without it reads as decided -- the same call `_objective_prose`
    makes one function up. The field was parsed and rendered nowhere until
    cycle 1562, so the state of every key result was invisible on his phone."""
    texts = _paragraphs(_projects_doc_with_seats(SEATS))
    key_result = next(t for t in texts if t.startswith("Key result"))
    assert key_result.startswith(
        "Key result (discussing) — The work closes your rows.")


def test_a_key_result_with_no_status_prints_no_comma():
    """Absent is not a defect and must not render as a dangling `, .` --
    `project_goals.problems` deliberately lets a block carry no status."""
    from agora_runner.nova_plan import _key_result_prose

    bare = _key_result_prose(["id: k", "name: A thing", "measure: m"])
    assert bare.startswith("**Key result — A thing.**")
    assert "()" not in bare
    blank = _key_result_prose(
        ["id: k", "name: A thing", "measure: m", "status:   "])
    assert blank.startswith("**Key result — A thing.**")


def test_a_status_this_page_does_not_recognise_is_still_printed():
    """The builder prints, `project_goals.problems` judges. A word dropped
    here because it is wrong would hide the very defect the checker reports,
    on the one screen he actually opens."""
    from agora_runner.nova_plan import _key_result_prose

    out = _key_result_prose(["id: k", "name: A thing", "status: proposed"])
    assert out.startswith("**Key result (proposed) — A thing.**")


def test_a_name_with_a_comma_in_it_still_reads_as_one_sentence():
    """Three live key results are named "X, not Y" -- his own phrasing. A
    trailing `, discussing` on those reads as a third clause of his sentence,
    which is why the status is parenthetical and attached to the label."""
    from agora_runner.nova_plan import _key_result_prose

    out = _key_result_prose(
        ["id: k", "name: The work closes your rows, not my own plumbing",
         "status: discussing"])
    assert out.startswith(
        "**Key result (discussing) — The work closes your rows, "
        "not my own plumbing.**")
