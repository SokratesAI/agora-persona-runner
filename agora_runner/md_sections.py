"""One definition of "find a real `##` heading", for every file this loop edits.

Three cycles have now shipped a bug from looking for a heading and
finding text that merely *mentions* it. The files this loop maintains
all carry a `contract:` or `maintenance:` line in their YAML
frontmatter that quotes their own headings back at the reader, on
purpose -- that is how a cycle learns the file's rules -- so the first
occurrence of `## Acknowledged` in `comments.md` is not the heading, it
is a sentence about the heading, 320 characters into the frontmatter.

Measured, rather than reasoned about: `comments.md` revision 19 in
Nova's CouchDB (written 2026-08-13 07:06:25 Oslo, repaired by hand as
revision 20) is revision 18 with 740 characters spliced in at exactly
that offset. Edvard's newest comment, moved "into `## Acknowledged`" by
a cycle's throwaway script, landed inside the frontmatter -- where the
app's parser cannot see it and neither can the next cycle reading
`## New`. It was found by accident.

So the rule this module exists to hold: **a heading is a whole line,
outside the frontmatter, outside any fenced code block.** Not a
substring, which is what `str.find` / `str.index` / `str.replace` /
`str.partition` give you, and every one of those is one keystroke away
when you are writing a five-line script at three in the morning.

`nova_comments` already matched whole lines and was right to; what it
did not do is skip the frontmatter and the fences, so it was one
multi-line YAML value away from the same failure. Use these two
functions instead of writing the loop again.
"""

import re

# A heading line: two hashes, whitespace, then something. Anchored at both
# ends, so `## Acknowledged` inside a longer sentence is not a heading and
# neither is `### Cycle 12`.
_SECTION_RE = re.compile(r"^##[ \t]+(?P<name>.+?)[ \t]*$")

# A level-one title line. The second character must be whitespace, so `##`
# is not a title -- the two patterns partition the headings rather than
# overlapping.
_TITLE_RE = re.compile(r"^#[ \t]+(?P<name>.+?)[ \t]*$")

_FENCE_RE = re.compile(r"^[ \t]*(?P<fence>```+|~~~+)")


def _normalise(line):
    """A heading line reduced to what markdown actually renders.

    Runs of whitespace collapse, because `##  Digest` and `## Digest` are
    the same heading to every renderer and to Edvard, and differ to
    `str.find`. This docstring used to promise whitespace tolerance while
    only stripping the ends -- a hand-edit in Obsidian that widened the
    gap after the hashes made `roll_digest` refuse to run on a file that
    was not malformed.
    """
    return " ".join(line.lower().split())


def _skippable(lines):
    """Line indexes that cannot hold a heading: frontmatter and fenced code.

    Frontmatter is only frontmatter when `---` is the very first line, and
    it ends at the next `---` on its own line. A file that opens with a
    horizontal rule and never closes it has no frontmatter rather than
    being entirely frontmatter -- failing that way round means a heading
    stays findable, which is the direction that loses nothing.
    """
    skip = set()
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                skip.update(range(0, i + 1))
                start = i + 1
                break

    fence = None
    for i in range(start, len(lines)):
        match = _FENCE_RE.match(lines[i])
        if fence is None:
            if match:
                fence = match.group("fence")[0]
                skip.add(i)
            continue
        skip.add(i)
        # A fence closes on the same character it opened with, so a ``` block
        # quoting ~~~ stays open. Length is not checked: a longer closing
        # run is legal and a shorter one is somebody's typo, and treating a
        # typo as "still inside the block" hides a heading that is really
        # there.
        if match and match.group("fence")[0] == fence:
            fence = None
    return skip


def find_heading(lines, heading):
    """Index of the line that *is* `heading`, or None.

    Case-insensitive and whitespace-tolerant, because these files are also
    hand-edited in Obsidian. The first real one wins: a heading repeated
    lower down is a malformed file, and picking the first keeps this
    agreeing with every parser that reads top to bottom.
    """
    wanted = _normalise(heading)
    skip = _skippable(lines)
    for i, line in enumerate(lines):
        if i not in skip and _normalise(line) == wanted:
            return i
    return None


def find_title(text):
    """The file's first real `# ` heading line, stripped, or None.

    The `##` functions above are given the heading they are looking for;
    this one is the other direction -- `roll_captures` derives an archive
    title from whatever title the live file happens to carry, so it has
    to *find* one rather than confirm one. It scanned for the first line
    starting with `"# "`, which is the same substring search this module
    exists to replace, one level up.

    Measured, not imagined: `# ` at the start of a line inside YAML
    frontmatter is a **comment**, which is legal and which several of
    these files are one hand-edit away from carrying. Given a frontmatter
    commented `# Nova's captures, newest first`, the old scan derived the
    archive title `# Nova's captures, newest first Archive` -- so a first
    roll would create a permanently mistitled archive and every roll
    after it would refuse, having correctly failed to find that title in
    the file it just wrote.
    """
    lines = text.split("\n")
    skip = _skippable(lines)
    for i, line in enumerate(lines):
        if i not in skip and _TITLE_RE.match(line):
            return line.strip()
    return None


def section_bounds(lines, heading):
    """(start, end) of the body under `heading`, or None if it is absent.

    `start` is the line after the heading; `end` is the next real `##`
    heading or the end of the file. Both ends respect `_skippable`, so a
    `##` line quoted inside a fenced example does not truncate the section
    it is sitting in.
    """
    at = find_heading(lines, heading)
    if at is None:
        return None
    skip = _skippable(lines)
    for i in range(at + 1, len(lines)):
        if i not in skip and _SECTION_RE.match(lines[i]):
            return at + 1, i
    return at + 1, len(lines)


def split_at_heading(text, heading):
    """(text up to and including the heading's own line, the rest), or None.

    The rolling scripts all want this one shape: keep the top of the file
    verbatim, rewrite what is below a heading. Each of them reached for
    `text.find("\\n## Digest\\n")` to get it, which is the substring search
    this module exists to replace -- and on 2026-08-13 at 08:13 it fired
    for real. A cycle wrote a sentence into **Next cycle** naming the
    marker `roll_digest` searches for, with real newlines around it
    instead of escaped ones; the script then cut there, read the rest of
    the Next cycle list as digest lines, and refused to roll. Refusing was
    the correct end of a wrong cut.

    The heading line's trailing newline goes with the head, so the body
    starts at the first character below it and the two halves still
    concatenate back to the input.
    """
    lines = text.split("\n")
    at = find_heading(lines, heading)
    if at is None:
        return None
    cut = sum(len(line) + 1 for line in lines[: at + 1])
    return text[:cut], text[cut:]
