"""An ask written into `journal-digest.md` reaches no page, so the roll refuses it.

The `## Needs input` box was deleted from the owner's page on 2026-08-16 at
his own ask: runner#229 took the page half, #236 the server half. Since then
`prompt.md` step 7 has said the section is gone and that an ask belongs on the
cycle's own journal card, where the site renders it as a yellow block with the
comment drawer open.

Cycle 921 wrote two questions into the digest section anyway, at 19:21 on
2026-09-04. I found them there two hours later, by eye. One of them asks
whether `platform-config` may go public, which is what decides whether this
loop can keep merging once the private CI allowance runs out -- so the most
consequential question I have put to him this month was sitting in markdown
that `/api/digest` does not carry a key for.

`roll_digest` is the only code that reads the live digest out of the vault
every cycle, seconds after it is written, so the check costs nothing here and
tells the cycle that made the mistake while it can still fix it.
"""

import pytest

from tools import roll_digest
from tools.rolling import RollError

_HEAD = """---
type: log
maintenance: Nova rewrites all three sections every cycle. Do not write a
  Needs input section.
---

# Journal — Digest
"""

_TAIL = """
## Next cycle

**[a-slug]** Do the thing.

## Digest

**Cycle 2** (2026-09-04 19:21) — Second.

**Cycle 1** (2026-09-04 18:36) — First.
"""

# What the file is supposed to look like. Two shapes are legal and both are
# here: no section at all, and a section that says it wants nothing. The
# second one matters because `is_empty_needs` strips emphasis, and every
# cycle that ever wrote that line wrote it bold.
NO_SECTION = _HEAD + _TAIL
EMPTY_SECTION = _HEAD + """
## Needs input

**Nothing.**
""" + _TAIL

# The incident. Cycle 921's own first sentence, kept verbatim, because the
# point of the fixture is that this is a well-formed ask -- correctly shaped,
# opening with the question the way `personality.md` asks for -- in the wrong
# file. Nothing about the writing is the bug.
WITH_AN_ASK = _HEAD + """
## Needs input

**Yes or no: may `platform-config` be made public?** Public repos' Actions minutes are free and private ones are not. Cycle 921.
""" + _TAIL

# The pre-rename spelling, still readable in `digest-archive.md` and in every
# digest revision written before 2026-08-21.
OLD_SPELLING = WITH_AN_ASK.replace("## Needs input", "## Needs Edvard")


def test_an_ask_left_in_the_digest_is_refused():
    with pytest.raises(RollError) as caught:
        roll_digest.plan(WITH_AN_ASK, "")
    message = str(caught.value)
    assert "no renderer reads it" in message
    # The refusal has to be actionable on its own: it names the section it
    # found, the place the ask belongs, and quotes what is actually in it,
    # so a cycle can act without going and reading this test.
    assert "## Needs input" in message
    assert "**Needs input:**" in message
    assert "platform-config" in message


def test_the_old_spelling_is_caught_too():
    with pytest.raises(RollError, match="no renderer reads it"):
        roll_digest.plan(OLD_SPELLING, "")


@pytest.mark.parametrize("live", [NO_SECTION, EMPTY_SECTION], ids=["absent", "nothing"])
def test_a_good_digest_still_rolls(live):
    """The other half of the pair -- a guard that only ever refuses is not a guard.

    `plan` is asked to do real work here rather than merely not raise: with
    `--keep` at 1 there are two lines in and one has to move, so a check that
    quietly aborted the roll would show up as an empty archive.
    """
    new_live, new_archive = roll_digest.plan(live, "", keep=1)
    assert "Cycle 2" in new_live and "Cycle 1" not in new_live
    assert "Cycle 1" in new_archive


def test_the_fixtures_differ_only_in_that_section():
    """The precondition. Without it, a refusal could be about anything.

    `WITH_AN_ASK` is `EMPTY_SECTION` with the ask swapped in for
    `**Nothing.**` and nothing else, so the two tests above are pinned to
    that one difference rather than to some other flaw in one fixture.
    """
    assert WITH_AN_ASK.replace(
        "**Yes or no: may `platform-config` be made public?** Public repos' "
        "Actions minutes are free and private ones are not. Cycle 921.",
        "**Nothing.**",
    ) == EMPTY_SECTION
