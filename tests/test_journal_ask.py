"""An ask belongs to the cycle that raised it, not to a shared block.

The owner, comments board 2026-08-16: *"the solution i want is to remove the
'needs the owner' block entirely. If you need something from me, it should be
added in the Journal card somehow and i'll answer in the comment of a journal
card."*
"""

from agora_runner.nova_journal import build_status, open_asks, parse_journal, split_ask


def test_an_ask_paragraph_leaves_the_body():
    body = (
        "I did the thing.\n\n"
        "**Needs Edvard:** do you use Codex against these repos?\n\n"
        "And then I did the other thing."
    )
    remainder, ask = split_ask(body)
    assert ask == "do you use Codex against these repos?"
    assert "Needs Edvard" not in remainder
    assert "I did the thing." in remainder
    assert "And then I did the other thing." in remainder


def test_prose_naming_the_old_section_is_not_an_ask():
    """The colon is what makes it a label rather than a mention.

    This is the real opening of entry 011-cycle-11.md, which describes the
    old digest layout. It starts a line, so an optional colon matched it,
    and it parsed as an open ask -- the oldest in the corpus, which is how
    the header's "waiting on you" pill came to point at 2026-08-11 instead
    of at the ask that was actually live.
    """
    body = (
        "**Needs Edvard**, **Next cycle**, and a one-line-per-cycle **Digest**,\n"
        "with the prose kept underneath for when he wants the why."
    )
    remainder, ask = split_ask(body)
    assert ask == ""
    assert remainder == body


def test_a_colon_outside_the_bold_still_reads_as_a_label():
    """`**Needs Edvard**:` is a typo of the convention, not a mention."""  # not-prose: quoting a literal
    _, ask = split_ask("**Needs Edvard**: raise the spending limit.")
    assert ask == "raise the spending limit."


def test_a_wrapped_ask_comes_back_as_one_line():
    """The journal is hard-wrapped, and the wrap is not a sentence boundary."""
    body = "**Needs Edvard:** do you use Codex\nagainst these repos, or not?"
    _, ask = split_ask(body)
    assert ask == "do you use Codex against these repos, or not?"


def test_an_entry_with_no_ask_is_untouched():
    body = "Nothing here needs him.\n\nIt really does not."
    remainder, ask = split_ask(body)
    assert ask == ""
    assert remainder == body


def test_the_label_alone_is_not_an_ask():
    """A cycle that typed the label and no question is asking nothing.

    Rendering the empty case would put a yellow block on the card saying
    only "Needs Edvard", which is the box-claiming-his-attention-for-nothing  # not-prose: quoting a literal
    that hiding the old block on `**Nothing.**` existed to prevent.
    """
    remainder, ask = split_ask("**Needs Edvard:**\n\nThe real prose.")
    assert ask == ""


def test_the_ask_reaches_the_card_payload():
    entries = parse_journal(
        "### 2026-08-16 21:00 (Oslo) — Cycle 247 · a thing\n\n"
        "**Needs Edvard:** answer this.\n\n"
        "The rest of the entry.\n\n"
        "PR: none | Outcome: shipped\n"
    )
    assert len(entries) == 1
    assert entries[0]["ask"] == "answer this."
    assert entries[0]["askSpans"]
    assert "Needs Edvard" not in entries[0]["body"]


def test_the_brief_is_taken_after_the_ask_is_cut():
    """Otherwise an entry that opens with its ask briefs as the ask.

    The card would then print the same sentence twice -- once in the yellow
    block and once as the summary under it -- which is the wall of duplicated
    text this replaced.
    """
    entries = parse_journal(
        "### 2026-08-16 21:00 (Oslo) — Cycle 247 · a thing\n\n"
        "**Needs Edvard:** answer this.\n\n"
        "The rest of the entry.\n\n"
        "PR: none | Outcome: shipped\n"
    )
    brief = " ".join(span.get("text", "") for span in entries[0]["briefSpans"])
    assert "answer this" not in brief
    assert "The rest of the entry" in brief


def test_the_bare_label_is_still_cut_from_the_body():
    """Reviewer finding, Cycle 247. The empty case returned the body untouched.

    `_first_paragraph` then briefed on `**Needs Edvard:**`, so the collapsed  # not-prose: quoting a literal
    card's one line read as the label and the entry's real opening sentence
    never appeared on his page at all.
    """
    remainder, ask = split_ask("**Needs Edvard:**\n\nThe real prose.")
    assert ask == ""
    assert remainder == "The real prose."

    entries = parse_journal(
        "### 2026-08-16 21:00 (Oslo) — Cycle 247 · a thing\n\n"
        "**Needs Edvard:**\n\n"
        "The real prose.\n\n"
        "PR: none | Outcome: shipped\n"
    )
    brief = " ".join(span.get("text", "") for span in entries[0]["briefSpans"])
    assert "Needs Edvard" not in brief
    assert "The real prose" in brief


def test_both_parts_of_a_two_part_cycle_keep_their_asks():
    """Reviewer finding, Cycle 247. The card showed the first and dropped the second.

    The server cuts each part's ask out of its own prose, so a part whose ask
    the card declines to render has lost it outright.
    """
    entries = parse_journal(
        "### 2026-08-16 21:00 (Oslo) — Cycle 247 · the addendum\n\n"
        "**Needs Edvard:** second ask.\n\n"
        "More prose.\n\n"
        "PR: none | Outcome: shipped\n\n"
        "### 2026-08-16 20:00 (Oslo) — Cycle 247 · a thing\n\n"
        "**Needs Edvard:** first ask.\n\n"
        "Some prose.\n\n"
        "PR: none | Outcome: shipped\n"
    )
    assert [e["ask"] for e in entries] == ["second ask.", "first ask."]
    for entry in entries:
        assert "Needs Edvard" not in entry["body"]


def test_open_asks_names_every_card_that_raised_one():
    """The page holds twenty entries; the oldest ask is outside that window.

    #94's ask waited a day on card 247 while the row it blocks sat at the
    top of the owner's board, and nothing on the page said so -- by then the
    card was fourteen down the feed. The header can only point at it if the
    server names it, because the client never fetched that far back.
    """
    entries = parse_journal(
        "### 2026-08-17 09:00 (Oslo) — Cycle 249 · no ask here\n\n"
        "Just work.\n\n"
        "PR: none | Outcome: shipped\n\n"
        "### 2026-08-16 21:20 (Oslo) — Cycle 247 · an ask\n\n"
        "**Needs Edvard:** do you use Codex against these repos?\n\n"
        "PR: none | Outcome: shipped\n"
    )
    assert open_asks(entries) == [
        {"cycle": 247, "date": "2026-08-16", "time": "21:20"},
    ]


def test_open_asks_keeps_the_newest_first_order():
    """Which end is the oldest is the whole answer, so the order is a contract.

    The client takes the last match as the longest-waiting one. Reverse this
    list and the header points at the freshest ask instead -- still an ask,
    still plausible, and exactly the wrong one.
    """
    entries = parse_journal(
        "### 2026-08-17 09:00 (Oslo) — Cycle 249 · newer ask\n\n"
        "**Needs Edvard:** the newer question.\n\n"
        "PR: none | Outcome: shipped\n\n"
        "### 2026-08-16 21:20 (Oslo) — Cycle 247 · older ask\n\n"
        "**Needs Edvard:** the older question.\n\n"
        "PR: none | Outcome: shipped\n"
    )
    assert [a["cycle"] for a in open_asks(entries)] == [249, 247]


def test_an_ask_with_no_cycle_number_is_not_listed():
    """A report has no card, so an ask in one has nowhere to be answered."""
    assert open_asks([{"ask": "answer me", "cycle": None, "date": "2026-08-16"}]) == []


def test_the_status_header_carries_the_asks():
    entries = parse_journal(
        "### 2026-08-16 21:20 (Oslo) — Cycle 247 · an ask\n\n"
        "**Needs Edvard:** answer this.\n\n"
        "PR: none | Outcome: shipped\n"
    )
    assert build_status(entries)["asks"] == [
        {"cycle": 247, "date": "2026-08-16", "time": "21:20"},
    ]


# --- The label the owner reads changed; the archive's did not -----------------
#
# the owner, unboarded capture 2026-08-21: *"Change the 'needs the owner' to
# 'needs input'."* Every test above this line writes the old spelling, and
# that is the backward-compatibility half of this change under test -- the
# 363 entries already in the vault are never edited, so the day `Needs
# the owner` stops parsing is the day every ask in the archive unrenders.


def test_the_new_label_parses_as_an_ask():
    body = (
        "I did the thing.\n\n"
        "**Needs input:** do you use Codex against these repos?\n\n"
        "And then I did the other thing."
    )
    remainder, ask = split_ask(body)
    assert ask == "do you use Codex against these repos?"
    assert "Needs input" not in remainder
    assert "I did the thing." in remainder


def test_the_new_label_needs_the_colon_too():
    """The colon is what separates a label from a mention, both spellings."""
    assert split_ask("**Needs input** raise the spending limit.")[1] == ""
    assert split_ask("**Needs input**: raise the limit.")[1] == "raise the limit."


def test_the_new_label_reaches_the_status_header():
    entries = parse_journal(
        "### 2026-08-21 21:20 (Oslo) — Cycle 308 · an ask\n\n"
        "**Needs input:** Yes or no, is this the label you meant?\n\n"
        "PR: none | Outcome: shipped\n"
    )
    assert build_status(entries)["asks"] == [
        {"cycle": 308, "date": "2026-08-21", "time": "21:20"},
    ]
    assert "Needs input" not in entries[0]["body"]
