"""The digest roll reads the whole document, not only the section it moves.

On 2026-08-26 `journal-digest.md` went live holding two `## Needs input`
sections and two `## Next cycle` sections: the previous cycle's block and
the current one's, stacked. The site hid it -- `md_sections._sections`
keys by heading text and keeps the last of each, so `parse_digest` on the
damaged file returns the newer block and nothing else, measured against
the real file. Obsidian, which the owner actually reads, renders both.

`tools.doc_integrity` found it, correctly, at the *start* of the next
cycle -- twenty minutes on, with a whole cycle's writes still to land on
top. `roll_digest` had read the same bytes out of the vault seconds after
they were written and said nothing, because every check it carries
reasons about the `## Digest` section `plan` split out, and the damage was
above it. A faithful roll of a damaged document is a damaged document.

So the invariant moved to where the file is already in hand. The two
tests below are the pair: the guard turns red on the real shape of the
incident, and stays green on the shape the file is supposed to have --
a check that only ever refuses would pass the first test while refusing
every good digest too.
"""

import pytest

from tools import roll_digest
from tools.rolling import RollError

_HEAD = """---
type: log
---

# Journal — Digest
"""

_TAIL = """
## Digest

**Cycle 3** (2026-08-26 19:14) — Third.

**Cycle 2** (2026-08-26 18:58) — Second.

**Cycle 1** (2026-08-26 18:36) — First.
"""

WHOLE = _HEAD + """
## Next cycle

**[a-slug]** Do the thing.
""" + _TAIL

# The incident, in its own shape: the older block left in place and the
# newer one written under it. Only the headings matter to the guard, but
# keeping the two bodies different is what makes the fixture the bug
# rather than an accidental copy-paste of one line.
SPLICED = _HEAD + """
## Needs input

Yes or no, will you run one `kubectl patch` for me?

## Next cycle

**[a-slug]** The stale copy.

## Needs input

Yes or no, will you run one `kubectl patch` for me?

## Next cycle

**[a-slug]** The copy the site renders.
""" + _TAIL


def test_a_spliced_live_digest_is_refused_before_anything_is_written():
    with pytest.raises(RollError) as excinfo:
        roll_digest.plan(SPLICED, "", keep=2)
    message = str(excinfo.value)
    assert "duplicated heading" in message
    # Both duplicated headings named, not just the first one found: a
    # cycle repairing this needs the whole list or it fixes one and is
    # refused again on the next run.
    assert "'## needs input'" in message
    assert "'## next cycle'" in message


def test_an_undamaged_live_digest_still_rolls():
    new_live, new_archive = roll_digest.plan(WHOLE, "", keep=2)
    assert "**Cycle 3**" in new_live
    assert "**Cycle 1**" in new_archive
    assert "**Cycle 1**" not in new_live


def test_the_guard_runs_before_the_marker_is_located():
    """A document with no `## Digest` at all is still told what is wrong.

    `_body` raises its own error when the marker is missing, so ordering
    the hook after it would report a spliced file as a file with no
    digest section -- the wrong cause, which is the failure this loop
    keeps paying for. Pinned because the ordering is a one-line move.
    """
    with pytest.raises(RollError) as excinfo:
        roll_digest.plan(SPLICED.replace("\n## Digest\n", "\n## Something\n"), "", keep=2)
    assert "duplicated heading" in str(excinfo.value)
