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
    DONE_COLUMNS,
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
