"""Acknowledging a comment is a command now, and the command refuses to
corrupt the file the way the hand-rolled version did on 2026-08-13.

The fixture below is not invented. It is the shape of the real
`comments.md`: a `contract:` line in the frontmatter that quotes both
`## New` and `## Acknowledged` back at the reader, 320 characters before
either heading actually appears. Every test here would pass against a
substring search too, except the ones that name it -- which is why the
real bug survived a file whose parser had been correct all along.
"""

import pytest

from agora_runner.md_sections import find_heading, section_bounds
from agora_runner.nova_comments import parse_comments
from tools.ack_comment import AckError, acknowledge

CONTRACT = (
    "contract: Nova reads `## New` at the start of every cycle, acts, then "
    "moves the item under `## Acknowledged` with one line saying what it did."
)

FIXTURE = f"""---
type: log
tags: [agora, nova, comments]
{CONTRACT}
---

# Comments

## New

### Cycle 156 · 2026-08-13 06:44

That report was easier to read than the journals.

#### Nova · 2026-08-13 06:45

Filed.

### Cycle 150 · 2026-08-12 22:31

Older one, still unhandled.

## Acknowledged

### Cycle 120 · 2026-08-11 22:26

Again, go
"""


def test_the_first_mention_of_a_heading_is_in_the_frontmatter():
    """The premise every other test rests on. If this ever stops being true
    the fixture has drifted away from the real file and the suite is
    guarding a shape that no longer exists."""
    body = FIXTURE.split("\n---\n", 1)[0]
    assert "## Acknowledged" in body
    assert FIXTURE.index("## Acknowledged") < FIXTURE.index("\n## New")


def test_find_heading_skips_the_frontmatter_mention():
    lines = FIXTURE.split("\n")
    at = find_heading(lines, "## Acknowledged")
    assert lines[at] == "## Acknowledged"
    assert at > 3


def test_find_heading_skips_a_fenced_example():
    lines = ["# T", "", "```", "## New", "```", "", "## New", "", "x"].copy()
    assert find_heading(lines, "## New") == 6


def test_a_fence_does_not_truncate_the_section_it_sits_in():
    lines = "## New\n\nwrite it like\n\n```\n## Acknowledged\n```\n\ndone".split("\n")
    start, end = section_bounds(lines, "## New")
    assert end == len(lines)


def test_no_frontmatter_at_all_still_finds_the_heading():
    lines = "# Comments\n\n## New\n\nx".split("\n")
    assert find_heading(lines, "## New") == 2


def test_an_unterminated_opening_rule_is_not_frontmatter():
    """Failing this way round keeps a heading findable. The other way round
    swallows the whole file."""
    lines = "---\n\n## New\n\nx".split("\n")
    assert find_heading(lines, "## New") == 2


def _ack(**kwargs):
    args = dict(
        markdown=FIXTURE,
        cycle=156,
        stamp="2026-08-13 06:44",
        note="Boarded as #73.",
        note_stamp="2026-08-13 08:10",
    )
    args.update(kwargs)
    return acknowledge(**args)


def test_the_comment_moves_and_the_frontmatter_does_not():
    out = _ack()
    assert out.split("\n---\n")[0] == FIXTURE.split("\n---\n")[0]
    moved = [c for c in parse_comments(out) if c["cycle"] == 156][0]
    assert moved["acknowledged"]
    assert moved["text"] == "That report was easier to read than the journals."


def test_the_block_lands_below_the_real_heading_not_the_quoted_one():
    """The exact failure. A substring splice puts it at char 320, inside
    `contract:`, where the app's parser cannot see it and no cycle reading
    `## New` ever will either."""
    out = _ack()
    assert out.index("### Cycle 156") > out.index("\n## Acknowledged")
    assert "### Cycle 156" not in out.split("\n---\n")[0]


def test_it_goes_to_the_top_of_acknowledged_not_the_bottom():
    out = _ack()
    assert out.index("### Cycle 156") < out.index("### Cycle 120")


def test_the_note_is_appended_inside_the_comment():
    out = _ack()
    moved = [c for c in parse_comments(out) if c["cycle"] == 156][0]
    assert "Boarded as #73." in moved["reply"]
    assert "Filed." in moved["reply"], "an existing reply must survive the move"


def test_the_other_comments_are_untouched():
    out = _ack()
    before = {c["cycle"]: c for c in parse_comments(FIXTURE)}
    after = {c["cycle"]: c for c in parse_comments(out)}
    assert set(before) == set(after)
    for cycle in (150, 120):
        assert after[cycle]["text"] == before[cycle]["text"]
        assert after[cycle]["acknowledged"] == before[cycle]["acknowledged"]


def test_the_remaining_new_comment_stays_in_new():
    out = _ack()
    lines = out.split("\n")
    start, end = section_bounds(lines, "## New")
    assert any("### Cycle 150" in line for line in lines[start:end])


def test_a_needs_edvard_reply_moves_too():
    src = FIXTURE.replace("### Cycle 150 · 2026-08-12 22:31",
                          "### Needs Edvard · 2026-08-12 22:31")
    out = acknowledge(src, None, "2026-08-12 22:31", "Done.", "2026-08-13 08:10")
    moved = [c for c in parse_comments(out) if c["cycle"] is None][0]
    assert moved["acknowledged"]


def test_a_missing_comment_is_refused_rather_than_guessed():
    with pytest.raises(AckError, match="nothing written"):
        _ack(stamp="2026-08-13 06:45")


def test_a_comment_already_acknowledged_is_refused():
    """It is not under `## New`, so moving it is a no-op at best and a
    duplicate at worst."""
    with pytest.raises(AckError, match="nothing written"):
        _ack(cycle=120, stamp="2026-08-11 22:26")


def test_a_file_with_no_real_acknowledged_heading_is_refused():
    src = FIXTURE.replace("\n## Acknowledged\n", "\n## Retired\n")
    with pytest.raises(AckError, match="no real"):
        acknowledge(src, 156, "2026-08-13 06:44", "x", "2026-08-13 08:10")
    assert "## Acknowledged" in src, "still quoted in the frontmatter"


def test_a_file_with_only_the_frontmatter_mention_is_refused():
    """The strongest one: the headings are gone and only the sentence
    describing them is left. A substring search finds two happy matches
    here and writes into the middle of a YAML value."""
    src = FIXTURE.replace("\n## New\n", "\n## Inbox\n").replace(
        "\n## Acknowledged\n", "\n## Retired\n")
    with pytest.raises(AckError, match="no real"):
        acknowledge(src, 156, "2026-08-13 06:44", "x", "2026-08-13 08:10")


def test_the_verifier_catches_a_frontmatter_edit_it_did_not_intend(monkeypatch):
    """The last line of defence, tested by breaking the mover underneath it
    rather than by trusting that it can never be reached."""
    import tools.ack_comment as module

    monkeypatch.setattr(module, "find_heading", lambda lines, heading: 3)
    with pytest.raises(AckError, match="frontmatter changed"):
        _ack()


def test_writing_it_twice_is_refused_the_second_time():
    once = _ack()
    with pytest.raises(AckError, match="nothing written"):
        acknowledge(once, 156, "2026-08-13 06:44", "again", "2026-08-13 08:11")


def test_the_output_is_stable_when_nothing_follows_the_block():
    """The last comment in `## New` is bounded by the next `##` heading, not
    by another comment -- the case a `_block_bounds` off-by-one eats."""
    out = acknowledge(FIXTURE, 150, "2026-08-12 22:31", "ok", "2026-08-13 08:10")
    moved = [c for c in parse_comments(out) if c["cycle"] == 150][0]
    assert moved["acknowledged"]
    assert moved["text"] == "Older one, still unhandled."
    assert [c["cycle"] for c in parse_comments(out) if not c["acknowledged"]] == [156]
