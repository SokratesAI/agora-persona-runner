"""My own capture files get a board too -- issue #97.

His two files are boarded (a numbered row, a status, a rating, a write-up)
and mine were a flat bullet stream, so the Nova tab on the board page drew
a notes list and his drew rows. He said the tidiness was the point and the
separation was not: *"making your board like mine and giving yourself more
tidiness is an improvement"*.

The half that could go wrong quietly is the note stream. `## Board` and
`# Details` go at the *end* of my files, after `## Entries` and
`## Retired`, so `parse_notes`, `backlog_brief.head_section` and
`rolling`'s `section_bounds` all see exactly the section they saw before.
A test that only checked the new rows appeared would pass just as happily
with 654 notes silently truncated, so the two are asserted together.
"""

from agora_runner.nova_boards import parse_board, parse_notes

# The shape of one of my files after this change: prose, head bullets,
# the note stream, the retired tail, then the board at the bottom.
MINE = """---
type: note
---

# Nova — Issues

Crude capture only, my own notes, one line each.

- 2026-08-24 (Cycle 376) — a head bullet, current friction.

## Entries

- 2026-08-25 (Cycle 406) — the newest note.
- 2026-08-24 (Cycle 405) — an older note.

## Retired

- 2026-08-01 (Cycle 1) — retired, nothing reads this.

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#1 — Dead newspaper feeds\\|1]] | Dead newspaper feeds | 🟡 In progress | 08-25 | 🟠 High |
| [[#2 — Unrated row\\|2]] | Unrated row | ⚪ Backlog | 08-25 |

# Details

### #1 — Dead newspaper feeds

Four RSS feeds have failed on 82 consecutive nights.

### #2 — Unrated row

No rating on the table row above, on purpose.
"""


def test_my_board_rows_parse_like_his():
    board = parse_board(MINE)
    assert [item["number"] for item in board["items"]] == [1, 2]
    first = board["items"][0]
    assert first["title"] == "Dead newspaper feeds"
    assert first["status"] == "🟡 In progress"
    assert first["statusKey"] == "in-progress"
    assert first["priority"] == "🟠 High"
    assert first["priorityKey"] == "high"
    assert first["updated"] == "08-25"


def test_an_unrated_row_carries_no_priority():
    # Blank means nobody has looked, and that has to survive the parse --
    # a defaulted rating would make every row look considered.
    second = parse_board(MINE)["items"][1]
    assert second["priority"] == ""
    assert second["priorityKey"] == ""


def test_write_ups_come_back_keyed_by_number():
    details = parse_board(MINE)["details"]
    assert set(details) == {1, 2}
    assert "82 consecutive nights" in details[1]
    # The `### #N —` heading is not part of its own body.
    assert "Dead newspaper feeds" not in details[1]


def test_the_note_stream_is_untouched_by_the_new_sections():
    # The failure this is really about: `## Board` at the end closes
    # `## Entries`, so a splitter that read to end-of-file would swallow
    # the table and the write-ups as notes, and a `section_bounds` that
    # did not would drop nothing. Only the two real notes are notes.
    notes = parse_notes(MINE)
    assert [note["cycle"] for note in notes] == [406, 405]
    assert notes[0]["text"] == "the newest note."
    assert all("Dead newspaper feeds" not in note["text"] for note in notes)


def test_the_payload_the_server_sends_carries_my_rows():
    """The parse tests above would pass with `board_payload` untouched.

    `parse_board` is not new; pointing it at my file is. The committed
    browser fixture is what `nova_site.board_payload` actually produced,
    so asserting the two keys exist there is an assertion about the
    server rather than about the parser.
    """
    import json
    import os

    fixture = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "browser", "fixtures", "payload.json"
    )
    with open(fixture, encoding="utf-8") as handle:
        payload = json.load(handle)
    board = payload["board"]
    assert "novaItems" in board
    assert "novaDetails" in board
