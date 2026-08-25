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


def test_board_payload_puts_my_rows_on_the_page(monkeypatch):
    """The parse tests above would pass with `board_payload` untouched.

    `parse_board` is not new; pointing it at my own file is, and that is
    one line in `nova_site`. The first version of this test read the
    committed browser fixture and asserted the two keys were in it --
    which the reviewer on runner#354 correctly called a no-op, because
    the fixture is a file on disk and reverting the server change plus
    re-running `regen.py` would leave it passing. It was a guard
    reporting itself working while guarding nothing, which is the one
    failure shape this loop keeps paying for.

    So this calls the real `board_payload` over real markdown, through
    the read the server actually makes.
    """
    from agora_runner import nova_sources
    from agora_runner.nova_boards import BOARD_PATHS
    from agora_runner.nova_site import board_payload

    def read(path):
        if path == BOARD_PATHS["issues"]["nova"]:
            return MINE
        return ""

    monkeypatch.setattr(nova_sources, "vault_read_path", read)
    payload = board_payload("issues")

    assert [item["number"] for item in payload["novaItems"]] == [1, 2]
    assert payload["novaItems"][0]["priority"] == "\U0001f7e0 High"
    # Rendered blocks, not raw markdown -- the page draws these directly.
    assert payload["novaDetails"]["1"][0]["type"] == "p"
    # And his side is untouched by mine: an empty file for him means no
    # rows, not my rows leaking across the tab.
    assert payload["items"] == []
    assert payload["details"] == {}
    # The note stream still arrives beside the rows rather than instead
    # of them.
    assert len(payload["notes"]) == 2


def test_my_write_ups_are_windowed_like_his_on_a_list_request():
    """`board_page(limit=...)` strips his `details` and must strip mine.

    Reviewer finding on runner#354, and it is right on the substance even
    though the comment it quoted was a plan rather than a claim: at eight
    rows my write-ups are ~4KB and riding along is free, but nothing was
    going to notice at eighty. The client asks for the bodies of the row
    it opens, the same way his tab does.
    """
    from agora_runner.nova_site import board_page

    payload = {
        "name": "issues",
        "items": [],
        "details": {"9": [{"type": "p"}]},
        "novaItems": [{"number": 1}],
        "novaDetails": {"1": [{"type": "p"}]},
        "notes": [],
    }
    listed = board_page(dict(payload), limit=10)
    assert listed["details"] == {}
    assert listed["novaDetails"] == {}
    # The rows themselves stay -- they are one line each, and dropping
    # them would empty the tab rather than window it.
    assert listed["novaItems"] == [{"number": 1}]

    # No limit is the pre-#354 client asking for everything; it still gets
    # everything, which is what that shape is for.
    whole = board_page(dict(payload))
    assert whole["novaDetails"] == {"1": [{"type": "p"}]}


def test_asking_for_one_of_my_rows_returns_its_write_up():
    """`?item=N&mine=1` -- the fetch the expanded row makes now.

    Deliberately a separate flag rather than reusing `item=`: his #1 and
    my #1 are different rows on the same page, and answering one query
    with whichever dict happened to have the key is the collision this
    keeps out.
    """
    from agora_runner.nova_site import board_page

    payload = {
        "name": "issues",
        "items": [{"number": 1, "title": "his"}],
        "details": {"1": [{"type": "p", "text": "his body"}]},
        "novaItems": [{"number": 1, "title": "mine"}],
        "novaDetails": {"1": [{"type": "p", "text": "my body"}]},
        "notes": [],
    }
    mine = board_page(payload, item=1, mine=True)
    assert mine["found"] is True
    assert mine["item"]["title"] == "mine"
    assert mine["item"]["blocks"][0]["text"] == "my body"

    his = board_page(payload, item=1)
    assert his["item"]["title"] == "his"
    assert his["item"]["blocks"][0]["text"] == "his body"
