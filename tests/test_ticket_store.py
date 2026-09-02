"""`agora_runner.ticket_store` -- a ticket survives the markdown and comes back.

This is the test the migration write-up asked the first cycle to write
(`research/couchdb-vs-a-real-ticket-store-2026-09-02.md`): the one-way
read into records, with a byte-identical re-render as its own test.

The fixture is deliberately not a tidy board. It carries every shape the
two live files actually contain and that a naive renderer gets wrong: an
Obsidian wikilink whose alias pipe is escaped `\\|` and therefore is not a
cell boundary, an empty priority cell, both live write-up heading shapes
(`### #N —` and `## N —`), a `## Discarded` table that is not tickets, a
`## Processed captures` archive, a `# Done — detail` second write-up
region, a write-up whose own body contains a `###` sub-heading and a
table, and trailing blank lines. Each one is a byte the round-trip has to
carry without understanding it.
"""

import pytest

from agora_runner import ticket_store


BOARD = """---
type: board
contract: He writes in the bare bullet list at the top.
---

- An unboarded capture he typed
  - Answered, Cycle 820.
- 

## Board

| # | Item | Status | Updated | Priority | Project |
|---|------|--------|---------|---|---|
| [[#3 — A third thing\\|3]] | A third thing | 🟡 In progress | 09-02 | 🟠 High | Nova |
| [[#2 — A second thing\\|2]] | A second thing | ✅ Done | 09-01 |  | Agora |
| [[#1 — The first thing\\|1]] | The first thing | ⚪ Backlog | 08-30 | ⚪ Low | Infra |

## Discarded

Kept so neither of us re-proposes them.

| Idea | Why not |
|---|---|
| Something we said no to | Rejected 08-25 |

# Details

### #3 — A third thing

His words, verbatim.

### The seam

| What | Size |
|---|---|
| a table inside a write-up | 1 |

**Nova, 09-02:** answered.

## 2 — A second thing

The older heading shape, still live in his files.

# Done — detail

### #1 — The first thing

Finished, and the write-up lives under a second region.

## Processed captures

- DONE (Cycle 247): an archived capture bullet



"""


def test_round_trip_is_byte_identical():
    records = ticket_store.to_records(BOARD)
    assert ticket_store.to_markdown(records) == BOARD


def test_every_row_becomes_a_ticket_with_its_write_up():
    records = ticket_store.to_records(BOARD)
    numbers = [ticket["number"] for ticket in records["tickets"]]
    assert numbers == [3, 2, 1]
    by_number = {ticket["number"]: ticket for ticket in records["tickets"]}
    assert by_number[3]["title"] == "A third thing"
    assert by_number[3]["status"] == "🟡 In progress"
    assert by_number[3]["priority"] == "🟠 High"
    assert by_number[3]["project"] == "Nova"
    assert by_number[2]["priority"] == ""
    # The sub-heading and the table inside the write-up stay inside it.
    assert "### The seam" in by_number[3]["details"]
    assert "| a table inside a write-up | 1 |" in by_number[3]["details"]
    assert by_number[1]["details"].strip().startswith("Finished,")


def test_the_escaped_pipe_in_a_wikilink_is_not_a_cell_boundary():
    """The one bug the live files caught, on all 398 of their rows.

    A `split("|")` cuts `[[#3 — A third thing\\|3]]` into two cells, and the
    re-render puts them back as `...\\ | 3]]`. Every character is still
    present, which is why only a byte comparison finds it.
    """
    records = ticket_store.to_records(BOARD)
    cells = records["tickets"][0]["cells"]
    assert cells[0] == "[[#3 — A third thing\\|3]]"
    assert len(cells) == 6


def test_a_write_up_with_no_row_is_carried_rather_than_dropped():
    """His words outlive his row, and losing them silently is the one
    thing this slice may not do."""
    orphaned = BOARD.replace(
        "| [[#1 — The first thing\\|1]] | The first thing | ⚪ Backlog | 08-30 | ⚪ Low | Infra |\n", "")
    records = ticket_store.to_records(orphaned)
    by_number = {ticket["number"]: ticket for ticket in records["tickets"]}
    assert 1 in by_number
    assert by_number[1]["cells"] is None
    assert "Finished," in by_number[1]["details"]
    assert ticket_store.to_markdown(records) == orphaned


def test_coverage_counts_tickets_against_the_whole_file():
    records = ticket_store.to_records(BOARD)
    owned, residue = ticket_store.coverage(BOARD, records)
    assert owned + residue == len(BOARD)
    # Pinned to the byte rather than to `> 0`, because a coverage number
    # that only has to be positive cannot tell a ticket from a blank line.
    # `owned` is the three rows plus the three write-ups; everything else
    # -- frontmatter, captures, `## Discarded`, `## Processed captures`,
    # every table header and every blank line -- is residue, and residue
    # is the number the next slice of this migration has to move.
    blocks = [kind for kind, _ in records["layout"]]
    assert blocks.count("row") == 3 and blocks.count("detail") == 3
    assert owned == 554
    assert residue == len(BOARD) - 554


def test_a_file_with_no_board_still_round_trips():
    plain = "---\ntype: board\n---\n\n- \n\n## Notes\n\nNothing here is a ticket.\n"
    records = ticket_store.to_records(plain)
    assert records["tickets"] == []
    assert ticket_store.to_markdown(records) == plain


@pytest.mark.parametrize("text", ["", "\n", "no frontmatter at all\n"])
def test_degenerate_input_round_trips(text):
    records = ticket_store.to_records(text)
    assert ticket_store.to_markdown(records) == text


def test_padding_only_and_lost_content_are_different_verdicts():
    """The tool's exit contract turns on this and nothing else.

    The live `issues.md` writes one empty priority cell as `| |` where the
    other 397 rows write `|  |`, so a round trip of that file is not
    byte-identical and loses no character of his text. That must exit 0.
    A round trip that drops a word must exit 2, and the two must not be
    the same branch.
    """
    from tools.ticket_migrate import _classify

    differing, padding_only = _classify("| a | | b |\n", "| a |  | b |\n")
    assert len(differing) == 1 and padding_only is True

    differing, padding_only = _classify("| a | keep me | b |\n", "| a |  | b |\n")
    assert len(differing) == 1 and padding_only is False


def test_a_clean_board_exits_zero_and_a_lossy_one_exits_two(monkeypatch):
    from tools import ticket_migrate

    status, records = ticket_migrate.check("board.md", BOARD)
    assert status == 0 and len(records["tickets"]) == 3

    monkeypatch.setattr(ticket_migrate.ticket_store, "to_markdown",
                        lambda records: "a word went missing\n")
    status, _ = ticket_migrate.check("board.md", BOARD)
    assert status == 2


DOUBLE_BOARDED = """---
type: board
---

- 

## Board

| # | Item | Status | Updated | Priority | Project |
|---|------|--------|---------|---|---|
| [[#1 — The first thing\\|1]] | The first thing | 🟡 In progress | 09-02 | 🟠 High | Nova |

## Done

| # | Item | Updated | Where |
|---|------|---------|-------|
| [[#1 — The first thing\\|1]] | The first thing | 08-30 | #12 |

# Details

### #1 — The first thing

His words.
"""


def test_a_number_in_two_tables_is_one_ticket_and_one_verbatim_line():
    """`parse_board` keeps the `## Board` row and calls the other a
    boarding slip. If both rows became records the ticket would render
    twice with the `## Done` row's four cells, so the second one stays
    verbatim text.
    """
    records = ticket_store.to_records(DOUBLE_BOARDED)
    assert [ticket["number"] for ticket in records["tickets"]] == [1]
    assert records["tickets"][0]["cells"][2] == "🟡 In progress"
    assert [kind for kind, _ in records["layout"]].count("row") == 1
    assert ticket_store.to_markdown(records) == DOUBLE_BOARDED
