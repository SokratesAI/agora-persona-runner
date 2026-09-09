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

from .nova_boards import DEFAULT_PROJECT

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


def render_table(items, columns):
    """A header, its rule and one line per record -- always all columns."""
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
