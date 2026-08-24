"""Acknowledging a comment is a command now, and the command refuses to
corrupt the file the way the hand-rolled version did on 2026-08-13.

The fixture below is not invented. It is the shape of the real
`comments.md`: a `contract:` line in the frontmatter that quotes both
`## New` and `## Acknowledged` back at the reader, at character 305, well
before either heading actually appears -- the 2026-08-13 splice landed at
320, immediately after that mention.

**Be honest about what most of these tests pin.** Measured, not assumed:
reverting `md_sections` to the plain whole-line matcher `nova_comments`
already had before this change leaves 19 of them green. That is not a
flaw in them -- they pin the move itself, which had no tests at all
because it had no code, only a script a cycle retyped every hour -- but
it means they are not evidence for the frontmatter and fence skips.
Whole-line matching alone defeats the real `contract:` line, because it
is one long sentence; only a raw substring search collides with it, and
no code in this repo ever did that. The three that genuinely need the new
logic say so in their names, and `test_acknowledge_survives_a_block_scalar
_contract` is the only one that needs it through the whole command.
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


def test_find_heading_skips_a_heading_that_is_a_whole_line_of_frontmatter():
    """The frontmatter skip and the whole-line match each stop the real
    2026-08-13 bug on their own, so neither can be seen by mutating the
    other away -- both survive alone and the pair only fails when both are
    broken. This is the case only the frontmatter skip catches: YAML block
    scalars are multi-line, so a `contract:` written as one puts a bare
    `## New` on a line of its own inside the frontmatter."""
    lines = [
        "---",
        "contract: |",
        "  A cycle reads",
        "## New",
        "  every cycle.",
        "---",
        "",
        "## New",
        "",
        "x",
    ]
    assert find_heading(lines, "## New") == 7


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
    """The note is its own `#### Nova` block below whatever the reply worker
    already said, so the two are separate replies rather than one -- which is
    what lets the app paint the cycle's answer purple instead of printing its
    heading as text in the middle of the blue one."""
    out = _ack()
    moved = [c for c in parse_comments(out) if c["cycle"] == 156][0]
    assert [(r["author"], r["text"]) for r in moved["replies"]] == [
        ("commentator", "Filed."),
        ("cycle", "Boarded as #73."),
    ]
    assert moved["reply"] == "Filed.", "an existing reply must survive the move"


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


BLOCK_SCALAR = FIXTURE.replace(
    CONTRACT,
    "contract: |\n"
    "  A cycle reads\n"
    "## New\n"
    "  at the start of every cycle, then moves what it acted on under\n"
    "## Acknowledged\n"
    "  with one line on what it did.",
)


def test_acknowledge_survives_a_block_scalar_contract():
    """The whole command, not just `find_heading`, on the one frontmatter
    shape whole-line matching cannot save you from. A YAML block scalar puts
    bare `## New` and `## Acknowledged` lines inside the frontmatter, so the
    pre-diff matcher finds both of them first and files the owner's comment
    into the file's own header -- which is the 2026-08-13 failure, reached
    through the real entry point."""
    out = acknowledge(
        BLOCK_SCALAR, 156, "2026-08-13 06:44", "Boarded.", "2026-08-13 08:10"
    )
    assert out.split("\n---\n")[0] == BLOCK_SCALAR.split("\n---\n")[0]
    moved = [c for c in parse_comments(out) if c["cycle"] == 156][0]
    assert moved["acknowledged"]
    assert out.index("### Cycle 156") > out.index("\n## Acknowledged\n\n### Cycle")


def test_a_bystander_losing_its_reply_is_refused(monkeypatch):
    """`_verify` promises every other comment is untouched. Before this it
    compared only text and section, so a bystander stripped of the reply Nova
    wrote it passed as unchanged."""
    # The parse the checks run on lives in `nova_comments` now, shared with
    # the two writers that run unattended -- so this patches it where it is
    # defined rather than where `ack_comment` imported it, and keys off the
    # text rather than a call count, because `acknowledge` parses the
    # original twice and the updated once.
    import agora_runner.nova_comments as module

    real = module.parse_comments
    seen = []

    def strip_a_reply(markdown):
        out = [dict(c) for c in real(markdown)]
        if not seen:
            seen.append(markdown)
        if markdown != seen[0]:  # the "after" parse only, so the two disagree
            for c in out:
                if c["cycle"] == 120:
                    c["reply"] = ""
        return out

    monkeypatch.setattr(module, "parse_comments", strip_a_reply)
    src = FIXTURE.replace("### Cycle 120 · 2026-08-11 22:26\n\nAgain, go",
                          "### Cycle 120 · 2026-08-11 22:26\n\nAgain, go\n\n"
                          "#### Nova · 2026-08-11 22:30\n\nDone.")
    with pytest.raises(AckError, match="changed too"):
        acknowledge(src, 156, "2026-08-13 06:44", "x", "2026-08-13 08:10")


def test_no_blank_line_is_gained_or_lost_around_the_move():
    """Whitespace is not cosmetic here: the site ends a card at a blank line,
    so a block that drags its trailing blanks along and then gets another
    one appended splits into two cards on the owner's screen."""
    out = _ack()
    assert "\n\n\n" not in out


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
