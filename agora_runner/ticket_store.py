"""One record per ticket, lifted out of one of the owner's board files.

The first slice of the store migration the owner approved on 2026-09-02
(*"go ahead with that idea. You have my full support!"*, capture on
`ideas.md`; idea #5 / idea #231). The measurement behind it is in
`nova/resources/research/couchdb-vs-a-real-ticket-store-2026-09-02.md`:
his two boards are 396 tickets living inside two markdown documents of
1.15 MB, CouchDB already stores `issues.md` as a manifest plus 46 chunks
sliced by *byte offset* rather than by ticket, and every status change
rewrites the whole document -- most of the 38.2 MB of dead revisions the
nightly compaction reclaims.

That write-up says the first cycle of the migration should be the one-way
read into records with a byte-identical re-render as its own test, and
this is that. Nothing here writes to CouchDB and nothing changes how the
boards are stored today. The only claim it makes is the one that has to
hold before any of that is safe: **a ticket can be lifted out of the
markdown and put back without losing a byte of his text.**

The design decision worth knowing before reading the code. A renderer
that rebuilds the file from ticket fields alone cannot be byte-identical,
because these documents carry more than tickets -- `## Discarded`,
`## Processed captures`, a `# Done — detail` heading, two different
write-up heading shapes (`### #70 —` and `## 69 —`, both live, both
deliberately accepted by `nova_boards`), and sixty trailing blank lines.
A renderer that normalised those would pass its own test while quietly
reformatting his prose.

So `to_records` returns a **layout** beside the tickets: the document as
an ordered list of blocks, where a block is either a ticket's table row,
a ticket's write-up, or verbatim text nothing here claims to understand.
`to_markdown` walks it. That makes the round-trip a real test -- it fails
on any byte a ticket record fails to carry -- and it makes the residue
*visible and measurable* rather than lost. `tools.ticket_migrate` prints
how much of each file is tickets and how much is still residue, which is
the number the next slice of this migration has to move.

Parsing reuses `nova_boards` rather than a second set of regexes. Its
rules are what the site and every board tool already run, so a clean
round-trip here is evidence about *those* parsers, not about this module
agreeing with itself.
"""

import re

from . import nova_boards


# A cell boundary is an *unescaped* pipe. Every row on both boards opens
# with an Obsidian wikilink whose alias separator is written `\|`, so a
# naive `split("|")` cuts `[[#174 — ...\|174]]` in half and the re-render
# puts the halves back with spaces around the pipe. That is not a
# hypothetical: it was the only difference between the live files and
# their round-trip, on all 398 rows.
_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _row_cells(line):
    """The cells of a markdown table row, or `None` if it is not one."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in _CELL_SPLIT_RE.split(stripped[1:-1])]


def _render_row(cells):
    return "| " + " | ".join(cells) + " |"


def to_records(markdown):
    """A board file -> `{"tickets": [...], "layout": [...]}`.

    A ticket is `{number, title, status, statusKey, updated, priority,
    priorityKey, project, where, done}` -- exactly the fields
    `parse_board` already hands the site -- plus `cells` (the row as it is
    written today), `detailHeading` (verbatim, because both heading shapes
    are live) and `details` (the write-up body, verbatim).

    `layout` is the whole document in order: `("row", number)`,
    `("detail", number)` and `("text", line)`. Every line of the input is
    in exactly one block, which is what makes the round-trip total.
    """
    markdown = markdown or ""
    parsed = nova_boards.parse_board(markdown)
    lines = markdown.split("\n")
    tickets = {item["number"]: dict(item) for item in parsed["items"]}

    # Which lines belong to a write-up, and to which ticket.
    spans = nova_boards._detail_spans(markdown)
    detail_of = {}
    for number, (heading, body_start, end) in spans.items():
        for index in range(heading, end):
            detail_of[index] = number
        record = tickets.setdefault(number, {"number": number, "cells": None})
        record["detailHeading"] = lines[heading]
        record["details"] = "\n".join(lines[body_start:end])

    layout = []
    seen_rows = set()
    for index, line in enumerate(lines):
        number = detail_of.get(index)
        if number is not None:
            if not layout or layout[-1] != ("detail", number):
                layout.append(("detail", number))
            continue
        cells = _row_cells(line)
        row_number = None
        if cells and len(cells) >= 4:
            match = nova_boards._ROW_NUMBER_RE.search(cells[0])
            if match and int(match.group(1)) in tickets:
                candidate = int(match.group(1))
                # A number written into two tables is one ticket and one
                # boarding slip; `parse_board` keeps the first row, so the
                # second stays verbatim text rather than becoming a second
                # record that would render the first one twice.
                if candidate not in seen_rows and tickets[candidate].get("cells") is None:
                    row_number = candidate
        if row_number is not None:
            tickets[row_number]["cells"] = cells
            seen_rows.add(row_number)
            layout.append(("row", row_number))
            continue
        layout.append(("text", line))

    ordered = [tickets[number] for number in sorted(tickets, reverse=True)]
    for ticket in ordered:
        ticket.setdefault("cells", None)
        ticket.setdefault("details", "")
        ticket.setdefault("detailHeading", None)
    return {"tickets": ordered, "layout": layout}


def to_markdown(records):
    """Records -> the board file. The inverse of `to_records`."""
    by_number = {ticket["number"]: ticket for ticket in records["tickets"]}
    out = []
    for kind, value in records["layout"]:
        if kind == "text":
            out.append(value)
        elif kind == "row":
            out.append(_render_row(by_number[value]["cells"]))
        elif kind == "detail":
            ticket = by_number[value]
            out.append(ticket["detailHeading"])
            out.append(ticket["details"])
        else:  # pragma: no cover - a block kind nothing writes
            raise ValueError(f"unknown layout block {kind!r}")
    return "\n".join(out)


def coverage(markdown, records):
    """`(ticket_bytes, residue_bytes)` -- how much of the file is tickets.

    The number the next slice of the migration has to move. Residue is
    every line no ticket record claims: frontmatter, `## Discarded`,
    `## Processed captures`, table headers, the capture list, and the
    blank lines between everything.
    """
    by_number = {ticket["number"]: ticket for ticket in records["tickets"]}
    owned = 0
    for kind, value in records["layout"]:
        if kind == "row":
            owned += len(_render_row(by_number[value]["cells"])) + 1
        elif kind == "detail":
            ticket = by_number[value]
            owned += len(ticket["detailHeading"]) + len(ticket["details"]) + 2
    return owned, max(len(markdown or "") - owned, 0)
