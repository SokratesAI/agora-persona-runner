"""`tools.doc_integrity` -- does a spliced vault document get caught?

The fixture in `SPLICED` is the shape of the real damage, taken from
CouchDB revision 319 of `comments.md` (2026-08-26 10:07:59 Oslo): a
`#### Nova` reply, then the *tail of the frontmatter's own contract line*
starting at the literal `## Acknowledged` it quotes, then a whole second
document header. Reading it is the point -- the invariant only earns
trust if the thing it refuses is the thing that happened.
"""

import io

from tools import doc_integrity

HEALTHY = """---
type: log
contract: Nova moves the item under `## Acknowledged` with one line saying what it did.
---

# Comments

Prose.

## New

### Cycle 470 · 2026-08-26 10:11

Something he said.

## Acknowledged

### Cycle 461 · 2026-08-26 08:58

Something older.
"""

SPLICED = """---
type: log
contract: Nova moves the item under `## Acknowledged` with one line saying what it did.
---

# Comments

Prose.

## New

### Cycle 470 · 2026-08-26 10:11

Something he said.

## Acknowledged

#### Nova · 2026-08-26 10:11

A reply with no comment above it.

## Acknowledged` with one line saying what it did.
---

# Comments

Prose.

## New

### Cycle 466 · 2026-08-26 09:55

Something he said earlier.

## Acknowledged

### Cycle 461 · 2026-08-26 08:58

Something older.
"""


def test_a_healthy_document_has_no_duplicate_headings():
    assert doc_integrity.duplicate_headings(HEALTHY) == {}


def test_the_real_splice_is_caught():
    assert doc_integrity.duplicate_headings(SPLICED) == {
        "# comments": 2,
        "## new": 2,
        "## acknowledged": 2,
    }
    # The spliced-in `## Acknowledged` with one line saying what it did.` is
    # not counted, and should not be: it carries trailing prose, so it is the
    # tail of the frontmatter's contract line rather than a heading. Two real
    # `## Acknowledged` headings is already the finding.


def test_the_frontmatter_is_not_counted():
    """A `contract:` line quoting `## Acknowledged` is not a second heading.

    This is why the module goes through `md_sections._skippable` rather
    than scanning every line: the file names its own headings inside its
    header, on purpose, and counting those reads every healthy document
    as damaged.
    """
    block_scalar = """---
contract: |
  Nova writes under
  ## New
  and retires under
  ## Acknowledged
---

# Comments

## New

## Acknowledged
"""
    assert doc_integrity.duplicate_headings(block_scalar) == {}


def test_a_heading_inside_a_fence_is_not_a_heading():
    fenced = "# Comments\n\n```\n# Comments\n## New\n```\n\n## New\n"
    assert doc_integrity.duplicate_headings(fenced) == {}


def test_repeated_comment_headings_are_legal():
    """`### ` and deeper are not counted -- two comments in one minute are legal."""
    twice = "# Comments\n\n## New\n\n### Cycle 1 · x\n\na\n\n### Cycle 1 · x\n\nb\n"
    assert doc_integrity.duplicate_headings(twice) == {}


def test_a_whole_line_match_is_required():
    """`## Newer` is a different heading from `## New`, twice over."""
    text = "# T\n\n## New\n\n## Newer\n"
    assert doc_integrity.duplicate_headings(text) == {}


def test_check_sorts_paths_into_three_buckets():
    docs = {"a.md": SPLICED, "b.md": HEALTHY, "c.md": None}
    damaged, unreadable, clean = doc_integrity.check(
        ("a.md", "b.md", "c.md"), fetch=docs.get)
    assert [path for path, _ in damaged] == ["a.md"]
    assert unreadable == ["c.md"]
    assert clean == ["b.md"]


def test_damage_exits_2_and_names_the_file():
    out = io.StringIO()
    code = doc_integrity.report([("a.md", {"## new": 2})], [], ["b.md"], out=out)
    assert code == 2
    assert "SPLICED — a.md" in out.getvalue()
    assert "'## new' appears 2 times" in out.getvalue()


def test_unreadable_exits_1_rather_than_0():
    """A document I could not read is no instrument, never a clean bill."""
    out = io.StringIO()
    assert doc_integrity.report([], ["a.md"], ["b.md"], out=out) == 1
    assert "COULD NOT READ — a.md" in out.getvalue()


def test_whole_exits_0_and_names_what_it_swept():
    out = io.StringIO()
    assert doc_integrity.report([], [], ["a.md", "b.md"], out=out) == 0
    assert "Swept 2 document(s): a.md, b.md" in out.getvalue()


def test_damage_outranks_unreadable_in_the_exit_code():
    out = io.StringIO()
    assert doc_integrity.report([("a.md", {"# t": 2})], ["b.md"], [], out=out) == 2


def test_not_found_reads_as_unreadable_not_as_empty(monkeypatch):
    """`vault_tool.py get` prints `[not found: ...]` and exits 0.

    A return code alone would file a vanished document as an undamaged
    one, which is the most reassuring possible way to be wrong.
    """
    class Done:
        returncode = 0
        stdout = "[not found: projects/x.md]\n"

    monkeypatch.setattr(doc_integrity.subprocess, "run", lambda *a, **k: Done())
    assert doc_integrity._fetch("projects/x.md") is None
