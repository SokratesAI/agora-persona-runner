"""An ask belongs to the cycle that raised it, not to a shared block.

Edvard, comments board 2026-08-16: *"the solution i want is to remove the
'needs Edvard' block entirely. If you need something from me, it should be
added in the Journal card somehow and i'll answer in the comment of a journal
card."*
"""

from agora_runner.nova_journal import parse_journal, split_ask


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
    only "Needs Edvard", which is the box-claiming-his-attention-for-nothing
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
