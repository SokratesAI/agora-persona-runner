"""Board row records -> the generated markdown view of a board.

The third primitive of issue #203, the owner's decision of 2026-09-08 that
the boards get a real schema (`projects/sokrates/projects/nova/board-records.md`,
`status: approved`), after `rank_key` and `entity_id`. That spec asks for
this one by name, twice: *"Markdown becomes a rendered view, not the
record"*, and in the list of what makes the migration safe, *"keep the
generated markdown view from day one, so the GitHub backup and
`vault-drift` keep working and a bad migration is visible in a diff."*

This is the inverse of `nova_boards.parse_board`'s two tables. Records in,
`## Board` and `## Done` out.

**Nothing calls it yet, deliberately**, same as the two primitives before
it. The spec is explicit that the store, the migration and all 29 readers
move in one change with no facade phase, because a facade is the window in
which two stores are both live. This is the pure function that change
needs and that can be checked on its own today.

## The round trip that holds, and the one that does not

`parse_board` -> `render_tables` -> `parse_board` returns the same items.
That is the invariant the migration rests on and the one the tests pin.

Markdown -> records -> markdown is **not** byte-identical, and pretending
otherwise would be the more comfortable claim to make. The live rows are
ragged: on `issues.md` this morning row #204 carries five cells and row
#203 carries six, because `Priority`, `Project`, `Size`, `Milestone` and
`Order` were each appended to the table as a new column and no row was
rewritten on the day. This renders every row at its full width, so the
first generated backup is a one-time reflow of the whole file. It changes
no meaning -- an absent trailing cell and an empty one parse identically
-- and it is visible in exactly the diff the spec wants it visible in.

## A pipe in a cell raises rather than escapes

`_table_rows` masks pipes inside a `[[wikilink]]` and splits on every
other one; nothing there undoes a `\\|`. So a title carrying a pipe cannot
be written into this table by any escaping this parser would read back,
and quietly emitting one would split a row into the wrong cells and lose
its tail. `nova_boards.add_row` already refuses such a title at the door.
This raises `ValueError` instead of returning `None` because a renderer in
a backup path has no caller positioned to check a sentinel.

## `done` is which table, not which status

The record's `done` flag says which of the two tables the row lives in,
and `status` is a separate field that may say `✅ Done` in either. That is
not a redundancy to tidy away: row #204 sits in `## Board` with status
`✅ Done` today. Keying the render off the status instead of the flag
would move it, and `parse_board` reads the `## Board` table first, so the
row would come back with a different `updated` and a `where` it never had.

The `## Done` table is four columns wide and therefore cannot carry a
priority, project, size, milestone or position. That is `parse_board`'s
own shape and not a choice made here, but it does mean the generated view
is lossy for a finished row whose record carries a project other than the
default. The record keeps it; the markdown cannot say it.
"""

from .nova_boards import (
    DEFAULT_PROJECT, _SECTION_RE, _detail_spans)

#: The `## Board` table, left to right. `parse_board` reads these by
#: index, so the order is load-bearing and the names are not -- it drops
#: the header by shape (the first `|---|` rule), never by matching a word.
BOARD_COLUMNS = (
    "#", "Item", "Status", "Updated", "Priority",
    "Project", "Size", "Milestone", "Order",
)

#: The `## Done` table. Four columns, and the third is the date rather
#: than the status: a row in this table is done by virtue of being here.
DONE_COLUMNS = ("#", "Item", "Updated", "Where")


def _cell(value):
    """One record field -> a table cell. Raises on anything unwritable."""
    text = "" if value is None else str(value)
    if "|" in text or "\n" in text:
        raise ValueError(
            "a board cell cannot carry a pipe or a newline; "
            "the table parser splits on every pipe outside a wikilink "
            f"and nothing unescapes one: {text!r}"
        )
    return text.strip()


def _link(number, title):
    """The first cell: an Obsidian wikilink whose alias is the number.

    The same shape `nova_boards.add_row` writes, and the escaped alias
    pipe is what keeps `_table_rows` from splitting the cell in two.
    """
    return f"[[#{number} — {title}\\|{number}]]"


def _line(cells):
    return "| " + " | ".join(cells) + " |"


def _rule(width):
    return "|" + "|".join(["---"] * width) + "|"


def render_row(item):
    """One record -> its `| ... |` line, in whichever table it belongs to.

    An `order` of `None` and an `order` of `0` are both written as an
    empty cell, because `parse_project_order_cell` reads both back as
    unplaced -- a row nobody has dragged has no position at all, which is
    a different answer from position zero and the one this can express.
    """
    number = int(item["number"])
    title = _cell(item.get("title"))
    if item.get("done"):
        return _line([
            _link(number, title),
            title,
            _cell(item.get("updated")),
            _cell(item.get("where")),
        ])
    order = item.get("order")
    return _line([
        _link(number, title),
        title,
        _cell(item.get("status")),
        _cell(item.get("updated")),
        _cell(item.get("priority")),
        _cell(item.get("project") or DEFAULT_PROJECT),
        _cell(item.get("size")),
        _cell(item.get("milestone")),
        _cell(order) if isinstance(order, int) and order > 0 else "",
    ])


def render_table(items, columns, width=None):
    """A header, its rule and one line per record -- always all columns.

    `width` is how many cells `render_row` will write, and a `columns`
    shorter than it is padded from the default names. That is not a
    nicety: `ideas.md`'s header stops at `Milestone` because `Order` was
    appended to the table without the header being rewritten, so keeping
    the owner's header verbatim would draw an eight-column header over
    nine-cell rows. `parse_board` reads by position and never looks at the
    header, so it would still parse -- and it would still be a broken
    table on his page.
    """
    columns = tuple(columns)
    if width and len(columns) < width:
        default = BOARD_COLUMNS if width == len(BOARD_COLUMNS) else DONE_COLUMNS
        columns = columns + tuple(default[len(columns):width])
    lines = [_line([_cell(name) for name in columns]), _rule(len(columns))]
    lines.extend(render_row(item) for item in items)
    return "\n".join(lines)


def render_tables(items):
    """Records -> `{"board": markdown, "done": markdown}`, in list order.

    Order is the caller's. This does not sort, because the rank the spec
    introduces is the caller's business and a renderer that imposed its
    own would silently outrank it.
    """
    return {
        "board": render_table(
            [row for row in items if not row.get("done")], BOARD_COLUMNS),
        "done": render_table(
            [row for row in items if row.get("done")], DONE_COLUMNS),
    }


def render_detail(number, title, body):
    """One row's detail section: its heading and its body.

    `### #N — title`, which is the newer of the two shapes `_DETAIL_RE`
    accepts and the one every cycle has written since. The older `## N —`
    is still parsed and deliberately not emitted: two shapes in one
    generated file would be a difference nothing chose.

    `title` may be empty, because `parse_board` returns detail bodies
    keyed by number and throws the heading text away -- a detail whose row
    is gone from both tables has no title left anywhere in the document.
    The regex allows the empty title, so the body still round-trips; it is
    an orphan either way and inventing a title would hide that.
    """
    heading = f"### #{int(number)} —"
    if title:
        heading = f"{heading} {title}"
    body = (body or "").strip()
    return f"{heading}\n\n{body}" if body else heading


def _header_cells(lines):
    """The header row of the first markdown table in `lines`, or `()`.

    Read here rather than through `nova_boards._table_rows`, which drops
    the header by shape -- it starts at the first `|---|` rule, because
    that is what a *row* reader wants. This wants the line the rule sits
    under, and only that line, so it stops at the rule and never sees a
    cell carrying an escaped wikilink pipe.
    """
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if set(stripped) <= set("|- \t:"):
            return ()
        return tuple(
            cell.strip() for cell in stripped.strip("|").split("|"))
    return ()


#: The block kinds a layout may name. `board` and `done` are the two
#: generated tables, `detail` is one row's write-up by number, and
#: `verbatim` is markdown copied through untouched.
LAYOUT_KINDS = ("board", "done", "detail", "verbatim")


def document_layout(markdown):
    """One board file -> the order of its blocks, with the residue kept whole.

    This is the record home the `tail` argument below was a placeholder
    for. `parse_board` models four things -- captures, the two tables and
    the detail bodies -- and a board file contains more than that: the
    owner's `## Processed captures` archive, the `# Done — detail` heading
    that splits the write-ups in two, and `ideas.md`'s `## Discarded`
    table. Measured 2026-09-09, that residue is 19,653 words of `issues.md`
    and 6,469 of `ideas.md`, and a view rendered from the four keys alone
    deletes every one of them.

    A block is `{"kind": "board"}`, `{"kind": "done"}`,
    `{"kind": "detail", "number": n}` or
    `{"kind": "verbatim", "markdown": ...}`, in document order, covering
    everything from the first heading to the end of the file. The preamble
    -- frontmatter and the capture bullets -- is not in here, because
    `render_document` already draws it from the records and the frontmatter
    it is handed.

    **Position is the whole point, not just presence.** `## Discarded` sits
    *between* the tables and `# Details` on `ideas.md`, and `# Done — detail`
    sits between two runs of write-ups on both boards, so a residue appended
    at the end would be reported lost by
    `board_migration_preflight.words_lost` -- it is a sequence diff, and a
    word deleted here and added there is a document that changed. Holding
    the residue as ordered blocks is what lets the render put each piece
    back where the owner has it.

    It reads `nova_boards`' own line scanner rather than a second one.
    `_detail_spans` already answers "which lines are a write-up" for the
    parser and the delete path, and a third copy of that question here is
    the duplication `prompt.md` says to stop writing at the third instance.
    """
    lines = (markdown or "").split("\n")
    claimed = {}
    for number, (heading_line, _, end) in _detail_spans(markdown).items():
        claimed[heading_line] = (end, {"kind": "detail", "number": int(number)})

    headings = sorted(
        {index for index, line in enumerate(lines) if _SECTION_RE.match(line)}
        | set(claimed))
    for position, line_no in enumerate(headings):
        if line_no in claimed:
            continue
        match = _SECTION_RE.match(lines[line_no])
        name = match.group(2).strip().lower()
        if name not in ("board", "done"):
            continue
        end = headings[position + 1] if position + 1 < len(headings) else len(lines)
        block = {"kind": name}
        # The owner's own header row, kept rather than regenerated:
        # `ideas.md` calls its second column `Idea` where `BOARD_COLUMNS`
        # says `Item`. `parse_board` drops the header by shape and never
        # reads a word of it, so this is invisible to every check written
        # in the parser's terms -- and rendering the constant would rename
        # a column on his page for no reason anyone chose.
        header = _header_cells(lines[line_no + 1:end])
        if header:
            block["columns"] = header
        claimed[line_no] = (end, block)

    blocks = []
    pending = []

    def flush():
        text = "\n".join(pending).strip()
        pending.clear()
        if text:
            blocks.append({"kind": "verbatim", "markdown": text})

    index = headings[0] if headings else len(lines)
    while index < len(lines):
        if index in claimed:
            end, block = claimed[index]
            flush()
            blocks.append(block)
            index = max(end, index + 1)
            continue
        pending.append(lines[index])
        index += 1
    flush()
    return blocks


def _default_order(items, details, titles):
    """The fixed order this drew before a layout could be stored.

    Still the answer for a caller with no layout -- a board being rendered
    for the first time, and every test that only cares about the tables.
    """
    parts = []
    tables = render_tables(items)
    parts.append("## Board\n\n" + tables["board"])
    if any(item.get("done") for item in items):
        parts.append("## Done\n\n" + tables["done"])
    if details:
        sections = ["# Details"]
        for number in details:
            sections.append(
                render_detail(number, titles.get(int(number), ""), details[number]))
        parts.append("\n\n".join(sections))
    return parts


def _laid_out(items, details, titles, layout):
    """The blocks `document_layout` recorded, filled from today's records.

    Three rules, and each of them is a way the stored layout and the live
    records can disagree:

    **A detail the layout does not name is still written.** A row boarded
    since the layout was captured has a write-up and no seat, and dropping
    it would make the generated view lossy in the one direction the whole
    exercise is meant to close. They go after the last detail block, which
    is where a cycle appends one today.

    **A detail the layout names and the records no longer hold is skipped.**
    That row's write-up was deleted; the layout is a memory of the document,
    not a claim about what still exists.

    **`## Done` is written only when a row is in it**, the same rule the
    fixed order follows. A stored layout that names the section outlives
    the last done row, and emitting an empty table would put a heading on
    his page that nothing chose.
    """
    written = set()
    parts = []
    detail_positions = [
        index for index, block in enumerate(layout)
        if block.get("kind") == "detail"]
    last_detail = detail_positions[-1] if detail_positions else None
    for index, block in enumerate(layout):
        kind = block.get("kind")
        if kind == "board":
            parts.append("## Board\n\n" + render_table(
                [row for row in items if not row.get("done")],
                block.get("columns") or BOARD_COLUMNS,
                width=len(BOARD_COLUMNS)))
        elif kind == "done":
            if any(item.get("done") for item in items):
                parts.append("## Done\n\n" + render_table(
                    [row for row in items if row.get("done")],
                    block.get("columns") or DONE_COLUMNS,
                    width=len(DONE_COLUMNS)))
        elif kind == "verbatim":
            text = (block.get("markdown") or "").strip()
            if text:
                parts.append(text)
        elif kind == "detail":
            number = int(block["number"])
            if number in details:
                written.add(number)
                parts.append(
                    render_detail(number, titles.get(number, ""), details[number]))
        else:
            raise ValueError(
                f"a layout block's kind must be one of {LAYOUT_KINDS}, "
                f"not {kind!r}")
        if index == last_detail:
            for number in details:
                if int(number) in written:
                    continue
                written.add(int(number))
                parts.append(render_detail(
                    number, titles.get(int(number), ""), details[number]))
    if last_detail is None:
        for number in details:
            parts.append(render_detail(
                number, titles.get(int(number), ""), details[number]))
    return parts


def render_document(contents, frontmatter="", tail="", layout=None):
    """A parsed board -> the whole markdown document, ready to write back.

    This is the envelope `render_tables` does not draw. The spec asks for
    the generated view by name -- *"keep the generated markdown view from
    day one, so the GitHub backup and `vault-drift` keep working"* -- and
    a board file is more than its two tables: the owner's capture bullets
    sit above the first heading and every row's write-up sits under
    `# Details`. Without this, each of the sixteen writers still on
    `parse_board` would splice its own, which is the twenty-one-joins
    argument that put `board_records.contents` in one place.

    `contents` is `parse_board`'s four keys, which is also exactly what
    `board_records.contents` returns -- that shared shape is the seam, so
    this renders a store read and a parse the same way and neither side
    has to know which it was handed.

    `frontmatter` is passed in rather than derived: it is the owner's, it
    carries the `contract:` line each board file explains itself with, and
    nothing in the records holds it. A caller with no document to take it
    from gets a file without one, which parses.

    The invariant is `parse_board(render_document(parse_board(md)))
    == parse_board(md)`, not byte-identity. `board_view`'s module comment
    says why the stronger claim is false and the tables are only half of
    it: the live files carry detail headings in both accepted shapes and a
    ragged number of table cells, so the first generated write is a
    one-time reflow.

    `layout` is `document_layout`'s answer for the source document, and it
    is the record home for what the four keys do not model. **`parse_board`
    does not model the whole document and a view built from its four keys
    alone deletes the rest.** Measured on the two live boards, 2026-09-09:
    rendering `issues.md` from its own parse drops 19,653 words and
    `ideas.md` drops 6,469 -- his `## Processed captures` archive on both,
    the `# Done — detail` heading, and `ideas.md`'s `## Discarded` table.
    Rendering through the layout takes those to 120 and 216, and what is
    left is `render_detail`'s deliberate heading reflow rather than prose.
    A caller with no layout gets the fixed order and the old hole; that is
    the right answer for a board being rendered for the first time and the
    wrong one for a write-back.

    `tail` predates the layout and is verbatim markdown appended after
    everything else. It stays because a caller that only needs to bolt one
    section on has no document to take a layout from, and because deleting
    an argument in the same commit that supersedes it hides which of the
    two a bug came from.

    `## Done` is written only when a row is in it. Both live boards have
    zero done rows today and neither carries the section, so emitting an
    empty one would put a heading on his page that no cycle chose.
    """
    parts = []
    frontmatter = (frontmatter or "").strip()
    if frontmatter:
        parts.append(frontmatter)

    captures = list(contents.get("captures") or [])
    replies = list(contents.get("captureReplies") or [])
    bullets = []
    for index, text in enumerate(captures):
        bullets.append(f"- {text}")
        for reply in (replies[index] if index < len(replies) else []):
            bullets.append(f"    - {reply}")
    # The empty bullet is not in `contents` and cannot be: `parse_board`
    # drops a bullet with no text, so a generated view built from the parse
    # alone would quietly remove the box the owner types into. Both board
    # files state the contract in their own frontmatter -- *"always leaves
    # exactly one empty bullet there so he can start typing immediately"* --
    # so it is written unconditionally rather than carried through the
    # records.
    bullets.append("- ")
    parts.append("\n".join(bullets))

    items = list(contents.get("items") or [])
    details = contents.get("details") or {}
    titles = {int(item["number"]): item.get("title") or "" for item in items}
    if layout:
        parts.extend(_laid_out(items, details, titles, layout))
    else:
        parts.extend(_default_order(items, details, titles))

    tail = (tail or "").strip()
    if tail:
        parts.append(tail)

    return "\n\n".join(parts) + "\n"
