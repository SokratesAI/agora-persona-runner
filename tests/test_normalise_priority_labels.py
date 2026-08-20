"""The tool that rewrites the ratings already sitting in Edvard's files.

The code change alone only fixes rows something touches again; this
rewrites the ones already written. The whole risk is that the same four
glyphs appear throughout the `# Details` prose -- in his own sentences
and in my write-ups explaining a rating -- so the tool has to be
structural, and these tests are aimed at that rather than at the rename.

It used to go one way only (glyph -> word, Cycle 268). Edvard reversed
that the next morning, so the tests now fix the property that matters
instead: **whatever `PRIORITY_LABELS` says is what ends up in the file,
from any spelling that has ever been in it.** `test_it_follows_the_labels
_rather_than_a_direction` is the one that would have caught the original
design, and it is written against a patched dict so it keeps working
whichever way the real one points.
"""

from unittest.mock import patch

from agora_runner import nova_boards
from tools.normalise_priority_labels import normalise

# Deliberately shaped like the live files rather than minimally: an alias
# pipe inside the wiki-link (which shifts every column right if the split
# is naive), a four-cell row that never grew a rating, a status glyph that
# must survive, a capture bullet, a closed capture, and prose in
# `# Details` that mentions two ratings and must come back untouched.
#
# The two rating *cells* carry the wordless spelling because that is what
# Cycle 268 left in his files and what this run has to convert back; the
# first capture bullet carries the bare glyph, because captures he typed
# before that cycle still do.
DOC = """---
type: board
---

- 🟠 the thing I typed on my phone
- DONE (Cycle 9): Immediately: the older thing
- DONE (Cycle 8): shipped it — 🔵 glyph mid-sentence
- an unrated capture

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#95 — A title with a \\| pipe in it\\|95]] | A title | 🟡 In progress | 08-19 | High |
| [[#90 — Another\\|90]] | Another | ⚪ Backlog | 08-17 | Low |
| [[#51 — No rating cell\\|51]] | No rating cell | ⚪ Backlog | 08-10 |

# Details

## 95 — A title

Rated 🟠 High rather than 🔴 because nothing is on fire.
"""


def test_a_board_rows_rating_gets_its_glyph_back():
    out, changes = normalise(DOC)
    assert "| 08-19 | 🟠 High |" in out
    assert "| 08-17 | ⚪ Low |" in out
    assert len([c for c in changes if c[0] == "row"]) == 2


def test_the_status_cell_is_not_touched():
    """He complained about the priority vocabulary, not the status one --
    and `⚪` is in both, so a search-and-replace would have eaten it."""
    out, _ = normalise(DOC)
    assert "🟡 In progress" in out
    assert out.count("⚪ Backlog") == 2


def test_his_prose_is_not_touched():
    """The failure this tool exists to avoid: editing his sentences."""
    out, _ = normalise(DOC)
    assert "Rated 🟠 High rather than 🔴 because nothing is on fire." in out


def test_a_bare_glyph_capture_gains_the_word_and_the_colon():
    """The spelling that was never readable on its own -- Edvard's whole
    complaint -- and the one this has to leave carrying both halves."""
    out, changes = normalise(DOC)
    assert "- 🟠 High: the thing I typed on my phone" in out
    assert len([c for c in changes if c[0] == "capture"]) == 2


def test_a_wordless_capture_gains_the_glyph():
    """The other direction, on a bullet Cycle 268 itself rewrote."""
    out, _ = normalise(DOC)
    assert "- DONE (Cycle 9): 🔴 Immediately: the older thing" in out


def test_a_glyph_partway_into_a_bullet_is_prose_and_stays():
    """A real line in his `issues.md` looks exactly like this -- a cycle
    wrote its DONE note in front of the original capture and pushed the
    rating into the middle of the sentence. From here that is
    indistinguishable from him writing about a coloured dot, and guessing
    would mean editing his words. It keeps the glyph; nothing parses it
    as a rating in that position anyway."""
    out, _ = normalise(DOC)
    assert "- DONE (Cycle 8): shipped it — 🔵 glyph mid-sentence" in out


def test_a_row_with_no_rating_cell_is_left_alone():
    out, _ = normalise(DOC)
    assert "| [[#51 — No rating cell\\|51]] | No rating cell | ⚪ Backlog | 08-10 |" in out


def test_a_bullet_that_is_only_a_glyph_is_not_turned_into_a_rating():
    """Rewriting it would invent a capture out of an empty one."""
    out, changes = normalise("---\n---\n\n- 🟠\n\n## Board\n")
    assert "- 🟠" in out and changes == []


def test_a_cell_that_is_not_a_rating_at_all_is_left_alone():
    """`priority_key` maps anything to *something*; only the four buckets
    may be rewritten, or a stray note in the fifth column becomes a
    rating."""
    doc = "---\n---\n\n## Board\n\n| [[#1\\|1]] | T | ⚪ Backlog | 08-01 | ask Edvard |\n"
    out, changes = normalise(doc)
    assert "| ask Edvard |" in out and changes == []


def test_running_it_twice_changes_nothing_the_second_time():
    once, _ = normalise(DOC)
    twice, changes = normalise(once)
    assert twice == once and changes == []


def test_it_follows_the_labels_rather_than_a_direction():
    """The property the first version of this tool did not have.

    It matched on the glyph and wrote the word, so when Edvard reversed
    the decision it could not run: the cells it had already rewritten no
    longer carried anything it recognised. Keying on `priority_key`
    instead means a third reversal is an edit to `PRIORITY_LABELS` and
    one run of this, with no code change here at all -- so the test
    patches that dict to a spelling neither cycle ever used and checks
    the tool simply follows it.
    """
    invented = dict(nova_boards.PRIORITY_LABELS, high="HIGH!!")
    with patch.object(nova_boards, "PRIORITY_LABELS", invented), \
            patch("tools.normalise_priority_labels.PRIORITY_LABELS", invented):
        out, changes = normalise(DOC)
    assert "| 08-19 | HIGH!! |" in out
    assert "- HIGH!!: the thing I typed on my phone" in out
    assert len(changes) >= 2
