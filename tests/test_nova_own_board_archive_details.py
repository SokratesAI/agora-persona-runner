"""A write-up of mine may live in the archive, and the page still draws it.

My own `issues.md` is 141,832 bytes and 82,839 of that is `# Details` --
the write-ups for 26 boarded rows -- so the file cannot be fetched in one
`get` while every check on it reports clean. The obvious fix is to roll a
finished row's write-up into `issues-archive.md` the way
`tools/roll_captures.py` already rolls my older captures, and that fix was
unsafe to write: `board_payload` built `novaDetails` from the *live* file
alone, so a rolled write-up would leave its row on the board with an empty
body, on the page, with nothing failing anywhere.

So this is the half that has to ship first. Nothing rolls a body yet; what
changes here is only that the page can read one when something does.
"""

MINE = """---
type: note
---

# Nova — Issues

## Entries

- 2026-08-25 (Cycle 406) — the newest note.

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#1 — Dead newspaper feeds\\|1]] | Dead newspaper feeds | ✅ Done | 08-25 | 🟠 High |
| [[#2 — Unrated row\\|2]] | Unrated row | ⚪ Backlog | 08-25 |

# Details

### #2 — Unrated row

The live write-up, still where it always was.
"""

ARCHIVE = """---
type: note
maintenance: Captures rolled off issues.md land here.
---

# Nova — Issues (archive)

## Entries

- 2026-08-01 (Cycle 1) — a rolled-off note.

# Details

### #1 — Dead newspaper feeds

Four RSS feeds have failed on 82 consecutive nights.
"""


def _plain(blocks):
    """The text of a rendered write-up. `render_blocks` returns spans, not
    a string, and asserting on the block dict's shape rather than on its
    words made this test pass on a body it had never read."""
    return " ".join(
        span["text"]
        for block in blocks
        for span in block.get("spans", [])
        if "text" in span
    )


def _payload(monkeypatch, live=MINE, archive=ARCHIVE):
    from agora_runner import nova_sources
    from agora_runner.nova_boards import BOARD_PATHS
    from agora_runner.nova_site import board_payload

    paths = BOARD_PATHS["issues"]

    def read(path):
        if path == paths["nova"]:
            return live
        if path == paths["nova_archive"]:
            return archive
        return ""

    monkeypatch.setattr(nova_sources, "vault_read_path", read)
    return board_payload("issues")


def test_a_rolled_write_up_still_reaches_the_page(monkeypatch):
    payload = _payload(monkeypatch)

    # Both rows are on the board and both carry a body, though only one
    # of the two bodies is in the live file.
    assert [item["number"] for item in payload["novaItems"]] == [1, 2]
    assert set(payload["novaDetails"]) == {"1", "2"}
    rendered = payload["novaDetails"]["1"]
    assert rendered[0]["type"] == "p"
    assert "82 consecutive nights" in _plain(rendered)


def test_without_the_archive_that_row_would_draw_an_empty_body(monkeypatch):
    """The precondition the test above needs, asserted rather than assumed.

    A merge test passes whether or not the merge does anything if the live
    file happens to carry the body too. This is the same fixture with the
    archive taken away: row 1 is still on the board and its write-up is
    gone, which is exactly what the page would have shown after a roll.
    """
    payload = _payload(monkeypatch, archive="")

    assert [item["number"] for item in payload["novaItems"]] == [1, 2]
    assert set(payload["novaDetails"]) == {"2"}


def test_the_live_write_up_wins_when_both_files_have_one(monkeypatch):
    """A number in both files is a body that was rolled and then written
    again, and the live file is the newer of the two."""
    archive = ARCHIVE.replace(
        "### #1 — Dead newspaper feeds", "### #2 — Unrated row"
    )
    payload = _payload(monkeypatch, archive=archive)

    text = _plain(payload["novaDetails"]["2"])
    assert "still where it always was" in text
    assert "82 consecutive nights" not in text


def test_the_archive_contributes_bodies_and_never_rows(monkeypatch):
    """An archived write-up does not put a second row on the board.

    The archive has no `## Board` table, so `parse_board` finds no items
    in it -- but a future archive that grew one must not add rows here.
    """
    archive = ARCHIVE + """
## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#9 — A row nobody put here\\|9]] | A row nobody put here | ⚪ Backlog | 08-01 |
"""
    payload = _payload(monkeypatch, archive=archive)

    assert [item["number"] for item in payload["novaItems"]] == [1, 2]


def test_a_rolled_write_up_is_searchable(monkeypatch):
    """`board_page` windows `novaDetails` away on every list request, so
    the page cannot search a write-up itself -- rolled or not."""
    blobs = _payload(monkeypatch)["novaSearchText"]

    assert set(blobs) == {"1", "2"}
    assert "82 consecutive nights" in blobs["1"]


def test_both_note_streams_still_arrive(monkeypatch):
    """The live and the archived note, once each. `board_markdown` parses
    the two files separately for this reason and the merge above must not
    have disturbed it."""
    notes = _payload(monkeypatch)["notes"]

    assert [note["cycle"] for note in notes] == [406, 1]
