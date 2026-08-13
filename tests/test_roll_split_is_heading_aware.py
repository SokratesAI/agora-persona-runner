"""The rolling scripts find their section by heading, not by substring.

`md_sections` was built for `nova_comments` after a comment landed inside
`comments.md`'s frontmatter. The three rolling tools kept their own
`text.find("\\n## Digest\\n")`, which is the same search with the same blind
spots, and they run against the two files Edvard actually opens.

**Five of the six tests here fail when the tool goes back to `str.find`,
and the sixth is named as the exception.** That count is the claim, and
it is checked rather than asserted -- an earlier draft of this docstring
said "every test", which was false for three of them at the time and
would have stayed false silently. `test_a_file_with_no_such_heading_is_
still_refused_by_name` is the deliberate exception: it passes either way
because it is not about the fix, it is about the refactor turning a `-1`
return into a `None`, which without a caller check is a `TypeError`
instead of the named refusal these scripts are run by hand on.
"""

import pytest

from agora_runner.md_sections import split_at_heading
from tools import normalise_captures, roll_captures, roll_digest
from tools.rolling import RollError

# **A file already damaged by the splice bug, not a tidy hypothetical.**
# The first draft of this fixture put the marker in a YAML block scalar,
# which reads as the realistic case and pins nothing: a block scalar
# indents its lines, so `"\n## Digest\n"` never matches one and the test
# passed with the substring search restored. What a splice actually
# produces is a heading at column zero inside the frontmatter -- that is
# `comments.md` revision 19, written 2026-08-13 07:06 -- and it is the
# state in which a tool that rewrites the file most needs to not cut.
DIGEST_WITH_MARKER_IN_FRONTMATTER = """---
type: log
maintenance: Nova rewrites all three sections every cycle.
## Digest
holds the newest 12 lines; the rest roll off to digest-archive.md.
---

# Journal — Digest

## Needs Edvard

Nothing.

## Next cycle

Roll the digest.

## Digest

**Cycle 5** (2026-08-11 17:00) — Fifth.

**Cycle 4** (2026-08-11 16:00) — Fourth.

**Cycle 3** (2026-08-11 15:00) — Third.
"""

CAPTURES_WITH_MARKER_IN_A_FENCE = """---
type: note
---

# Nova — Issues

The append command needs the marker:

```bash
python3 vault_tool.py append issues.md /tmp/cap.md '
## Entries
'
```

## Entries

- 2026-08-13 (Cycle 3) — Third.

- 2026-08-12 (Cycle 2) — Second.

- 2026-08-11 (Cycle 1) — First.
"""


def test_a_digest_marker_quoted_in_frontmatter_is_not_the_section():
    """`str.find` cuts inside the frontmatter and reads the rest of the
    file as digest lines -- so the first "entry" is the closing `---`,
    `_check_entry` refuses, and the roll never happens."""
    new_live, new_archive = roll_digest.plan(
        DIGEST_WITH_MARKER_IN_FRONTMATTER, "", keep=2
    )
    assert "**Cycle 3**" in new_archive
    assert "**Cycle 3**" not in new_live
    # The frontmatter is above the cut and comes through untouched.
    assert new_live.startswith(DIGEST_WITH_MARKER_IN_FRONTMATTER.split("---\n\n")[0])
    assert "## Digest\n" in new_live


def test_verification_of_that_digest_passes_its_own_check_render():
    """`_check_render` rebuilds the input around the same marker, so it
    has to agree with `plan` about where the section starts.

    Deliberately assertion-free: the whole claim is that `verify` does not
    raise, and there is nothing else to compare it against that `plan` did
    not also produce. With the substring search restored it raises, which
    is what makes this a test rather than a smoke run.
    """
    live = DIGEST_WITH_MARKER_IN_FRONTMATTER
    roll_digest.verify(live, "", *roll_digest.plan(live, "", keep=2))


def test_a_capture_marker_inside_a_fenced_block_is_not_the_section():
    """The command that appends to these files is itself an example that
    contains the marker, and cycles paste examples into captures."""
    new_live, new_archive = roll_captures.plan(
        CAPTURES_WITH_MARKER_IN_A_FENCE, "", keep=2
    )
    assert "```bash" in new_live
    assert "(Cycle 1) — First." in new_archive
    assert "(Cycle 1) — First." not in new_live
    # Nothing from inside the fence was carried out as a capture.
    assert "vault_tool.py append" not in new_archive


def test_normalise_reorders_below_the_real_heading_not_the_fenced_one():
    # The shape `normalise_captures` exists for: a newest-first stream on
    # top and an oldest-first one below it, from unmarked appends.
    scrambled = CAPTURES_WITH_MARKER_IN_A_FENCE.replace(
        "- 2026-08-12 (Cycle 2) — Second.\n\n- 2026-08-11 (Cycle 1) — First.",
        "- 2026-08-11 (Cycle 1) — First.\n\n- 2026-08-12 (Cycle 2) — Second.",
    )
    out = normalise_captures.normalise(scrambled)
    # Asserted against a literal rather than by re-splitting `out` with
    # the helper under test: a substring split cuts inside the fence, so
    # the closing quote and backticks fall into the body and come back out
    # reordered as if they were captures. Only the whole fenced block,
    # verbatim, can tell those two outcomes apart.
    assert "'\n## Entries\n'\n```\n\n## Entries\n" in out
    # Split the result the same heading-aware way for the ordering claim,
    # or this test does the very thing it is checking the tool no longer does.
    head, body = split_at_heading(out, "## Entries")
    assert body.index("(Cycle 3)") < body.index("(Cycle 2)")
    assert "```bash" in head


def test_a_hand_edited_heading_with_stray_whitespace_still_rolls():
    """These files are also edited in Obsidian. `"\\n## Digest\\n"` misses
    `## Digest ` outright and the tool refuses to run on a file that is
    not actually malformed."""
    live = DIGEST_WITH_MARKER_IN_FRONTMATTER.replace(
        "\n## Digest\n", "\n##   Digest  \n"
    )
    new_live, new_archive = roll_digest.plan(live, "", keep=2)
    assert "**Cycle 3**" in new_archive
    assert "##   Digest  \n" in new_live


def test_a_file_with_no_such_heading_is_still_refused_by_name():
    """The failure path has to stay a named refusal, not a crash: the
    scripts are run by hand every cycle and the message is the whole
    interface."""
    with pytest.raises(RollError, match="'## Digest'"):
        roll_digest.plan("# Journal — Digest\n\nNothing here.\n", "", keep=2)
    with pytest.raises(RollError, match="'## Entries'"):
        normalise_captures.normalise("# Nova — Issues\n\n- one\n")
