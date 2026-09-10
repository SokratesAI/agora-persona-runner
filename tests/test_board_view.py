"""The renderer's job is a round trip, so most tests are a round trip.

The failure to guard against is not an exception. It is a table that looks
like a board and parses back into rows that are subtly not the rows that
went in -- a lost project, a row that moved between the two tables, a
position that came back as `None`. So the assertions here mostly feed the
output straight back to `nova_boards.parse_board` and compare records, and
the ones that don't are about a cell that cannot be written at all.
"""

import re

import pytest

from agora_runner.board_view import (
    BOARD_COLUMNS,
    board_width,
    DONE_COLUMNS,
    render_detail,
    render_document,
    render_row,
    render_table,
    render_tables,
)
from agora_runner.nova_boards import (
    DEFAULT_PROJECT,
    canonical_priority,
    canonical_size,
    parse_board,
    priority_key,
    size_key,
    status_key,
)


def board(items):
    """Records -> a whole board document, the way a backup would write it."""
    tables = render_tables(items)
    return (
        "# Issues\n\n## Board\n\n" + tables["board"]
        + "\n\n## Done\n\n" + tables["done"] + "\n"
    )


def roundtrip(items):
    return {row["number"]: row for row in parse_board(board(items))["items"]}


def row(number, **overrides):
    """A record shaped exactly as `parse_board` returns one.

    The four derived keys are computed here rather than written by hand,
    because they are `parse_board`'s normalisation of the display strings
    beside them and a fixture that spells them itself gets them wrong --
    `🟠 High` keys to `high` but `🔴 Immediately` keys to `immediate`, and
    a `🍔 Medium` size comes back displayed as `M`. Nothing in
    `board_view` reads any of them, so deriving them here is mirroring
    the parser rather than re-spelling the code under test.
    """
    item = {
        "number": number,
        "title": f"Row {number}",
        "status": "⚪ Backlog",
        "updated": "09-09",
        "where": "",
        "priority": "",
        "project": DEFAULT_PROJECT,
        "size": "",
        "milestone": "",
        "order": None,
        "done": False,
    }
    item.update(overrides)
    if item["done"]:
        item["status"] = "✅ Done"
    item["priority"] = canonical_priority(item["priority"]) or item["priority"]
    item["size"] = canonical_size(item["size"]) or item["size"].strip()
    item["statusKey"] = "done" if item["done"] else status_key(item["status"])
    item["priorityKey"] = priority_key(item["priority"])
    item["sizeKey"] = size_key(item["size"])
    return item


def cell_count(line):
    """Cells in a rendered row, with the wikilink's escaped pipe masked
    out the way `_table_rows` masks it before it splits."""
    return len(re.sub(r"\[\[[^\[\]]*\]\]", "L", line).strip("|").split("|"))


def test_a_full_row_survives_the_round_trip():
    item = row(
        41,
        title="Give the boards a real schema",
        status="🟡 In progress",
        priority="🔴 Immediately",
        priorityKey="immediately",
        project="Marcus",
        size="🍔 Medium",
        sizeKey="m",
        milestone="Backup",
        order=3,
    )
    assert roundtrip([item])[41] == item


def test_every_field_of_an_empty_row_survives_too():
    assert roundtrip([row(7)])[7] == row(7)


def test_a_done_row_lands_in_the_done_table():
    item = row(9, done=True, updated="09-01", where="#123")
    assert roundtrip([item])[9] == item


def test_done_is_which_table_not_which_status():
    """Row #204 sits in `## Board` today reading `✅ Done`, and stays there.

    Keying the render off the status would move it into `## Done`, where
    the third column is the date and the fourth is the PR list -- so it
    would come back carrying `updated` in `status` and a `where` it never
    had. The flag and the status are separate fields on purpose.
    """
    item = row(204, status="✅ Done", done=False)
    back = roundtrip([item])[204]
    assert back == item
    assert back["where"] == ""
    assert "#204" in render_tables([item])["board"]
    assert "#204" not in render_tables([item])["done"]


def test_a_blank_project_is_written_as_the_default():
    """`parse_board` defaults a blank cell, so leaving it blank would be
    an identical round trip and a worse backup: the generated file is what
    a human reads in the GitHub mirror, and it should say which project."""
    line = render_row(row(5, project=""))
    assert f"| {DEFAULT_PROJECT} |" in line
    assert roundtrip([row(5, project="")])[5]["project"] == DEFAULT_PROJECT


def test_an_unplaced_row_and_position_zero_both_render_empty():
    """`parse_project_order_cell` reads both back as unplaced, and a `0`
    in the cell would be a position the parser cannot return."""
    for order in (None, 0):
        assert render_row(row(5, order=order)).endswith("|  |")
        assert roundtrip([row(5, order=order)])[5]["order"] is None


def test_a_real_position_survives():
    assert roundtrip([row(5, order=12)])[5]["order"] == 12


def test_every_row_is_rendered_at_full_width():
    """The live rows are ragged -- five cells on one, six on the next --
    because five columns were appended over time and no row was rewritten.
    The generated view is canonical, so the first backup is a one-time
    reflow that changes no meaning."""
    assert cell_count(render_row(row(5))) == len(BOARD_COLUMNS)
    assert cell_count(render_row(row(6, done=True))) == len(DONE_COLUMNS)


def test_a_ragged_row_read_from_a_file_renders_full_width():
    ragged = (
        "## Board\n\n"
        "| # | Item | Status | Updated | Priority |\n"
        "|---|---|---|---|---|\n"
        "| [[#204 — Short\\|204]] | Short | ✅ Done | 09-09 |  |\n"
    )
    parsed = parse_board(ragged)["items"]
    assert cell_count(render_row(parsed[0])) == len(BOARD_COLUMNS)
    assert roundtrip(parsed)[204] == parsed[0]


def test_list_order_is_kept_and_nothing_is_sorted():
    """The rank the spec introduces is the caller's; a renderer with its
    own sort would silently outrank it."""
    numbers = [9, 3, 41, 7]
    table = render_tables([row(n) for n in numbers])["board"]
    seen = [int(line.split("[[#")[1].split(" ")[0])
            for line in table.split("\n") if "[[#" in line]
    assert seen == numbers


def test_the_two_tables_are_split_by_the_flag():
    tables = render_tables([row(1), row(2, done=True), row(3)])
    assert [r["number"] for r in parse_board("## Board\n\n" + tables["board"])["items"]] == [1, 3]
    assert [r["number"] for r in parse_board("## Done\n\n" + tables["done"])["items"]] == [2]


def test_a_pipe_in_a_title_raises():
    """`_table_rows` splits on every pipe outside a wikilink and nothing
    unescapes one, so emitting it would split the row into the wrong cells
    and lose its tail. `nova_boards.add_row` refuses the same title."""
    with pytest.raises(ValueError):
        render_row(row(5, title="a | b"))


def test_a_pipe_anywhere_else_raises_too():
    with pytest.raises(ValueError):
        render_row(row(5, milestone="a|b"))


def test_a_newline_in_a_cell_raises():
    with pytest.raises(ValueError):
        render_row(row(5, title="two\nlines"))


def test_an_empty_board_still_renders_a_parseable_table():
    tables = render_tables([])
    assert parse_board("## Board\n\n" + tables["board"])["items"] == []
    assert tables["board"].split("\n")[0].startswith("| # |")


def test_the_header_and_rule_are_dropped_by_the_parser():
    """`parse_board` finds the rule by shape, so the header must not look
    like a data row and the rule must be all dashes."""
    table = render_table([row(5)], BOARD_COLUMNS)
    header, rule, data = table.split("\n")
    assert "[[#" not in header
    assert set(rule) <= set("-|")
    assert "[[#5" in data


def test_the_done_table_cannot_carry_a_project():
    """Four columns is `parse_board`'s own shape, not a choice made here.
    The record keeps the project; the markdown view cannot say it, and a
    reader of the backup should not be told otherwise."""
    item = row(9, done=True, project="Marcus")
    assert "Marcus" not in render_tables([item])["done"]
    assert roundtrip([item])[9]["project"] == DEFAULT_PROJECT


# --- render_document -------------------------------------------------------
#
# `render_tables` draws two tables; a board file is an envelope around them
# — frontmatter, the owner's capture bullets, and every row's write-up under
# `# Details`. These tests are round trips for the same reason the ones above
# are: the failure to guard against is a document that looks right and parses
# back into something subtly different.

FRONTMATTER = "---\ntype: board\ncontract: Edvard writes in the bullets.\n---"


def contents(items, captures=(), replies=None, details=None):
    """`parse_board`'s four keys, which is also `board_records.contents`'."""
    return {
        "captures": list(captures),
        "captureReplies": [list(r) for r in (replies or [[] for _ in captures])],
        "items": list(items),
        "details": dict(details or {}),
    }


def test_document_round_trips_rows_captures_and_details():
    want = contents(
        [row(1), row(2, priority="🔴 Immediately", milestone="M4", order=1),
         row(3, done=True, where="#12")],
        captures=["something broke", "an idea"],
        replies=[["Nova, 09-09: boarded as #4"], []],
        details={1: "The write-up for one.\n\n**Nova, 09-09:** and a reply.",
                 3: "Landed in #12."},
    )
    back = parse_board(render_document(want, FRONTMATTER))
    assert back["captures"] == want["captures"]
    assert back["captureReplies"] == want["captureReplies"]
    assert back["items"] == want["items"]
    assert back["details"] == want["details"]


def test_document_keeps_the_empty_bullet_the_owner_types_into():
    """`parse_board` drops a bullet with no text, so the records cannot carry
    it and a view rebuilt from the parse alone would delete his capture box.
    Both board files state the contract in their own frontmatter."""
    document = render_document(contents([row(1)]), FRONTMATTER)
    above = document.split("## Board")[0]
    assert [line for line in above.split("\n") if line.strip() == "-"] == ["- "]


def test_the_empty_bullet_is_last_so_his_typing_lands_below_the_captures():
    document = render_document(
        contents([row(1)], captures=["something broke"]), FRONTMATTER)
    above = document.split("## Board")[0].split("---")[-1]
    bullets = [line for line in above.split("\n")
               if line.strip().startswith("- ") or line.strip() == "-"]
    assert bullets == ["- something broke", "- "]


def test_a_reply_is_indented_so_it_is_not_read_as_its_own_capture():
    document = render_document(
        contents([row(1)], captures=["his words"], replies=[["my answer"]]),
        FRONTMATTER)
    assert "\n    - my answer\n" in document
    parsed = parse_board(document)
    assert parsed["captures"] == ["his words"]
    assert parsed["captureReplies"] == [["my answer"]]


def test_frontmatter_is_kept_verbatim_and_the_captures_still_parse():
    document = render_document(contents([row(1)], captures=["hi"]), FRONTMATTER)
    assert document.startswith(FRONTMATTER + "\n")
    assert parse_board(document)["captures"] == ["hi"]


def test_a_document_without_frontmatter_still_parses():
    document = render_document(contents([row(1)], captures=["hi"]))
    assert not document.startswith("---")
    assert parse_board(document)["captures"] == ["hi"]


def test_no_done_section_when_no_row_is_in_it():
    """Both live boards have zero done rows and neither carries the heading,
    so writing an empty one would put a section on his page nobody chose."""
    assert "## Done" not in render_document(contents([row(1), row(2)]))
    assert "## Done" in render_document(contents([row(1), row(2, done=True)]))


def test_a_done_row_stays_in_the_done_table():
    document = render_document(contents([row(1), row(2, done=True)]))
    back = {item["number"]: item for item in parse_board(document)["items"]}
    assert back[2]["done"] and not back[1]["done"]


def test_an_orphan_detail_renders_without_a_title_and_keeps_its_body():
    """`parse_board` keys details by number and throws the heading text away,
    so a detail whose row is in neither table has no title left in the
    document. The body still has to survive."""
    document = render_document(contents([row(1)], details={99: "orphan body"}))
    assert "### #99 —\n" in document
    assert parse_board(document)["details"][99] == "orphan body"


def test_a_detail_with_no_body_renders_its_heading_alone():
    assert render_detail(7, "Row 7", "  \n ") == "### #7 — Row 7"


def test_an_empty_board_still_renders_a_parseable_document():
    document = render_document(contents([]))
    parsed = parse_board(document)
    assert parsed["items"] == [] and parsed["captures"] == []


def test_a_pipe_in_a_title_raises_rather_than_splitting_the_row():
    with pytest.raises(ValueError):
        render_document(contents([row(1, title="a | b")]))


# --- the width of the board table ------------------------------------------
#
# `Order` is the one `## Board` column a live board file may legitimately not
# have: it was appended to `issues.md` and never to `ideas.md`, so his ideas
# header stops at `Milestone`. Drawing it anyway made `board_publish` refuse
# `ideas.md` outright (cycle 1362) — the rendered document carried a column
# his does not. The rule is that the stored layout decides the width and the
# renderer only ever widens it, the day a row actually carries a position.

IDEAS_COLUMNS = BOARD_COLUMNS[:-1]


def laid_out(columns, extra=()):
    """A layout whose `## Board` block has exactly `columns`."""
    return [{"kind": "board", "columns": list(columns)}, *extra]


def cells(line):
    """A rendered table line's cells.

    The first cell of a row is a wikilink carrying an escaped `\\|`, the
    same one `_table_rows` masks before it splits, so counting bare pipes
    over-reports every row by one and a test that did would pass on an
    eight-cell row under a nine-cell header.
    """
    masked = line.replace("\\|", "\x00")
    return [cell.strip() for cell in masked.strip().strip("|").split("|")]


def board_header(document):
    """The `## Board` table's header cells."""
    return cells(document.split("## Board\n\n", 1)[1].splitlines()[0])


def board_body(document):
    """The `## Board` table's row lines, header and rule dropped."""
    table = document.split("## Board\n\n", 1)[1]
    return [line for line in table.splitlines()[2:] if line.startswith("|")]


def test_board_width_is_eight_until_a_row_carries_a_position():
    assert board_width([row(1), row(2)]) == len(BOARD_COLUMNS) - 1
    assert board_width([row(1), row(2, order=3)]) == len(BOARD_COLUMNS)


def test_a_done_rows_position_does_not_widen_the_board_table():
    """The `## Done` table is four columns and cannot carry a position, so a
    stray `order` on a done record must not add a column to the other one."""
    assert board_width([row(1), row(2, done=True, order=3)]) == (
        len(BOARD_COLUMNS) - 1)


def test_render_tables_draws_no_order_column_until_a_row_needs_one():
    """The fixed order, for a caller with no stored layout to be faithful to."""
    assert "Order" not in render_tables([row(1), row(2)])["board"]
    assert "Order" in render_tables([row(1), row(2, order=1)])["board"]


def test_an_eight_column_layout_renders_no_order_column():
    document = render_document(
        contents([row(1), row(2)]), FRONTMATTER,
        layout=laid_out(IDEAS_COLUMNS))
    assert board_header(document) == list(IDEAS_COLUMNS)
    assert "Order" not in board_header(document)
    assert all(len(cells(line)) == len(IDEAS_COLUMNS)
               for line in board_body(document))


def test_a_nine_column_layout_still_renders_the_order_column():
    document = render_document(
        contents([row(1), row(2)]), FRONTMATTER,
        layout=laid_out(BOARD_COLUMNS))
    assert board_header(document) == list(BOARD_COLUMNS)
    assert all(len(cells(line)) == len(BOARD_COLUMNS)
               for line in board_body(document))


def test_an_eight_column_layout_widens_when_a_row_gains_a_position():
    """The column appears the day it carries something, not before."""
    document = render_document(
        contents([row(1), row(2, order=1)]), FRONTMATTER,
        layout=laid_out(IDEAS_COLUMNS))
    assert board_header(document) == list(BOARD_COLUMNS)
    assert parse_board(document)["items"][1]["order"] == 1


def test_a_narrowed_board_still_round_trips_its_rows():
    want = contents([row(1, priority="🟠 High", milestone="M4"), row(2)])
    back = parse_board(render_document(
        want, FRONTMATTER, layout=laid_out(IDEAS_COLUMNS)))
    assert back["items"] == want["items"]


def test_render_row_refuses_to_narrow_away_a_position():
    """Truncation must never be silent: the markdown is the only copy."""
    with pytest.raises(ValueError, match="would lose the value"):
        render_row(row(1, order=7), width=len(BOARD_COLUMNS) - 1)


def test_render_row_narrows_an_empty_trailing_cell():
    line = render_row(row(1), width=len(BOARD_COLUMNS) - 1)
    assert len(cells(line)) == len(BOARD_COLUMNS) - 1


def test_render_row_pads_a_table_wider_than_the_record():
    line = render_row(row(1), width=len(BOARD_COLUMNS) + 1)
    assert len(cells(line)) == len(BOARD_COLUMNS) + 1
