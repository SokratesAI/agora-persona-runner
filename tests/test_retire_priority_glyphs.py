"""The one-off that rewrote the ratings already sitting in Edvard's files.

The code change alone only fixed rows something touches again; these are
the 87 that were already written. The whole risk is that the same four
glyphs appear throughout the `# Details` prose -- in his own sentences
and in my write-ups explaining a rating -- so the tool has to be
structural, and these tests are aimed at that rather than at the rename.
"""

from tools.retire_priority_glyphs import retire

# Deliberately shaped like the live files rather than minimally: an alias
# pipe inside the wiki-link (which shifts every column right if the split
# is naive), a four-cell row that never grew a rating, a status glyph that
# must survive, a capture bullet, a closed capture, and prose in
# `# Details` that mentions two ratings and must come back untouched.
DOC = """---
type: board
---

- 🟠 the thing I typed on my phone
- DONE (Cycle 9): 🔴 the older thing
- DONE (Cycle 8): shipped it — 🔵 glyph mid-sentence
- an unrated capture

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#95 — A title with a \\| pipe in it\\|95]] | A title | 🟡 In progress | 08-19 | 🟠 High |
| [[#90 — Another\\|90]] | Another | ⚪ Backlog | 08-17 | ⚪ Low |
| [[#51 — No rating cell\\|51]] | No rating cell | ⚪ Backlog | 08-10 |

# Details

## 95 — A title

Rated 🟠 High rather than 🔴 because nothing is on fire.
"""


def test_a_board_rows_rating_becomes_a_word():
    out, changes = retire(DOC)
    assert "| 08-19 | High |" in out
    assert "| 08-17 | Low |" in out
    assert len([c for c in changes if c[0] == "row"]) == 2


def test_the_status_cell_is_not_touched():
    """He complained about the priority vocabulary, not the status one --
    and `⚪` is in both, so a search-and-replace would have eaten it."""
    out, _ = retire(DOC)
    assert "🟡 In progress" in out
    assert out.count("⚪ Backlog") == 2


def test_his_prose_is_not_touched():
    """The failure this tool exists to avoid: editing his sentences."""
    out, _ = retire(DOC)
    assert "Rated 🟠 High rather than 🔴 because nothing is on fire." in out


def test_a_capture_bullet_gets_the_word_and_the_colon():
    out, changes = retire(DOC)
    assert "- High: the thing I typed on my phone" in out
    assert len([c for c in changes if c[0] == "capture"]) == 2


def test_the_done_marker_stays_outside_the_rating():
    """Both are prefixes on one line and the marker sits outermost. Read
    the rating first and a closed capture reports unrated."""
    out, _ = retire(DOC)
    assert "- DONE (Cycle 9): Immediately: the older thing" in out


def test_a_glyph_partway_into_a_bullet_is_prose_and_stays():
    """A real line in his `issues.md` looks exactly like this -- a cycle
    wrote its DONE note in front of the original capture and pushed the
    rating into the middle of the sentence. From here that is
    indistinguishable from him writing about a coloured dot, and guessing
    would mean editing his words. It keeps the glyph; nothing parses it
    as a rating in that position anyway."""
    out, _ = retire(DOC)
    assert "- DONE (Cycle 8): shipped it — 🔵 glyph mid-sentence" in out


def test_a_row_with_no_rating_cell_is_left_alone():
    out, _ = retire(DOC)
    assert "| [[#51 — No rating cell\\|51]] | No rating cell | ⚪ Backlog | 08-10 |" in out


def test_a_bullet_that_is_only_a_glyph_is_not_turned_into_a_rating():
    """Rewriting it would invent a capture out of an empty one."""
    out, changes = retire("---\n---\n\n- 🟠\n\n## Board\n")
    assert "- 🟠" in out and changes == []


def test_running_it_twice_changes_nothing_the_second_time():
    once, _ = retire(DOC)
    twice, changes = retire(once)
    assert twice == once and changes == []
