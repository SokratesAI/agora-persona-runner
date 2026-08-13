"""The archive's *title* is found by heading too, not by substring.

`test_roll_split_is_heading_aware` moved the rolling tools onto
`md_sections` for the `## ` section marker they cut the live file at. It
left three substring searches behind, all about the level-one title:
`_archived` and `_archive_header` located the archive's title with
`archive.split(title, 1)`, `plan` tested for it with `title in archive`,
and `roll_captures.archive_title` derived one from the first live line
starting with `"# "`.

Every file in this family carries a `maintenance:` or `contract:` line
quoting its own structure back at whichever cycle opens it -- that is how
a cycle learns the rules -- so a frontmatter naming its own title is the
normal shape of these files, not a hypothetical.

**No real archive names its own title today**, and the second reader was
right to make that precise: an earlier draft of this docstring called
`digest-archive.md` "one sentence away", which overstates it. What that
file's `maintenance:` line discusses is the *opposite* heading -- "No
`##` heading anywhere in this file" -- and it never mentions the
level-one title at all. This is a latent bug in a file family whose
convention invites it, not a live one; old and new code produce
byte-identical output on all three real archives.

**Reproduced before it was fixed**, on `test_a_frontmatter_naming_the_
archive_title`'s fixture: four archived entries where there were two, one
of them the fragment `title is append only.\\n---`; the rolling cycle line
written *above* the frontmatter's closing `---`, where no reader can see
it; and the frontmatter handed back severed mid-sentence with that `---`
gone. The site concatenates this archive onto the digest section Edvard
opens, so the fragment lands on his page as a cycle line.

**`verify` passed all of that**, which is why the assertions below are
structural rather than a call to `verify`. It compares
`_archived(archive)` against `_archived(new_archive)`, so a splitter that
is wrong the same way on both sides agrees with itself and reports the
roll as sound. A guard sharing its instrument with the thing it guards is
the one shape that costs more than no guard.

Six of the seven tests here fail with the substring searches restored,
and the three halves of the change break **separately and disjointly**,
measured one at a time rather than reasoned about: reverting `_archived`
/ `_archive_header` fails four, `plan`'s presence test fails one,
`archive_title`'s scan fails one, and 4 + 1 + 1 accounts for all six. No
test there is carried by a half it is not named for. The seventh,
`test_an_archive_keeps_its_own_title_line_rather_than_the_specs`, is not
a mutation test and is named as the exception: it pins a behaviour the
fix *created* rather than one it restored, so there is no "before" for it
to differ from.

One further test was written and **deleted rather than shipped**: it put
a `# Nova — Something Else` inside a fenced block to pin `find_title`
skipping fences, and it passed under all three mutations. The title of
these files is always the first `# ` line below the frontmatter, so no
fence can precede it and first-match wins with or without the fix. The
fence skipping is real and lives in `_skippable`, which is tested where
it is defined; a test here would only have looked like coverage.
"""

import pytest

from agora_runner.md_sections import find_title
from tools import roll_captures, roll_digest
from tools.rolling import RollError, _archived, _archive_header

DIGEST_LIVE = """---
type: log
---

# Journal — Digest

## Needs Edvard

Nothing.

## Digest

**Cycle 5** (2026-08-11 17:00) — Fifth.

**Cycle 4** (2026-08-11 16:00) — Fourth.

**Cycle 3** (2026-08-11 15:00) — Third.
"""

# The `maintenance:` line names the title on purpose: that is what these
# files do. It is a plain scalar at column zero, so -- unlike a YAML block
# scalar, which indents and would let a whole-line matcher off the hook --
# only skipping the frontmatter saves you here.
DIGEST_ARCHIVE_NAMING_ITS_TITLE = """---
type: log
maintenance: Digest lines rolled off journal-digest.md, newest first. Everything below the # Journal — Digest Archive title is append only.
---

# Journal — Digest Archive

**Cycle 2** (2026-08-11 14:00) — Second.

**Cycle 1** (2026-08-11 13:00) — First.
"""

CAPTURES_LIVE = """---
type: log
---

# Nova — Issues

## Entries

- 2026-08-13 (Cycle 5) — Fifth.

- 2026-08-12 (Cycle 4) — Fourth.

- 2026-08-11 (Cycle 3) — Third.
"""

CAPTURES_ARCHIVE_NAMING_ITS_TITLE = """---
type: log
maintenance: Captures rolled off Nova's live capture file. Everything under # Nova — Issues Archive is append only, newest first.
---

# Nova — Issues Archive

## Entries

- 2026-08-10 (Cycle 2) — Second.

- 2026-08-09 (Cycle 1) — First.
"""


def _frontmatter(text):
    """The frontmatter block, closing `---` included, or None."""
    if not text.startswith("---\n"):
        return None
    end = text.index("\n---\n", 3)
    return text[: end + len("\n---\n")]


def test_a_frontmatter_naming_the_archive_title_is_not_the_title():
    """Two archived entries, not four, and none of them frontmatter."""
    entries = _archived(DIGEST_ARCHIVE_NAMING_ITS_TITLE, roll_digest.SPEC)
    assert len(entries) == 2
    assert entries == [
        "**Cycle 2** (2026-08-11 14:00) — Second.",
        "**Cycle 1** (2026-08-11 13:00) — First.",
    ]


def test_the_archive_header_keeps_its_frontmatter_whole():
    """The severed-frontmatter half, pinned on its own.

    The substring version cut mid-sentence and dropped the closing `---`,
    so the file stopped being frontmatter at all and its remainder
    rendered as body text on a page Edvard opens.
    """
    header = _archive_header(DIGEST_ARCHIVE_NAMING_ITS_TITLE, roll_digest.SPEC)
    assert _frontmatter(header) == _frontmatter(DIGEST_ARCHIVE_NAMING_ITS_TITLE)
    assert header.endswith("# Journal — Digest Archive\n\n")


def test_the_rolled_digest_line_lands_below_the_title_not_inside_the_frontmatter():
    _, new_archive = roll_digest.plan(
        DIGEST_LIVE, DIGEST_ARCHIVE_NAMING_ITS_TITLE, keep=2
    )
    # Frontmatter byte-identical, which is the claim `_archive_header`'s
    # docstring makes and the substring version broke.
    assert _frontmatter(new_archive) == _frontmatter(DIGEST_ARCHIVE_NAMING_ITS_TITLE)
    assert new_archive.index("**Cycle 3**") > new_archive.index(
        "\n# Journal — Digest Archive\n"
    )
    # And the whole file still reads as three entries under one title.
    assert _archived(new_archive, roll_digest.SPEC) == [
        "**Cycle 3** (2026-08-11 15:00) — Third.",
        "**Cycle 2** (2026-08-11 14:00) — Second.",
        "**Cycle 1** (2026-08-11 13:00) — First.",
    ]


def test_the_same_holds_for_a_capture_archive_with_its_entries_heading():
    """The capture spec cuts twice -- title, then `## Entries` -- so the
    second cut is only correct if the first one was."""
    spec = roll_captures.spec_for(CAPTURES_LIVE)
    assert _archived(CAPTURES_ARCHIVE_NAMING_ITS_TITLE, spec) == [
        "- 2026-08-10 (Cycle 2) — Second.",
        "- 2026-08-09 (Cycle 1) — First.",
    ]
    _, new_archive = roll_captures.plan(
        CAPTURES_LIVE, CAPTURES_ARCHIVE_NAMING_ITS_TITLE, keep=2
    )
    assert _frontmatter(new_archive) == _frontmatter(CAPTURES_ARCHIVE_NAMING_ITS_TITLE)
    assert new_archive.index("(Cycle 3)") > new_archive.index("\n## Entries\n")


def test_an_archive_that_only_mentions_its_title_is_refused_by_name():
    """`plan`'s presence test, broken separately from the splitters.

    A file whose title exists only inside its frontmatter is malformed,
    and the substring test waved it through to be cut there. Refusing is
    the right end of it: guessing where its entries begin is exactly what
    produced the splice.
    """
    mentions_only = DIGEST_ARCHIVE_NAMING_ITS_TITLE.replace(
        "\n# Journal — Digest Archive\n", "\n"
    )
    with pytest.raises(RollError, match="'# Journal — Digest Archive'"):
        roll_digest.plan(DIGEST_LIVE, mentions_only, keep=2)


def test_a_yaml_comment_in_the_frontmatter_is_not_the_live_files_title():
    """`# ` at the start of a frontmatter line is a YAML comment.

    Legal YAML, and one hand-edit away in any of these files. The old
    scan derived `# Nova's captures, newest first Archive`, which a first
    roll would write as a real archive title -- and every roll after it
    would then refuse, having correctly failed to find that title in the
    file it had itself just created.
    """
    live = CAPTURES_LIVE.replace(
        "type: log\n", "type: log\n# Nova's captures, newest first\n"
    )
    assert find_title(live) == "# Nova — Issues"
    assert roll_captures.archive_title(live) == "# Nova — Issues Archive"


def test_an_archive_keeps_its_own_title_line_rather_than_the_specs():
    """`_archive_header` writes back what the file had, verbatim.

    **This became reachable with the fix and the second reader was right
    that nothing pinned it.** It read the old code correctly -- there,
    `plan`'s guard and `_archive_header`'s branch were the same exact
    substring test, so a title differing in case was refused before any
    header was built and the behaviour was dead. `find_heading` is
    case-insensitive and whitespace-tolerant, so under the fix the guard
    *finds* that title, the roll proceeds, and which of the two spellings
    gets written is a live question with a real answer.

    The archive's own is the right one: these files are hand-edited in
    Obsidian, and silently restyling a heading Edvard typed is an edit
    nobody asked for in a file the site renders.
    """
    archive = DIGEST_ARCHIVE_NAMING_ITS_TITLE.replace(
        "# Journal — Digest Archive\n\n**Cycle 2**",
        "# journal — DIGEST archive\n\n**Cycle 2**",
    )
    _, new_archive = roll_digest.plan(DIGEST_LIVE, archive, keep=2)
    titles = [line for line in new_archive.split("\n") if line.startswith("# ")]
    assert titles == ["# journal — DIGEST archive"]
